"""Small async clients for local services and native cloud text APIs."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import aclosing
from urllib.parse import unquote, urlsplit, urlunsplit

import httpx


PROVIDERS = {"openai_compatible", "lmstudio", "ollama", "koboldcpp", "anthropic", "openai", "gemini"}
NATIVE_CLOUD_PROVIDERS = {"anthropic", "openai", "gemini"}


def normalize_base_url(value: str | None, provider: str = "openai_compatible") -> str:
    """Validate a service root and preserve safe reverse-proxy path prefixes."""
    if not value or len(value) > 2048 or provider not in PROVIDERS:
        raise ValueError("Enter a valid service URL.")
    raw = value.strip()
    if any(ord(char) < 0x20 or char.isspace() for char in raw):
        raise ValueError("Service URL cannot contain whitespace or control characters.")
    try:
        parts = urlsplit(raw)
        _ = parts.port  # validates malformed ports
    except ValueError:
        raise ValueError("Enter a valid service URL.") from None
    if (parts.scheme.lower() not in {"http", "https"} or not parts.hostname
            or parts.username is not None or parts.password is not None or parts.query or parts.fragment
            or "?" in raw or "#" in raw):
        raise ValueError("Service URL must use http or https and cannot contain credentials, a query, or a fragment.")
    decoded_segments = [unquote(part) for part in parts.path.split("/")]
    if any(part in {".", ".."} for part in decoded_segments):
        raise ValueError("Service URL path cannot contain traversal segments.")
    path = parts.path.rstrip("/")
    # OpenAI-compatible services commonly end at /v1. Google documents a
    # different explicit compatibility root, /v1beta/openai/, which must be
    # preserved so requests reach its models and chat-completions routes.
    if provider == "gemini":
        if not (path.endswith("/v1") or path.endswith("/v1beta")):
            path += "/v1beta"
    elif provider != "ollama" and not (path.endswith("/v1")
                                     or (provider not in NATIVE_CLOUD_PROVIDERS and path.endswith("/v1beta/openai"))):
        path += "/v1"
    return urlunsplit((parts.scheme.lower(), parts.netloc, path, "", ""))


def _safe_models(payload: object, provider: str) -> list[dict[str, str]]:
    if provider == "ollama":
        models = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(models, list):
            raise ValueError("The service returned an invalid model list.")
        return [{"id": item["name"]} for item in models
                if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"]]
    models = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        raise ValueError("The service returned an invalid model list.")
    return [{"id": item["id"]} for item in models
            if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]]


async def list_models(base_url: str, api_key: str | None = None,
                      transport: httpx.AsyncBaseTransport | None = None, *,
                      provider: str = "openai_compatible") -> list[dict[str, str]]:
    if provider not in PROVIDERS:
        raise ValueError("Unsupported model service provider.")
    if provider in {"openai", "gemini"}:
        from . import openai_native, gemini
        adapter = openai_native if provider == "openai" else gemini
        return await adapter.list_models(base_url, api_key, transport=transport)
    if provider == "anthropic":
        _require_anthropic_key(api_key)
        headers = _anthropic_headers(api_key)
        async with httpx.AsyncClient(timeout=15, transport=transport) as client:
            try:
                response = await client.get(f"{base_url.rstrip('/')}/models", params={"limit": 100}, headers=headers)
            except httpx.RequestError:
                raise StreamProtocolError("The model service could not be reached.") from None
        if response.status_code >= 400:
            raise UpstreamError(response.status_code)
        try:
            payload = response.json()
        except (ValueError, httpx.DecodingError):
            raise ValueError("The service returned an invalid model list.") from None
        models = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(models, list):
            raise ValueError("The service returned an invalid model list.")
        return [{"id": item["id"]} for item in models[:100]
                if isinstance(item, dict) and _safe_model_id(item.get("id"))]
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(timeout=15, transport=transport) as client:
        if provider == "ollama":
            response = await client.get(f"{base_url}/api/tags", headers=headers)
            if response.status_code >= 400:
                raise UpstreamError(response.status_code)
            try:
                return _safe_models(response.json(), provider)
            except (ValueError, httpx.DecodingError):
                raise ValueError("The service returned an invalid model list.") from None

        response = await client.get(f"{base_url}/models", headers=headers)
        if response.status_code == 404 and provider == "koboldcpp":
            # KoboldCpp documents this legacy route for the loaded model name.
            root = base_url[:-3] if base_url.endswith("/v1") else base_url
            response = await client.get(f"{root}/api/v1/model", headers=headers)
            if response.status_code >= 400:
                raise UpstreamError(response.status_code)
            try:
                payload = response.json()
            except (ValueError, httpx.DecodingError):
                raise ValueError("The service returned an invalid model list.") from None
            name = payload.get("result") if isinstance(payload, dict) else None
            if not isinstance(name, str) or not name:
                raise ValueError("The service returned an invalid model list.")
            return [{"id": name}]
        if response.status_code >= 400:
            raise UpstreamError(response.status_code)
        try:
            return _safe_models(response.json(), provider)
        except (ValueError, httpx.DecodingError):
            raise ValueError("The service returned an invalid model list.") from None


async def stream_chat(base_url: str, model: str, messages: list[dict[str, str]], api_key: str | None = None,
                      transport: httpx.AsyncBaseTransport | None = None, *,
                      provider: str = "openai_compatible") -> AsyncIterator[str]:
    if provider not in PROVIDERS:
        raise ValueError("Unsupported model service provider.")
    if provider in {"openai", "gemini"}:
        from . import openai_native, gemini
        adapter = openai_native if provider == "openai" else gemini
        async with aclosing(adapter.stream_chat(base_url, model, messages, api_key, transport=transport)) as stream:
            async for text in stream:
                yield text
        return
    if provider == "anthropic":
        _require_anthropic_key(api_key)
        async with aclosing(_stream_anthropic(base_url, model, messages, api_key, transport)) as stream:
            async for text in stream:
                yield text
        return
    headers = {"Accept": "application/x-ndjson" if provider == "ollama" else "text/event-stream"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if provider == "ollama":
        async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10), transport=transport) as client:
            async with client.stream("POST", f"{base_url}/api/chat", headers=headers,
                                     json={"model": model, "messages": messages, "stream": True}) as response:
                if response.status_code >= 400:
                    raise UpstreamError(response.status_code)
                completed = False
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("error"):
                        raise StreamProtocolError("The model service reported an error.")
                    message = event.get("message")
                    text = message.get("content") if isinstance(message, dict) else None
                    if isinstance(text, str) and text:
                        yield text
                    if event.get("done") is True:
                        completed = True
                        break
                if not completed:
                    raise StreamProtocolError("The model service ended the reply early.")
        return

    async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10), transport=transport) as client:
        async with client.stream("POST", f"{base_url}/chat/completions", headers=headers,
                                 json={"model": model, "messages": messages, "stream": True}) as response:
            if response.status_code >= 400:
                # Do not expose upstream body; services sometimes echo request credentials.
                raise UpstreamError(response.status_code)
            completed = False
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    completed = True
                    break
                try:
                    event = json.loads(data)
                    if isinstance(event, dict) and event.get("error"):
                        raise StreamProtocolError("The model service reported an error.")
                    choice = event["choices"][0]
                    text = choice["delta"].get("content")
                    if isinstance(text, str) and text:
                        yield text
                    finish_reason = choice.get("finish_reason")
                    if finish_reason is not None:
                        completed = True
                        if finish_reason != "stop":
                            raise CompletionStoppedError(finish_reason)
                except (json.JSONDecodeError, TypeError, KeyError, IndexError, AttributeError):
                    # Skip malformed chunks; valid later chunks can still complete the turn.
                    continue
            if not completed:
                raise StreamProtocolError("The model service ended the reply early.")


_ANTHROPIC_VERSION = "2023-06-01"
_MAX_SSE_EVENT_BYTES = 1024 * 1024
_MAX_SSE_STREAM_BYTES = 8 * 1024 * 1024
_MAX_SSE_EVENTS = 4096


def _safe_model_id(value: object) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= 200
            and all(ord(char) >= 0x21 and ord(char) != 0x7f for char in value))


def _require_anthropic_key(api_key: str | None) -> None:
    if (not isinstance(api_key, str) or not api_key or len(api_key) > 8192
            or any(ord(char) < 0x21 or ord(char) > 0x7e for char in api_key)):
        raise ValueError("Enter a valid Anthropic API key.")


def _anthropic_headers(api_key: str) -> dict[str, str]:
    return {"x-api-key": api_key, "anthropic-version": _ANTHROPIC_VERSION,
            "Accept": "application/json"}


def _anthropic_request_messages(messages: list[dict[str, str]]) -> tuple[str | None, list[dict[str, str]]]:
    system_parts: list[str] = []
    output: list[dict[str, str]] = []
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "".join(block.get("text", "") for block in content
                           if isinstance(block, dict) and block.get("type") == "text"
                           and isinstance(block.get("text"), str))
        else:
            continue
        if not text:
            continue
        if role == "system":
            system_parts.append(text)
        elif role in {"user", "assistant"}:
            output.append({"role": role, "content": text})
    return ("\n".join(system_parts) if system_parts else None), output


async def _anthropic_events(response: httpx.Response) -> AsyncIterator[tuple[str, dict]]:
    event_name: str | None = None
    data_lines: list[str] = []
    event_bytes = 0
    stream_bytes = 0
    event_count = 0

    async def dispatch() -> tuple[str, dict] | None:
        nonlocal event_name, data_lines, event_bytes, event_count
        if event_name is None and not data_lines:
            return None
        name = event_name or ""
        raw = "\n".join(data_lines)
        event_name, data_lines, event_bytes = None, [], 0
        event_count += 1
        if event_count > _MAX_SSE_EVENTS or not name or not raw:
            raise StreamProtocolError("The model service returned an invalid event stream.")
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            raise StreamProtocolError("The model service returned an invalid event stream.") from None
        if not isinstance(payload, dict) or (payload.get("type") is not None and payload["type"] != name):
            raise StreamProtocolError("The model service returned an invalid event stream.")
        return name, payload

    try:
        async for line in response.aiter_lines():
            encoded_size = len(line.encode("utf-8", errors="replace")) + 1
            stream_bytes += encoded_size
            event_bytes += encoded_size
            if stream_bytes > _MAX_SSE_STREAM_BYTES or event_bytes > _MAX_SSE_EVENT_BYTES:
                raise StreamProtocolError("The model service event stream exceeded its size limit.")
            if line == "":
                parsed = await dispatch()
                if parsed:
                    yield parsed
                continue
            if line.startswith(":"):
                continue
            field, separator, value = line.partition(":")
            if separator and value.startswith(" "):
                value = value[1:]
            if field == "event":
                event_name = value
            elif field == "data":
                data_lines.append(value)
            # id/retry are irrelevant to this one-shot, non-retrying client.
    except httpx.RequestError:
        raise StreamProtocolError("The model service stream ended unexpectedly.") from None
    final_event = await dispatch()
    if final_event:
        yield final_event


async def _stream_anthropic(base_url: str, model: str, messages: list[dict[str, str]], api_key: str,
                            transport: httpx.AsyncBaseTransport | None) -> AsyncIterator[str]:
    system, native_messages = _anthropic_request_messages(messages)
    if not native_messages:
        raise ValueError("Add a user message before starting a reply.")
    body: dict[str, object] = {"model": model, "max_tokens": 2048, "messages": native_messages, "stream": True}
    if system:
        body["system"] = system
    headers = _anthropic_headers(api_key)
    headers["Accept"] = "text/event-stream"
    headers["Content-Type"] = "application/json"
    async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10), transport=transport) as client:
        try:
            async with client.stream("POST", f"{base_url.rstrip('/')}/messages", headers=headers, json=body) as response:
                if response.status_code >= 400:
                    raise UpstreamError(response.status_code)
                started = False
                active_blocks: dict[int, str] = {}
                stop_reason: str | None = None
                complete = False
                async for event_name, payload in _anthropic_events(response):
                    if event_name == "ping":
                        continue
                    if event_name == "error":
                        raise StreamProtocolError("The Anthropic stream reported an error.")
                    if event_name == "message_start":
                        if started or not isinstance(payload.get("message"), dict):
                            raise StreamProtocolError("The Anthropic stream was malformed.")
                        started = True
                    elif event_name == "content_block_start":
                        index, block = payload.get("index"), payload.get("content_block")
                        if not started or not isinstance(index, int) or index < 0 or not isinstance(block, dict) or index in active_blocks:
                            raise StreamProtocolError("The Anthropic stream was malformed.")
                        active_blocks[index] = block.get("type", "") if isinstance(block.get("type"), str) else ""
                    elif event_name == "content_block_delta":
                        index, delta = payload.get("index"), payload.get("delta")
                        if not isinstance(index, int) or index not in active_blocks or not isinstance(delta, dict):
                            raise StreamProtocolError("The Anthropic stream was malformed.")
                        if active_blocks[index] == "text" and delta.get("type") == "text_delta":
                            text = delta.get("text")
                            if not isinstance(text, str):
                                raise StreamProtocolError("The Anthropic stream was malformed.")
                            if text:
                                yield text
                    elif event_name == "content_block_stop":
                        index = payload.get("index")
                        if not isinstance(index, int) or index not in active_blocks:
                            raise StreamProtocolError("The Anthropic stream was malformed.")
                        del active_blocks[index]
                    elif event_name == "message_delta":
                        delta = payload.get("delta")
                        if not started or not isinstance(delta, dict):
                            raise StreamProtocolError("The Anthropic stream was malformed.")
                        new_reason = delta.get("stop_reason")
                        if new_reason is not None and not isinstance(new_reason, str):
                            raise StreamProtocolError("The Anthropic stream was malformed.")
                        if isinstance(new_reason, str):
                            stop_reason = new_reason
                    elif event_name == "message_stop":
                        if not started or active_blocks or stop_reason is None:
                            raise StreamProtocolError("The Anthropic stream ended before completion.")
                        complete = True
                        if stop_reason == "end_turn" or stop_reason == "stop_sequence":
                            return
                        if stop_reason == "max_tokens":
                            raise CompletionStoppedError("length")
                        if stop_reason == "tool_use":
                            raise CompletionStoppedError("tool_calls")
                        raise CompletionStoppedError(stop_reason)
                    # Unknown named events are ignored as Anthropic may add events.
                if not complete:
                    raise StreamProtocolError("The Anthropic stream ended before completion.")
        except httpx.RequestError:
            raise StreamProtocolError("The model service could not be reached.") from None


class UpstreamError(Exception):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"Upstream returned HTTP {status_code}")


class StreamProtocolError(Exception):
    """The service closed or failed a stream before a valid completion marker."""


class CompletionStoppedError(StreamProtocolError):
    """A Chat Completions stream stopped for a non-success reason."""

    _MESSAGES = {
        "length": "The response stopped at the model's output-token limit. Shorten the prompt or ask for a shorter reply, then try again.",
        "content_filter": "The provider's content filter stopped the response. Review the prompt and try a different phrasing.",
        "tool_calls": "The model attempted a tool call that this chat does not support.",
        "function_call": "The model attempted a function call that this chat does not support.",
        "other": "The model ended the response before a successful stop.",
    }

    def __init__(self, finish_reason: object):
        self.reason = finish_reason if isinstance(finish_reason, str) and finish_reason in self._MESSAGES else "other"
        self.message = self._MESSAGES[self.reason]
        super().__init__(self.message)
