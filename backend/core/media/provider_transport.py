"""Shared, credential-safe HTTP transport for media metadata providers."""
from __future__ import annotations

import asyncio
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import timezone
from email.utils import parsedate_to_datetime
import hashlib
import json as jsonlib
import random
import time
from collections.abc import Mapping
from typing import Any

import httpx

from core.discovery_retention import get_snapshot_store


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
PACING_SECONDS = {"anilist": 2.0, "igdb": 0.3, "tmdb": 0.1, "wikidata": 0.5, "wikipedia": 0.5}
_TIMEOUT_SECONDS = {"anilist": 15.0, "igdb": 20.0, "tmdb": 15.0, "wikidata": 15.0, "wikipedia": 20.0}
_client_factory = None
_clock = time.time
_sleep = asyncio.sleep
_jitter = random.uniform
_locks: dict[tuple[int, str, str | None], asyncio.Lock] = {}


class ProviderError(Exception):
    """Safe provider failure; intentionally not a ValueError."""

    def __init__(self, message: str, *, status: int | None = None, code: str = "upstream",
                 next_allowed_at: float | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.next_allowed_at = next_allowed_at


class ProviderNotModified(Exception):
    """The provider confirmed a matching cached representation is unchanged."""

    def __init__(self, metadata: dict[str, Any]):
        super().__init__("Provider representation has not changed.")
        self.status = 304
        self.metadata = metadata


@dataclass
class _RequestMetadata:
    no_store: bool = False
    no_cache: bool = False
    must_revalidate: bool = False
    response_count: int = 0
    etag: str | None = None
    last_modified: str | None = None
    validators_mixed: bool = False
    max_age: int | None = None
    validator_key: str | None = None
    cache_control_seen: bool = False


_request_metadata: ContextVar[_RequestMetadata | None] = ContextVar("provider_request_metadata", default=None)


def reset_request_metadata(previous_validators: Mapping[str, Any] | None = None) -> Token:
    """Start aggregate cache metadata collection for one caller task."""
    metadata = _RequestMetadata()
    if isinstance(previous_validators, Mapping) and previous_validators.get("response_count") == 1:
        metadata.etag = _safe_validator(previous_validators.get("etag"))
        metadata.last_modified = _safe_validator(previous_validators.get("last_modified"))
        metadata.validator_key = _safe_validator(previous_validators.get("validator_key"))
        metadata.no_store = previous_validators.get("no_store") is True
        metadata.no_cache = previous_validators.get("no_cache") is True
        metadata.must_revalidate = previous_validators.get("must_revalidate") is True
        age = previous_validators.get("max_age")
        metadata.max_age = age if isinstance(age, int) and not isinstance(age, bool) and age >= 0 else None
    return _request_metadata.set(metadata)


def _safe_validator(value: Any) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 1024:
        return None
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        return None
    return value


def restore_request_metadata(token: Token) -> None:
    _request_metadata.reset(token)


def get_request_metadata() -> dict[str, Any]:
    metadata = _request_metadata.get()
    if metadata is None:
        return {"no_store": False, "no_cache": False, "must_revalidate": False,
                "response_count": 0, "etag": None, "last_modified": None,
                "validators_mixed": False, "max_age": None, "validator_key": None}
    return {"no_store": metadata.no_store, "no_cache": metadata.no_cache,
            "must_revalidate": metadata.must_revalidate, "response_count": metadata.response_count,
            "etag": None if metadata.validators_mixed else metadata.etag,
            "last_modified": None if metadata.validators_mixed else metadata.last_modified,
            "validators_mixed": metadata.validators_mixed, "max_age": metadata.max_age,
            "validator_key": None if metadata.validators_mixed else metadata.validator_key}


def credential_scope(provider: str, *credentials: str | None) -> str:
    """Opaque account scope; callers must never persist or log raw credentials."""
    digest = hashlib.sha256()
    for value in (provider, *credentials):
        encoded = (value or "").encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _lock_for(provider: str, scope: str | None) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    key = (id(loop), provider, scope)
    lock = _locks.get(key)
    if lock is None:
        lock = _locks[key] = asyncio.Lock()
    return lock


def _header(headers: Any, name: str, default: Any = None) -> Any:
    value = headers.get(name)
    if value is not None:
        return value
    lowered = name.lower()
    try:
        return next((v for k, v in headers.items() if str(k).lower() == lowered), default)
    except (AttributeError, TypeError):
        return default


def _request_validator_key(method: str, url: str, params: Any, scope: str | None) -> str:
    value = jsonlib.dumps({"method": method, "url": url, "params": params, "scope": scope},
                          sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _record_headers(headers: Any, *, validator_key: str | None = None,
                    not_modified: bool = False) -> None:
    metadata = _request_metadata.get()
    if metadata is None:
        return
    cache_control_value = _header(headers, "cache-control")
    cache_control = str(cache_control_value or "")
    directives = {part.strip().split("=", 1)[0].lower() for part in cache_control.split(",")}
    etag = _safe_validator(_header(headers, "etag"))
    modified = _safe_validator(_header(headers, "last-modified"))
    if not_modified:
        if etag is not None:
            metadata.etag = etag
        if modified is not None:
            metadata.last_modified = modified
        if cache_control_value is not None:
            metadata.no_store = "no-store" in directives
            metadata.no_cache = "no-cache" in directives
            metadata.must_revalidate = "must-revalidate" in directives
            metadata.cache_control_seen = True
        for part in cache_control.split(","):
            name, sep, value = part.strip().partition("=")
            if sep and name.strip().lower() == "max-age":
                try:
                    metadata.max_age = max(0, int(value.strip().strip('"')))
                except ValueError:
                    pass
        metadata.response_count = 1
        metadata.validator_key = validator_key
        return
    if metadata.response_count == 0:
        # A newly fetched representation replaces metadata inherited for a
        # possible 304; only 304 responses preserve the cached directives.
        metadata.no_store = metadata.no_cache = metadata.must_revalidate = False
        metadata.max_age = None
        metadata.etag = etag
        metadata.last_modified = modified
        metadata.validator_key = validator_key
    else:
        metadata.validators_mixed = True
        metadata.validator_key = None
    metadata.response_count += 1
    metadata.no_store |= "no-store" in directives
    metadata.no_cache |= "no-cache" in directives
    metadata.must_revalidate |= "must-revalidate" in directives
    metadata.cache_control_seen = metadata.cache_control_seen or cache_control_value is not None
    for part in cache_control.split(","):
        name, sep, value = part.strip().partition("=")
        if sep and name.strip().lower() == "max-age":
            try:
                age = max(0, int(value.strip().strip('"')))
            except ValueError:
                continue
            metadata.max_age = age if metadata.max_age is None else min(metadata.max_age, age)


def _retry_after(headers: Any, now: float) -> float | None:
    value = _header(headers, "retry-after")
    if value:
        try:
            return max(now, now + max(0.0, float(value)))
        except (TypeError, ValueError):
            try:
                parsed = parsedate_to_datetime(value)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return max(now, parsed.timestamp())
            except (TypeError, ValueError, OverflowError):
                pass
    value = _header(headers, "x-ratelimit-reset") or _header(headers, "ratelimit-reset")
    if value:
        try:
            reset = float(value)
            return max(now, reset if reset > now else now + max(0.0, reset))
        except (TypeError, ValueError):
            pass
    return None


def _store_state(provider: str, scope: str | None, *, next_allowed_at: float | None,
                 error: str | None = None, error_at: float | None = None) -> None:
    try:
        get_snapshot_store().set_provider_state(provider, scope, next_allowed_at=next_allowed_at,
                                                error=error, error_at=error_at)
    except Exception:
        # Provider scheduling remains available in-process if the optional sidecar is unavailable.
        pass


def _read_state(provider: str, scope: str | None) -> dict[str, Any] | None:
    try:
        return get_snapshot_store().get_provider_state(provider, scope)
    except Exception:
        return None


async def _wait_for_slot(provider: str, scope: str | None) -> float:
    now = _clock()
    stored = _read_state(provider, scope)
    due = max(now, float((stored or {}).get("next_allowed_at") or 0.0))
    previous_error = (stored or {}).get("error")
    error_at = float((stored or {}).get("error_at") or 0.0)
    # Surface durable backoffs immediately so one provider cannot occupy a
    # request task for minutes or hours. Only ordinary pacing is slept through.
    if due > now and previous_error in {"rate_limit", "auth", "network", "upstream", "invalid_response"}:
        if previous_error != "auth" or now - error_at < 60:
            if previous_error == "rate_limit":
                raise ProviderError("Provider rate limit reached; retry after the indicated time.",
                                    status=429, code="rate_limit", next_allowed_at=due)
            if previous_error == "auth":
                raise ProviderError("Provider authorization failed. Check the configured account credentials.",
                                    status=401, code="auth", next_allowed_at=due)
            raise ProviderError("Provider is temporarily unavailable; retry after a short cooldown.",
                                code=previous_error, next_allowed_at=due)
    if due > now:
        await _sleep(due - now)
    start = _clock()
    next_allowed_at = start + PACING_SECONDS.get(provider, 0.25)
    _store_state(provider, scope, next_allowed_at=next_allowed_at)
    return next_allowed_at


def _error_for_status(status: int, *, provider: str, next_allowed_at: float | None = None) -> ProviderError:
    if status == 401 or (status == 403 and provider != "anilist"):
        return ProviderError("Provider authorization failed. Check the configured account credentials.",
                             status=status, code="auth")
    if status == 404:
        return ProviderError("Provider resource was not found.", status=status, code="not_found")
    if status == 429:
        return ProviderError("Provider rate limit reached; retry after the indicated time.",
                             status=status, code="rate_limit", next_allowed_at=next_allowed_at)
    if status >= 500:
        return ProviderError("Provider service is temporarily unavailable.", status=status, code="upstream")
    return ProviderError("Provider rejected the request.", status=status, code="upstream")


async def request_json(provider: str, method: str, url: str, *, params: Any = None,
                       json: Any = None, content: Any = None, headers: Any = None,
                       account_scope: str | None = None, graphql: bool = False) -> dict | list:
    """Issue one bounded JSON request with shared pacing, safe errors and limited retry."""
    provider = provider.lower().strip()
    if not provider or len(provider) > 40:
        raise ProviderError("Unknown metadata provider.", code="invalid_response")
    method_key = method.upper()
    validator_key = (_request_validator_key(method, url, params, account_scope)
                     if method_key == "GET" else None)
    request_headers = dict(headers or {})
    metadata = _request_metadata.get()
    conditional_get = False
    if method_key == "GET":
        request_headers = {key: value for key, value in request_headers.items()
                           if str(key).lower() not in {"if-none-match", "if-modified-since"}}
        if (metadata is not None and metadata.response_count == 0
                and metadata.validator_key == validator_key):
            if metadata.etag:
                request_headers["If-None-Match"] = metadata.etag
                conditional_get = True
            if metadata.last_modified:
                request_headers["If-Modified-Since"] = metadata.last_modified
                conditional_get = True
    lock = _lock_for(provider, account_scope)
    async with lock:
        for attempt in range(2):
            next_allowed_at = await _wait_for_slot(provider, account_scope)
            try:
                client_factory = _client_factory or httpx.AsyncClient
                async with client_factory(timeout=_TIMEOUT_SECONDS.get(provider, 20.0)) as client:
                    response = await client.request(method, url, params=params, json=json,
                                                    content=content, headers=request_headers)
            except httpx.RequestError:
                if attempt == 0:
                    delay = min(2.0, 0.4 + _jitter(0.0, 0.4))
                    _store_state(provider, account_scope, next_allowed_at=max(next_allowed_at, _clock() + delay),
                                 error=None, error_at=None)
                    await _sleep(delay)
                    continue
                cooldown = _clock() + 15.0
                _store_state(provider, account_scope, next_allowed_at=cooldown,
                             error="network", error_at=_clock())
                raise ProviderError("Could not reach the metadata provider. Check the internet connection.",
                                    code="network", next_allowed_at=cooldown) from None

            status = int(response.status_code)
            if status == 304:
                if conditional_get:
                    _record_headers(response.headers, validator_key=validator_key, not_modified=True)
                    _store_state(provider, account_scope, next_allowed_at=next_allowed_at)
                    raise ProviderNotModified(get_request_metadata())
                cooldown = _clock() + 15.0
                _store_state(provider, account_scope, next_allowed_at=cooldown,
                             error="invalid_response", error_at=_clock())
                raise ProviderError("Provider returned an unexpected not-modified response.",
                                    status=304, code="invalid_response", next_allowed_at=cooldown)
            if status == 429:
                now = _clock()
                retry_at = _retry_after(response.headers, now)
                if retry_at is None:
                    retry_at = now + min(300.0, 1.0 + _jitter(0.0, 1.0))
                retry_at = max(retry_at, next_allowed_at)
                _store_state(provider, account_scope, next_allowed_at=retry_at,
                             error="rate_limit", error_at=now)
                raise _error_for_status(status, provider=provider, next_allowed_at=retry_at)
            if status >= 500 and attempt == 0:
                delay = min(2.0, 0.4 + _jitter(0.0, 0.4))
                _store_state(provider, account_scope, next_allowed_at=max(next_allowed_at, _clock() + delay),
                             error=None, error_at=None)
                await _sleep(delay)
                continue
            if status < 200 or status >= 300:
                if status == 403 and provider == "anilist":
                    err = ProviderError("AniList is not accepting requests right now.", status=status, code="upstream")
                    cooldown = _clock() + 15.0
                    _store_state(provider, account_scope, next_allowed_at=cooldown,
                                 error="upstream", error_at=_clock())
                    err.next_allowed_at = cooldown
                elif status in (401, 403):
                    cooldown = _clock() + 60.0
                    err = _error_for_status(status, provider=provider, next_allowed_at=cooldown)
                    _store_state(provider, account_scope, next_allowed_at=cooldown,
                                 error="auth", error_at=_clock())
                else:
                    err = _error_for_status(status, provider=provider)
                    if status >= 500:
                        cooldown = _clock() + 15.0
                        _store_state(provider, account_scope, next_allowed_at=cooldown,
                                     error="upstream", error_at=_clock())
                raise err

            try:
                body = getattr(response, "content", None)
                if isinstance(body, (bytes, bytearray)) and len(body) > MAX_RESPONSE_BYTES:
                    raise ProviderError("Provider response exceeded the 2 MiB limit.",
                                        status=status, code="invalid_response")
                payload = response.json()
                if not isinstance(payload, (dict, list)):
                    raise ProviderError("Provider returned an invalid JSON shape.",
                                        status=status, code="invalid_response")
                if not isinstance(body, (bytes, bytearray)):
                    encoded_size = len(jsonlib.dumps(payload, ensure_ascii=False).encode("utf-8"))
                    if encoded_size > MAX_RESPONSE_BYTES:
                        raise ProviderError("Provider response exceeded the 2 MiB limit.",
                                            status=status, code="invalid_response")
                if graphql:
                    if not isinstance(payload, dict) or payload.get("errors") or payload.get("data") is None:
                        raise ProviderError("Provider returned a GraphQL error or incomplete response.",
                                            status=status, code="invalid_response")
                    if not isinstance(payload["data"], dict):
                        raise ProviderError("Provider returned an invalid GraphQL data shape.",
                                            status=status, code="invalid_response")
            except ProviderError as exc:
                if exc.code == "invalid_response":
                    cooldown = _clock() + 15.0
                    _store_state(provider, account_scope, next_allowed_at=cooldown,
                                 error="invalid_response", error_at=_clock())
                    exc.next_allowed_at = cooldown
                raise
            except Exception:
                cooldown = _clock() + 15.0
                _store_state(provider, account_scope, next_allowed_at=cooldown,
                             error="invalid_response", error_at=_clock())
                raise ProviderError("Provider returned invalid JSON.", status=status,
                                    code="invalid_response", next_allowed_at=cooldown) from None
            _record_headers(response.headers, validator_key=validator_key)
            _store_state(provider, account_scope, next_allowed_at=next_allowed_at)
            return payload
    raise ProviderError("Provider request could not be completed.", code="upstream")
