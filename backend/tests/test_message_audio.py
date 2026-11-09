"""Replay: linking spoken audio to a chat reply."""
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.converse as converse
import api.tts as tts
from engines.audio_io import save_generation


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(converse, "STORE_DIR", tmp_path / "conversations")
    monkeypatch.setattr(converse, "_active_turns", set())
    outputs = tmp_path / "outputs"
    monkeypatch.setattr(tts, "_outputs_dir", lambda: outputs)
    app = FastAPI()
    app.include_router(converse.router, prefix="/api/converse")
    c = TestClient(app)
    c.outputs = outputs
    return c


def generation(outputs, text="hello"):
    record = save_generation(outputs, np.zeros(2400, np.float32), 24000, {"engine": "kokoro", "text": text})
    return record["file"]


def chat_with_replies(client):
    session = client.post("/api/converse/sessions", json={"kind": "chat", "roleplay": True, "profile": {"name": "Levi"}}).json()
    path = converse._session_path(session["id"])
    data = converse._read(path)
    data["messages"] = [
        {"role": "user", "content": "Captain?"},
        {"role": "assistant", "content": "Tch. Clean this up. Then report back.", "speaker_id": "primary"},
        {"role": "user", "content": "Yes sir."},
        {"role": "assistant", "content": "*sighs* Don't waste my time, recruit.", "speaker_id": "primary"},
    ]
    converse._save(data)
    return session["id"]


def test_live_reply_is_matched_by_its_first_spoken_sentence(client):
    sid = chat_with_replies(client)
    files = [generation(client.outputs, "Don't waste my time"), generation(client.outputs, "recruit")]
    r = client.post(f"/api/converse/sessions/{sid}/audio", json={"files": files, "text": "Don't waste my time,", "speaker_id": "primary"})
    assert r.status_code == 200, r.text
    assert r.json()["index"] == 3
    saved = client.get(f"/api/converse/sessions/{sid}").json()["messages"][3]
    assert [a["file"] for a in saved["audio"]] == files and saved["audio"][0]["duration_s"] == 0.1


def test_older_reply_by_position_and_replacing(client):
    sid = chat_with_replies(client)
    first = generation(client.outputs)
    assert client.post(f"/api/converse/sessions/{sid}/audio", json={"files": [first], "index": 1}).json()["index"] == 1
    second = generation(client.outputs)
    client.post(f"/api/converse/sessions/{sid}/audio", json={"files": [second], "index": 1})
    assert [a["file"] for a in client.get(f"/api/converse/sessions/{sid}").json()["messages"][1]["audio"]] == [second]


def test_only_voicevault_generations_can_be_linked(client):
    sid = chat_with_replies(client)
    for bad in ["../../secrets.wav", "C:/Users/x/a.wav", "20260101_kokoro_missing.wav"]:
        assert client.post(f"/api/converse/sessions/{sid}/audio", json={"files": [bad], "index": 1}).status_code == 422
    client.outputs.mkdir(parents=True, exist_ok=True)
    stray = client.outputs / "stray.wav"
    stray.write_bytes(b"RIFF")                       # a WAV with no VoiceVault record
    assert client.post(f"/api/converse/sessions/{sid}/audio", json={"files": ["stray.wav"], "index": 1}).status_code == 422


def test_bad_targets_and_busy_chats(client, monkeypatch):
    sid = chat_with_replies(client)
    f = generation(client.outputs)
    assert client.post(f"/api/converse/sessions/{sid}/audio", json={"files": [f], "index": 0}).status_code == 404   # a user line
    assert client.post(f"/api/converse/sessions/{sid}/audio", json={"files": [f], "text": "never said"}).status_code == 404
    assert client.post(f"/api/converse/sessions/{sid}/audio", json={"files": [f]}).status_code == 422
    monkeypatch.setattr(converse, "_active_turns", {sid})
    assert client.post(f"/api/converse/sessions/{sid}/audio", json={"files": [f], "index": 1}).status_code == 409


def test_replies_with_actions_still_match_their_spoken_text(client):
    sid = chat_with_replies(client)
    data = converse._read(converse._session_path(sid))
    data["messages"].append({"role": "assistant", "content": "Hello *waves* there, friend (quietly). [sighs] Sit.",
                             "speaker_id": "primary"})
    converse._save(data)
    f = generation(client.outputs)
    r = client.post(f"/api/converse/sessions/{sid}/audio", json={"files": [f], "text": "Hello there, friend.", "speaker_id": "primary"})
    assert r.status_code == 200 and r.json()["index"] == 4
