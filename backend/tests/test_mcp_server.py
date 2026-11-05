"""MCP server: protocol, strict arguments, name resolution, path scrubbing and the no-delete guarantee."""
import json

import pytest

import mcp_server as vv

VOICES = [{"reference_id": "a" * 32, "name": "Mara courtroom", "transcript": "Make it so", "duration_s": 20,
           "url": "/api/tts/references/aaa/audio"},
          {"reference_id": "b" * 32, "name": "Mara raw", "transcript": "", "duration_s": 30, "url": "/x"},
          {"reference_id": "c" * 32, "name": "Nova Reyes", "transcript": "", "duration_s": 25, "url": "/y"}]
MODELS = [{"id": "fish_drama3:TTS/Drama-3-GGUF-final/drama-3-final-Q4_K.gguf", "engine_id": "fish_drama3", "family": "Fish Audio Drama 3",
           "state": "integrated", "path": r"C:\Users\someone\Models\x.gguf"},
          {"id": "vibevoice:vibevoice/models--aoi-ot--VibeVoice-Large/snapshots/8f3169c25ac944e4", "engine_id": "vibevoice",
           "family": "VibeVoice", "state": "integrated"},
          {"id": "vibevoice:TTS/vibevoice/VibeVoice-Large", "engine_id": "vibevoice", "family": "VibeVoice", "state": "integrated"},
          {"id": "maya1:maya1", "engine_id": "maya1", "family": "Maya1", "state": "integrated"},
          {"id": "broken:x", "engine_id": "broken", "family": "Broken", "state": "missing"}]
ENGINES = [{"id": "fish_drama3", "capabilities": {"reference_audio": True}},
           {"id": "vibevoice", "capabilities": {"reference_audio": True}},
           {"id": "maya1", "capabilities": {"voice_description": True, "reference_audio": False}}]


@pytest.fixture
def calls(monkeypatch):
    log = []

    def fake(method, path, body=None, params=None, timeout=60):
        log.append((method, path, body))
        if path == "/api/tts/references":
            return {"items": VOICES}
        if path == "/api/tts/models":
            return {"models": [dict(m) for m in MODELS], "roots": [r"E:\secret\models"]}
        if path == "/api/tts/engines":
            return {"engines": ENGINES}
        if path == "/api/tts/synthesize":
            return {"file": "20261104_x.wav", "duration_s": 2.0, "meta": {"quality": {"warnings": []}},
                    "path": r"C:\Users\someone\Library\x.wav"}
        if path == "/api/library/items" and method == "GET":
            return {"items": [{"key": "voice:" + "c" * 32, "type": "voice", "title": "Nova Reyes", "labels": ["calm"],
                               "assignable": True, "play_url": "/p", "path": r"D:\private\Emma.wav"},
                              {"key": "sfx:p:t", "type": "sfx", "title": "Door", "labels": [], "assignable": False}],
                    "labels": ["calm"], "categories": []}
        if path == "/api/library/items" and method == "PATCH":
            return {**body, "title": "Nova Reyes"}
        raise AssertionError(f"unexpected {method} {path}")
    monkeypatch.setattr(vv, "api", fake)
    return log


def rpc(*messages):
    return [vv.handle(m) for m in messages]


def test_protocol_handshake_and_tool_list():
    init, listed, note = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                             {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                             {"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert init["result"]["serverInfo"]["name"] == "voicevault" and note is None
    tools = listed["result"]["tools"]
    assert {"vv_status", "vv_speak", "vv_library_tag", "vv_music_compose", "vv_job_status"} <= {t["name"] for t in tools}
    assert all(t["inputSchema"]["additionalProperties"] is False for t in tools)
    assert not any(t["annotations"]["destructiveHint"] for t in tools)
    assert vv.handle({"jsonrpc": "2.0", "id": 3, "method": "nope"})["error"]["code"] == -32601


def test_no_tool_can_delete():
    for name, t in vv.TOOLS.items():
        assert not any(w in name for w in ("delete", "remove_item", "move")), name
    import inspect
    assert '"DELETE"' not in inspect.getsource(vv)


def test_strict_arguments_with_helpful_errors(calls):
    r = vv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "vv_speak", "arguments": {"txt": "hi"}}})
    assert r["result"]["isError"] and "Allowed: text" in r["result"]["content"][0]["text"]
    with pytest.raises(vv.ToolError, match="required"):
        vv.call("vv_speak", {})
    with pytest.raises(vv.ToolError, match="between"):
        vv.call("vv_list_voices", {"limit": 0})
    with pytest.raises(vv.ToolError, match="one of"):
        vv.call("vv_library_search", {"type": "videos"})
    with pytest.raises(vv.ToolError, match="Did you mean vv_speak"):
        vv.call("vv_speek", {})
    assert vv.call("vv_list_voices", {"limit": "1"})["total"] == 3  # numbers as strings are fine


def test_names_resolve_or_fail_with_choices(calls):
    assert vv.find_voice("nova reyes")["reference_id"] == "c" * 32
    assert vv.find_voice("courtroom")["name"] == "Mara courtroom"
    with pytest.raises(vv.ToolError, match="matches 2 voices"):
        vv.find_voice("mara")
    with pytest.raises(vv.ToolError, match="Did you mean"):
        vv.find_voice("Emma Rubee")


def test_speak_picks_a_cloning_model_and_hides_paths(calls):
    out = vv.call("vv_speak", {"text": "Make it so.", "voice": "Nova Reyes"})
    body = next(b for m, p, b in calls if p == "/api/tts/synthesize")
    assert body["model_id"].startswith("fish_drama3") and body["reference_id"] == "c" * 32
    assert out["listen"].endswith("/api/tts/audio/20261104_x.wav") and out["library_key"] == "generation:20261104_x.wav"
    with pytest.raises(vv.ToolError, match="cannot use a saved voice"):
        vv.call("vv_speak", {"text": "hi", "voice": "Nova Reyes", "model": "maya1"})
    models = vv.call("vv_list_speech_models", {})["models"]
    names = [m["name"] for m in models]
    assert len(names) == len(set(names)) == 4 and not any("8f3169" in n for n in names)
    assert "path" not in json.dumps(models)


def test_scrub_removes_local_paths():
    data = {"path": r"C:\Users\x\a.wav", "nested": [{"root": "/home/x"}], "note": r"E:\stuff\b.wav", "ok": "hello"}
    assert vv.scrub(data) == {"nested": [{}], "note": "(local path hidden)", "ok": "hello"}


def test_library_tag_merges_labels_and_refuses_character_on_sfx(calls):
    key = "voice:" + "c" * 32
    vv.call("vv_library_tag", {"key": key, "add_labels": "narrator, Calm", "category": "Test"})
    patch = next(b for m, p, b in calls if m == "PATCH")
    assert patch["labels"] == ["calm", "narrator"] and patch["category"] == "Test"
    with pytest.raises(vv.ToolError, match="cannot have a character"):
        vv.call("vv_library_tag", {"key": "sfx:p:t", "character": "Bob"})
    with pytest.raises(vv.ToolError, match="Nothing to change"):
        vv.call("vv_library_tag", {"key": key})
    with pytest.raises(vv.ToolError, match="No Library item"):
        vv.call("vv_library_tag", {"key": "voice:nope", "add_labels": ["x"]})


def test_unreachable_backend_says_what_to_do(monkeypatch):
    monkeypatch.setattr(vv, "BASE", "http://127.0.0.1:9")
    with pytest.raises(vv.ToolError, match="start VoiceVault"):
        vv.api("GET", "/api/health", timeout=2)


def test_chat_memory_tools(monkeypatch):
    calls = []
    chats = [{"id": "c1", "title": "Evenings with Mira", "kind": "chat", "profile": {"name": "Mira"}, "messages_total": 2400}]

    def fake(method, path, body=None, params=None, timeout=60):
        calls.append((method, path, body, params))
        if path == "/api/converse/sessions":
            return {"sessions": chats}
        if path == "/api/converse/memory/search":
            return {"messages": [{"chat": "c1", "text": "Our song is Moonlit Harbor."}], "summaries": [], "facts": []}
        if path == "/api/converse/sessions/c1/memory":
            return {"id": "f1", "scope": body["scope"], "text": body["text"]}
        raise AssertionError(path)
    monkeypatch.setattr(vv, "api", fake)
    listed = vv.call("vv_chat_list", {})["chats"][0]
    assert listed["character"] == "Mira" and listed["messages"] == 2400
    out = vv.call("vv_memory_recall", {"query": "our song", "character": "Mira", "chat": "mira"})
    assert out["messages"][0]["text"].startswith("Our song")
    assert calls[-1][3] == {"q": "our song", "limit": 8, "character": "Mira", "session_id": "c1"}
    saved = vv.call("vv_memory_remember", {"text": "Sam loves rain.", "scope": "user"})
    assert saved["saved"] and calls[-1][2]["pinned"] is True
    with pytest.raises(vv.ToolError, match="which chat"):
        chats.clear()
        vv.call("vv_memory_remember", {"text": "x", "scope": "chat"})
