"""Ordinary LLM chat behavior alongside legacy Scene Creation sessions."""
import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.converse as converse


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(converse, "STORE_DIR", tmp_path / "conversations")
    monkeypatch.setattr(converse, "_active_turns", set())
    app = FastAPI()
    app.include_router(converse.router, prefix="/api/converse")
    return TestClient(app)


def test_chat_uses_plain_prompt_real_history_and_exact_voice_snapshots(client, monkeypatch):
    profile = {
        "name": "A fictional hero", "description": "Secret biography", "personality": "Very dramatic",
        "source_notes": "Private source data", "voice_description": "Warm voice",
        "voice_settings": {"model_id": "tts/voice-a", "reference_id": "ref-a", "speech_mode": "reference",
                           "reference_text": "Hello there.", "language": "English",
                           "generation": {"temperature": 0.7, "top_p": 0.9, "repetition_penalty": 1.1,
                                          "max_new_tokens": 1300, "seed": 17}},
    }
    session = client.post("/api/converse/sessions", json={"kind": "chat", "profile": profile}).json()
    assert session["kind"] == "chat" and session["spoken_replies"] is True
    captured = []
    call_count = 0

    async def model(base_url, model, messages, api_key=None):
        nonlocal call_count
        call_count += 1
        captured.append(messages)
        yield f"Assistant answer {call_count}."

    monkeypatch.setattr(converse, "stream_chat", model)

    async def send(text, api_key=None):
        response = await converse.send_message(session["id"], converse.MessageRequest(
            text=text, model="local", api_key=api_key))
        return [packet async for packet in response.body_iterator]

    asyncio.run(send("First question", "never-save-this-api-key"))
    first_saved = client.get(f"/api/converse/sessions/{session['id']}").json()
    frozen_profile = first_saved["profile"]
    first_reply = first_saved["messages"][-1]
    assert first_reply["profile_snapshot"] == frozen_profile
    assert b"never-save-this-api-key" not in converse._session_path(session["id"]).read_bytes()
    assert captured[0][0]["role"] == "system"
    assert "helpful AI assistant" in captured[0][0]["content"]
    for private_field in ("A fictional hero", "Secret biography", "Very dramatic", "Private source data", "Warm voice"):
        assert private_field not in captured[0][0]["content"]
    assert captured[0][1:] == [{"role": "user", "content": "First question"}]

    updated_profile = {**profile, "voice_description": "Changed voice", "voice_settings": {
        **profile["voice_settings"], "reference_text": "Changed transcript.", "generation": {
            **profile["voice_settings"]["generation"], "seed": 91, "max_new_tokens": 2500}}}
    patched = client.patch(f"/api/converse/sessions/{session['id']}", json={
        "profile": updated_profile, "spoken_replies": False, "kind": "scene",
    }).json()
    assert patched["kind"] == "chat" and patched["spoken_replies"] is False
    asyncio.run(send("Follow-up question"))
    saved = client.get(f"/api/converse/sessions/{session['id']}").json()
    assert saved["kind"] == "chat" and saved["spoken_replies"] is False
    assert saved["messages"][1]["profile_snapshot"] == frozen_profile
    assert saved["messages"][3]["profile_snapshot"] == saved["profile"]
    assert saved["messages"][0] == {"role": "user", "content": "First question"}
    assert captured[1][1:] == [
        {"role": "user", "content": "First question"},
        {"role": "assistant", "content": "Assistant answer 1."},
        {"role": "user", "content": "Follow-up question"},
    ]
    assert "Changed voice" not in captured[1][0]["content"]
    assert "Changed transcript." not in captured[1][0]["content"]


def test_chat_requires_primary_only_and_rejects_scene_generation(client, monkeypatch):
    response = client.post("/api/converse/sessions", json={"kind": "chat", "characters": [
        {"id": "primary", "profile": {}}, {"id": "sidekick", "profile": {}},
    ]})
    assert response.status_code == 422
    session = client.post("/api/converse/sessions", json={"kind": "chat"}).json()
    response = client.patch(f"/api/converse/sessions/{session['id']}", json={"characters": [
        {"id": "primary", "profile": {}}, {"id": "sidekick", "profile": {}},
    ]})
    assert response.status_code == 422
    monkeypatch.setattr(converse, "stream_chat", lambda *args, **kwargs: pytest.fail("no generation expected"))

    async def run():
        with pytest.raises(converse.HTTPException) as scene_error:
            await converse.run_scene(session["id"], converse.SceneRequest(model="local"))
        assert scene_error.value.status_code == 422
        with pytest.raises(converse.HTTPException) as speaker_error:
            await converse.send_message(session["id"], converse.MessageRequest(
                text="hello", model="local", speaker_id="sidekick"))
        assert speaker_error.value.status_code == 422
    asyncio.run(run())


def test_chat_roleplay_uses_editable_primary_profile_and_preserves_exact_character_id(client, monkeypatch):
    character_id = "provider/role:case-Sensitive exact ID"
    profile = {"name": "Mira", "description": "A lighthouse keeper", "personality": "Careful and curious",
               "source_notes": "Her history is user supplied and may be uncertain."}
    session = client.post("/api/converse/sessions", json={
        "kind": "chat", "roleplay": True, "character_id": character_id, "profile": profile,
    }).json()
    assert session["roleplay"] is True and session["character_id"] == character_id
    captured = []

    async def model(base_url, model, messages, api_key=None):
        captured.append(messages)
        yield f"In character reply {len(captured)}."
    monkeypatch.setattr(converse, "stream_chat", model)

    async def send(text):
        response = await converse.send_message(session["id"], converse.MessageRequest(text=text, model="local"))
        return [packet async for packet in response.body_iterator]
    asyncio.run(send("What do you see?"))
    assert "Name: Mira" in captured[0][0]["content"]
    assert "Description: A lighthouse keeper" in captured[0][0]["content"]
    assert "Personality: Careful and curious" in captured[0][0]["content"]
    assert "Source notes (unverified character context): Her history is user supplied and may be uncertain." in captured[0][0]["content"]
    assert "Active speaker: Mira" in captured[0][0]["content"]
    assert captured[0][1:] == [{"role": "user", "content": "What do you see?"}]

    # Creation settings are immutable for this history, even if an older client sends them in PATCH.
    client.patch(f"/api/converse/sessions/{session['id']}", json={
        "roleplay": False, "character_id": "replacement-role-id", "spoken_replies": False,
    })
    asyncio.run(send("And now?"))
    loaded = client.get(f"/api/converse/sessions/{session['id']}").json()
    assert loaded["roleplay"] is True and loaded["character_id"] == character_id
    assert loaded["messages"][1]["profile_snapshot"] == loaded["profile"]
    assert captured[1][0]["content"] == captured[0][0]["content"]
    assert captured[1][1:] == [
        {"role": "user", "content": "What do you see?"},
        {"role": "assistant", "content": "In character reply 1."},
        {"role": "user", "content": "And now?"},
    ]


def test_profile_portrait_persists_and_is_frozen_on_roleplay_replies(client, monkeypatch):
    portrait = "/api/voice-library/characters/mira/portrait.webp?size=384"
    profile = {"name": "Mira", "image_url": portrait, "voice_description": "Warm voice"}
    session = client.post("/api/converse/sessions", json={
        "kind": "chat", "roleplay": True, "character_id": "library:mira", "profile": profile,
    }).json()
    assert session["profile"]["image_url"] == portrait

    async def model(*args, **kwargs):
        yield "A reply with portrait."
    monkeypatch.setattr(converse, "stream_chat", model)
    async def run():
        response = await converse.send_message(session["id"], converse.MessageRequest(text="Hello", model="local"))
        return [packet async for packet in response.body_iterator]
    asyncio.run(run())
    saved = client.get(f"/api/converse/sessions/{session['id']}").json()
    assert saved["messages"][-1]["profile_snapshot"]["image_url"] == portrait

    changed_profile = {**profile, "image_url": "https://images.example.test/mira-new.webp"}
    patched = client.patch(f"/api/converse/sessions/{session['id']}", json={"profile": changed_profile}).json()
    assert patched["profile"]["image_url"] == changed_profile["image_url"]
    saved = client.get(f"/api/converse/sessions/{session['id']}").json()
    assert saved["messages"][-1]["profile_snapshot"]["image_url"] == portrait


def test_uploaded_portrait_override_persists_clears_and_keeps_catalog_portrait(client, monkeypatch):
    catalog_portrait = "/api/voice-library/characters/mira/catalog.webp"
    upload_portrait = "/api/tiers/images/portrait-asset-123.png"
    profile = {"name": "Mira", "image_url": catalog_portrait, "portrait_override_url": upload_portrait}
    session = client.post("/api/converse/sessions", json={
        "kind": "chat", "roleplay": True, "profile": profile,
    }).json()
    assert session["profile"]["image_url"] == catalog_portrait
    assert session["profile"]["portrait_override_url"] == upload_portrait

    async def model(*args, **kwargs):
        yield "Reply with uploaded portrait."
    monkeypatch.setattr(converse, "stream_chat", model)
    async def run():
        response = await converse.send_message(session["id"], converse.MessageRequest(text="Hi", model="local"))
        return [packet async for packet in response.body_iterator]
    asyncio.run(run())
    saved = client.get(f"/api/converse/sessions/{session['id']}").json()
    assert saved["messages"][-1]["profile_snapshot"]["portrait_override_url"] == upload_portrait
    assert saved["messages"][-1]["profile_snapshot"]["image_url"] == catalog_portrait

    patched = client.patch(f"/api/converse/sessions/{session['id']}", json={"profile": {
        **profile, "portrait_override_url": None,
    }}).json()
    assert patched["profile"]["image_url"] == catalog_portrait
    assert "portrait_override_url" not in patched["profile"]
    assert patched["messages"][-1]["profile_snapshot"]["portrait_override_url"] == upload_portrait


def test_manual_scene_reply_keeps_portrait_snapshot_when_available(client, monkeypatch):
    profile = {"name": "Mira", "image_url": "/catalog/mira.webp",
               "portrait_override_url": "/api/tiers/images/mira-upload.png"}
    session = client.post("/api/converse/sessions", json={"profile": profile}).json()
    async def model(*args, **kwargs):
        yield "Hello from Mira."
    monkeypatch.setattr(converse, "stream_chat", model)
    async def run():
        response = await converse.send_message(session["id"], converse.MessageRequest(text="Hello", model="local"))
        return [packet async for packet in response.body_iterator]
    asyncio.run(run())
    saved = client.get(f"/api/converse/sessions/{session['id']}").json()
    assert saved["messages"][-1]["profile_snapshot"] == session["profile"]


def test_profile_image_url_is_optional_bounded_and_rejects_unsafe_schemes(client):
    image_less = client.post("/api/converse/sessions", json={"kind": "chat"}).json()
    assert "image_url" not in image_less["profile"]
    assert image_less["characters"][0]["profile"] == image_less["profile"]
    blank_image = client.post("/api/converse/sessions", json={
        "kind": "chat", "profile": {"name": "Mira", "image_url": ""},
    }).json()
    assert blank_image["profile"]["name"] == "Mira" and "image_url" not in blank_image["profile"]
    patched = client.patch(f"/api/converse/sessions/{blank_image['id']}", json={
        "profile": {"name": "Mira", "image_url": "   "},
    }).json()
    assert "image_url" not in patched["profile"]

    for image_url in (
        "javascript:alert(1)", "file:///private/image.png", "data:image/png;base64,abc",
        "//outside.example.test/image.png", "/api/images/../private.png", "https://user:pass@host/image.png",
        "x" * 2049,
    ):
        response = client.post("/api/converse/sessions", json={"kind": "chat", "profile": {"image_url": image_url}})
        assert response.status_code == 422, image_url[:40]


def test_new_chat_preferences_and_legacy_scene_defaults_are_persisted_without_rewriting(client):
    created = client.post("/api/converse/sessions", json={"kind": "chat"}).json()
    assert created["kind"] == "chat" and created["spoken_replies"] is True
    patched = client.patch(f"/api/converse/sessions/{created['id']}", json={"spoken_replies": False}).json()
    assert patched["kind"] == "chat" and patched["spoken_replies"] is False
    assert client.get(f"/api/converse/sessions/{created['id']}").json()["spoken_replies"] is False

    legacy_id = "2213a211-5fe1-4c6d-97f1-19b3ea83c387"
    legacy = {"id": legacy_id, "title": "Older scene", "profile": {"name": "Ada"}, "messages": []}
    path = converse.STORE_DIR / f"{legacy_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(legacy), encoding="utf-8")
    loaded = client.get(f"/api/converse/sessions/{legacy_id}").json()
    assert loaded["kind"] == "scene" and loaded["spoken_replies"] is False
    assert loaded["scene"] == {"context": "", "participant_ids": [], "rounds": 1}
    assert json.loads(path.read_text(encoding="utf-8")) == legacy
