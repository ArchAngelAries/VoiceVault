"""Focused tests for persisted character sessions and OpenAI-compatible streaming."""
import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.converse as converse
from llm.client import CompletionStoppedError, StreamProtocolError, UpstreamError, normalize_base_url, stream_chat


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(converse, "STORE_DIR", tmp_path / "conversations")
    monkeypatch.setattr(converse, "_active_turns", set())
    app = FastAPI()
    app.include_router(converse.router, prefix="/api/converse")
    return TestClient(app)


def test_session_create_edit_and_persistence(client):
    created = client.post("/api/converse/sessions", json={"title": "Mira", "profile": {
        "name": "Mira", "description": "A pilot", "source_notes": "May be unreliable"}}).json()
    assert created["profile"]["name"] == "Mira"
    edited = client.patch(f"/api/converse/sessions/{created['id']}", json={"title": "Mira updated"}).json()
    assert edited["title"] == "Mira updated"
    assert client.get(f"/api/converse/sessions/{created['id']}").json() == edited
    assert client.get("/api/converse/sessions").json()["sessions"][0]["id"] == created["id"]


@pytest.mark.parametrize("reason", ["length", "content_filter"])
def test_provider_cutoff_keeps_partial_chat_and_releases_turn_with_safe_notice(client, monkeypatch, reason):
    session = client.post("/api/converse/sessions", json={"kind": "chat"}).json()
    calls = []
    async def model(*args, **kwargs):
        calls.append(True)
        yield "The unfinished reply"
        raise CompletionStoppedError(reason)
    monkeypatch.setattr(converse, "stream_chat", model)
    async def run():
        response = await converse.send_message(session["id"], converse.MessageRequest(text="Hello", model="local"))
        return [packet async for packet in response.body_iterator]
    packets = asyncio.run(run())
    assert any(b'event: delta' in packet and b'The unfinished reply' in packet for packet in packets)
    assert any(b'event: error' in packet and CompletionStoppedError(reason).message.encode() in packet for packet in packets)
    assert not any(b'event: done' in packet for packet in packets)
    reply = client.get(f"/api/converse/sessions/{session['id']}").json()["messages"][-1]
    assert reply["content"] == "The unfinished reply" and reply["status"] == "error"
    assert session["id"] not in converse._active_turns and len(calls) == 1


def test_invalid_id_and_profile_bounds(client):
    assert client.get("/api/converse/sessions/../../secret").status_code == 404
    response = client.post("/api/converse/sessions", json={"profile": {"name": "x" * 121}})
    assert response.status_code == 422


def test_voice_settings_round_trip_and_legacy_profile_compatibility(client):
    voice_settings = {
        "model_id": "breeze/model-small",
        "reference_id": "reference-abc123",
        "speech_mode": "reference",
    }
    created = client.post("/api/converse/sessions", json={"title": "Mira", "profile": {
        "name": "Mira", "voice_description": "Warm, measured delivery", "api_key": "must-not-persist",
        "voice_settings": {**voice_settings, "api_key": "must-not-persist"},
    }}).json()
    assert created["profile"]["voice_settings"] == voice_settings
    assert "api_key" not in created["profile"] and "api_key" not in created["profile"]["voice_settings"]
    updated = client.patch(f"/api/converse/sessions/{created['id']}", json={"profile": {
        "name": "Mira", "voice_description": "Still editable", "voice_settings": voice_settings,
    }}).json()
    assert updated["profile"]["voice_description"] == "Still editable"
    assert client.get(f"/api/converse/sessions/{created['id']}").json()["profile"]["voice_settings"] == voice_settings

    legacy_id = "2213a211-5fe1-4c6d-97f1-19b3ea83c387"
    legacy = {"id": legacy_id, "title": "Old session", "updated_at": "2026-01-01T00:00:00Z",
              "profile": {"name": "Old", "voice_description": "Legacy voice"}, "messages": []}
    legacy_path = converse.STORE_DIR / f"{legacy_id}.json"
    legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
    loaded = client.get(f"/api/converse/sessions/{legacy_id}").json()
    assert loaded["characters"] == [{"id": "primary", "profile": legacy["profile"]}]
    assert json.loads(legacy_path.read_text(encoding="utf-8")) == legacy
    edited_legacy = client.patch(f"/api/converse/sessions/{legacy_id}", json={"profile": {"name": "Older Mira"}}).json()
    assert edited_legacy["characters"][0]["profile"] == edited_legacy["profile"]
    assert edited_legacy["profile"]["name"] == "Older Mira"


def test_preset_assignment_settings_survive_save_reload_and_validation(client):
    settings = {"engine_id": "breeze", "model_id": "exact-copy:123", "reference_id": "exact-ref",
                "speech_mode": "reference", "reference_text": "A supplied transcript.", "language": "Japanese",
                "delivery": {"emotion": "calm", "style": "natural", "expression": None},
                "generation": {"temperature": 0.4, "top_p": 0.9, "repetition_penalty": 1.1,
                               "max_new_tokens": 2500, "seed": 732}}
    created = client.post("/api/converse/sessions", json={"profile": {"name": "Mira", "voice_settings": settings}}).json()
    assert client.get(f"/api/converse/sessions/{created['id']}").json()["profile"]["voice_settings"] == settings
    response = client.patch(f"/api/converse/sessions/{created['id']}", json={"profile": {
        "voice_settings": {**settings, "generation": {"seed": -1}}}})
    assert response.status_code == 422
    assert client.get(f"/api/converse/sessions/{created['id']}").json()["profile"]["voice_settings"] == settings


def test_character_roster_validates_unique_bounded_identifiers_and_primary(client):
    profile = {"name": "Mira"}
    cases = [
        [{"id": "primary", "profile": profile}, {"id": "primary", "profile": profile}],
        [{"id": "bad id", "profile": profile}],
        [{"id": "secondary", "profile": profile}],
        [{"id": "primary", "profile": profile}, *[{"id": f"c{i}", "profile": profile} for i in range(1, 7)]],
    ]
    for characters in cases:
        response = client.post("/api/converse/sessions", json={"profile": profile, "characters": characters})
        assert response.status_code == 422


def test_distinct_character_profiles_survive_patch_and_reload(client):
    primary = {"name": "Mira", "personality": "Patient", "voice_description": "Warm voice",
               "voice_settings": {"model_id": "breeze/mira", "reference_id": "mira-ref", "speech_mode": "reference"}}
    sol = {"name": "Sol", "personality": "Blunt", "voice_description": "Low voice",
           "voice_settings": {"model_id": "breeze/sol", "reference_id": "sol-ref", "speech_mode": "reference"}}
    created = client.post("/api/converse/sessions", json={"profile": primary, "characters": [
        {"id": "primary", "profile": primary}, {"id": "sol", "profile": sol},
    ]}).json()
    changed_sol = {**sol, "personality": "Dry and direct"}
    patched = client.patch(f"/api/converse/sessions/{created['id']}", json={"characters": [
        {"id": "primary", "profile": primary}, {"id": "sol", "profile": changed_sol},
    ]}).json()
    reloaded = client.get(f"/api/converse/sessions/{created['id']}").json()
    assert patched["profile"]["name"] == primary["name"]
    assert patched["profile"]["voice_settings"] == primary["voice_settings"]
    assert [item["id"] for item in reloaded["characters"]] == ["primary", "sol"]
    assert reloaded["characters"][0]["profile"] == reloaded["profile"]
    assert reloaded["profile"]["name"] == primary["name"]
    assert reloaded["characters"][1]["profile"]["name"] == changed_sol["name"]
    assert reloaded["characters"][1]["profile"]["personality"] == changed_sol["personality"]
    assert reloaded["characters"][1]["profile"]["voice_settings"] == changed_sol["voice_settings"]


@pytest.mark.parametrize("voice_settings", [
    {"speech_mode": "automatic"},
    {"model_id": "m" * 501},
    {"reference_id": "r" * 501},
    {"model_id": ""},
    {"reference_id": ""},
])
def test_voice_settings_validate_bounded_ids_and_mode(client, voice_settings):
    response = client.post("/api/converse/sessions", json={"profile": {"voice_settings": voice_settings}})
    assert response.status_code == 422


def test_connection_exposes_saved_provider_and_default_url(client, monkeypatch):
    monkeypatch.setattr(converse, "get_setting", lambda key, default: "http://localhost:1234")
    assert client.get("/api/converse/connection").json() == {
        "provider": "openai_compatible", "default_endpoint": "http://localhost:1234/v1",
        "base_url": "http://localhost:1234/v1"}


def test_url_normalization_and_rejection():
    assert normalize_base_url("http://localhost:1234/") == "http://localhost:1234/v1"
    assert normalize_base_url("https://host/api/v1") == "https://host/api/v1"
    for url in ("file:///tmp", "http://user:pass@host", "http://host/?token=x", "http://host/#frag"):
        with pytest.raises(ValueError):
            normalize_base_url(url)


def test_stream_parser_skips_malformed_chunks():
    async def handler(request):
        body = (b"data: not-json\n\n"
                b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
                b'data: {"choices":[{"delta":{"content":" there"}}]}\n\n'
                b"data: [DONE]\n\n")
        return httpx.Response(200, content=body)

    async def run():
        return [part async for part in stream_chat("http://llm/v1", "m", [], transport=httpx.MockTransport(handler))]
    assert asyncio.run(run()) == ["Hi", " there"]


def test_stream_http_error_is_sanitized():
    async def handler(request):
        return httpx.Response(401, text="secret-token echoed by server")

    async def run():
        return [part async for part in stream_chat("http://llm/v1", "m", [], transport=httpx.MockTransport(handler))]
    with pytest.raises(UpstreamError) as exc:
        asyncio.run(run())
    assert exc.value.status_code == 401
    assert "secret-token" not in str(exc.value)


def test_stream_without_completion_marker_is_rejected():
    async def handler(request):
        return httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')

    async def run():
        return [part async for part in stream_chat("http://llm/v1", "m", [], transport=httpx.MockTransport(handler))]
    with pytest.raises(StreamProtocolError):
        asyncio.run(run())


def test_message_stream_persists_partial_on_cancellation(client, monkeypatch):
    primary = {"name": "Mira"}
    sol = {"name": "Sol", "personality": "Speak tersely"}
    session = client.post("/api/converse/sessions", json={"title": "test", "profile": primary,
        "characters": [{"id": "primary", "profile": primary}, {"id": "sol", "profile": sol}]}).json()

    async def fake_stream(*args, **kwargs):
        yield "partial"
        await asyncio.Event().wait()

    monkeypatch.setattr(converse, "stream_chat", fake_stream)

    async def run():
        response = await converse.send_message(session["id"], converse.MessageRequest(text="hello", model="local", speaker_id="sol"))
        iterator = response.body_iterator
        first = await anext(iterator)
        assert b'"text":"partial"' in first and b'"speaker_id":"sol"' in first and b'"speaker_name":"Sol"' in first
        pending = asyncio.create_task(anext(iterator))
        await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    asyncio.run(run())
    saved = client.get(f"/api/converse/sessions/{session['id']}").json()
    assert saved["messages"] == [{"role": "user", "content": "hello"},
                                  {"role": "assistant", "content": "partial", "status": "interrupted",
                                   "speaker_id": "sol", "speaker_name": "Sol"}]


def test_speaker_selects_profile_and_attributes_other_character_history(client, monkeypatch):
    primary = {"name": "Mira", "personality": "Patient and curious"}
    sol = {"name": "Sol", "personality": "Blunt and dry", "voice_description": "Low voice"}
    session = client.post("/api/converse/sessions", json={"profile": primary, "characters": [
        {"id": "primary", "profile": primary}, {"id": "sol", "profile": sol},
    ]}).json()
    session["messages"] = [
        {"role": "user", "content": "Earlier question"},
        {"role": "assistant", "content": "Mira's old reply", "speaker_id": "primary", "speaker_name": "Mira", "status": "complete"},
        {"role": "assistant", "content": "Sol's old reply", "speaker_id": "sol", "speaker_name": "Sol", "status": "complete"},
    ]
    converse._save(session)
    captured = []

    async def fake_stream(base_url, model, messages, api_key=None):
        captured.append(messages)
        yield "Sol answers."

    monkeypatch.setattr(converse, "stream_chat", fake_stream)

    async def run():
        response = await converse.send_message(session["id"], converse.MessageRequest(text="Now answer", model="local", speaker_id="sol"))
        return [event async for event in response.body_iterator]

    events = asyncio.run(run())
    messages = captured[0]
    assert "Personality: Blunt and dry" in messages[0]["content"]
    assert "Active speaker: Sol" in messages[0]["content"]
    assert "Other participants in this conversation: Mira." in messages[0]["content"]
    assert messages[1:] == [
        {"role": "user", "content": "Earlier question"},
        {"role": "user", "content": "[Previous character turn from Mira]: Mira's old reply"},
        {"role": "assistant", "content": "Sol's old reply"},
        {"role": "user", "content": "Now answer"},
    ]
    delta = next(event for event in events if b"event: delta" in event)
    done = next(event for event in events if b"event: done" in event)
    assert b'"speaker_id":"sol"' in delta and b'"speaker_name":"Sol"' in delta
    assert b'"speaker_id":"sol"' in done and b'"speaker_name":"Sol"' in done
    saved = client.get(f"/api/converse/sessions/{session['id']}").json()
    assert saved["messages"][-1] == {"role": "assistant", "content": "Sol answers.", "status": "complete",
                                       "speaker_id": "sol", "speaker_name": "Sol"}


def test_unknown_speaker_is_rejected_without_persisting_user_turn(client, monkeypatch):
    profile = {"name": "Mira"}
    session = client.post("/api/converse/sessions", json={"profile": profile, "characters": [
        {"id": "primary", "profile": profile},
    ]}).json()
    path = converse._session_path(session["id"])
    before = path.read_bytes()
    monkeypatch.setattr(converse, "stream_chat", lambda *args, **kwargs: pytest.fail("model call must not happen"))

    async def run():
        with pytest.raises(converse.HTTPException) as exc:
            await converse.send_message(session["id"], converse.MessageRequest(text="not saved", model="local", speaker_id="missing"))
        assert exc.value.status_code == 422

    asyncio.run(run())
    assert path.read_bytes() == before


def test_completed_reply_is_marked_and_internal_status_is_not_sent_upstream(client, monkeypatch):
    session = client.post("/api/converse/sessions", json={}).json()
    captured = []

    async def fake_stream(base_url, model, messages, api_key=None):
        captured.append(messages)
        yield "reply"

    monkeypatch.setattr(converse, "stream_chat", fake_stream)
    async def run():
        response = await converse.send_message(session["id"], converse.MessageRequest(text="hi", model="local"))
        first_events = [event async for event in response.body_iterator]
        response = await converse.send_message(session["id"], converse.MessageRequest(text="again", model="local"))
        second_events = [event async for event in response.body_iterator]
        return first_events, second_events
    first_events, second_events = asyncio.run(run())
    assert b'event: done' in first_events[-1]
    assert b'event: done' in second_events[-1]
    saved = client.get(f"/api/converse/sessions/{session['id']}").json()
    assert saved["messages"][-1] == {"role": "assistant", "content": "reply", "status": "complete",
                                     "speaker_id": "primary", "speaker_name": "Primary character"}
    assert len(captured) == 2
    assert all(set(message) == {"role", "content"} for message in captured[1])
    assert captured[1][-2:] == [{"role": "assistant", "content": "reply"},
                                {"role": "user", "content": "again"}]


def test_output_limit_persists_error_status_and_releases_turn(client, monkeypatch):
    session = client.post("/api/converse/sessions", json={}).json()
    monkeypatch.setattr(converse, "MAX_REPLY_CHARS", 5)

    async def fake_stream(*args, **kwargs):
        yield "123456789"

    monkeypatch.setattr(converse, "stream_chat", fake_stream)
    async def run():
        response = await converse.send_message(session["id"], converse.MessageRequest(text="hi", model="local"))
        return [event async for event in response.body_iterator]
    events = asyncio.run(run())
    assert any(b'event: error' in event for event in events)
    assert not any(b'event: done' in event for event in events)
    saved = client.get(f"/api/converse/sessions/{session['id']}").json()
    assistant = saved["messages"][-1]
    assert assistant["role"] == "assistant"
    assert assistant["status"] == "error"
    assert len(assistant["content"]) <= 5
    assert session["id"] not in converse._active_turns


def test_failed_final_persist_is_sanitized_without_duplicate_reply(client, monkeypatch):
    session = client.post("/api/converse/sessions", json={}).json()
    real_save = converse._save

    def fail_assistant_save(value):
        if any(message["role"] == "assistant" for message in value["messages"]):
            raise OSError("secret filesystem detail")
        real_save(value)

    async def fake_stream(*args, **kwargs):
        yield "partial"

    monkeypatch.setattr(converse, "_save", fail_assistant_save)
    monkeypatch.setattr(converse, "stream_chat", fake_stream)
    async def run():
        response = await converse.send_message(session["id"], converse.MessageRequest(text="hi", model="local"))
        return [event async for event in response.body_iterator]
    events = asyncio.run(run())
    error = next(event for event in events if b'event: error' in event)
    assert b"Could not save the reply" in error
    assert b"secret filesystem detail" not in error
    saved = client.get(f"/api/converse/sessions/{session['id']}").json()
    assert saved["messages"] == [{"role": "user", "content": "hi"}]
    assert session["id"] not in converse._active_turns


def test_rejects_concurrent_turn_for_same_session(client, monkeypatch):
    session = client.post("/api/converse/sessions", json={}).json()

    async def fake_stream(*args, **kwargs):
        await asyncio.Event().wait()
        yield "never"

    monkeypatch.setattr(converse, "stream_chat", fake_stream)
    async def run():
        await converse.send_message(session["id"], converse.MessageRequest(text="first", model="local"))
        with pytest.raises(converse.HTTPException) as exc:
            await converse.send_message(session["id"], converse.MessageRequest(text="second", model="local"))
        assert exc.value.status_code == 409
    asyncio.run(run())


def test_turn_guard_canonicalizes_uuid_alias_and_blocks_edits(client):
    session = client.post("/api/converse/sessions", json={}).json()
    alias = session["id"].upper()
    converse._active_turns.add(session["id"])
    response = client.post(f"/api/converse/sessions/{alias}/message", json={"text": "x", "model": "m"})
    assert response.status_code == 409
    response = client.patch(f"/api/converse/sessions/{alias}", json={"title": "edited"})
    assert response.status_code == 409
    converse._active_turns.clear()


def test_failed_initial_persist_releases_turn_lock(client, monkeypatch):
    session = client.post("/api/converse/sessions", json={}).json()
    real_save = converse._save

    def fail_message_save(value):
        if value["messages"]:
            raise OSError("disk unavailable")
        real_save(value)

    monkeypatch.setattr(converse, "_save", fail_message_save)
    async def run():
        with pytest.raises(OSError):
            await converse.send_message(session["id"], converse.MessageRequest(text="x", model="m"))
    asyncio.run(run())
    assert session["id"] not in converse._active_turns
