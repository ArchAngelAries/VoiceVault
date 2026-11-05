"""Library: unified audio list, editing (titles, characters, labels), show on disk, delete, folder layout."""
import json

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import library as libapi
from api import tts as tts_api
from api import voice as av
from core import library as lib
from engines import audio_store
from engines.audio_io import save_generation


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEVAULT_LIBRARY_DIR", str(tmp_path / "Library"))
    voices = lib.voices_dir()
    gens = lib.generations_dir()
    monkeypatch.setattr(tts_api, "REFERENCE_DIR", voices)
    monkeypatch.setattr(tts_api, "_outputs_dir", lambda: gens)
    monkeypatch.setattr(av, "SOURCES", lib.voice_lab_uploads_dir())
    song = lib.music_projects_dir() / "p1" / "t1.wav"
    song.parent.mkdir(parents=True)
    song.write_bytes(b"RIFF")
    monkeypatch.setattr(libapi, "_harvest", lambda: [])
    monkeypatch.setattr(libapi, "_music", lambda: [
        {"key": "music:p1:t1", "type": "music", "title": "Song take", "duration_s": 30, "created": "2026-11-04",
         "path": str(song), "play_url": "/x", "detail": "Song"},
        {"key": "sfx:p1:t2", "type": "sfx", "title": "Door slam", "duration_s": 2, "created": "2026-11-03",
         "path": str(song), "play_url": "/y", "detail": "Song"}])
    revealed = []
    monkeypatch.setattr(libapi, "_reveal", lambda p: revealed.append(p))
    tone = (0.2 * np.sin(np.linspace(0, 600, 24000))).astype(np.float32)
    ref = audio_store.save_reference(voices, tone, 24000, "Mara test")
    save_generation(gens, tone, 24000, {"engine": "test", "model_id": "t", "model_family": "T", "text": "Make it so."})
    app = FastAPI()
    app.include_router(libapi.router, prefix="/api/library")
    c = TestClient(app)
    c.ref_id, c.revealed, c.tmp = ref["reference_id"], revealed, tmp_path
    return c


def test_layout_is_one_clear_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEVAULT_LIBRARY_DIR", str(tmp_path / "L"))
    lib.ensure_folders()
    for parts in lib.FOLDERS.values():
        assert (tmp_path / "L").joinpath(*parts).is_dir()
    assert lib.index_file() == tmp_path / "L" / "library.json"


def test_lists_every_kind_of_audio(client):
    data = client.get("/api/library/items").json()
    types = {i["type"] for i in data["items"]}
    assert {"voice", "generation", "music", "sfx"} <= types
    voice = next(i for i in data["items"] if i["type"] == "voice")
    assert voice["title"] == "Mara test" and voice["assignable"]
    assert not next(i for i in data["items"] if i["type"] == "sfx")["assignable"]
    assert {i["type"] for i in client.get("/api/library/items", params={"type": "music,sfx"}).json()["items"]} == {"music", "sfx"}


def test_edit_assign_label_and_filter(client):
    key = f"voice:{client.ref_id}"
    r = client.patch("/api/library/items", json={"key": key, "title": "Captain Mara Vale (calm)",
                                                 "character": {"name": "Mara Vale", "kind": "character"},
                                                 "labels": ["calm", "Calm", " speech "], "category": "Star Trek"})
    assert r.status_code == 200, r.text
    item = r.json()
    assert item["title"] == "Captain Mara Vale (calm)" and item["character"]["name"] == "Mara Vale"
    assert item["labels"] == ["calm", "speech"] and item["category"] == "Star Trek"
    # The voice's own record is renamed too, so Generate shows the new name.
    assert json.loads((tts_api.REFERENCE_DIR / f"{client.ref_id}.json").read_text())["name"] == "Captain Mara Vale (calm)"
    assert [i["key"] for i in client.get("/api/library/items", params={"character": "mara vale"}).json()["items"]] == [key]
    assert [i["key"] for i in client.get("/api/library/items", params={"label": "speech"}).json()["items"]] == [key]
    assert [i["key"] for i in client.get("/api/library/items", params={"q": "star trek"}).json()["items"]] == [key]
    assert any(c["name"] == "Mara Vale" for c in client.get("/api/library/characters").json())
    r = client.patch("/api/library/items", json={"key": key, "clear_character": True})
    assert r.json()["character"] is None and r.json()["labels"] == ["calm", "speech"]


def test_music_and_sfx_cannot_take_a_character(client):
    r = client.patch("/api/library/items", json={"key": "sfx:p1:t2", "character": {"name": "Mara Vale"}})
    assert r.status_code == 400 and "labels" in r.json()["detail"]
    r = client.patch("/api/library/items", json={"key": "music:p1:t1", "labels": ["ambient"], "category": "Score"})
    assert r.status_code == 200 and r.json()["labels"] == ["ambient"]


def test_show_on_disk(client):
    r = client.post("/api/library/reveal", json={"key": f"voice:{client.ref_id}"})
    assert r.status_code == 200 and client.revealed[-1].name == f"{client.ref_id}.wav"
    assert client.post("/api/library/reveal", json={"folder": "voices"}).status_code == 200
    assert client.post("/api/library/reveal", json={"folder": "../../Windows"}).status_code == 400
    assert client.post("/api/library/reveal", json={"key": "voice:nope"}).status_code == 404


def test_delete_voice_and_unknown(client):
    key = f"voice:{client.ref_id}"
    client.patch("/api/library/items", json={"key": key, "labels": ["x"]})
    assert client.delete("/api/library/items", params={"key": key}).status_code == 200
    assert not (tts_api.REFERENCE_DIR / f"{client.ref_id}.wav").exists()
    assert key not in json.loads(lib.index_file().read_text())["items"]
    assert client.delete("/api/library/items", params={"key": key}).status_code == 404


def test_migration_moves_without_overwriting(tmp_path, monkeypatch):
    from core import paths
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.delenv("VOICEVAULT_LIBRARY_DIR", raising=False)
    old = tmp_path / "data" / "references"
    old.mkdir(parents=True)
    (old / "a.wav").write_bytes(b"a")
    (old / "b.wav").write_bytes(b"old")
    lib.voices_dir().mkdir(parents=True)
    (lib.voices_dir() / "b.wav").write_bytes(b"new")
    assert lib.migrate_legacy(dry_run=True)["moved"] == 0 and (old / "a.wav").exists()
    report = lib.migrate_legacy()
    assert report["moved"] == 1 and len(report["skipped"]) == 1 and report["backup"]
    assert (lib.voices_dir() / "a.wav").read_bytes() == b"a" and (lib.voices_dir() / "b.wav").read_bytes() == b"new"
    assert (old / "b.wav").exists()
