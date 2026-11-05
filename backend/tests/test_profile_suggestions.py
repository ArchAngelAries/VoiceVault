import json
import asyncio

import httpx
import pytest
from fastapi import FastAPI
from fastapi import HTTPException
from starlette.requests import Request

import api.converse as converse
import api.profile_suggestions as suggestions


@pytest.fixture
def fixture_app(tmp_path, monkeypatch):
    monkeypatch.setattr(converse, "STORE_DIR", tmp_path / "sessions")
    monkeypatch.setattr(suggestions, "COMPLETION_TIMEOUT_SECONDS", 1)
    captured = {}
    class FakeStore:
        def get_entity(self, provider, entity_type, source_id):
            captured["identity_query"] = (provider, entity_type, source_id)
            return {"fetched_at": 123.0, "metadata": {
                "title": "Catalog name", "description": "Public description",
                "actor": "Stage performer", "media_titles": ["Title one", "Title two"],
                "voice_actors": [{"name": "Voice performer", "language": "Japanese"}],
                "cover_url": "https://private.example/image.jpg", "source_notes": "must not leak",
            }}
    monkeypatch.setattr(suggestions, "get_snapshot_store", lambda: FakeStore())
    async def stream(_url, _model, messages, _key, _provider):
        captured["messages"] = messages
        yield '{"personality":"Patient and observant",'
        yield '"voice_description":"Measured, clear delivery",'
        yield '"preset_id":"quiet_librarian",'
        yield '"rationale":"A flexible creative direction.","uncertainty":[]}'
    monkeypatch.setattr(converse, "_stream_for_provider", stream)
    app = FastAPI()
    app.include_router(converse.router, prefix="/api/converse")
    return app, captured


async def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_actual_router_returns_draft_and_only_safe_exact_catalog_fields(fixture_app):
    app, captured = fixture_app
    async with await client_for(app) as client:
        response = await client.post("/api/converse/profile-suggestions", json={
            "provider": "ollama", "model": "local-model", "api_key": "one-time-secret",
            "name": "Ada", "catalog_identity": "character:anilist:17",
            "description": "User description", "source_notes": "unverified note",
            "creative_direction": "quiet, low pitch",
            "voice_constraints": "Omni uses fixed vocabulary; no reference clips are available.",
            "candidates": [{"id": "quiet_librarian", "name": "Quiet librarian", "description": "quiet"}],
        })
    assert response.status_code == 200
    body = response.json()
    assert body["catalog_evidence"] == {
        "identity": "character:anilist:17", "provider": "anilist", "fetched_at": 123.0,
        "fields": [{"label": "Title", "text": "Catalog name"},
                   {"label": "Description", "text": "Public description"},
                   {"label": "Actor name", "text": "Stage performer"},
                   {"label": "Associated titles", "text": "Title one, Title two"},
                   {"label": "Voice actor", "text": "Voice performer (Japanese)"}],
    }
    assert body["interpretation"]["preset_id"] == "quiet_librarian"
    assert body["user_context"]["source_notes"] == "unverified note"
    prompt = json.dumps(captured["messages"])
    assert "one-time-secret" not in prompt
    assert "private.example" not in prompt
    assert "must not leak" not in prompt
    assert "Omni uses fixed vocabulary" in prompt
    assert captured["identity_query"] == ("anilist", "character", "character:anilist:17")


@pytest.mark.asyncio
async def test_invalid_request_does_not_echo_api_key(fixture_app):
    app, _captured = fixture_app
    async with await client_for(app) as client:
        response = await client.post("/api/converse/profile-suggestions", json={
            "model": "m", "api_key": "do-not-echo", "unexpected": "field",
        })
    assert response.status_code == 422
    assert "do-not-echo" not in response.text


@pytest.mark.asyncio
async def test_unknown_candidate_preset_is_rejected(fixture_app):
    app, _captured = fixture_app
    async with await client_for(app) as client:
        response = await client.post("/api/converse/profile-suggestions", json={
            "model": "local-model", "candidates": [{"id": "not-a-preset", "name": "Choice"}],
        })
    assert response.status_code == 422


@pytest.mark.parametrize("completion", [
    '{"personality":"Only one field"}',
    '{"personality":"p","voice_description":"v","preset_id":null,"rationale":"r","uncertainty":[],"extra":"x"}',
    '{"personality":"p","voice_description":"v","preset_id":null,"rationale":"r"}',
    '{"personality":"p","voice_description":"v","rationale":"r","uncertainty":[]}',
])
@pytest.mark.asyncio
async def test_missing_or_extra_completion_fields_are_rejected(fixture_app, monkeypatch, completion):
    app, _captured = fixture_app
    async def generated(*_args):
        yield completion
    monkeypatch.setattr(converse, "_stream_for_provider", generated)
    async with await client_for(app) as client:
        response = await client.post("/api/converse/profile-suggestions", json={"model": "local-model"})
    assert response.status_code == 502
    assert "extra" not in response.text


@pytest.mark.asyncio
async def test_malformed_completion_is_safe_502(fixture_app, monkeypatch):
    app, _captured = fixture_app
    async def malformed(*_args):
        yield "```json\n{}\n```"
    monkeypatch.setattr(converse, "_stream_for_provider", malformed)
    async with await client_for(app) as client:
        response = await client.post("/api/converse/profile-suggestions", json={"model": "local-model"})
    assert response.status_code == 502
    assert "```" not in response.text


@pytest.mark.asyncio
async def test_preset_must_be_one_of_the_submitted_candidates(fixture_app):
    app, _captured = fixture_app
    async with await client_for(app) as client:
        response = await client.post("/api/converse/profile-suggestions", json={
            "model": "local-model", "candidates": [
                {"id": "quiet_observer", "name": "Quiet observer"}],
        })
    assert response.status_code == 502


@pytest.mark.asyncio
async def test_legacy_actor_identity_gets_explicit_unknown_and_source_missing_warning(fixture_app, monkeypatch):
    app, _captured = fixture_app
    class LegacyStore:
        def get_entity(self, *_args):
            return {"fetched_at": 2, "metadata": {"title": "Old role", "source_status": "missing"}}
    monkeypatch.setattr(suggestions, "get_snapshot_store", lambda: LegacyStore())
    async with await client_for(app) as client:
        response = await client.post("/api/converse/profile-suggestions", json={
            "model": "local-model", "catalog_identity": "character:tmdb:42",
            "candidates": [{"id": "quiet_librarian", "name": "Quiet librarian"}],
        })
    body = response.json()
    assert response.status_code == 200
    assert any("Actor and voice actor identity" in item for item in body["interpretation"]["uncertainty"])
    assert any("marked this retained record missing" in warning for warning in body["warnings"])
    assert any("exact title-specific character role is unknown" in warning for warning in body["warnings"])


@pytest.mark.asyncio
async def test_timeout_closes_upstream_stream_and_returns_safe_504(fixture_app, monkeypatch):
    app, _captured = fixture_app
    closed = []
    async def hanging(*_args):
        try:
            yield '{"personality":"waiting"'
            await asyncio.Event().wait()
        finally:
            closed.append(True)
    monkeypatch.setattr(converse, "_stream_for_provider", hanging)
    monkeypatch.setattr(suggestions, "COMPLETION_TIMEOUT_SECONDS", 0.02)
    async with await client_for(app) as client:
        response = await client.post("/api/converse/profile-suggestions", json={"model": "local-model"})
    assert response.status_code == 504
    assert closed == [True]


@pytest.mark.asyncio
async def test_overlong_completion_closes_stream_and_returns_safe_502(fixture_app, monkeypatch):
    app, _captured = fixture_app
    closed = []
    async def too_long(*_args):
        try:
            yield "x" * (suggestions.MAX_COMPLETION_CHARS + 1)
        finally:
            closed.append(True)
    monkeypatch.setattr(converse, "_stream_for_provider", too_long)
    async with await client_for(app) as client:
        response = await client.post("/api/converse/profile-suggestions", json={"model": "local-model"})
    assert response.status_code == 502
    assert closed == [True]


@pytest.mark.asyncio
async def test_provider_failure_is_safe_502(fixture_app, monkeypatch):
    app, _captured = fixture_app
    async def failed(*_args):
        raise RuntimeError("private-upstream-body")
        yield "unreachable"
    monkeypatch.setattr(converse, "_stream_for_provider", failed)
    async with await client_for(app) as client:
        response = await client.post("/api/converse/profile-suggestions", json={"model": "local-model"})
    assert response.status_code == 502
    assert "private-upstream-body" not in response.text


@pytest.mark.asyncio
async def test_client_disconnect_cancels_and_closes_generator(monkeypatch):
    closed = []
    async def hanging(*_args):
        try:
            yield '{"personality":"waiting"'
            await asyncio.Event().wait()
        finally:
            closed.append(True)
    monkeypatch.setattr(converse, "_stream_for_provider", hanging)
    async def disconnected_receive():
        return {"type": "http.disconnect"}
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []}, disconnected_receive)
    payload = suggestions.SuggestionRequest(model="local-model")
    with pytest.raises(HTTPException) as caught:
        await suggestions._completion(payload, [], request)
    assert caught.value.status_code == 499
    assert closed == [True]


@pytest.mark.asyncio
async def test_profile_catalog_identity_round_trip_and_validation(fixture_app):
    app, _captured = fixture_app
    async with await client_for(app) as client:
        created = await client.post("/api/converse/sessions", json={
            "kind": "chat", "profile": {"name": "Ada", "catalog_identity": "character:tmdb:movie-42-abcdef0123456789abcdef01"},
        })
        assert created.status_code == 200
        session_id = created.json()["id"]
        loaded = await client.get(f"/api/converse/sessions/{session_id}")
        invalid = await client.post("/api/converse/sessions", json={
            "kind": "chat", "profile": {"catalog_identity": "anime:anilist:17"},
        })
    assert loaded.json()["profile"]["catalog_identity"] == "character:tmdb:movie-42-abcdef0123456789abcdef01"
    assert invalid.status_code == 422
