from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from core.media import provider_transport as transport


class MemoryStore:
    def __init__(self):
        self.state = {}

    def get_provider_state(self, provider, scope):
        return self.state.get((provider, scope))

    def set_provider_state(self, provider, scope, *, next_allowed_at=None, error=None, error_at=None):
        value = {"next_allowed_at": next_allowed_at, "error": error, "error_at": error_at}
        self.state[(provider, scope)] = value
        return value


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None, *, with_content=True):
        self.status_code = status
        self.headers = headers or {}
        self._payload = payload
        if with_content:
            self.content = json.dumps(payload).encode("utf-8") if payload is not None else b""

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture(autouse=True)
def isolated_transport(monkeypatch):
    clock = [100.0]
    store = MemoryStore()
    monkeypatch.setattr(transport, "get_snapshot_store", lambda: store)
    monkeypatch.setattr(transport, "_locks", {})
    monkeypatch.setattr(transport, "_clock", lambda: clock[0])
    monkeypatch.setattr(transport, "_jitter", lambda low, high: 0.0)

    async def fake_sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr(transport, "_sleep", fake_sleep)
    yield clock, store


def _client_factory(monkeypatch, responses, *, with_content=True):
    client = FakeClient(responses)
    monkeypatch.setattr(transport, "_client_factory", lambda **_: client)
    return client


@pytest.mark.asyncio
async def test_serializes_same_scope_and_applies_provider_pacing(monkeypatch, isolated_transport):
    clock, _store = isolated_transport
    client = _client_factory(monkeypatch, [FakeResponse(payload=[]), FakeResponse(payload=[])])
    starts = []

    async def record_request(method, url, **kwargs):
        starts.append(clock[0])
        return client.responses.pop(0)

    client.request = record_request
    await asyncio.gather(
        transport.request_json("anilist", "GET", "https://example.test", account_scope=None),
        transport.request_json("anilist", "GET", "https://example.test", account_scope=None),
    )
    assert starts == [100.0, 102.0]


@pytest.mark.asyncio
async def test_429_honors_retry_after_and_redacts_response_body(monkeypatch, isolated_transport):
    _clock, store = isolated_transport
    _client_factory(monkeypatch, [FakeResponse(429, {"message": "secret details"}, {"Retry-After": "7"})])
    with pytest.raises(transport.ProviderError) as caught:
        await transport.request_json("tmdb", "GET", "https://example.test", account_scope="opaque")
    error = caught.value
    assert error.code == "rate_limit" and error.status == 429
    assert error.next_allowed_at == 107.0
    assert "secret details" not in str(error)
    assert store.state[("tmdb", "opaque")]["next_allowed_at"] == 107.0
    with pytest.raises(transport.ProviderError) as cooled:
        await transport.request_json("tmdb", "GET", "https://example.test", account_scope="opaque")
    assert cooled.value.code == "rate_limit" and cooled.value.next_allowed_at == 107.0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,code,attempts", [
    (httpx.ConnectError("https://user:secret@example.test"), "network", 2),
    (FakeResponse(503, {"error": "secret"}), "upstream", 2),
    (FakeResponse(401, {"error": "secret"}), "auth", 1),
])
async def test_retry_policy_is_bounded_and_safe(monkeypatch, failure, code, attempts):
    responses = [failure, FakeResponse(payload={"ok": True})] if attempts == 2 else [failure]
    client = _client_factory(monkeypatch, responses)
    if attempts == 2:
        assert await transport.request_json("tmdb", "GET", "https://example.test", account_scope="opaque") == {"ok": True}
    else:
        with pytest.raises(transport.ProviderError) as caught:
            await transport.request_json("tmdb", "GET", "https://example.test", account_scope="opaque")
        assert caught.value.code == code
    assert len(client.calls) == attempts
    if attempts == 1:
        assert "secret" not in str(caught.value)
        assert "user:" not in str(caught.value)


@pytest.mark.asyncio
async def test_graphql_errors_partial_null_and_empty_list_shapes(monkeypatch, isolated_transport):
    _clock, store = isolated_transport
    client = _client_factory(monkeypatch, [
        FakeResponse(payload={"data": {"Page": {}}, "errors": [{"message": "raw secret"}]}),
    ])
    with pytest.raises(transport.ProviderError) as caught:
        await transport.request_json("anilist", "POST", "https://example.test", graphql=True)
    assert caught.value.code == "invalid_response"
    assert "raw secret" not in str(caught.value)

    store.state.clear()
    _client_factory(monkeypatch, [FakeResponse(payload={"data": None})])
    with pytest.raises(transport.ProviderError, match="GraphQL"):
        await transport.request_json("anilist", "POST", "https://example.test", graphql=True)

    _client_factory(monkeypatch, [FakeResponse(payload=[])])
    assert await transport.request_json("igdb", "POST", "https://example.test") == []


@pytest.mark.asyncio
async def test_size_limit_and_contentless_fixture_fallback(monkeypatch, isolated_transport):
    _clock, store = isolated_transport
    oversized = {"value": "x" * transport.MAX_RESPONSE_BYTES}
    _client_factory(monkeypatch, [FakeResponse(payload=oversized)])
    with pytest.raises(transport.ProviderError, match="2 MiB"):
        await transport.request_json("tmdb", "GET", "https://example.test")

    store.state.clear()
    _client_factory(monkeypatch, [FakeResponse(payload={"ok": 1}, with_content=False)])
    assert await transport.request_json("tmdb", "GET", "https://example.test") == {"ok": 1}


@pytest.mark.asyncio
async def test_cache_metadata_aggregates_directives_but_drops_mixed_validators(monkeypatch):
    token = transport.reset_request_metadata()
    try:
        _client_factory(monkeypatch, [
            FakeResponse(payload={"items": []}, headers={"Cache-Control": "no-cache, must-revalidate", "ETag": '"a"'}),
            FakeResponse(payload={"items": []}, headers={"ETag": '"b"'}),
        ])
        await transport.request_json("tmdb", "GET", "https://example.test/a")
        await transport.request_json("tmdb", "GET", "https://example.test/b")
        metadata = transport.get_request_metadata()
        assert metadata["no_cache"] and metadata["must_revalidate"]
        assert metadata["response_count"] == 2
        assert metadata["etag"] is None and metadata["validators_mixed"]
    finally:
        transport.restore_request_metadata(token)


@pytest.mark.asyncio
async def test_anilist_403_is_not_misreported_as_missing_credentials(monkeypatch):
    _client_factory(monkeypatch, [FakeResponse(403, {"message": "secret"})])
    with pytest.raises(transport.ProviderError) as caught:
        await transport.request_json("anilist", "POST", "https://example.test", graphql=True)
    assert caught.value.code == "upstream"
    assert "credential" not in str(caught.value).lower()
    assert "secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_failed_response_headers_do_not_contaminate_cache_metadata(monkeypatch):
    token = transport.reset_request_metadata()
    try:
        _client_factory(monkeypatch, [
            FakeResponse(503, {"message": "private"}, {"Cache-Control": "no-store", "ETag": '"failed"'}),
            FakeResponse(payload={"ok": True}, headers={"Cache-Control": "max-age=30", "ETag": '"ok"'}),
        ])
        assert await transport.request_json("tmdb", "GET", "https://example.test") == {"ok": True}
        metadata = transport.get_request_metadata()
        assert metadata["response_count"] == 1
        assert not metadata["no_store"]
        assert metadata["etag"] == '"ok"' and metadata["max_age"] == 30
    finally:
        transport.restore_request_metadata(token)


@pytest.mark.asyncio
async def test_matching_single_get_validator_accepts_304_and_preserves_cached_metadata(monkeypatch, isolated_transport):
    _clock, store = isolated_transport
    store.state.clear()
    token = transport.reset_request_metadata()
    try:
        _client_factory(monkeypatch, [FakeResponse(payload={"ok": True}, headers={
            "ETag": '"cached"', "Cache-Control": "no-cache, max-age=40"
        })])
        await transport.request_json("tmdb", "GET", "https://example.test/item", params={"page": 1},
                                     account_scope="opaque")
        previous = transport.get_request_metadata()
    finally:
        transport.restore_request_metadata(token)

    token = transport.reset_request_metadata(previous)
    try:
        client = _client_factory(monkeypatch, [FakeResponse(304, None)])
        with pytest.raises(transport.ProviderNotModified) as caught:
            await transport.request_json("tmdb", "GET", "https://example.test/item", params={"page": 1},
                                         account_scope="opaque")
        assert client.calls[0][2]["headers"]["If-None-Match"] == '"cached"'
        assert caught.value.status == 304
        assert caught.value.metadata["no_cache"]
        assert caught.value.metadata["max_age"] == 40
        assert caught.value.metadata["validator_key"] == previous["validator_key"]
    finally:
        transport.restore_request_metadata(token)


@pytest.mark.asyncio
async def test_mismatched_get_validator_is_not_sent_and_304_is_rejected(monkeypatch, isolated_transport):
    _clock, store = isolated_transport
    store.state.clear()
    token = transport.reset_request_metadata()
    try:
        _client_factory(monkeypatch, [FakeResponse(payload={"ok": True}, headers={"ETag": '"a"'})])
        await transport.request_json("tmdb", "GET", "https://example.test/a", account_scope="opaque")
        previous = transport.get_request_metadata()
    finally:
        transport.restore_request_metadata(token)

    token = transport.reset_request_metadata(previous)
    try:
        client = _client_factory(monkeypatch, [FakeResponse(304, None)])
        with pytest.raises(transport.ProviderError) as caught:
            await transport.request_json("tmdb", "GET", "https://example.test/b", account_scope="opaque")
        assert caught.value.status == 304 and caught.value.code == "invalid_response"
        assert "If-None-Match" not in client.calls[0][2]["headers"]
    finally:
        transport.restore_request_metadata(token)


@pytest.mark.asyncio
async def test_cached_get_validators_are_never_applied_to_post(monkeypatch, isolated_transport):
    _clock, store = isolated_transport
    store.state.clear()
    token = transport.reset_request_metadata()
    try:
        _client_factory(monkeypatch, [FakeResponse(payload={"ok": True}, headers={"ETag": '"a"'})])
        await transport.request_json("tmdb", "GET", "https://example.test/item", account_scope="opaque")
        previous = transport.get_request_metadata()
    finally:
        transport.restore_request_metadata(token)

    token = transport.reset_request_metadata(previous)
    try:
        client = _client_factory(monkeypatch, [FakeResponse(payload={"ok": True})])
        await transport.request_json("tmdb", "POST", "https://example.test/item", json={"q": 1},
                                     account_scope="opaque")
        assert "If-None-Match" not in client.calls[0][2]["headers"]
        assert "If-Modified-Since" not in client.calls[0][2]["headers"]
    finally:
        transport.restore_request_metadata(token)


def test_credential_scope_is_deterministic_and_never_raw():
    one = transport.credential_scope("igdb", "client-id", "secret-value")
    assert one == transport.credential_scope("igdb", "client-id", "secret-value")
    assert len(one) == 64 and "secret-value" not in one
