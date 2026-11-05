"""GET /api/jobs/active — the top-bar job strip's aggregator (api/jobs.py)."""
import time
from datetime import datetime, timedelta

import httpx
import pytest

from api import jobs as jobs_api


class FakeStudio:
    def __init__(self, items):
        self.items = items

    def jobs(self, project=None):
        return self.items

    def queue_position(self, jid):
        return 2


class FakeHarvest:
    def jobs(self):
        return [{"id": "h1", "state": "running", "stage": "Removing music", "created_at": "2026-01-01T00:00:00+00:00"},
                {"id": "h2", "state": "completed", "stage": "Complete", "created_at": "2026-01-01T00:00:00+00:00"}]


@pytest.fixture
def fake_sources(monkeypatch):
    from api import harvest as harvest_api
    from api import music as music_api
    from models import manager

    started = (datetime.now() - timedelta(seconds=30)).isoformat(timespec="seconds")
    monkeypatch.setitem(music_api._studio, "s", FakeStudio([
        {"id": "m1", "kind": "generate", "status": "running", "started": started, "created": started, "message": "Rendering"},
        {"id": "m2", "kind": "stems", "status": "queued", "started": None, "created": started},
        {"id": "m3", "kind": "generate", "status": "done", "started": started, "created": started},
    ]))
    monkeypatch.setattr(harvest_api, "service", FakeHarvest())
    monkeypatch.setattr(manager, "_jobs", {
        "d1": {"id": "d1", "entry": "not-a-real-entry", "state": "running", "created": time.time() - 5,
               "done_bytes": 25, "total_bytes": 100},
        "d2": {"id": "d2", "entry": "x", "state": "done", "created": time.time(), "done_bytes": 1, "total_bytes": 1},
    })


def test_aggregates_only_active_jobs(fake_sources):
    items = jobs_api.active_jobs()
    by_id = {i["id"]: i for i in items}
    assert set(by_id) == {"m1", "m2", "h1", "d1"}   # finished work and an idle speech lock are left out
    assert by_id["m1"]["source"] == "music" and by_id["m1"]["route"] == "/music"
    assert 25 <= by_id["m1"]["elapsed_s"] <= 120
    assert by_id["m2"]["state"] == "queued" and by_id["m2"]["position"] == 2
    assert by_id["h1"]["label"] == "Harvest: Removing music" and by_id["h1"]["route"] == "/harvest"
    assert by_id["d1"]["progress"] == 0.25 and by_id["d1"]["label"] == "Downloading not-a-real-entry"
    for item in items:
        assert {"source", "label", "state", "elapsed_s"} <= set(item)


def test_a_broken_source_does_not_break_the_rest(fake_sources, monkeypatch):
    def boom():
        raise RuntimeError("source exploded")
    monkeypatch.setattr(jobs_api, "SOURCES", [("music", boom), *jobs_api.SOURCES[1:]])
    sources = {i["source"] for i in jobs_api.active_jobs()}
    assert "music" not in sources and {"harvest", "models"} <= sources


def test_idle_app_returns_empty_list(monkeypatch):
    from api import harvest as harvest_api
    from api import music as music_api
    from models import manager

    monkeypatch.delitem(music_api._studio, "s", raising=False)
    monkeypatch.setattr(harvest_api, "service", None)
    monkeypatch.setattr(manager, "_jobs", {})
    assert jobs_api.active_jobs() == []


def test_speech_lock_held_elsewhere_reports_speech(monkeypatch):
    import threading

    from api import harvest as harvest_api
    from api import music as music_api
    from core.gpu_scheduler import speech_resource_lock
    from models import manager

    monkeypatch.delitem(music_api._studio, "s", raising=False)
    monkeypatch.setattr(harvest_api, "service", None)
    monkeypatch.setattr(manager, "_jobs", {})
    held, release = threading.Event(), threading.Event()

    def hold():
        with speech_resource_lock:
            held.set()
            release.wait(5)
    t = threading.Thread(target=hold)
    t.start()
    try:
        held.wait(5)
        items = jobs_api.active_jobs()
    finally:
        release.set()
        t.join(5)
    assert [i["source"] for i in items] == ["speech"]
    assert jobs_api.active_jobs() == []


@pytest.mark.asyncio
async def test_endpoint_is_registered(fake_sources):
    from main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/jobs/active")
    assert r.status_code == 200
    assert {i["id"] for i in r.json()} == {"m1", "m2", "h1", "d1"}
