"""Quick API integration checks; no upstream services or user databases."""
import asyncio
import time

import httpx
import pytest
from fastapi import APIRouter, FastAPI

from core import discovery_cache as cache
from core.discovery_snapshots import DiscoverySnapshotStore
from core.media.provider_transport import ProviderError, ProviderNotModified


CARD = {'id': 'anime:anilist:1', 'source': 'anilist', 'source_id': 1,
        'media_type': 'anime', 'title': 'Retained title', 'description': 'Rich details'}
PAGE = {'items': [CARD], 'page_info': {'currentPage': 1, 'hasNextPage': False}}


@pytest.fixture
def fixture_app(tmp_path, monkeypatch):
    store = DiscoverySnapshotStore(tmp_path)
    monkeypatch.setattr(cache, 'get_snapshot_store', lambda: store)
    monkeypatch.setattr(cache, '_tasks', {})
    monkeypatch.setattr(cache, '_clock', lambda: 1000.0)
    state = {'calls': 0, 'payload': PAGE, 'error': None, 'gate': None, 'policy': {}}
    monkeypatch.setattr(cache, 'get_request_metadata', lambda: state['policy'])
    app = FastAPI()
    router = APIRouter(route_class=cache.SnapshotRoute)

    @router.get('/anime/browse')
    async def browse(search: str = '', page: int = 1):
        state['calls'] += 1
        if state['gate']: await state['gate'].wait()
        if state['error']: raise state['error']
        return state['payload']

    @router.get('/anime/{media_id}')
    async def detail(media_id: int):
        if state['error']: raise state['error']
        return state['payload']

    app.include_router(router, prefix='/api/discover')
    return app, store, state


async def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')


@pytest.mark.asyncio
async def test_exact_pages_warm_restart_and_unseen_query(fixture_app, monkeypatch):
    app, store, state = fixture_app
    async with await _client(app) as client:
        first = await client.get('/api/discover/anime/browse?search=a&page=1')
        assert first.json()['_snapshot']['retained']
        reopened = DiscoverySnapshotStore(store.data_dir)
        monkeypatch.setattr(cache, 'get_snapshot_store', lambda: reopened)
        assert (await client.get('/api/discover/anime/browse?page=1&search=a')).json()['items'] == [CARD]
        assert state['calls'] == 1
        state['error'] = ProviderError('Offline', code='network')
        unseen = await client.get('/api/discover/anime/browse?search=b&page=1')
        assert unseen.status_code == 503 and 'items' not in unseen.json()
        assert reopened.search_local()['total'] == 1


@pytest.mark.asyncio
async def test_stale_immediate_refresh_dedup_and_failure_preserves(fixture_app, monkeypatch):
    app, store, state = fixture_app
    async with await _client(app) as client:
        await client.get('/api/discover/anime/browse')
        monkeypatch.setattr(cache, '_clock', lambda: 30000.0)
        state['gate'] = asyncio.Event()
        a, b = await asyncio.gather(client.get('/api/discover/anime/browse'), client.get('/api/discover/anime/browse'))
        assert a.json()['_snapshot']['state'] == b.json()['_snapshot']['state'] == 'refreshing'
        await asyncio.sleep(0)
        assert state['calls'] == 2
        task = next(iter(cache._tasks.values()))
        state['error'] = ProviderError('Rate limited', code='rate_limit', next_allowed_at=31000)
        state['gate'].set()
        await task
        assert store.search_local()['items'][0]['metadata']['description'] == 'Rich details'
        assert (await client.get('/api/discover/anime/browse')).json()['items'] == [CARD]
        await cache.shutdown_discovery_refreshes()


@pytest.mark.asyncio
async def test_cold_concurrent_requests_share_one_fetch(fixture_app):
    app, store, state = fixture_app
    state['gate'] = asyncio.Event()
    async with await _client(app) as client:
        requests = [asyncio.create_task(client.get('/api/discover/anime/browse')) for _ in range(3)]
        for _ in range(30):
            if state['calls']: break
            await asyncio.sleep(.002)
        state['gate'].set()
        responses = await asyncio.gather(*requests)
        assert all(result.status_code == 200 for result in responses)
        assert state['calls'] == 1


@pytest.mark.asyncio
async def test_no_store_and_partial_data_never_replace_good_page(fixture_app, monkeypatch):
    app, store, state = fixture_app
    async with await _client(app) as client:
        await client.get('/api/discover/anime/browse')
        state['payload'] = {'items': [{'title': 'Incomplete'}], 'page_info': {}}
        monkeypatch.setattr(cache, '_clock', lambda: 30000.0)
        await client.get('/api/discover/anime/browse')
        await next(iter(cache._tasks.values()))
        assert store.search_local()['total'] == 1
        state['payload'] = PAGE
        state['policy'] = {'no_store': True}
        monkeypatch.setattr(cache, '_clock', lambda: 30016.0)
        await client.get('/api/discover/anime/browse?refresh=true')
        await next(iter(cache._tasks.values()))
        state['error'] = ProviderError('Offline', code='network')
        assert (await client.get('/api/discover/anime/browse')).status_code == 503


@pytest.mark.asyncio
async def test_no_cache_requires_live_validation(fixture_app):
    app, store, state = fixture_app
    state['policy'] = {'no_cache': True}
    async with await _client(app) as client:
        await client.get('/api/discover/anime/browse')
        state['error'] = ProviderError('Offline', code='network')
        assert (await client.get('/api/discover/anime/browse')).status_code == 503
        assert store.search_local()['total'] == 1


@pytest.mark.asyncio
async def test_unchanged_validation_updates_check_time_without_replacing_source_age(fixture_app, monkeypatch):
    app, store, state = fixture_app
    state['policy'] = {'no_cache': True, 'response_count': 1, 'etag': '"one"'}
    async with await _client(app) as client:
        original = (await client.get('/api/discover/anime/browse')).json()
        monkeypatch.setattr(cache, '_clock', lambda: 1001.0)
        state['error'] = ProviderNotModified(state['policy'])
        confirmed = (await client.get('/api/discover/anime/browse')).json()
        assert confirmed['items'] == original['items']
        assert confirmed['_snapshot']['fetched_at'] == 1000.0
        assert confirmed['_snapshot']['checked_at'] == 1001.0


@pytest.mark.asyncio
async def test_provider_max_age_caps_refresh_eligibility(fixture_app):
    app, store, state = fixture_app
    state['policy'] = {'max_age': 5}
    async with await _client(app) as client:
        result = (await client.get('/api/discover/anime/browse')).json()
        assert result['_snapshot']['refresh_due_at'] == 1005.0


@pytest.mark.asyncio
async def test_missing_provider_profile_marks_but_preserves_saved_metadata(fixture_app, monkeypatch):
    app, store, state = fixture_app
    state['payload'] = CARD
    async with await _client(app) as client:
        await client.get('/api/discover/anime/1')
        store.retain_entity('anilist', 'anime', CARD['id'], CARD, 'collection:1')
        monkeypatch.setattr(cache, '_clock', lambda: 30000.0)
        state['error'] = ProviderError('No longer listed', code='not_found', status=404)
        await client.get('/api/discover/anime/1')
        await next(iter(cache._tasks.values()))
        profile = store.get_entity('anilist', 'anime', CARD['id'])
        assert profile['metadata']['source_status'] == 'missing'
        assert profile['metadata']['description'] == 'Rich details'
        retained = (await client.get('/api/discover/anime/1')).json()
        assert retained['_snapshot']['error_code'] == 'not_found'
        assert retained['_snapshot']['fetched_at'] == retained['_snapshot']['checked_at'] == 1000


@pytest.mark.asyncio
async def test_mixed_search_retains_successes_without_caching_failed_empty_groups(tmp_path, monkeypatch):
    from api import discover
    store = DiscoverySnapshotStore(tmp_path)
    monkeypatch.setattr(cache, 'get_snapshot_store', lambda: store)
    monkeypatch.setattr(cache, '_tasks', {})
    calls = []
    async def anime(**kwargs):
        calls.append(kwargs)
        if len(calls) > 1: raise ProviderError('Offline', code='network')
        return PAGE
    async def unavailable(**kwargs): raise ProviderError('Not configured', code='auth')
    monkeypatch.setattr(discover.anilist, 'browse', anime)
    monkeypatch.setattr(discover.tmdb, 'browse_movies', unavailable)
    monkeypatch.setattr(discover.tmdb, 'browse_tv', unavailable)
    monkeypatch.setattr(discover.igdb, 'browse', unavailable)
    result = await discover.unified_search(q='known')
    assert result['anime'] == [CARD] and 'movies' in result['_errors']
    retained = await discover.unified_search(q='known')
    assert retained['anime'] == [CARD] and len(calls) == 1
    unknown = await discover.unified_search(q='unseen')
    assert unknown['anime'] == [] and 'anime' in unknown['_errors']
    assert store.search_local()['total'] == 1
