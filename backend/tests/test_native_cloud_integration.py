"""Native cloud selection and source handling through actual conversation routes."""
import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.converse as converse
import api.profile_suggestions as suggestions
from llm.client import normalize_base_url


@pytest.fixture(params=[
    ("anthropic", "https://api.anthropic.com/v1"),
    ("openai", "https://api.openai.com/v1"),
    ("gemini", "https://generativelanguage.googleapis.com/v1beta"),
])
def native(request, tmp_path, monkeypatch):
    provider, endpoint = request.param
    monkeypatch.setattr(converse, "STORE_DIR", tmp_path / "conversations")
    monkeypatch.setattr(converse, "_active_turns", set())
    monkeypatch.setattr(converse, "get_setting", lambda name, default=None: provider if name == "llm_default_backend" else default)
    app = FastAPI()
    app.include_router(converse.router, prefix="/api/converse")
    return TestClient(app), provider, endpoint


def test_native_defaults_and_explicit_authenticated_model_load(native, monkeypatch):
    client, provider, endpoint = native
    calls = []

    async def models(url, key=None, *, provider):
        calls.append((url, key, provider))
        return [{"id": "text-test"}]

    monkeypatch.setattr(converse, "list_models", models)
    assert client.get("/api/converse/connection").json()["default_endpoint"] == endpoint
    assert calls == []
    assert client.post("/api/converse/models", json={"provider": provider}).status_code == 422
    assert calls == []
    assert client.post("/api/converse/models", json={"provider": provider, "api_key": "request-only-key"}).json() == {"models": [{"id": "text-test"}]}
    assert calls == [(endpoint, "request-only-key", provider)]
    assert normalize_base_url(endpoint + "/", provider) == endpoint


def test_native_name_only_request_omits_imports_and_retains_local_profile(native, monkeypatch):
    client, provider, endpoint = native
    profile = {"name": "Mira", "catalog_identity": "character:anilist:17", "description": "Imported description", "source_notes": "Imported source notes"}
    session = client.post("/api/converse/sessions", json={"kind": "chat", "roleplay": True, "profile": profile}).json()
    calls = []

    async def stream(url, model, messages, key, selected_provider):
        calls.append((url, messages, key, selected_provider))
        yield "Hello from the selected service."

    monkeypatch.setattr(converse, "_stream_for_provider", stream)
    payload = {"text": "Say hello", "model": "text-test", "provider": provider, "api_key": "request-only-key"}
    rejected = client.post(f"/api/converse/sessions/{session['id']}/message", json=payload)
    assert rejected.status_code == 422 and not calls
    assert client.get(f"/api/converse/sessions/{session['id']}").json()["messages"] == []

    async def send():
        response = await converse.send_message(session["id"], converse.MessageRequest(**payload, name_only_context=True))
        return [packet async for packet in response.body_iterator]

    packets = asyncio.run(send())
    assert any(b"event: done" in packet for packet in packets)
    assert calls[0][0] == endpoint and calls[0][2:] == ("request-only-key", provider)
    outbound = json.dumps(calls[0][1])
    assert "Mira" in outbound and "Say hello" in outbound
    assert "Imported" not in outbound and "anilist" not in outbound
    stored = client.get(f"/api/converse/sessions/{session['id']}").json()
    assert stored["profile"]["catalog_identity"] == profile["catalog_identity"]
    assert stored["profile"]["description"] == profile["description"]
    assert stored["messages"][-1]["status"] == "complete"
    assert "request-only-key" not in json.dumps(stored)


def test_native_profile_draft_uses_only_explicit_names_only_context(native, monkeypatch):
    client, provider, _endpoint = native
    captured = []
    monkeypatch.setattr(suggestions, "_catalog_evidence", lambda *_args: pytest.fail("must omit retained evidence"))

    async def completion(payload, messages, request):
        captured.append(messages)
        return json.dumps({"personality": "Patient", "voice_description": "Measured", "preset_id": None,
                           "rationale": "Creative direction", "uncertainty": []})

    monkeypatch.setattr(suggestions, "_completion", completion)
    response = client.post("/api/converse/profile-suggestions", json={
        "provider": provider, "api_key": "request-only-key", "model": "text-test", "name_only_context": True,
        "name": "Mira", "catalog_identity": "character:tmdb:movie-10-0123456789abcdef01234567", "description": "Imported description",
        "source_notes": "Imported notes", "creative_direction": "Patient courier", "voice_constraints": "Measured delivery",
    })
    assert response.status_code == 200
    outbound = json.dumps(captured)
    assert "Patient courier" in outbound and "Measured delivery" in outbound
    assert "Imported" not in outbound and "tmdb" not in outbound and "request-only-key" not in outbound
    assert response.json()["catalog_evidence"] is None
