"""
mcp_server.py — VoiceVault tools for AI agents (Model Context Protocol, stdio).

Lets a local LLM or any MCP client (Claude, LM Studio, Open WebUI, ...) use VoiceVault through
its running HTTP API. Standard library only; start the VoiceVault backend first.

    python backend/mcp_server.py                     # talks to http://127.0.0.1:7763
    set VOICEVAULT_URL=http://127.0.0.1:7763         # optional override

Built to be hard to misuse, also by small local models:
  - No tool deletes, moves or overwrites anything. Library edits only add or change metadata,
    and labels are merged unless the agent asks to remove one.
  - Voices, models, songs and presets can be named loosely ("mara", "drama q4"); unclear or
    unknown names fail with the closest matches instead of guessing.
  - Unknown arguments, wrong types and out-of-range values are rejected with a message that
    says what to send instead. Every error ends with the next step to try.
  - Long work (music, voice conversion) returns a job id; vv_job_status reports progress.
  - Results never include file paths from the user's PC, only ids, names and URLs.
See docs/AGENT_MANUAL.md.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

PROTOCOL = "2025-06-18"
SERVER = {"name": "voicevault", "version": "0.1.0"}
BASE = os.environ.get("VOICEVAULT_URL", "http://127.0.0.1:7763").rstrip("/")
PATH_KEYS = {"path", "paths", "root", "roots", "runtime", "file_path", "folder", "source_path", "original_file",
             "preview_file", "dir"}


class ToolError(Exception):
    """A mistake the agent can fix; the message says how."""


# ── HTTP ────────────────────────────────────────────────────────────────────

def api(method: str, path: str, body: Any = None, params: dict | None = None, timeout: float = 60) -> Any:
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get("detail")
        except Exception:
            detail = None
        if isinstance(detail, list):  # FastAPI validation errors
            detail = "; ".join(f"{'.'.join(str(x) for x in d.get('loc', [])[1:])}: {d.get('msg')}" for d in detail)
        raise ToolError(f"VoiceVault refused the request ({exc.code}): {detail or exc.reason}.")
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise ToolError(f"VoiceVault is not reachable at {BASE} ({getattr(exc, 'reason', exc)}). "
                        "Ask the user to start VoiceVault, then try again.")


def scrub(value: Any) -> Any:
    """Drop local file paths so nothing about the user's PC reaches the agent."""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if k not in PATH_KEYS}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, str) and re.match(r"^[A-Za-z]:[\\/]|^/(home|Users|mnt)/", value):
        return "(local path hidden)"
    return value


def url(path: str) -> str:
    return BASE + path


# ── Name resolution ─────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def resolve(query: str, items: list[dict], key: Callable[[dict], str], ident: Callable[[dict], str], what: str) -> dict:
    """Exact id, exact name, or the one item whose name contains every word. Never a guess."""
    if not items:
        raise ToolError(f"There are no {what}s yet.")
    q = _norm(query)
    for it in items:
        if query == ident(it) or q == _norm(key(it)):
            return it
    words = q.split()
    hits = [it for it in items if words and all(w in _norm(key(it)) or w in _norm(ident(it)) for w in words)]
    if len(hits) == 1:
        return hits[0]
    names = [key(it) for it in items]
    if len(hits) > 1:
        raise ToolError(f"'{query}' matches {len(hits)} {what}s: " + "; ".join(key(h) for h in hits[:8]) +
                        f". Use one of these exact names (or its id).")
    close = difflib.get_close_matches(query, names, n=5, cutoff=0.3) or         [key(it) for it in items if any(w in _norm(key(it)) for w in words)][:5]
    hint = f" Did you mean: {'; '.join(close)}?" if close else f" Call the list tool to see all {what}s."
    raise ToolError(f"No {what} called '{query}'.{hint}")


def voices() -> list[dict]:
    return api("GET", "/api/tts/references").get("items", [])


def find_voice(name: str) -> dict:
    return resolve(name, voices(), lambda v: v.get("name") or "", lambda v: v["reference_id"], "voice")


def models() -> tuple[list[dict], dict]:
    m = api("GET", "/api/tts/models").get("models", [])
    engines = {e["id"]: e for e in api("GET", "/api/tts/engines").get("engines", [])}
    ready = [x for x in m if x.get("state") == "integrated"]
    seen: dict[str, int] = {}
    for x in ready:  # unique, readable names, so a model can always be picked by name
        base = _base_label(x)
        seen[base] = seen.get(base, 0) + 1
        x["label"] = base if seen[base] == 1 else f"{base} #{seen[base]}"
    return ready, engines


def model_label(m: dict) -> str:
    return m.get("label") or _base_label(m)


def _base_label(m: dict) -> str:
    """Readable name: family plus the meaningful part of the folder/file, never a hash or a path."""
    parts = []
    for seg in m["id"].split(":", 1)[-1].replace("\\", "/").split("/"):
        if re.fullmatch(r"[0-9a-f]{8,}", seg) or seg in ("snapshots", "TTS", "models"):
            continue
        hub = re.fullmatch(r"models--[^-]+(?:-[^-]+)*?--(.+)", seg)
        parts.append(hub.group(1) if hub else re.sub(r"\.gguf$", "", seg))
    name = parts[-1] if parts else m["engine_id"]
    if len(name) > 48:
        name = name[:45] + "…"
    return f"{m.get('family') or m['engine_id']} ({name})"


# ── Argument checking ───────────────────────────────────────────────────────

def check(args: dict, schema: dict) -> dict:
    props, required = schema.get("properties", {}), schema.get("required", [])
    unknown = [k for k in args if k not in props]
    if unknown:
        raise ToolError(f"Unknown argument(s): {', '.join(unknown)}. Allowed: {', '.join(props)}.")
    for k in required:
        if args.get(k) in (None, ""):
            raise ToolError(f"'{k}' is required: {props[k].get('description', '')}")
    out = {}
    for k, v in args.items():
        spec = props[k]
        t = spec.get("type")
        if v is None:
            continue
        if t == "integer":
            if isinstance(v, str) and re.fullmatch(r"-?\d+", v.strip()):
                v = int(v)
            if not isinstance(v, int) or isinstance(v, bool):
                raise ToolError(f"'{k}' must be a whole number.")
        elif t == "number":
            if isinstance(v, str):
                try:
                    v = float(v)
                except ValueError:
                    pass
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                raise ToolError(f"'{k}' must be a number.")
        elif t == "string":
            if not isinstance(v, str):
                v = str(v)
            v = v.strip()
            if "maxLength" in spec and len(v) > spec["maxLength"]:
                raise ToolError(f"'{k}' is too long ({len(v)} characters, at most {spec['maxLength']}).")
        elif t == "boolean":
            if isinstance(v, str) and v.lower() in ("true", "false"):
                v = v.lower() == "true"
            if not isinstance(v, bool):
                raise ToolError(f"'{k}' must be true or false.")
        elif t == "array":
            if isinstance(v, str):
                v = [s.strip() for s in v.split(",") if s.strip()]
            if not isinstance(v, list):
                raise ToolError(f"'{k}' must be a list.")
        if "enum" in spec and v not in spec["enum"]:
            raise ToolError(f"'{k}' must be one of: {', '.join(map(str, spec['enum']))}.")
        if "minimum" in spec and v < spec["minimum"] or "maximum" in spec and v > spec["maximum"]:
            raise ToolError(f"'{k}' must be between {spec.get('minimum')} and {spec.get('maximum')}.")
        out[k] = v
    return out


# ── Tools ───────────────────────────────────────────────────────────────────

TOOLS: dict[str, dict] = {}


def tool(name: str, description: str, properties: dict | None = None, required: list | None = None,
         read_only: bool = True):
    def deco(fn):
        TOOLS[name] = {"fn": fn, "schema": {"type": "object", "properties": properties or {},
                                            "required": required or [], "additionalProperties": False},
                       "description": description, "read_only": read_only}
        return fn
    return deco


S = lambda d, **kw: {"type": "string", "description": d, **kw}
I = lambda d, **kw: {"type": "integer", "description": d, **kw}
N = lambda d, **kw: {"type": "number", "description": d, **kw}
B = lambda d: {"type": "boolean", "description": d}
A = lambda d: {"type": "array", "items": {"type": "string"}, "description": d}


@tool("vv_status", "Check that VoiceVault is running and list what it can do right now (speech models, music engines). "
      "Call this first.")
def vv_status(_):
    health = api("GET", "/api/health")
    ready, _engines = models()
    try:
        music = api("GET", "/api/music/status")
        music_roles = sorted((music.get("packages") or {}).keys())
    except ToolError:
        music_roles = []
    return {"voicevault": health.get("status"), "version": health.get("version"),
            "speech_models": [model_label(m) for m in ready], "voices": len(voices()),
            "music_tools": music_roles,
            "next": "vv_list_voices to pick a voice, then vv_speak. For music: vv_music_list_songs."}


@tool("vv_list_speech_models", "List speech models that are ready, with what each can do (clone a voice, describe a voice, languages).")
def vv_list_speech_models(_):
    ready, engines = models()
    out = []
    for m in ready:
        cap = (engines.get(m["engine_id"]) or {}).get("capabilities") or {}
        out.append({"name": model_label(m), "model_id": m["id"], "clones_voices": bool(cap.get("reference_audio")),
                    "needs_voice": bool(cap.get("reference_required")),
                    "voice_from_description": bool(cap.get("voice_description")),
                    "languages": cap.get("languages") or [], "emotion_tags": (cap.get("emotion_tags") or [])[:20],
                    "notes": m.get("notes")})
    return {"models": out}


@tool("vv_list_voices", "List saved voices (cloning references), optionally filtered by words in the name or transcript.",
      {"query": S("Optional words to filter by, e.g. 'mara'.", maxLength=100),
       "limit": I("How many to return (default 50).", minimum=1, maximum=500)})
def vv_list_voices(a):
    items = voices()
    q = _norm(a.get("query", ""))
    if q:
        items = [v for v in items if all(w in _norm((v.get("name") or "") + " " + (v.get("transcript") or "")) for w in q.split())]
    return {"voices": [{"name": v.get("name"), "voice_id": v["reference_id"], "seconds": v.get("duration_s"),
                        "transcript": (v.get("transcript") or "")[:160], "listen": url(v["url"])}
                       for v in items[: a.get("limit", 50)]], "total": len(items)}


def _pick_model(query: str | None, need_clone: bool) -> dict:
    ready, engines = models()
    if query:
        m = resolve(query, ready, model_label, lambda m: m["id"], "speech model")
        cap = (engines.get(m["engine_id"]) or {}).get("capabilities") or {}
        if need_clone and not cap.get("reference_audio"):
            raise ToolError(f"{model_label(m)} cannot use a saved voice. Leave 'voice' empty, or pick a model "
                            "with clones_voices=true from vv_list_speech_models.")
        return m
    def cap(m):
        return (engines.get(m["engine_id"]) or {}).get("capabilities") or {}
    pool = [m for m in ready if cap(m).get("reference_audio")] if need_clone else \
           [m for m in ready if not cap(m).get("reference_required")]
    pool = [m for m in pool if "cloud" not in m["engine_id"]] or pool
    if not pool:
        raise ToolError("No ready speech model fits. Call vv_list_speech_models and pass 'model'.")
    pref = ("fish_drama3", "omnivoice", "breeze", "vibevoice", "maya1")
    pool.sort(key=lambda m: (pref.index(m["engine_id"]) if m["engine_id"] in pref else 99,
                             "q4" not in m["id"].lower()))
    return pool[0]


@tool("vv_speak", "Turn text into speech and save it to the Library (Generations). Give a saved 'voice' to clone it, "
      "or a 'voice_description' for models that support it. Takes from seconds to a few minutes.",
      {"text": S("What to say. Up to 5000 characters.", maxLength=5000),
       "voice": S("Optional saved voice name or id from vv_list_voices.", maxLength=120),
       "voice_description": S("Optional description, e.g. 'warm elderly male narrator' (models with voice_from_description).", maxLength=1000),
       "model": S("Optional speech model name or id from vv_list_speech_models. Picked automatically if empty.", maxLength=200),
       "language": S("Optional language, e.g. 'en'.", maxLength=40),
       "seed": I("Optional seed for a repeatable result.", minimum=0, maximum=2**32 - 1)},
      ["text"], read_only=False)
def vv_speak(a):
    voice = find_voice(a["voice"]) if a.get("voice") else None
    m = _pick_model(a.get("model"), need_clone=voice is not None)
    body = {"model_id": m["id"], "text": a["text"]}
    if voice:
        body["reference_id"] = voice["reference_id"]
        if voice.get("transcript"):
            body["reference_text"] = voice["transcript"]
    for k in ("voice_description", "language", "seed"):
        if a.get(k) not in (None, ""):
            body[k] = a[k]
    r = api("POST", "/api/tts/synthesize", body, timeout=1800)
    f = r.get("file")
    return {"saved_as": f, "listen": url(f"/api/tts/audio/{f}") if f else None, "model": model_label(m),
            "voice": voice.get("name") if voice else None, "seconds": r.get("duration_s"),
            "warnings": ((r.get("meta") or {}).get("quality") or {}).get("warnings") or [],
            "library_key": f"generation:{f}" if f else None}


@tool("vv_library_search", "Search everything in the Library: voices, generations, harvested clips, Voice Lab uploads, music, sound effects.",
      {"query": S("Optional words (title, labels, character, category, notes, transcript).", maxLength=200),
       "type": S("Optional type filter.", enum=["voice", "generation", "source", "clip", "voicelab", "music", "sfx"]),
       "character": S("Optional character or actor name.", maxLength=120),
       "label": S("Optional label.", maxLength=40),
       "limit": I("How many to return (default 30).", minimum=1, maximum=300)})
def vv_library_search(a):
    r = api("GET", "/api/library/items", params={"q": a.get("query"), "type": a.get("type"),
                                                 "character": a.get("character"), "label": a.get("label")})
    items = r.get("items", [])
    keep = ("key", "type", "title", "character", "labels", "category", "notes", "duration_s", "created", "detail")
    return {"items": [{**{k: i.get(k) for k in keep}, "listen": url(i["play_url"]) if i.get("play_url") else None}
                      for i in items[: a.get("limit", 30)]],
            "total": len(items), "all_labels": r.get("labels"), "all_categories": r.get("categories")}


@tool("vv_library_tag", "Add labels, a category, notes or a character/actor to one Library item. Never deletes the item. "
      "Labels are ADDED to existing ones; use remove_labels to take some off. Music and sound effects cannot have a character.",
      {"key": S("Item key from vv_library_search, e.g. 'voice:abc…'.", maxLength=200),
       "add_labels": A("Labels to add."), "remove_labels": A("Labels to remove."),
       "category": S("Optional category to set.", maxLength=80),
       "notes": S("Optional notes to set (replaces the old notes).", maxLength=2000),
       "character": S("Optional character or actor name to assign.", maxLength=120),
       "character_kind": S("What the name is.", enum=["character", "actor", "person"]),
       "title": S("Optional new title.", maxLength=120)},
      ["key"], read_only=False)
def vv_library_tag(a):
    items = api("GET", "/api/library/items").get("items", [])
    item = next((i for i in items if i["key"] == a["key"]), None)
    if item is None:
        raise ToolError(f"No Library item with key '{a['key']}'. Use a key exactly as vv_library_search returns it.")
    if a.get("character") and item.get("assignable") is False:
        raise ToolError("Music and sound effects cannot have a character. Use labels or a category instead.")
    patch: dict[str, Any] = {"key": a["key"]}
    if a.get("add_labels") or a.get("remove_labels"):
        drop = {_norm(x) for x in a.get("remove_labels") or []}
        labels = [l for l in item.get("labels") or [] if _norm(l) not in drop]
        labels += [l for l in a.get("add_labels") or [] if _norm(l) not in {_norm(x) for x in labels}]
        patch["labels"] = labels
    for k in ("category", "notes", "title"):
        if a.get(k) is not None:
            patch[k] = a[k]
    if a.get("character"):
        patch["character"] = {"name": a["character"], "kind": a.get("character_kind") or "character"}
    if len(patch) == 1:
        raise ToolError("Nothing to change. Pass add_labels, remove_labels, category, notes, character or title.")
    r = api("PATCH", "/api/library/items", patch)
    return {k: r.get(k) for k in ("key", "title", "character", "labels", "category", "notes")}


@tool("vv_voice_presets", "List Voice Lab presets (age, gender, characters, effects) that vv_voice_shape can apply.")
def vv_voice_presets(_):
    p = api("GET", "/api/voice/presets").get("presets", {})
    return {"presets": [{"preset": k, "name": v.get("label"), "group": v.get("group")} for k, v in p.items()]}


def _source(a: dict) -> dict:
    if a.get("voice"):
        return {"kind": "reference", "id": find_voice(a["voice"])["reference_id"]}
    if a.get("generation"):
        return {"kind": "generation", "name": a["generation"]}
    raise ToolError("Say what audio to use: 'voice' (a saved voice) or 'generation' (a file name from vv_speak or vv_library_search).")


@tool("vv_voice_shape", "Reshape a voice with a Voice Lab preset (e.g. older, younger, female version, robot). "
      "Makes a preview only; call vv_voice_save to keep it.",
      {"preset": S("Preset key or name from vv_voice_presets.", maxLength=80),
       "voice": S("A saved voice to reshape (name or id).", maxLength=120),
       "generation": S("Or a generation file name to reshape.", maxLength=200)},
      ["preset"], read_only=False)
def vv_voice_shape(a):
    presets = api("GET", "/api/voice/presets").get("presets", {})
    items = [{"key": k, **v} for k, v in presets.items()]
    p = resolve(a["preset"], items, lambda x: x.get("label") or x["key"], lambda x: x["key"], "preset")
    body = {"source": _source(a), "simple": p.get("simple") or {}, "advanced": p.get("advanced") or {},
            "effects": p.get("effects") or []}
    r = api("POST", "/api/voice/shape", body, timeout=600)
    pid = r.get("preview_id") or r.get("id")
    return {"preview_id": pid, "listen": url(f"/api/voice/previews/{pid}"), "preset": p.get("label"),
            "next": "If it sounds right, call vv_voice_save with this preview_id."}


@tool("vv_voice_convert", "Voice-to-voice: keep the delivery of one recording but make it sound like another saved voice. "
      "Takes about 5–7× the clip length. Makes a preview only; call vv_voice_save to keep it.",
      {"target_voice": S("The saved voice it should sound like (name or id).", maxLength=120),
       "voice": S("Source: a saved voice (name or id).", maxLength=120),
       "generation": S("Or source: a generation file name.", maxLength=200)},
      ["target_voice"], read_only=False)
def vv_voice_convert(a):
    target = find_voice(a["target_voice"])
    body = {"source": _source(a), "target_reference_id": target["reference_id"], "enhance": True}
    r = api("POST", "/api/voice/convert", body, timeout=3600)
    pid = r.get("preview_id") or r.get("id")
    out = {"preview_id": pid, "listen": url(f"/api/voice/previews/{pid}"), "target": target.get("name"),
           "next": "If it sounds right, call vv_voice_save with this preview_id."}
    if r.get("enhance_error"):
        out["warning"] = r["enhance_error"]
    return out


@tool("vv_voice_save", "Keep a Voice Lab preview: as a generation (a finished clip) or as a new saved voice for cloning.",
      {"preview_id": S("From vv_voice_shape or vv_voice_convert.", maxLength=64),
       "save_as": S("What to save it as.", enum=["generation", "voice"]),
       "name": S("Name for it.", maxLength=120),
       "transcript": S("Optional words spoken (helps cloning when saved as a voice).", maxLength=2000)},
      ["preview_id", "save_as", "name"], read_only=False)
def vv_voice_save(a):
    body = {"preview_id": a["preview_id"], "as": "reference" if a["save_as"] == "voice" else "generation", "name": a["name"]}
    if a.get("transcript"):
        body["transcript"] = a["transcript"]
    r = api("POST", "/api/voice/save", body)
    if a["save_as"] == "voice":
        rid = r.get("reference_id") or r.get("id")
        return {"saved_as": "voice", "name": r.get("name") or a["name"], "voice_id": rid,
                "library_key": f"voice:{rid}" if rid else None,
                "next": "Use it with vv_speak voice=<this name>."}
    f = r.get("file")
    return {"saved_as": "generation", "name": a["name"], "file": f,
            "listen": url(f"/api/tts/audio/{f}") if f else None, "library_key": f"generation:{f}" if f else None}


# Music ---------------------------------------------------------------------

def songs() -> list[dict]:
    return api("GET", "/api/music/projects")


def find_song(name: str) -> dict:
    return resolve(name, songs(), lambda s: s.get("title") or "", lambda s: s["id"], "song")


def _take(song: dict, take: str | None) -> dict:
    takes = song.get("takes") or []
    if not takes:
        raise ToolError("This song has no takes yet. Compose one with vv_music_compose first.")
    if not take:
        return next((t for t in takes if t["id"] == song.get("active_take")), takes[-1])
    return resolve(take, takes, lambda t: t.get("label") or "", lambda t: t["id"], "take")


@tool("vv_music_list_songs", "List songs in the Music Studio with their takes.",
      {"song": S("Optional song name or id to show in detail (style, lyrics, takes, analysis).", maxLength=120)})
def vv_music_list_songs(a):
    if a.get("song"):
        s = api("GET", f"/api/music/projects/{find_song(a['song'])['id']}")
        return {"song": s["title"], "song_id": s["id"], "style": s.get("style"), "lyrics": s.get("lyrics"),
                "takes": [{"take": t["id"], "label": t.get("label"), "made_by": t.get("op"), "seconds": t.get("duration"),
                           "active": t["id"] == s.get("active_take"), "has_melody_sheet": bool(t.get("abc")),
                           "analysis": (t.get("analysis") or {}).get("suggestion"),
                           "listen": url(f"/api/music/projects/{s['id']}/takes/{t['id']}/audio")} for t in s.get("takes", [])]}
    return {"songs": [{"song": s.get("title"), "song_id": s["id"], "takes": s.get("takes"), "style": s.get("style")}
                      for s in songs()]}


@tool("vv_music_create_song", "Create a new song project with a style and lyrics. Does not compose yet.",
      {"title": S("Song title.", maxLength=120),
       "style": S("Style tags: genre, instruments, vocal, mood, mix. See vv_music_style_words.", maxLength=1000),
       "lyrics": S("Lyrics with section headers on their own lines: [Verse] [Chorus] [Bridge] [Outro]. "
                   "Use [Instrumental] for no singing.", maxLength=8000)},
      ["title"], read_only=False)
def vv_music_create_song(a):
    s = api("POST", "/api/music/projects", {"title": a["title"], "style": a.get("style", ""), "lyrics": a.get("lyrics", "")})
    return {"song_id": s["id"], "song": s["title"], "next": "vv_music_compose to make the first take."}


@tool("vv_music_style_words", "Words that work well in a music style prompt, by group, plus tips per engine.")
def vv_music_style_words(_):
    v = api("GET", "/api/music/style-vocabulary")
    return {"groups": v.get("groups"), "tips": {
        "yue2": "Short tags: genre, 2-4 instruments, vocal gender + timbre, mood. Full songs with lyrics; no BPM/key fields.",
        "ace_step": "Comma-separated tags, most important first; set bpm, key and beats_per_bar as fields. "
                    "Add 'instrumental' and use [Instrumental] lyrics for no vocals."}}


@tool("vv_music_compose", "Compose a new take for a song (adds a take, never replaces one). Returns a job id; "
      "follow it with vv_job_status. YuE2 takes several minutes; ACE-Step is faster.",
      {"song": S("Song name or id.", maxLength=120),
       "engine": S("yue2 = full songs with planned melody; ace_step = fast, takes BPM and key.", enum=["yue2", "ace_step"]),
       "style": S("Optional style for this take; empty = the song's style.", maxLength=1000),
       "lyrics": S("Optional lyrics for this take; empty = the song's lyrics.", maxLength=8000),
       "seconds": I("ACE-Step length in seconds (default 120).", minimum=10, maximum=600),
       "bpm": I("ACE-Step tempo.", minimum=40, maximum=240),
       "key": S("ACE-Step key, e.g. 'A minor'.", maxLength=20),
       "beats_per_bar": S("ACE-Step beats per bar.", enum=["2", "3", "4", "6"]),
       "seed": I("Optional seed.", minimum=0, maximum=2**32 - 1)},
      ["song"], read_only=False)
def vv_music_compose(a):
    song = find_song(a["song"])
    engine = a.get("engine") or "yue2"
    if a.get("key") and not re.fullmatch(r"[A-Ga-g][#b]?\s+(major|minor)", a["key"]):
        raise ToolError("'key' must look like 'C major' or 'F# minor'.")
    params = {"engine": engine, "plan": "full", "steps": 8, "seed": a.get("seed"),
              "duration": a.get("seconds") or 120, "bpm": a.get("bpm"), "key": a.get("key"),
              "time_signature": a.get("beats_per_bar")}
    for k in ("style", "lyrics"):
        if a.get(k):
            params[k] = a[k]
    job = api("POST", f"/api/music/projects/{song['id']}/jobs", {"kind": "generate", "params": params})
    return {"job_id": job["id"], "status": job.get("status"), "next": "Call vv_job_status with this job_id until it is done."}


@tool("vv_music_analyze", "Measure a take's tempo, key and beats per bar, and suggest style tags (genre, mood, instruments). "
      "Good for 'make something like this song'. Returns a job id.",
      {"song": S("Song name or id.", maxLength=120), "take": S("Optional take label or id; default the active take.", maxLength=120)},
      ["song"], read_only=False)
def vv_music_analyze(a):
    song = api("GET", f"/api/music/projects/{find_song(a['song'])['id']}")
    t = _take(song, a.get("take"))
    job = api("POST", f"/api/music/projects/{song['id']}/jobs", {"kind": "analyze", "params": {"take": t["id"]}})
    return {"job_id": job["id"], "next": "Call vv_job_status; the result's 'analysis' holds the suggested style, bpm and key."}


@tool("vv_job_status", "Progress or result of a music job from vv_music_compose or vv_music_analyze.",
      {"job_id": S("The job id.", maxLength=40)}, ["job_id"])
def vv_job_status(a):
    j = api("GET", f"/api/music/jobs/{urllib.parse.quote(a['job_id'])}")
    out = {"status": j.get("status"), "message": j.get("message"), "error": j.get("error"), "seconds": j.get("seconds"),
           "queue_position": j.get("queue_position")}
    if j.get("status") == "done" and j.get("take"):
        s = api("GET", f"/api/music/projects/{j['project']}")
        t = next((t for t in s.get("takes", []) if t["id"] == j["take"]), None)
        if t:
            out["take"] = {"take": t["id"], "label": t.get("label"), "seconds": t.get("duration"),
                           "listen": url(f"/api/music/projects/{s['id']}/takes/{t['id']}/audio")}
            if j.get("kind") == "analyze" and t.get("analysis"):
                an = t["analysis"]
                out["analysis"] = {"suggestion": an.get("suggestion"), "bpm": (an.get("tempo") or {}).get("bpm"),
                                   "key": (an.get("key") or {}).get("key"), "tags": an.get("tags")}
    elif j.get("status") in ("queued", "running"):
        out["next"] = "Still working. Check again in 20-60 seconds."
    return out


# Chats and memory ------------------------------------------------------------

def _chats() -> list[dict]:
    return api("GET", "/api/converse/sessions").get("sessions", [])


def _chat_label(c: dict) -> str:
    name = ((c.get("profile") or {}).get("name") or "").strip()
    return f"{c.get('title') or 'Chat'}" + (f" ({name})" if name and name not in (c.get("title") or "") else "")


@tool("vv_chat_list", "List saved chats and scenes, newest first, with their character and length. "
      "Optionally search titles and message text.",
      {"query": S("Optional words to search for in chat titles and messages.", maxLength=200),
       "limit": I("How many to return (default 30).", minimum=1, maximum=200)})
def vv_chat_list(a):
    chats = (api("GET", "/api/converse/sessions", params={"q": a["query"]}).get("sessions", [])
             if a.get("query") else _chats())
    return {"chats": [{"chat": _chat_label(c), "chat_id": c["id"], "kind": c.get("kind"),
                       "character": ((c.get("profile") or {}).get("name") or None),
                       "messages": c.get("messages_total"), "updated": c.get("updated_at"),
                       "matches": [m.get("text") for m in c.get("matches", [])][:2]}
                      for c in chats[: a.get("limit", 30)]]}


@tool("vv_memory_recall", "Search everything VoiceVault's chats remember: matching messages from any chat, "
      "summaries of long chats, and saved facts. Use it before answering questions about past conversations. "
      "Give a character to search only that persona's chats.",
      {"query": S("What to look for, e.g. 'name of my dog' or 'promise about the trip'.", maxLength=300),
       "character": S("Optional character or persona name.", maxLength=120),
       "chat": S("Optional chat title or id to search only that chat.", maxLength=160),
       "limit": I("How many results per kind (default 8).", minimum=1, maximum=50)},
      ["query"])
def vv_memory_recall(a):
    params = {"q": a["query"], "limit": a.get("limit", 8)}
    if a.get("character"):
        params["character"] = a["character"]
    if a.get("chat"):
        params["session_id"] = resolve(a["chat"], _chats(), _chat_label, lambda c: c["id"], "chat")["id"]
    return api("GET", "/api/converse/memory/search", params=params)


@tool("vv_memory_remember", "Save a lasting memory for chats: about the user (every chat), a character (every chat "
      "with that persona), or one chat. Never deletes anything; near-duplicates are skipped.",
      {"text": S("The memory, one short sentence, e.g. 'Sam is allergic to peanuts.'", maxLength=600),
       "scope": S("Who or what it is about.", enum=["user", "character", "chat"]),
       "chat": S("The chat it belongs to (title or id). Needed for 'chat' and 'character' scopes.", maxLength=160),
       "character": S("Character name, for the 'character' scope when the chat has several characters.", maxLength=120)},
      ["text", "scope"], read_only=False)
def vv_memory_remember(a):
    chats = _chats()
    if a.get("chat"):
        chat = resolve(a["chat"], chats, _chat_label, lambda c: c["id"], "chat")
    elif a["scope"] == "user" and chats:
        chat = chats[0]
    else:
        raise ToolError("Say which chat this memory belongs to ('chat'), or use scope 'user'.")
    body = {"text": a["text"], "scope": a["scope"], "pinned": True}
    if a.get("character"):
        body["character"] = a["character"]
    try:
        r = api("POST", f"/api/converse/sessions/{chat['id']}/memory", body)
    except ToolError as exc:
        if "409" in str(exc):
            return {"saved": False, "note": "Already remembered."}
        raise
    return {"saved": True, "memory_id": r.get("id"), "scope": r.get("scope"), "text": r.get("text")}


# ── MCP plumbing (JSON-RPC 2.0 over stdio, one message per line) ────────────

def call(name: str, args: dict) -> dict:
    if name not in TOOLS:
        close = difflib.get_close_matches(name, TOOLS, n=3)
        raise ToolError(f"No tool called '{name}'." + (f" Did you mean {', '.join(close)}?" if close else ""))
    t = TOOLS[name]
    return scrub(t["fn"](check(args or {}, t["schema"])))


def handle(msg: dict) -> dict | None:
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:  # notification
        return None
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": params.get("protocolVersion") or PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER,
            "instructions": "VoiceVault speech, voice, music and chat-memory tools. Start with vv_status; use vv_memory_recall for anything said in earlier chats. Nothing here deletes "
                            "anything. Long music work returns a job_id for vv_job_status."}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": [
            {"name": n, "description": t["description"], "inputSchema": t["schema"],
             "annotations": {"readOnlyHint": t["read_only"], "destructiveHint": False}} for n, t in TOOLS.items()]}}
    if method == "tools/call":
        try:
            result = call(params.get("name", ""), params.get("arguments") or {})
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=1)}],
                "structuredContent": result if isinstance(result, dict) else {"result": result}, "isError": False}}
        except ToolError as exc:
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": str(exc)}], "isError": True}}
        except Exception as exc:  # never crash the server on one bad call
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text":
                    f"Unexpected error ({exc.__class__.__name__}): {exc}. Check the arguments, or vv_status."}], "isError": True}}
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Unknown method {method}"}}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            out = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            out = handle(msg)
        if out is not None:
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
