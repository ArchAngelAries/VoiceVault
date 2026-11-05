import asyncio
import hashlib
import json
import sys

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from backend.api import discover
from backend.core.discovery_snapshots import DiscoverySnapshotStore

discovery_cache = sys.modules[discover.clear_recent_cache_after_drain.__module__]
discovery_retention = sys.modules[discover.sync_saved_entities.__module__]


@pytest.fixture
def fixture_app(tmp_path, monkeypatch):
    store = DiscoverySnapshotStore(tmp_path)
    store.put_request("tmdb", "browse", {}, {"items": []})
    store.put_entity("tmdb", "movie", "movie:tmdb:8", {"title": "Saved"})
    store.protect_entity("tmdb", "movie", "movie:tmdb:8", "favorite")
    store.set_overrides("tmdb", "movie", "movie:tmdb:8", {"display_name": "My saved title"})
    store.set_provider_state("tmdb", "scope-a", next_allowed_at=1234, error="rate_limit")
    async def fake_db():
        return object()
    release_flags = []
    async def reconcile(_db, _store, *, release_missing=True):
        release_flags.append(release_missing)
        return {"retained": 0, "warnings": []}
    monkeypatch.setattr(discover, "get_snapshot_store", lambda: store)
    monkeypatch.setattr(discovery_cache, "get_snapshot_store", lambda: store)
    monkeypatch.setattr(discover, "get_db", fake_db)
    monkeypatch.setattr(discover, "sync_saved_entities", reconcile)
    monkeypatch.setattr(discovery_cache, "_tasks", {})
    monkeypatch.setattr(discovery_cache, "_inflight_requests", set())
    monkeypatch.setattr(discovery_cache, "_maintenance_event", None)
    app = FastAPI()
    app.include_router(discover.router, prefix="/api/discover")
    return app, store, release_flags


async def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_actual_router_reports_and_clears_cache(fixture_app):
    app, store, release_flags = fixture_app
    async with await client_for(app) as client:
        stats = await client.get("/api/discover/cache")
        assert stats.status_code == 200
        assert stats.json()["request_count"] == 1
        result = await client.post("/api/discover/cache/clear-recent")
    assert result.status_code == 200
    assert result.json()["requests_deleted"] == 1
    assert result.json()["stats"]["request_count"] == 0
    assert store.get_entity("tmdb", "movie", "movie:tmdb:8") is not None
    assert store.get_overrides("tmdb", "movie", "movie:tmdb:8") == {"display_name": "My saved title"}
    assert store.get_provider_state("tmdb", "scope-a")["error"] == "rate_limit"
    assert release_flags == [False]


@pytest.mark.asyncio
async def test_reconciliation_warning_aborts_before_deletion(fixture_app, monkeypatch):
    app, store, _ = fixture_app
    async def failed_reconcile(_db, _store):
        return {"retained": 0, "warnings": ["saved catalog could not be reconciled"]}
    monkeypatch.setattr(discover, "sync_saved_entities", failed_reconcile)
    async with await client_for(app) as client:
        result = await client.post("/api/discover/cache/clear-recent")
    assert result.status_code == 503
    assert store.cache_stats()["request_count"] == 1


@pytest.mark.asyncio
async def test_clear_drains_refresh_writes_before_deleting(fixture_app):
    app, store, _ = fixture_app
    started, release = asyncio.Event(), asyncio.Event()
    async def refresh():
        try:
            started.set()
            await release.wait()
            store.put_request("tmdb", "background-refresh", {}, {"items": ["new"]})
        finally:
            discovery_cache._tasks.pop("refresh-key", None)
    task = asyncio.create_task(refresh())
    discovery_cache._tasks["refresh-key"] = task
    await started.wait()
    async with await client_for(app) as client:
        clear_call = asyncio.create_task(client.post("/api/discover/cache/clear-recent"))
        for _ in range(20):
            await asyncio.sleep(0)
            if discovery_cache._maintenance_event is not None and not discovery_cache._maintenance_event.is_set():
                break
        assert not clear_call.done()
        identity = ("tmdb", "queued-request", {}, None, hashlib.sha256(
            json.dumps(["tmdb", "queued-request", {}, None], sort_keys=True).encode()).hexdigest())
        async def queued_handler(_request):
            return JSONResponse({"id": "movie:tmdb:999"})
        queued_call = asyncio.create_task(discovery_cache._snapshot_request(queued_handler, None, identity))
        await asyncio.sleep(0)
        assert not queued_call.done()
        release.set()
        result = await clear_call
        queued_response = await queued_call
    assert result.status_code == 200
    assert result.json()["requests_deleted"] == 2
    assert store.cache_stats()["request_count"] == 1
    assert json.loads(queued_response.body)["id"] == "movie:tmdb:999"


@pytest.mark.asyncio
async def test_additive_reconciliation_keeps_protection_absent_from_scan(tmp_path):
    store = DiscoverySnapshotStore(tmp_path)
    store.put_entity("tmdb", "movie", "movie:tmdb:42", {"title": "Saved"})
    store.protect_entity("tmdb", "movie", "movie:tmdb:42", "collection:new-save")
    class EmptyTable:
        async def find_many(self, **_kwargs):
            return []
    class EmptyDatabase:
        collection = EmptyTable()
        tierlist = EmptyTable()
    await discovery_retention.sync_saved_entities(EmptyDatabase(), store, release_missing=False)
    assert store.cache_stats()["protected_entity_count"] == 1
    await discovery_retention.sync_saved_entities(EmptyDatabase(), store)
    assert store.cache_stats()["protected_entity_count"] == 0
