"""
api/library.py — The in-app Library: every piece of audio in one list.

GET    /api/library/items?type=&q=&character=&label=   voices, harvested clips and sources, generations,
                                                     Voice Lab uploads, songs and sound effects
PATCH  /api/library/items                            {key, title?, character?, labels?, category?, notes?}
DELETE /api/library/items?key=                       delete one item (types that support it)
POST   /api/library/reveal                           {key} or {folder} → open Explorer with the file selected
GET    /api/library/characters                       saved Discover characters/people + names already used
GET    /api/library/folders                          the Library's folders on disk

Item keys: voice:<ref id> · generation:<file> · clip:<source id>:<clip id> · source:<source id> ·
voicelab:<upload id> · music:<song id>:<take id> · sfx:<song id>:<take id>

Characters/actors can be assigned to voices, clips, sources, generations and Voice Lab uploads;
music and sound effects take labels and categories only. Edits live in Library/library.json
(atomic writes); voice titles are also written to the voice's own record so Generate shows them.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core import library as lib

router = APIRouter()
_lock = threading.RLock()
ASSIGNABLE = {"voice", "clip", "source", "generation", "voicelab"}
TYPES = ("voice", "clip", "source", "generation", "voicelab", "music", "sfx")
TYPE_NAMES = {"voice": "Voice", "clip": "Harvested clip", "source": "Source", "generation": "Generation",
              "voicelab": "Voice Lab upload", "music": "Music", "sfx": "Sound effect"}


# ── Index (titles, characters, labels, categories, notes) ───────────────────

def _load_index() -> dict:
    path = lib.index_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and isinstance(data.get("items"), dict) else {"items": {}}
    except (OSError, ValueError):
        return {"items": {}}


def _save_index(data: dict) -> None:
    path = lib.index_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


# ── Collect items from every feature ────────────────────────────────────────

def _voices() -> list[dict]:
    from api import tts as tts_api
    from engines import audio_store

    out = []
    for r in audio_store.list_references(tts_api.REFERENCE_DIR):
        rid = r["reference_id"]
        out.append({"key": f"voice:{rid}", "type": "voice", "title": r.get("name") or f"Voice {rid[:8]}",
                    "duration_s": r.get("duration_s"), "created": r.get("created"),
                    "path": str(tts_api.REFERENCE_DIR / f"{rid}.wav"), "play_url": f"/api/tts/references/{rid}/audio",
                    "detail": (r.get("transcript") or "")[:200]})
    return out


def _generations() -> list[dict]:
    from api import tts as tts_api
    from engines.audio_io import list_generations

    folder = tts_api._outputs_dir()
    out = []
    for g in list_generations(folder, limit=2000):
        out.append({"key": f"generation:{g['file']}", "type": "generation",
                    "title": (g.get("text") or g["file"])[:120], "duration_s": g.get("duration_s"),
                    "created": g.get("created"), "path": str(folder / g["file"]),
                    "play_url": f"/api/tts/audio/{g['file']}", "detail": g.get("engine") or ""})
    return out


def _harvest() -> list[dict]:
    from api.harvest import _service

    svc = _service()
    out = []
    for s in svc.sources():
        src = svc.get_source(s["id"])
        prov = src.get("provenance") or {}
        out.append({"key": f"source:{src['id']}", "type": "source", "title": src.get("name") or "Source",
                    "duration_s": src.get("duration_s"), "created": src.get("created_at"),
                    "path": str(svc.root / src["id"] / (src.get("original_file") if (svc.root / src["id"] / (src.get("original_file") or "-")).is_file() else src.get("preview_file") or "")), "play_url": src.get("audio_url"),
                    "detail": prov.get("url") or prov.get("original_name") or prov.get("type") or ""})
        for v in src.get("variants", []):
            out.append({"key": f"clip:{src['id']}:{v['id']}", "type": "clip", "title": v.get("name") or "Clip",
                        "duration_s": v.get("duration_s"), "created": src.get("created_at"),
                        "path": str(svc.root / src["id"] / (v.get("hq_file") or v["file"])), "play_url": v.get("audio_url"),
                        "detail": (v.get("transcript") or " → ".join(v.get("cleanup_steps") or []) or "")[:200]})
    return out


def _voicelab() -> list[dict]:
    from api import voice as voice_api

    out = []
    for u in voice_api.list_sources():
        out.append({"key": f"voicelab:{u['id']}", "type": "voicelab", "title": u.get("name") or "Upload",
                    "duration_s": None, "created": u.get("created"),
                    "path": str(voice_api.SOURCES / f"{u['id']}.wav"), "play_url": f"/api/voice/sources/{u['id']}/audio",
                    "detail": ""})
    return out


def _music() -> list[dict]:
    from api.music import get_studio

    store = get_studio().store
    out = []
    for p in store.list():
        project = store.get(p["id"])
        for t in project.get("takes", []):
            kind = "sfx" if t.get("op") == "sfx" or t.get("sfx") else "music"
            out.append({"key": f"{kind}:{project['id']}:{t['id']}", "type": kind, "title": t.get("label") or "Take",
                        "duration_s": t.get("duration"), "created": t.get("created"),
                        "path": str(store.take_path(project["id"], t["id"])),
                        "play_url": f"/api/music/projects/{project['id']}/takes/{t['id']}/audio",
                        "detail": project.get("title") or ""})
    return out


def _collect_raw() -> list[dict]:
    items: list[dict] = []
    for source in (_voices, _harvest, _generations, _voicelab, _music):
        try:
            items += source()
        except Exception:  # one broken store must not hide the rest of the Library
            import logging
            logging.getLogger(__name__).exception("Library: could not list %s", source.__name__)
    return items


def collect() -> list[dict]:
    return _annotate(_collect_raw())


def _annotate(items: list[dict], index: Optional[dict] = None) -> list[dict]:
    """Apply the Library index (titles, characters, labels, ...) to raw items, in place."""
    index = _load_index()["items"] if index is None else index
    for item in items:
        meta = index.get(item["key"], {})
        item["title"] = meta.get("title") or item["title"]
        item["character"] = meta.get("character") if item["type"] in ASSIGNABLE else None
        item["labels"] = meta.get("labels", [])
        item["category"] = meta.get("category")
        item["notes"] = meta.get("notes")
        item["assignable"] = item["type"] in ASSIGNABLE
        item["type_name"] = TYPE_NAMES[item["type"]]
    return items


def _find_raw(key: str) -> dict:
    item = next((i for i in _collect_raw() if i["key"] == key), None)
    if item is None:
        raise HTTPException(404, "That item is no longer in the Library.")
    return item


def _find(key: str) -> dict:
    return _annotate([_find_raw(key)])[0]


# ── API ─────────────────────────────────────────────────────────────────────

class Character(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    item_id: Optional[str] = Field(default=None, max_length=120)   # a saved Discover character/person, if any
    kind: Optional[str] = Field(default=None, max_length=20)      # character | actor | custom


class ItemPatch(BaseModel):
    key: str = Field(max_length=200)
    title: Optional[str] = Field(default=None, max_length=200)
    character: Optional[Character] = None
    clear_character: bool = False
    labels: Optional[list[str]] = Field(default=None, max_length=50)
    category: Optional[str] = Field(default=None, max_length=60)
    notes: Optional[str] = Field(default=None, max_length=4000)


class RevealIn(BaseModel):
    key: Optional[str] = Field(default=None, max_length=200)
    folder: Optional[str] = Field(default=None, max_length=40)


@router.get("/items")
def list_items(type: Optional[str] = None, q: Optional[str] = None, character: Optional[str] = None,
               label: Optional[str] = None):
    everything = collect()   # one scan of every store per request; filters work on this list
    items = everything
    if type:
        wanted = set(type.split(","))
        items = [i for i in items if i["type"] in wanted]
    if character:
        items = [i for i in items if i.get("character") and i["character"].get("name", "").lower() == character.lower()]
    if label:
        items = [i for i in items if label.lower() in (l.lower() for l in i["labels"])]
    if q:
        needle = q.lower()
        items = [i for i in items if needle in " ".join(str(i.get(k) or "") for k in ("title", "detail", "category", "notes")).lower()
                 or needle in " ".join(i["labels"]).lower() or (i.get("character") and needle in i["character"]["name"].lower())]
    items.sort(key=lambda i: str(i.get("created") or ""), reverse=True)
    labels = sorted({l for i in everything for l in i["labels"]}, key=str.lower)
    categories = sorted({i["category"] for i in everything if i.get("category")}, key=str.lower)
    return {"items": items, "labels": labels, "categories": categories, "types": TYPE_NAMES}


@router.patch("/items")
def edit_item(body: ItemPatch):
    raw = _find_raw(body.key)
    item = _annotate([dict(raw)])[0]
    if body.character and not item["assignable"]:
        raise HTTPException(400, "Music and sound effects cannot be assigned to a character or actor; use labels instead.")
    with _lock:
        data = _load_index()
        meta = data["items"].setdefault(body.key, {})
        if body.title is not None:
            meta["title"] = body.title.strip() or None
        if body.clear_character:
            meta.pop("character", None)
        elif body.character:
            meta["character"] = body.character.model_dump()
        if body.labels is not None:
            seen, labels = set(), []
            for l in (x.strip() for x in body.labels):
                if l and l.lower() not in seen:
                    seen.add(l.lower())
                    labels.append(l[:40])
            meta["labels"] = labels
        if body.category is not None:
            meta["category"] = body.category.strip() or None
        if body.notes is not None:
            meta["notes"] = body.notes.strip() or None
        data["items"][body.key] = {k: v for k, v in meta.items() if v not in (None, [], "")}
        _save_index(data)
    if body.title and item["type"] == "voice":
        name = body.title.strip()
        _rename_voice(item["key"].split(":", 1)[1], name)
        raw["title"] = name[:120] or f"Voice {item['key'].split(':', 1)[1][:8]}"   # what a fresh scan of the voice record now reports
    # Re-apply the saved index to this one item instead of re-scanning every store.
    return _annotate([raw], data["items"])[0]


def _rename_voice(rid: str, name: str) -> None:
    from api import tts as tts_api

    record = tts_api.REFERENCE_DIR / f"{rid}.json"
    try:
        data = json.loads(record.read_text(encoding="utf-8")) if record.is_file() else {"reference_id": rid}
        data["name"] = name[:120]
        tmp = record.with_name(record.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, record)
    except (OSError, ValueError):
        pass


@router.delete("/items")
def delete_item(key: str):
    item = _find(key)
    kind, _, rest = key.partition(":")
    if kind == "voice":
        from api import tts as tts_api
        from engines import audio_store
        result = audio_store.delete_references(tts_api.REFERENCE_DIR, [rest])
        if result.get("failed"):
            raise HTTPException(409, result["failed"][0]["reason"])
    elif kind == "generation":
        from api import tts as tts_api
        from engines import audio_store
        audio_store.delete_generations(tts_api._outputs_dir(), [rest])
    elif kind in ("music", "sfx"):
        from api.music import delete_take
        pid, tid = rest.split(":", 1)
        delete_take(pid, tid)
    else:
        raise HTTPException(400, f"Delete {item['type_name'].lower()}s from their own page for now.")
    with _lock:
        data = _load_index()
        data["items"].pop(key, None)
        _save_index(data)
    return {"deleted": key}


def _reveal(path: Path) -> None:
    if sys.platform.startswith("win"):
        target = str(path)
        args = ["explorer", f"/select,{target}"] if path.is_file() else ["explorer", target]
        subprocess.Popen(args, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)] if path.is_file() else ["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path if path.is_dir() else path.parent)])


def _allowed(path: Path) -> bool:
    """Only files the Library knows about, inside VoiceVault's own folders, can be revealed."""
    from api import tts as tts_api
    roots = [lib.library_dir(), tts_api.REFERENCE_DIR, tts_api._outputs_dir()]
    resolved = path.resolve()
    return any(resolved == r.resolve() or r.resolve() in resolved.parents for r in roots)


@router.post("/reveal")
def reveal(body: RevealIn):
    if body.folder:
        if body.folder == "library":
            path = lib.library_dir()
        elif body.folder in lib.FOLDERS:
            path = lib.folder(body.folder)
        else:
            raise HTTPException(400, "Unknown folder.")
        path.mkdir(parents=True, exist_ok=True)
    elif body.key:
        path = Path(_find(body.key)["path"])
        if not path.exists() or not _allowed(path):
            raise HTTPException(404, "The file is not on disk.")
    else:
        raise HTTPException(400, "Choose an item or a folder.")
    _reveal(path)
    return {"revealed": str(path)}


@router.get("/folders")
def folders():
    return {"library": str(lib.library_dir()), **{k: str(lib.folder(k)) for k in lib.FOLDERS}}


@router.get("/characters")
def characters():
    """Saved Discover characters and people (actors), plus names already used in the Library."""
    out: dict[str, dict] = {}
    try:
        from core.paths import database_path
        db = database_path()
        if db.is_file():
            con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                for item_id, item_type, title in con.execute(
                        "SELECT DISTINCT item_id, item_type, title FROM CollectionItem WHERE item_type IN ('character','person')"):
                    out[title.lower()] = {"name": title, "item_id": item_id, "kind": "actor" if item_type == "person" else "character"}
                for item_id, name in con.execute("SELECT item_id, name FROM Character"):
                    out.setdefault(name.lower(), {"name": name, "item_id": item_id, "kind": "character"})
            finally:
                con.close()
    except sqlite3.Error:
        pass
    for meta in _load_index()["items"].values():
        c = meta.get("character")
        if c and c.get("name"):
            out.setdefault(c["name"].lower(), c)
    return sorted(out.values(), key=lambda c: c["name"].lower())
