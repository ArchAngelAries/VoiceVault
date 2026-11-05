"""Anthropic API behavior through the conversation routes; all model calls are stubbed."""
import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.converse as converse
import api.profile_suggestions as suggestions
from llm.client import CompletionStoppedError


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(converse, "STORE_DIR", tmp_path / "conversations")
    monkeypatch.setattr(converse, "_active_turns", set())
    app = FastAPI()
    app.include_router(converse.router, prefix="/api/converse")
    return TestClient(app)


def _events(packets):
    return [(packet.split(b"\n", 1)[0][7:].decode(),
             json.loads(packet.split(b"data: ", 1)[1])) for packet in packets if packet]


def _send(client, session_id, **overrides):
    payload = {"text": "Hello Claude", "model": "claude-test", "provider": "anthropic",
               "api_key": "one-time-anthropic-key", **overrides}
    response = client.post(f"/api/converse/sessions/{session_id}/message", json=payload)
    return response


def test_models_route_uses_anthropic_default_endpoint_and_provider(client, monkeypatch):
    calls = []

    async def list_stub(base_url, api_key=None, **kwargs):
        calls.append((base_url, api_key, kwargs))
        return ["claude-test"]

    monkeypatch.setattr(converse, "list_models", list_stub)
    response = client.post("/api/converse/models", json={"provider": "anthropic", "api_key": "request-key"})

    assert response.status_code == 200
    assert response.json() == {"models": ["claude-test"]}
    assert calls == [("https://api.anthropic.com/v1", "request-key", {"provider": "anthropic"})]


def test_anthropic_missing_key_fails_before_saving_user_turn_or_calling_provider(client, monkeypatch):
    session = client.post("/api/converse/sessions", json={"kind": "chat"}).json()
    monkeypatch.setattr(converse, "_stream_for_provider", lambda *_a, **_k: pytest.fail("no model call"))

    response = client.post(f"/api/converse/sessions/{session['id']}/message", json={
        "text": "Do not persist this", "model": "claude-test", "provider": "anthropic",
    })

    assert response.status_code == 422
    loaded = client.get(f"/api/converse/sessions/{session['id']}").json()
    assert loaded["messages"] == []
    assert session["id"] not in converse._active_turns


@pytest.mark.parametrize("profile", [
    {},
    {"name": "User-created Mira", "description": "Custom profile"},
    {"name": "Wikidata Mira", "catalog_identity": "character:wikidata:Q42"},
])
def test_anthropic_chat_supports_plain_custom_and_wikidata_profiles_without_saving_key(client, monkeypatch, profile):
    session = client.post("/api/converse/sessions", json={"kind": "chat", "roleplay": bool(profile), "profile": profile}).json()
    calls = []

    async def stream(base_url, model, messages, api_key, provider):
        calls.append((base_url, model, messages, api_key, provider))
        yield "Claude reply"

    monkeypatch.setattr(converse, "_stream_for_provider", stream)
    response = _send(client, session["id"], base_url="https://proxy.example/prefix/v1")

    assert response.status_code == 200
    assert b"one-time-anthropic-key" not in response.content
    loaded = client.get(f"/api/converse/sessions/{session['id']}").json()
    assert loaded["messages"][-1]["content"] == "Claude reply"
    assert loaded["messages"][-1]["status"] == "complete"
    assert loaded["messages"][-1]["profile_snapshot"] == session["profile"]
    assert len(calls) == 1
    assert calls[0][0:2] == ("https://proxy.example/prefix/v1", "claude-test")
    assert calls[0][3:] == ("one-time-anthropic-key", "anthropic")
    assert "one-time-anthropic-key" not in converse._session_path(session["id"]).read_text(encoding="utf-8")


def test_default_local_chat_provider_still_works_without_anthropic_key(client, monkeypatch):
    session = client.post("/api/converse/sessions", json={"kind": "chat"}).json()
    calls = []

    async def stream(base_url, model, messages, api_key=None):
        calls.append((base_url, model, api_key))
        yield "Local reply"

    monkeypatch.setattr(converse, "stream_chat", stream)
    response = client.post(f"/api/converse/sessions/{session['id']}/message", json={
        "text": "Hello", "model": "local-model",
    })

    assert response.status_code == 200
    assert calls == [("http://localhost:1234/v1", "local-model", None)]
    assert client.get(f"/api/converse/sessions/{session['id']}").json()["messages"][-1]["status"] == "complete"


@pytest.mark.parametrize("reason", ["length", "tool_calls"])
def test_anthropic_partial_length_or_tool_use_is_persisted_as_error(client, monkeypatch, reason):
    session = client.post("/api/converse/sessions", json={"kind": "chat"}).json()

    async def stream(*_args, **_kwargs):
        yield "partial Claude text"
        raise CompletionStoppedError(reason)

    monkeypatch.setattr(converse, "_stream_for_provider", stream)
    response = _send(client, session["id"])

    assert response.status_code == 200
    events = _events(response.content.split(b"\n\n"))
    assert [event for event, _ in events] == ["delta", "error"]
    saved = client.get(f"/api/converse/sessions/{session['id']}").json()
    assert saved["messages"][-1]["content"] == "partial Claude text"
    assert saved["messages"][-1]["status"] == "error"
    assert session["id"] not in converse._active_turns


def test_anthropic_scene_length_cutoff_stops_remaining_turns_and_releases_lock(client, monkeypatch):
    session = client.post("/api/converse/sessions", json={
        "characters": [{"id": "primary", "profile": {}}, {"id": "bea", "profile": {}}],
        "scene": {"participant_ids": ["primary", "bea"], "rounds": 1},
    }).json()
    calls = []

    async def stream(_url, _model, messages, _key, _provider):
        calls.append(messages)
        yield "partial scene"
        raise CompletionStoppedError("length")

    monkeypatch.setattr(converse, "_stream_for_provider", stream)
    response = client.post(f"/api/converse/sessions/{session['id']}/scene", json={
        "provider": "anthropic", "api_key": "one-time-anthropic-key", "model": "claude-test",
    })

    assert response.status_code == 200
    events = _events(response.content.split(b"\n\n"))
    assert [event for event, _ in events] == ["turn_start", "delta", "error"]
    assert len(calls) == 1
    assert calls[0][-1]["role"] == "user"
    assert "Continue the current scene" in calls[0][-1]["content"]
    saved = client.get(f"/api/converse/sessions/{session['id']}").json()
    assert len(saved["messages"]) == 1
    assert saved["messages"][0]["status"] == "error"
    assert saved["messages"][0]["content"] == "partial scene"
    assert session["id"] not in converse._active_turns
    assert "one-time-anthropic-key" not in converse._session_path(session["id"]).read_text(encoding="utf-8")


def test_anthropic_catalog_guard_blocks_roleplay_before_turn_mutation(client, monkeypatch):
    session = client.post("/api/converse/sessions", json={
        "kind": "chat", "roleplay": True,
        "profile": {"name": "Catalog role", "catalog_identity": "character:anilist:17"},
    }).json()
    monkeypatch.setattr(converse, "_stream_for_provider", lambda *_a, **_k: pytest.fail("guard precedes provider call"))

    response = _send(client, session["id"])

    assert response.status_code == 422
    assert "policy review" in response.json()["detail"]
    saved = client.get(f"/api/converse/sessions/{session['id']}").json()
    assert saved["messages"] == []
    assert session["id"] not in converse._active_turns


def test_anthropic_plain_chat_allows_historical_non_wikidata_profile_snapshot(client, monkeypatch):
    session = client.post("/api/converse/sessions", json={"kind": "chat"}).json()
    raw = converse._read(converse._session_path(session["id"]))
    raw["messages"] = [{"role": "assistant", "content": "Old answer", "status": "complete",
                        "profile_snapshot": {"catalog_identity": "character:tmdb:42"}}]
    converse._save(raw)
    calls = []
    async def stream(_url, _model, messages, _key, _provider):
        calls.append(messages)
        yield "New reply"
    monkeypatch.setattr(converse, "_stream_for_provider", stream)

    response = _send(client, session["id"])

    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0][-2:] == [
        {"role": "assistant", "content": "Old answer"},
        {"role": "user", "content": "Hello Claude"},
    ]
    loaded = client.get(f"/api/converse/sessions/{session['id']}").json()
    assert loaded["messages"][0]["profile_snapshot"]["catalog_identity"] == "character:tmdb:42"
    assert loaded["messages"][-1]["status"] == "complete"


def test_anthropic_scene_guard_blocks_non_wikidata_active_profile_before_model_or_turn(client, monkeypatch):
    session = client.post("/api/converse/sessions", json={
        "profile": {"name": "Catalog role", "catalog_identity": "character:anilist:17"},
        "scene": {"participant_ids": ["primary"], "rounds": 1},
    }).json()
    monkeypatch.setattr(converse, "_stream_for_provider", lambda *_a, **_k: pytest.fail("guard precedes provider call"))

    response = client.post(f"/api/converse/sessions/{session['id']}/scene", json={
        "provider": "anthropic", "api_key": "request-key", "model": "claude-test",
    })

    assert response.status_code == 422
    assert client.get(f"/api/converse/sessions/{session['id']}").json()["messages"] == []
    assert session["id"] not in converse._active_turns


def test_anthropic_single_message_checks_only_selected_speaker_profile(client, monkeypatch):
    session = client.post("/api/converse/sessions", json={
        "characters": [
            {"id": "primary", "profile": {"name": "Mira", "description": "Selected custom profile"}},
            {"id": "bea", "profile": {"name": "Bea", "description": "Unselected imported biography",
                                         "catalog_identity": "character:anilist:17"}},
        ],
    }).json()
    calls = []

    async def stream(_url, _model, messages, _key, _provider):
        calls.append(messages)
        yield "Mira replies"

    monkeypatch.setattr(converse, "_stream_for_provider", stream)
    response = _send(client, session["id"], speaker_id="primary")

    assert response.status_code == 200, response.text
    prompt = calls[0][0]["content"]
    assert "Selected custom profile" in prompt
    assert "Bea" in prompt
    assert "Unselected imported biography" not in prompt
    assert client.get(f"/api/converse/sessions/{session['id']}").json()["messages"][-1]["status"] == "complete"


def test_anthropic_scene_checks_only_selected_profiles_but_sends_other_names(client, monkeypatch):
    session = client.post("/api/converse/sessions", json={
        "characters": [
            {"id": "primary", "profile": {"name": "Mira", "description": "Selected custom profile"}},
            {"id": "bea", "profile": {"name": "Bea", "description": "Unselected imported biography",
                                         "source_notes": "Private catalog context", "catalog_identity": "character:anilist:17"}},
        ],
        "scene": {"context": "At the station.", "participant_ids": ["primary"], "rounds": 1},
    }).json()
    calls = []

    async def stream(_url, _model, messages, _key, _provider):
        calls.append(messages)
        yield "Mira speaks"

    monkeypatch.setattr(converse, "_stream_for_provider", stream)
    response = client.post(f"/api/converse/sessions/{session['id']}/scene", json={
        "provider": "anthropic", "api_key": "request-key", "model": "claude-test",
    })

    assert response.status_code == 200, response.text
    prompt = calls[0][0]["content"]
    assert "Selected custom profile" in prompt
    assert "Bea" in prompt
    assert "Unselected imported biography" not in prompt
    assert "Private catalog context" not in prompt
    assert client.get(f"/api/converse/sessions/{session['id']}").json()["messages"][0]["status"] == "complete"


def test_anthropic_name_only_chat_sends_name_without_catalog_context_and_keeps_saved_identity(client, monkeypatch):
    profile = {"name": "Mira", "description": "Imported private bio", "personality": "Imported traits",
               "voice_description": "Imported voice", "source_notes": "Imported source notes",
               "catalog_identity": "character:anilist:17"}
    session = client.post("/api/converse/sessions", json={
        "kind": "chat", "roleplay": True, "profile": profile,
    }).json()
    raw = converse._read(converse._session_path(session["id"]))
    raw["messages"] = [{"role": "user", "content": "Old prompt"},
                        {"role": "assistant", "content": "Old answer", "status": "complete"}]
    converse._save(raw)
    calls = []

    async def stream(_url, _model, messages, _key, _provider):
        calls.append(messages)
        yield "Name-only response"

    monkeypatch.setattr(converse, "_stream_for_provider", stream)
    response = _send(client, session["id"], name_only_context=True)

    assert response.status_code == 200, response.text
    assert len(calls) == 1
    assert calls[0][-1] == {"role": "user", "content": "Hello Claude"}
    system_prompt = calls[0][0]["content"]
    assert "Mira" in system_prompt
    for private_field in ("Imported private bio", "Imported traits", "Imported voice", "Imported source notes"):
        assert private_field not in system_prompt
    loaded = client.get(f"/api/converse/sessions/{session['id']}").json()
    assert loaded["profile"]["catalog_identity"] == "character:anilist:17"
    assert loaded["messages"][-1]["profile_snapshot"]["catalog_identity"] == "character:anilist:17"


def test_anthropic_name_only_scene_omits_imported_profile_and_historical_turns(client, monkeypatch):
    profile = {"name": "Mira", "description": "Imported scene biography", "personality": "Private traits",
               "source_notes": "Catalog notes", "catalog_identity": "character:anilist:17"}
    session = client.post("/api/converse/sessions", json={
        "profile": profile,
        "characters": [{"id": "primary", "profile": profile}, {"id": "bea", "profile": {"name": "Bea"}}],
        "scene": {"context": "At a quiet station.", "participant_ids": ["primary", "bea"], "rounds": 1},
    }).json()
    raw = converse._read(converse._session_path(session["id"]))
    raw["messages"] = [{"role": "assistant", "content": "A historical answer", "status": "complete",
                        "speaker_id": "primary", "profile_snapshot": profile, "scene_run_id": "old-run"}]
    converse._save(raw)
    calls = []

    async def stream(_url, _model, messages, _key, _provider):
        calls.append(messages)
        yield f"Scene reply {len(calls)}"

    monkeypatch.setattr(converse, "_stream_for_provider", stream)
    response = client.post(f"/api/converse/sessions/{session['id']}/scene", json={
        "provider": "anthropic", "api_key": "request-key", "model": "claude-test", "name_only_context": True,
    })

    assert response.status_code == 200
    assert len(calls) == 2
    serialized = json.dumps(calls[0])
    assert calls[0][-1]["role"] == "user"
    assert "Continue the current scene" in calls[0][-1]["content"]
    assert "Mira" in serialized and "At a quiet station." in serialized
    for imported in ("Imported scene biography", "Private traits", "Catalog notes", "A historical answer"):
        assert imported not in serialized
    current_run_followup = json.dumps(calls[1])
    assert "Scene reply 1" in current_run_followup
    assert "A historical answer" not in current_run_followup
    loaded = client.get(f"/api/converse/sessions/{session['id']}").json()
    assert loaded["characters"][0]["profile"]["catalog_identity"] == "character:anilist:17"
    assert loaded["messages"][0]["profile_snapshot"]["catalog_identity"] == "character:anilist:17"
    assert [message["content"] for message in loaded["messages"][-2:]] == ["Scene reply 1", "Scene reply 2"]


def test_anthropic_name_only_profile_draft_drops_imported_evidence_but_keeps_user_context(client, monkeypatch):
    monkeypatch.setattr(suggestions, "get_snapshot_store", lambda: pytest.fail("name-only skips catalog lookup"))
    captured = []

    async def stream(_url, _model, messages, _key, _provider):
        captured.append(messages)
        yield json.dumps({"personality": "Patient", "voice_description": "Measured",
                          "preset_id": None, "rationale": "A useful draft.", "uncertainty": []})

    monkeypatch.setattr(converse, "_stream_for_provider", stream)
    response = client.post("/api/converse/profile-suggestions", json={
        "provider": "anthropic", "api_key": "request-key", "model": "claude-test",
        "name_only_context": True, "name": "Mira", "catalog_identity": "character:anilist:17",
        "description": "Imported biography", "source_notes": "Imported catalog notes",
        "creative_direction": "Gentle, lightly humorous", "voice_constraints": "Keep a measured pace.",
        "candidates": [{"id": "quiet_observer", "name": "Quiet observer"}],
    })

    assert response.status_code == 200, response.text
    assert response.json()["catalog_evidence"] is None
    prompt = json.dumps(captured[0])
    for imported in ("Imported biography", "Imported catalog notes", "character:anilist:17"):
        assert imported not in prompt
    assert "Gentle, lightly humorous" in prompt
    assert "Keep a measured pace." in prompt
    assert "quiet_observer" in prompt


@pytest.mark.parametrize("identity", [None, "character:wikidata:Q42"])
def test_anthropic_profile_suggestions_allow_custom_and_wikidata(client, monkeypatch, identity):
    class EvidenceStore:
        def get_entity(self, provider, entity_type, source_id):
            assert (provider, entity_type, source_id) == ("wikidata", "character", identity) if identity else True
            return {"fetched_at": 1, "metadata": {"title": "Wikidata character"}}

    looked_up = []
    def get_store():
        looked_up.append(True)
        return EvidenceStore()
    monkeypatch.setattr(suggestions, "get_snapshot_store", get_store)

    async def stream(base_url, model, messages, api_key, provider):
        assert provider == "anthropic" and api_key == "request-key"
        yield json.dumps({"personality": "Patient", "voice_description": "Measured",
                          "preset_id": None, "rationale": "A useful draft.", "uncertainty": []})

    monkeypatch.setattr(converse, "_stream_for_provider", stream)
    payload = {"provider": "anthropic", "api_key": "request-key", "model": "claude-test"}
    if identity:
        payload["catalog_identity"] = identity
    response = client.post("/api/converse/profile-suggestions", json=payload)

    assert response.status_code == 200, response.text
    assert response.json()["catalog_evidence"] is None if identity is None else response.json()["catalog_evidence"]["identity"] == identity
    assert len(looked_up) == int(identity is not None)
    assert "request-key" not in response.text


def test_anthropic_profile_suggestions_block_catalog_before_evidence_or_stream(client, monkeypatch):
    monkeypatch.setattr(suggestions, "get_snapshot_store", lambda: pytest.fail("guard precedes evidence lookup"))
    monkeypatch.setattr(converse, "_stream_for_provider", lambda *_a, **_k: pytest.fail("guard precedes generation"))

    response = client.post("/api/converse/profile-suggestions", json={
        "provider": "anthropic", "api_key": "request-key", "model": "claude-test",
        "catalog_identity": "character:anilist:17",
    })

    assert response.status_code == 422
    assert "policy review" in response.json()["detail"]
