"""Bounded, on-demand discovery snapshots; expiry never deletes saved data."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from core import settings
from core.discovery_retention import get_snapshot_store, public_entity_ref
from core.media.provider_transport import (
    ProviderError, ProviderNotModified, credential_scope, get_request_metadata,
    reset_request_metadata, restore_request_metadata,
)

logger = logging.getLogger(__name__)
SCHEMA_VERSION = 1
MAX_REFRESHES = 32
_tasks: dict[str, asyncio.Task] = {}
_maintenance = False
_maintenance_event: asyncio.Event | None = None
_inflight_requests: set[asyncio.Task] = set()
_clock = time.time


def _provider_scope(provider):
    if provider in {'anilist', 'wikidata'}:
        return None
    config = settings.load()
    return credential_scope(provider, *(
        [config.get('igdb_client_id'), config.get('igdb_client_secret')]
        if provider == 'igdb' else [config.get('tmdb_api_key')]))


def _identity(request: Request, route: APIRoute):
    path = route.path
    query = request.query_params
    if '/characters/' in path:
        provider = ('wikidata' if query.get('source', 'catalog').strip().lower() == 'wikidata' else
                    {'anime': 'anilist', 'game': 'igdb', 'movie': 'tmdb', 'tv': 'tmdb'}.get(query.get('category', 'anime').strip().lower()))
    elif '/anime/' in path:
        provider = 'anilist'
    elif '/games/' in path:
        provider = 'igdb'
    elif '/movies/' in path or '/tv/' in path:
        provider = 'tmdb'
    elif '/people' in path:
        source = query.get('source', 'tmdb')
        person_id = request.path_params.get('person_id', '')
        provider = 'anilist' if source == 'anilist' or ':anilist:' in str(person_id) else 'tmdb'
    else:
        return None
    if provider is None:
        return None
    scope = _provider_scope(provider)
    params = {'path': request.path_params, 'query': sorted(
        [(key, query.getlist(key)) for key in set(query.keys()) if key != 'refresh'])}
    operation = f'v1:{path}'
    key = hashlib.sha256(json.dumps([provider, operation, params, scope], sort_keys=True).encode()).hexdigest()
    return provider, operation, params, scope, key


def _meta(provider, record=None, *, state='fresh', provider_state=None, warning=None):
    record, provider_state = record or {}, provider_state or {}
    if record.get('validators', {}).get('source_missing'):
        state = 'paused'
        provider_state = {**provider_state, 'error': 'not_found'}
    return {'provider': provider, 'state': state, 'retained': bool(record),
            'fetched_at': record.get('fetched_at'), 'checked_at': record.get('checked_at'),
            'refresh_due_at': record.get('refresh_due_at'),
            'next_allowed_at': provider_state.get('next_allowed_at'),
            'error_code': provider_state.get('error'), 'storage_warning': warning}


def _response(payload, metadata, status=200):
    content = {**payload, '_snapshot': metadata} if isinstance(payload, dict) else payload
    return JSONResponse(content, status_code=status,
                        headers={'X-VoiceVault-Snapshot': json.dumps(metadata, separators=(',', ':'))})


def _valid_payload(payload: Any) -> bool:
    if isinstance(payload, list):
        return all(isinstance(item, (str, dict)) for item in payload)
    if not isinstance(payload, dict):
        return False
    if 'items' in payload:
        return (isinstance(payload['items'], list) and
                all(isinstance(item, dict) and isinstance(item.get('id'), str) for item in payload['items']) and
                isinstance(payload.get('page_info'), dict))
    return isinstance(payload.get('id'), str)


def _entities(payload):
    """Only normalized, typed public records; never infer entity IDs from numbers."""
    if isinstance(payload, dict):
        ref = public_entity_ref(payload)
        if ref:
            yield ref, payload
        for value in payload.values():
            if isinstance(value, (dict, list)):
                yield from _entities(value)
    elif isinstance(payload, list):
        for value in payload:
            yield from _entities(value)


def _write_snapshot(store, identity, payload, policy, now, fetched_at=None):
    provider, operation, params, scope, _ = identity
    entities = {ref: item for ref, item in _entities(payload) if ref[0] == provider}
    ttl = 24 * 3600 if isinstance(payload, list) else 6 * 3600
    if policy.get('max_age') is not None:
        ttl = min(ttl, max(0, policy['max_age']))
    if policy.get('no_cache'):
        ttl = 0
    record = store.put_request(provider, operation, params, payload, account_scope=scope,
                               fetched_at=now if fetched_at is None else fetched_at, checked_at=now, refresh_due_at=now + ttl,
                               schema_version=SCHEMA_VERSION,
                               entity_refs=[(ref[1], ref[2]) for ref in entities], validators=policy)
    warning = None
    for ref, item in entities.items():
        try:
            store.put_entity(*ref, {**item, 'source_status': 'available', 'status_checked_at': now},
                             coverage=item.keys(), fetched_at=record['fetched_at'])
        except Exception:
            warning = 'Some local catalog details could not be retained. Your saved library is unchanged.'
    # Entity writes may need to evict optional requests when the budget fills.
    # Report retention only if this exact page still exists after those writes.
    retained = store.get_request(provider, operation, params, scope, schema_version=SCHEMA_VERSION)
    if retained is None:
        warning = 'Live results are available, but this page did not fit the local cache budget.'
    return retained, warning


def _can_stale(record):
    policy = record.get('validators', {})
    return not (policy.get('no_cache') or policy.get('must_revalidate'))


async def _fetch(handler, request, identity, store, cached=None):
    provider, operation, params, scope, _ = identity
    token = reset_request_metadata(cached.get('validators') if cached else None)
    try:
        result = await handler(request)
        if result.status_code != 200:
            return result
        if len(result.body) > 2 * 1024 * 1024:
            raise ProviderError('Provider response was too large to use safely.', code='invalid_response')
        payload = json.loads(result.body)
        if not _valid_payload(payload):
            raise ProviderError('The provider returned incomplete discovery data.', code='invalid_response')
        policy = get_request_metadata()
        now = _clock()
        record, warning = None, None
        try:
            if policy.get('no_store'):
                await asyncio.to_thread(store.remove_request, provider, operation, params, scope)
            else:
                record, warning = await asyncio.to_thread(_write_snapshot, store, identity, payload, policy, now)
        except Exception:
            logger.warning('Could not retain discovery response for %s', provider)
            warning = 'Live results are available, but their local copy could not be saved.'
        metadata = _meta(provider, record, warning=warning)
        if not record:
            metadata.update(fetched_at=now, checked_at=now)
        return _response(payload, metadata)
    except ProviderNotModified as exc:
        if cached is None:
            return _response({'detail': 'Provider validation has no matching local result.'},
                             _meta(provider, state='unavailable'), 502)
        now, warning, record = _clock(), None, None
        try:
            if exc.metadata.get('no_store'):
                await asyncio.to_thread(store.remove_request, provider, operation, params, scope)
            else:
                record, warning = await asyncio.to_thread(_write_snapshot, store, identity, cached['payload'],
                                                          exc.metadata, now, cached['fetched_at'])
        except Exception:
            warning = 'The provider confirmed this data, but the local check time could not be saved.'
        metadata = _meta(provider, record, warning=warning)
        metadata.update(fetched_at=cached['fetched_at'], checked_at=now)
        return _response(cached['payload'], metadata)
    except ProviderError as exc:
        state = {'error': exc.code, 'next_allowed_at': exc.next_allowed_at}
        if exc.code == 'not_found' and cached and public_entity_ref(cached['payload']):
            try:
                ref = public_entity_ref(cached['payload'])
                await asyncio.to_thread(store.put_entity, *ref,
                    {'source_status': 'missing', 'status_checked_at': _clock()}, fetched_at=cached['fetched_at'])
                cached = await asyncio.to_thread(store.put_request, provider, operation, params, cached['payload'],
                    account_scope=scope, fetched_at=cached['fetched_at'], checked_at=cached['checked_at'],
                    refresh_due_at=_clock() + 300, schema_version=SCHEMA_VERSION,
                    validators={**cached.get('validators', {}), 'source_missing': True},
                    entity_refs=[(item[0][1], item[0][2]) for item in _entities(cached['payload'])])
            except Exception:
                pass
        if exc.code in {'rate_limit', 'network', 'upstream', 'invalid_response', 'auth'}:
            state['next_allowed_at'] = exc.next_allowed_at or (_clock() + (60 if exc.code == 'auth' else 15))
            try:
                await asyncio.to_thread(store.set_provider_state, provider, scope,
                                        next_allowed_at=state['next_allowed_at'], error=exc.code, error_at=_clock())
            except Exception:
                pass
        if cached and _can_stale(cached):
            return _response(cached['payload'], _meta(provider, cached, state='paused', provider_state=state))
        status = 429 if exc.code == 'rate_limit' else 503 if exc.code == 'network' else 502
        if exc.code == 'auth': status = 401
        if exc.code == 'not_found': status = 404
        return _response({'detail': str(exc)}, _meta(provider, state='unavailable', provider_state=state), status)
    finally:
        restore_request_metadata(token)


async def _refresh(handler, request, identity, store, cached):
    key = identity[-1]
    try:
        return await _fetch(handler, request, identity, store, cached)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning('Discovery refresh failed for %s', identity[0])
        raise
    finally:
        _tasks.pop(key, None)


def _observe_task(task):
    # A browser may leave after receiving stale data. Consume background errors
    # while allowing awaited cold requests to retain their normal API handlers.
    if not task.cancelled():
        task.exception()


def _start_refresh(handler, request, identity, store, cached):
    task = asyncio.create_task(_refresh(handler, request, identity, store, cached))
    _tasks[identity[-1]] = task
    task.add_done_callback(_observe_task)
    return task


async def _snapshot_request_impl(handler, request, identity, force=False):
    provider, operation, params, scope, key = identity
    store = get_snapshot_store()
    warning, cached, state = None, None, {}
    try:
        cached = await asyncio.to_thread(store.get_request, provider, operation, params, scope,
                                         schema_version=SCHEMA_VERSION)
        state = await asyncio.to_thread(store.get_provider_state, provider, scope) or {}
    except Exception:
        warning = 'The local catalog is unavailable; live browsing can still work.'
    now = _clock()
    paused = (state.get('next_allowed_at') or 0) > now or state.get('error') == 'auth'
    if cached and not force and (cached.get('refresh_due_at') or 0) > now:
        return _response(cached['payload'], _meta(provider, cached, warning=warning))
    if cached and _can_stale(cached):
        status = 'paused' if paused else 'refreshing'
        if not paused and key not in _tasks:
            if len(_tasks) < MAX_REFRESHES:
                _start_refresh(handler, request, identity, store, cached)
            else:
                status = 'stale'
        return _response(cached['payload'], _meta(provider, cached, state=status, provider_state=state, warning=warning))
    if key not in _tasks:
        if len(_tasks) >= MAX_REFRESHES:
            return _response({'detail': 'Discovery is busy. Try again shortly.'},
                             _meta(provider, state='unavailable'), 503)
        _start_refresh(handler, request, identity, store, cached)
    return await asyncio.shield(_tasks[key])


async def _snapshot_request(handler, request, identity, force=False):
    """Track request setup so maintenance can drain requests already admitted."""
    global _maintenance_event
    if _maintenance_event is None:
        _maintenance_event = asyncio.Event()
        _maintenance_event.set()
    await _maintenance_event.wait()
    current = asyncio.current_task()
    if current is not None:
        _inflight_requests.add(current)
    try:
        return await _snapshot_request_impl(handler, request, identity, force)
    finally:
        if current is not None:
            _inflight_requests.discard(current)


async def cached_provider_query(provider, operation, params, callback):
    """Mixed search retains each successful provider separately, never a partial aggregate."""
    scope = _provider_scope(provider)
    key = hashlib.sha256(json.dumps([provider, operation, params, scope], sort_keys=True).encode()).hexdigest()
    async def handler(_request):
        return JSONResponse(await callback())
    result = await _snapshot_request(handler, None, (provider, operation, params, scope, key))
    return result.status_code, json.loads(result.body)


class SnapshotRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def wrapped(request: Request):
            identity = _identity(request, self)
            if identity is None or request.method != 'GET':
                return await handler(request)
            return await _snapshot_request(handler, request, identity,
                force=request.query_params.get('refresh', '').lower() == 'true')
        return wrapped


async def shutdown_discovery_refreshes():
    tasks = list(_tasks.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _tasks.clear()


async def clear_recent_discovery_cache(store):
    """Gate new cache requests, let current refresh writes finish, then clear."""
    global _maintenance_event, _maintenance
    if _maintenance_event is None:
        _maintenance_event = asyncio.Event()
        _maintenance_event.set()
    await _maintenance_event.wait()
    _maintenance = True
    _maintenance_event.clear()
    try:
        # Requests admitted before the gate closed may still be between cache
        # lookup and refresh creation. Let those finish before inspecting tasks.
        active_requests = [task for task in _inflight_requests if task is not asyncio.current_task()]
        if active_requests:
            await asyncio.gather(*active_requests, return_exceptions=True)
        # Existing HTTP/provider operations have bounded timeouts. Gather
        # without cancellation so asyncio.to_thread SQLite writes finish too.
        while _tasks:
            completed = [key for key, task in _tasks.items() if task.done()]
            for key in completed:
                _tasks.pop(key, None)
            active = list(_tasks.values())
            if active:
                await asyncio.gather(*active, return_exceptions=True)
        deleted = store.clear_recent()
        stats = store.cache_stats()
        return {**deleted, "stats": stats, "warning": stats.get("warning")}
    finally:
        _maintenance = False
        _maintenance_event.set()
