"""Isolated coverage for bounded saved conversation scenes."""
import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.converse as converse
from llm.client import CompletionStoppedError


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(converse, "STORE_DIR", tmp_path / "conversations")
    monkeypatch.setattr(converse, "_active_turns", set())
    app = FastAPI()
    app.include_router(converse.router, prefix="/api/converse")
    return TestClient(app)


def _events(packets):
    return [(packet.split(b"\n", 1)[0][7:].decode(), json.loads(packet.split(b"data: ", 1)[1]))
            for packet in packets]


def test_provider_cutoff_keeps_partial_scene_and_stops_remaining_speakers(client, monkeypatch):
    session = client.post("/api/converse/sessions", json={
        "characters": [{"id": "primary", "profile": {"name": "Ada"}}, {"id": "bea", "profile": {"name": "Bea"}}],
        "scene": {"participant_ids": ["primary", "bea"], "rounds": 2},
    }).json()
    calls = []
    async def model(*args, **kwargs):
        calls.append(True)
        yield "A partial scene reply"
        raise CompletionStoppedError("length")
    monkeypatch.setattr(converse, "stream_chat", model)
    async def run():
        response = await converse.run_scene(session["id"], converse.SceneRequest(model="local"))
        return [packet async for packet in response.body_iterator]
    events = _events(asyncio.run(run()))
    assert len(calls) == 1
    assert [event for event, _ in events] == ["turn_start", "delta", "error"]
    assert events[-1][1]["message"] == CompletionStoppedError("length").message
    saved = client.get(f"/api/converse/sessions/{session['id']}").json()
    assert len(saved["messages"]) == 1
    assert saved["messages"][0]["content"] == "A partial scene reply"
    assert saved["messages"][0]["status"] == "error"
    assert saved["messages"][0]["speaker_id"] == "primary"
    assert session["id"] not in converse._active_turns


def test_saved_scene_runs_ordered_rounds_without_invented_users_and_freezes_profiles(client, monkeypatch):
    primary = {"name": "Ada", "personality": "Measured", "voice_settings": {"model_id": "voice-a"}}
    bea = {"name": "Bea", "personality": "Sharp", "voice_settings": {"model_id": "voice-b"}}
    session = client.post("/api/converse/sessions", json={
        "profile": primary,
        "characters": [{"id": "primary", "profile": primary}, {"id": "bea", "profile": bea}],
        "scene": {"context": "At the old station.", "participant_ids": ["bea", "primary"], "rounds": 2},
    }).json()
    captured = []

    async def model(base_url, model, messages, api_key=None):
        captured.append(messages)
        yield f"reply-{len(captured)}"

    monkeypatch.setattr(converse, "stream_chat", model)
    async def run():
        response = await converse.run_scene(session["id"], converse.SceneRequest(model="local"))
        return [packet async for packet in response.body_iterator]

    events = _events(asyncio.run(run()))
    starts = [payload for event, payload in events if event == "turn_start"]
    assert [(x["speaker_id"], x["turn_index"], x["total_turns"]) for x in starts] == [
        ("bea", 1, 4), ("primary", 2, 4), ("bea", 3, 4), ("primary", 4, 4)]
    assert starts[0]["profile_snapshot"] == session["characters"][1]["profile"]
    assert all("Current scene context" in messages[0]["content"] for messages in captured)
    assert all("At the old station." in messages[0]["content"] for messages in captured)
    assert all(all(message["content"].startswith("[Previous character turn from ")
                   for message in messages[1:] if message["role"] == "user") for messages in captured)
    saved = client.get(f"/api/converse/sessions/{session['id']}").json()
    assert [message["content"] for message in saved["messages"]] == ["reply-1", "reply-2", "reply-3", "reply-4"]
    assert [message["speaker_id"] for message in saved["messages"]] == ["bea", "primary", "bea", "primary"]
    assert [message["scene_turn_index"] for message in saved["messages"]] == [1, 2, 3, 4]
    assert len({message["scene_run_id"] for message in saved["messages"]}) == 1
    assert saved["messages"][0]["profile_snapshot"] == session["characters"][1]["profile"]
    assert [event for event, _ in events].count("scene_done") == 1


def test_scene_persist_and_reject_invalid_or_removed_participants(client):
    created = client.post("/api/converse/sessions", json={
        "characters": [{"id": "primary", "profile": {}}, {"id": "bea", "profile": {}}],
        "scene": {"context": "x", "participant_ids": ["bea"], "rounds": 3},
    }).json()
    assert client.get(f"/api/converse/sessions/{created['id']}").json()["scene"] == {
        "context": "x", "participant_ids": ["bea"], "rounds": 3}
    for value in (
        {"context": "x" * 4001}, {"participant_ids": ["primary"] * 2},
        {"participant_ids": ["missing"]}, {"participant_ids": ["primary"], "rounds": 4},
    ):
        response = client.post("/api/converse/sessions", json={"scene": value})
        assert response.status_code == 422
    response = client.patch(f"/api/converse/sessions/{created['id']}", json={
        "characters": [{"id": "primary", "profile": {}}],
    })
    assert response.status_code == 422
    assert client.get(f"/api/converse/sessions/{created['id']}").json()["scene"]["participant_ids"] == ["bea"]


def test_legacy_session_reads_default_scene_without_rewriting_its_file(client):
    legacy_id = "2213a211-5fe1-4c6d-97f1-19b3ea83c387"
    legacy = {"id": legacy_id, "title": "Legacy", "profile": {"name": "Ada"}, "messages": []}
    path = converse.STORE_DIR / f"{legacy_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(legacy), encoding="utf-8")
    loaded = client.get(f"/api/converse/sessions/{legacy_id}").json()
    assert loaded["scene"] == {"context": "", "participant_ids": [], "rounds": 1}
    assert json.loads(path.read_text(encoding="utf-8")) == legacy


def test_empty_saved_scene_is_valid_but_cannot_be_run(client, monkeypatch):
    session = client.post("/api/converse/sessions", json={}).json()
    assert session["scene"] == {"context": "", "participant_ids": [], "rounds": 1}
    monkeypatch.setattr(converse, "stream_chat", lambda *args, **kwargs: pytest.fail("empty scene must not call model"))

    async def run():
        with pytest.raises(converse.HTTPException) as exc:
            await converse.run_scene(session["id"], converse.SceneRequest(model="local"))
        assert exc.value.status_code == 422
    asyncio.run(run())


def test_scene_refuses_history_capacity_before_any_model_call(client, monkeypatch):
    session = client.post("/api/converse/sessions", json={
        "scene": {"participant_ids": ["primary"], "rounds": 2},
    }).json()
    session["messages"] = [{"role": "user", "content": "old"}] * (converse.MAX_HISTORY_MESSAGES - 1)
    converse._save(session)
    monkeypatch.setattr(converse, "stream_chat", lambda *args, **kwargs: pytest.fail("must check capacity first"))

    async def run():
        with pytest.raises(converse.HTTPException) as exc:
            await converse.run_scene(session["id"], converse.SceneRequest(model="local"))
        assert exc.value.status_code == 413

    asyncio.run(run())


def test_frozen_voice_profile_survives_later_roster_edit(client, monkeypatch):
    voice_settings = {
        "model_id": "breeze/exact-voice", "reference_id": "reference-42", "speech_mode": "reference",
        "reference_text": "Exact supplied transcript.", "language": "Japanese",
        "delivery": {"emotion": "calm", "style": "natural", "expression": None},
        "generation": {"temperature": 0.6, "top_p": 0.88, "repetition_penalty": 1.13,
                       "max_new_tokens": 1234, "seed": 98765},
    }
    primary = {"name": "Ada"}
    bea = {"name": "Bea", "voice_description": "Soft and steady", "voice_settings": voice_settings}
    session = client.post("/api/converse/sessions", json={
        "profile": primary,
        "characters": [{"id": "primary", "profile": primary}, {"id": "bea", "profile": bea}],
        "scene": {"participant_ids": ["bea"], "rounds": 2},
    }).json()
    frozen = session["characters"][1]["profile"]
    async def model(*args, **kwargs):
        yield "spoken turn"
    monkeypatch.setattr(converse, "stream_chat", model)

    async def run():
        response = await converse.run_scene(session["id"], converse.SceneRequest(model="local"))
        return [packet async for packet in response.body_iterator]
    events = _events(asyncio.run(run()))
    snapshots = [data["profile_snapshot"] for event, data in events if event == "turn_start"]
    assert snapshots == [frozen, frozen]
    assert frozen["voice_settings"] == {
        "model_id": "breeze/exact-voice", "reference_id": "reference-42", "speech_mode": "reference",
        "delivery": {"emotion": "calm", "style": "natural", "expression": None},
        "reference_text": "Exact supplied transcript.", "language": "Japanese",
        "generation": {"temperature": 0.6, "top_p": 0.88, "repetition_penalty": 1.13,
                       "max_new_tokens": 1234, "seed": 98765},
    }
    changed_bea = {**bea, "voice_settings": {**voice_settings, "reference_text": "Changed later", "generation": {
        **voice_settings["generation"], "seed": 3, "max_new_tokens": 4000}}}
    response = client.patch(f"/api/converse/sessions/{session['id']}", json={
        "characters": [{"id": "primary", "profile": session["profile"]}, {"id": "bea", "profile": changed_bea}],
    })
    assert response.status_code == 200
    saved = client.get(f"/api/converse/sessions/{session['id']}").json()
    assert [message["profile_snapshot"] for message in saved["messages"]] == [frozen, frozen]
    assert saved["characters"][1]["profile"]["voice_settings"]["reference_text"] == "Changed later"


def test_scene_proposed_delivery_is_persisted_on_its_assistant_turn(client, monkeypatch):
    session = client.post("/api/converse/sessions", json={
        "profile": {"name": "Ada", "voice_settings": {"delivery": {"emotion": "calm"}}},
        "scene": {"participant_ids": ["primary"], "rounds": 1},
    }).json()
    async def model(*args, **kwargs):
        yield '<delivery>{"emotion":"angry","style":"dramatic","expression":null}</delivery>\n'
        yield "Hello from the scene."
    monkeypatch.setattr(converse, "stream_chat", model)

    async def run():
        response = await converse.run_scene(session["id"], converse.SceneRequest(model="local", propose_delivery=True))
        return _events([packet async for packet in response.body_iterator])
    events = asyncio.run(run())
    assert any(event == "delivery" and data["delivery"]["emotion"] == "angry" for event, data in events)
    message = client.get(f"/api/converse/sessions/{session['id']}").json()["messages"][0]
    assert message["content"] == "Hello from the scene."
    assert message["delivery"] == {"emotion": "angry", "style": "dramatic", "expression": None}
    assert message["profile_snapshot"]["voice_settings"]["delivery"]["emotion"] == "calm"


def test_scene_error_stops_later_turns_and_cancellation_saves_attributed_partial(client, monkeypatch):
    session = client.post("/api/converse/sessions", json={
        "characters": [{"id": "primary", "profile": {}}, {"id": "bea", "profile": {"name": "Bea"}},
                       {"id": "cy", "profile": {"name": "Cy"}}],
        "scene": {"participant_ids": ["primary", "bea", "cy"], "rounds": 1},
    }).json()
    calls = 0

    async def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        yield "first"
        if calls == 2:
            raise RuntimeError("upstream private detail")
        yield "second"

    monkeypatch.setattr(converse, "stream_chat", fail_second)
    async def error_run():
        response = await converse.run_scene(session["id"], converse.SceneRequest(model="local"))
        return _events([packet async for packet in response.body_iterator])
    events = asyncio.run(error_run())
    assert [e for e, _ in events].count("turn_start") == 2
    assert calls == 2  # the third scheduled participant is never requested from the model
    error = next(data for event, data in events if event == "error")
    assert "private detail" not in error["message"]
    assert len(client.get(f"/api/converse/sessions/{session['id']}").json()["messages"]) == 2
    assert session["id"] not in converse._active_turns

    session = client.post("/api/converse/sessions", json={
        "scene": {"participant_ids": ["primary"], "rounds": 1},
    }).json()
    async def hold(*args, **kwargs):
        yield "partial"
        await asyncio.Event().wait()
    monkeypatch.setattr(converse, "stream_chat", hold)
    async def cancel_run():
        response = await converse.run_scene(session["id"], converse.SceneRequest(model="local"))
        iterator = response.body_iterator
        await anext(iterator)  # turn_start
        await anext(iterator)  # partial delta
        pending = asyncio.create_task(anext(iterator))
        await asyncio.sleep(0)
        with pytest.raises(converse.HTTPException) as edit_error:
            await converse.update_session(session["id"], converse.PatchSession(title="blocked"))
        assert edit_error.value.status_code == 409
        with pytest.raises(converse.HTTPException) as message_error:
            await converse.send_message(session["id"], converse.MessageRequest(text="blocked", model="local"))
        assert message_error.value.status_code == 409
        with pytest.raises(converse.HTTPException) as scene_error:
            await converse.run_scene(session["id"], converse.SceneRequest(model="local"))
        assert scene_error.value.status_code == 409
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    asyncio.run(cancel_run())
    assert session["id"] not in converse._active_turns
    message = client.get(f"/api/converse/sessions/{session['id']}").json()["messages"][-1]
    assert message["content"] == "partial" and message["status"] == "interrupted"
    assert message["speaker_id"] == "primary" and message["scene_turn_index"] == 1


def test_manual_turn_keeps_existing_user_message_semantics(client, monkeypatch):
    profile = {"name": "Ada", "voice_description": "Clear"}
    session = client.post("/api/converse/sessions", json={"profile": profile}).json()

    async def model(*args, **kwargs):
        yield "answer"
    monkeypatch.setattr(converse, "stream_chat", model)
    async def run():
        response = await converse.send_message(session["id"], converse.MessageRequest(text="hello", model="local"))
        return [packet async for packet in response.body_iterator]
    asyncio.run(run())
    saved = client.get(f"/api/converse/sessions/{session['id']}").json()
    assert saved["messages"][0] == {"role": "user", "content": "hello"}
    assert saved["messages"][1]["content"] == "answer"
    assert saved["messages"][1]["speaker_id"] == "primary"
