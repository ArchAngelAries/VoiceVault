"""Small async client for Google's native Gemini REST API."""
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from urllib.parse import unquote, urlsplit

import httpx


_MAX_KEY_LENGTH = 4096
_MAX_SSE_EVENT_BYTES = 1024 * 1024
_MAX_SSE_STREAM_BYTES = 8 * 1024 * 1024
_MAX_SSE_EVENTS = 4096
_MODEL_NAME = re.compile(r"^models/[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")


def _errors():
    # Imported lazily so client.py can dispatch here without a module cycle.
    from .client import CompletionStoppedError, StreamProtocolError, UpstreamError
    return CompletionStoppedError, StreamProtocolError, UpstreamError


def _require_key(api_key: str | None) -> str:
    if (not isinstance(api_key, str) or not api_key or len(api_key) > _MAX_KEY_LENGTH
            or any(ord(char) < 0x21 or ord(char) > 0x7E for char in api_key)):
        raise ValueError("Enter a valid Gemini API key.")
    return api_key


def _validate_base_url(base_url: str) -> str:
    if not isinstance(base_url, str) or not base_url or len(base_url) > 2048:
        raise ValueError("Enter a valid Gemini service URL.")
    try:
        parts = urlsplit(base_url)
        _ = parts.port
    except ValueError:
        raise ValueError("Enter a valid Gemini service URL.") from None
    if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.query or parts.fragment
            or any(ord(char) < 0x21 or ord(char) > 0x7E for char in base_url)):
        raise ValueError("Enter a valid Gemini service URL.")
    if any(unquote(segment) in {".", ".."} for segment in parts.path.split("/")):
        raise ValueError("Gemini service URL cannot contain path traversal segments.")
    return base_url.rstrip("/")


def _headers(api_key: str, *, stream: bool = False) -> dict[str, str]:
    headers = {"x-goog-api-key": api_key, "Accept": "text/event-stream" if stream else "application/json"}
    if stream:
        headers["Content-Type"] = "application/json"
    return headers


async def list_models(base_url: str, api_key: str, transport: httpx.AsyncBaseTransport | None = None
                      ) -> list[dict[str, str]]:
    key = _require_key(api_key)
    root = _validate_base_url(base_url)
    _, StreamProtocolError, UpstreamError = _errors()
    async with httpx.AsyncClient(timeout=15, transport=transport) as client:
        try:
            response = await client.get(f"{root}/models", params={"pageSize": 100}, headers=_headers(key))
        except httpx.RequestError:
            raise StreamProtocolError("The model service could not be reached.") from None
    if response.status_code >= 400:
        raise UpstreamError(response.status_code)
    try:
        payload = response.json()
    except (ValueError, httpx.DecodingError):
        raise ValueError("The service returned an invalid model list.") from None
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        raise ValueError("The service returned an invalid model list.")
    result: list[dict[str, str]] = []
    for item in models:
        name = item.get("name") if isinstance(item, dict) else None
        methods = item.get("supportedGenerationMethods") if isinstance(item, dict) else None
        if isinstance(name, str) and _MODEL_NAME.fullmatch(name) and isinstance(methods, list) \
                and "generateContent" in methods:
            result.append({"id": name})
            if len(result) == 100:
                break
    return result


def _request_contents(messages: list[dict[str, str]]) -> tuple[str | None, list[dict[str, object]]]:
    system: list[str] = []
    contents: list[dict[str, object]] = []
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict):
            continue
        role, value = message.get("role"), message.get("content")
        if isinstance(value, str):
            text = value
        elif isinstance(value, list):
            text = "".join(part.get("text", "") for part in value
                           if isinstance(part, dict) and part.get("type") == "text"
                           and isinstance(part.get("text"), str))
        else:
            continue
        if not text:
            continue
        if role == "system":
            system.append(text)
        elif role in {"user", "assistant"}:
            contents.append({"role": "model" if role == "assistant" else "user",
                             "parts": [{"text": text}]})
    return ("\n".join(system) if system else None), contents


async def _data_events(response: httpx.Response) -> AsyncIterator[dict]:
    data_lines: list[str] = []
    event_bytes = stream_bytes = event_count = 0
    _, StreamProtocolError, _ = _errors()

    def dispatch() -> dict | None:
        nonlocal data_lines, event_bytes, event_count
        if not data_lines:
            return None
        raw = "\n".join(data_lines)
        data_lines, event_bytes = [], 0
        event_count += 1
        if event_count > _MAX_SSE_EVENTS:
            raise StreamProtocolError("The model service event stream exceeded its limit.")
        try:
            event = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            raise StreamProtocolError("The model service returned an invalid event stream.") from None
        if not isinstance(event, dict):
            raise StreamProtocolError("The model service returned an invalid event stream.")
        return event

    try:
        async for line in response.aiter_lines():
            size = len(line.encode("utf-8", errors="replace")) + 1
            stream_bytes += size
            event_bytes += size
            if stream_bytes > _MAX_SSE_STREAM_BYTES or event_bytes > _MAX_SSE_EVENT_BYTES:
                raise StreamProtocolError("The model service event stream exceeded its size limit.")
            if not line:
                event = dispatch()
                if event is not None:
                    yield event
            elif line.startswith(":"):
                continue
            else:
                field, sep, value = line.partition(":")
                if sep and value.startswith(" "):
                    value = value[1:]
                if field == "data":
                    data_lines.append(value)
        event = dispatch()
        if event is not None:
            yield event
    except httpx.RequestError:
        raise StreamProtocolError("The model service stream ended unexpectedly.") from None


async def stream_chat(base_url: str, model: str, messages: list[dict[str, str]], api_key: str,
                      transport: httpx.AsyncBaseTransport | None = None) -> AsyncIterator[str]:
    key = _require_key(api_key)
    root = _validate_base_url(base_url)
    _, StreamProtocolError, UpstreamError = _errors()
    if not isinstance(model, str) or not _MODEL_NAME.fullmatch(model):
        raise ValueError("Choose a valid Gemini model.")
    system, contents = _request_contents(messages)
    if not any(item["role"] == "user" for item in contents):
        raise ValueError("Add a user message before starting a reply.")
    body: dict[str, object] = {
        "contents": contents,
        "generationConfig": {"maxOutputTokens": 2048, "candidateCount": 1},
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    url = f"{root}/{model}:streamGenerateContent"
    completed = False
    tool_requested = False
    saw_public_text = False
    async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10), transport=transport) as client:
        try:
            async with client.stream("POST", url, params={"alt": "sse"}, headers=_headers(key, stream=True),
                                    json=body) as response:
                if response.status_code >= 400:
                    raise UpstreamError(response.status_code)
                async for event in _data_events(response):
                    if event.get("error"):
                        raise StreamProtocolError("The Gemini stream reported an error.")
                    feedback = event.get("promptFeedback")
                    if isinstance(feedback, dict) and feedback.get("blockReason"):
                        from .client import CompletionStoppedError
                        raise CompletionStoppedError("content_filter")
                    candidates = event.get("candidates")
                    if candidates is None:
                        # Usage metadata may arrive without a candidate; it cannot finish the response.
                        continue
                    if not isinstance(candidates, list) or len(candidates) != 1 or not isinstance(candidates[0], dict):
                        raise StreamProtocolError("The Gemini stream was malformed.")
                    candidate = candidates[0]
                    candidate_index = candidate.get("index")
                    if candidate_index is not None and (type(candidate_index) is not int or candidate_index != 0):
                        raise StreamProtocolError("The Gemini stream was malformed.")
                    content = candidate.get("content")
                    if content is not None:
                        parts = content.get("parts") if isinstance(content, dict) else None
                        if not isinstance(parts, list):
                            raise StreamProtocolError("The Gemini stream was malformed.")
                        role = content.get("role")
                        if role is not None and role != "model":
                            raise StreamProtocolError("The Gemini stream was malformed.")
                        for part in parts:
                            if not isinstance(part, dict):
                                raise StreamProtocolError("The Gemini stream was malformed.")
                            thought = part.get("thought")
                            if "thought" in part and type(thought) is not bool:
                                raise StreamProtocolError("The Gemini stream was malformed.")
                            text = part.get("text")
                            if "text" in part and not isinstance(text, str):
                                raise StreamProtocolError("The Gemini stream was malformed.")
                            if any(key_name in part for key_name in
                                   ("functionCall", "executableCode", "codeExecutionResult")):
                                tool_requested = True
                                continue
                            if "inlineData" in part or "fileData" in part:
                                continue
                            if isinstance(text, str) and text and thought is not True:
                                saw_public_text = True
                                yield text
                    reason = candidate.get("finishReason")
                    if reason is not None:
                        if not isinstance(reason, str):
                            raise StreamProtocolError("The Gemini stream was malformed.")
                        if reason == "STOP":
                            if tool_requested:
                                from .client import CompletionStoppedError
                                raise CompletionStoppedError("tool_calls")
                            if not saw_public_text:
                                raise StreamProtocolError("The Gemini stream ended without public text.")
                            completed = True
                            return
                        if reason == "MAX_TOKENS":
                            from .client import CompletionStoppedError
                            raise CompletionStoppedError("length")
                        if reason in {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}:
                            from .client import CompletionStoppedError
                            raise CompletionStoppedError("content_filter")
                        if reason in {"MALFORMED_FUNCTION_CALL", "UNEXPECTED_TOOL_CALL"}:
                            from .client import CompletionStoppedError
                            raise CompletionStoppedError("tool_calls")
                        from .client import CompletionStoppedError
                        raise CompletionStoppedError("other")
                if not completed:
                    raise StreamProtocolError("The model service ended the reply early.")
        except httpx.RequestError:
            raise StreamProtocolError("The model service could not be reached.") from None
