"""Settings safety: keys never reach the browser, masked keys are kept, bad updates are refused, outside hosts blocked."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import settings as settings_api
from core import settings as store


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "voicevault_settings.json"
    monkeypatch.setattr(store, "SETTINGS_PATH", path)
    store.LAST_RESET.clear()
    path.write_text(json.dumps({"tmdb_api_key": "real-tmdb-token", "igdb_client_id": "id123",
                                "igdb_client_secret": "real-secret", "lmstudio_url": "http://localhost:1234"}))
    app = FastAPI()
    app.include_router(settings_api.router, prefix="/api/settings")
    c = TestClient(app)
    c.path = path
    return c


def test_keys_are_masked_and_kept(client):
    data = client.get("/api/settings/").json()
    assert data["tmdb_api_key"] == store.MASK and data["igdb_client_secret"] == store.MASK
    assert data["openai_api_key"] == ""            # unset keys stay empty, so the UI can tell
    assert data["igdb_client_id"] == "id123"        # ids are not secrets
    assert "real-tmdb-token" not in json.dumps(data)
    # The Settings page posts everything back, masks included, plus a changed URL.
    r = client.post("/api/settings/", json={"data": {**data, "lmstudio_url": "http://localhost:4321"}})
    assert r.status_code == 200 and "real-secret" not in r.text
    stored = json.loads(client.path.read_text())
    assert stored["tmdb_api_key"] == "real-tmdb-token" and stored["igdb_client_secret"] == "real-secret"
    assert stored["lmstudio_url"] == "http://localhost:4321"
    # Replacing and clearing keys still work.
    client.post("/api/settings/", json={"data": {"tmdb_api_key": "new-token", "igdb_client_secret": ""}})
    stored = json.loads(client.path.read_text())
    assert stored["tmdb_api_key"] == "new-token" and stored["igdb_client_secret"] == ""
    # The backend itself still reads the real key.
    assert store.load()["tmdb_api_key"] == "new-token"


@pytest.mark.parametrize("bad, reason", [
    ({"no_such_setting": 1}, "Unknown setting"),
    ({"harvest_max_duration": "long"}, "must be a int"),
    ({"outputs_dir": "relative/folder"}, "full folder path"),
    ({"outputs_dir": 5}, "must be a str"),
    ({"model_search_roots": "C:/models"}, "must be a list"),
    ({"model_search_roots": ["models"]}, "full path"),
    ({"theme": "x" * 5000}, "too long"),
])
def test_bad_updates_are_refused_and_nothing_is_written(client, bad, reason):
    before = client.path.read_text()
    r = client.post("/api/settings/", json={"data": bad})
    assert r.status_code == 422 and reason in r.json()["detail"]
    assert client.path.read_text() == before


def test_reset_keeps_keys(client):
    client.post("/api/settings/", json={"data": {"lmstudio_url": "http://localhost:9999"}})
    client.post("/api/settings/reset")
    stored = json.loads(client.path.read_text())
    assert stored["lmstudio_url"] == store.DEFAULTS["lmstudio_url"] and stored["tmdb_api_key"] == "real-tmdb-token"


def test_unreadable_settings_file_is_reported(client):
    client.path.write_text("{ not json")
    data = client.get("/api/settings/").json()
    assert "could not be read" in data["_notice"]
    assert list(client.path.parent.glob("*.corrupt-*.json"))
    client.post("/api/settings/", json={"data": {**data, "tmdb_api_key": "again"}})  # _notice is ignored
    assert "_notice" not in client.get("/api/settings/").json()


def test_outside_hosts_are_refused():
    from main import app

    c = TestClient(app)
    assert c.get("/api/health/", headers={"host": "127.0.0.1:7763"}).status_code == 200
    assert c.get("/api/health/", headers={"host": "localhost:7763"}).status_code == 200
    assert c.get("/api/health/", headers={"host": "evil.example.com"}).status_code == 400
