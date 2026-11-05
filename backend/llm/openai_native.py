"""Small HTTPX client for OpenAI's native Responses API."""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from .client import CompletionStoppedError, StreamProtocolError, UpstreamError, _anthropic_events


_MAX_API_KEY_CHARS = 4096
_MAX_MODEL_ID_CHARS = 200


def _require_api_key(api_key: str | None) -> None:
    if (not isinstance(api_key, str) or not api_key or len(api_key) > _MAX_API_KEY_CHARS
            or any(ord(char) < 0x21 or ord(char) > 0x7E for char in api_key)):
        raise ValueError("Enter a valid OpenAI API key.")


def _valid_model_id(value: object) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= _MAX_MODEL_ID_CHARS
            and all(0x21 <= ord(char) <= 0x7E for char in value))


def _is_tool_item(item_type: object) -> bool:
    return isinstance(item_type, str) and item_type.endswith("_call")


async def list_models(base_url: str, api_key: str | None,
                      transport: httpx.AsyncBaseTransport | None = None) -> list[dict[str, str]]:
    """Fetch up to 100 model IDs from the authenticated OpenAI-compatible models endpoint."""
    _require_api_key(api_key)
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=15, transport=transport) as client:
        try:
            response = await client.get(f"{base_url.rstrip('/')}/models", headers=headers)
        except httpx.RequestError:
            raise StreamProtocolError("The OpenAI model service could not be reached.") from None
    if response.status_code >= 400:
        raise UpstreamError(response.status_code)
    try:
        payload = response.json()
    except (ValueError, httpx.DecodingError):
        raise StreamProtocolError("The OpenAI model service returned an invalid model list.") from None
    values = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(values, list):
        raise StreamProtocolError("The OpenAI model service returned an invalid model list.")
    safe = [item["id"] for item in values if isinstance(item, dict) and _valid_model_id(item.get("id"))]
    return [{"id": model_id} for model_id in safe[:100]]


def _request_messages(messages: list[dict[str, str]]) -> tuple[str | None, list[dict[str, str]]]:
    instructions: list[str] = []
    inputs: list[dict[str, str]] = []
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict):
            continue
        role, content = message.get("role"), message.get("content")
        if role not in {"system", "user", "assistant"} or not isinstance(content, str) or not content:
            continue
        if role == "system":
            instructions.append(content)
        else:
            inputs.append({"role": role, "content": content})
    return ("\n".join(instructions) if instructions else None), inputs


async def stream_chat(base_url: str, model: str, messages: list[dict[str, str]], api_key: str | None,
                      transport: httpx.AsyncBaseTransport | None = None) -> AsyncIterator[str]:
    """Stream only text deltas from completed assistant message output items."""
    _require_api_key(api_key)
    instructions, inputs = _request_messages(messages)
    if not inputs:
        raise ValueError("Add a user message before starting a reply.")
    body: dict[str, object] = {
        "model": model, "input": inputs, "stream": True, "store": False, "max_output_tokens": 2048,
    }
    if instructions:
        body["instructions"] = instructions
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "text/event-stream",
               "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10), transport=transport) as client:
        try:
            async with client.stream("POST", f"{base_url.rstrip('/')}/responses", headers=headers, json=body) as response:
                if response.status_code >= 400:
                    raise UpstreamError(response.status_code)
                created = False
                completed = False
                saw_text = False
                refused = False
                items: dict[int, dict] = {}
                content_parts: dict[tuple[int, int], str] = {}
                streamed_message_ids: set[str] = set()
                async for event_name, payload in _anthropic_events(response):
                    if event_name == "response.created":
                        response_obj = payload.get("response")
                        if created or not isinstance(response_obj, dict) or response_obj.get("status") != "in_progress":
                            raise StreamProtocolError("The OpenAI response stream was malformed.")
                        created = True
                    elif event_name in {"response.in_progress", "response.queued"}:
                        if not created or not isinstance(payload.get("response"), dict):
                            raise StreamProtocolError("The OpenAI response stream was malformed.")
                    elif event_name == "response.output_item.added":
                        item, index = payload.get("item"), payload.get("output_index")
                        if (not created or not isinstance(index, int) or index < 0 or index in items
                                or not isinstance(item, dict) or not isinstance(item.get("id"), str)
                                or not isinstance(item.get("type"), str)):
                            raise StreamProtocolError("The OpenAI response stream was malformed.")
                        items[index] = {"id": item["id"], "type": item["type"], "role": item.get("role")}
                        if item["type"] == "message" and item.get("role") != "assistant":
                            raise StreamProtocolError("The OpenAI response stream was malformed.")
                    elif event_name == "response.content_part.added":
                        index, content_index = payload.get("output_index"), payload.get("content_index")
                        item_id, part = payload.get("item_id"), payload.get("part")
                        item = items.get(index) if isinstance(index, int) else None
                        if (not created or item is None or item["type"] != "message" or item["id"] != item_id
                                or not isinstance(content_index, int) or content_index < 0 or not isinstance(part, dict)
                                or not isinstance(part.get("type"), str)):
                            raise StreamProtocolError("The OpenAI response stream was malformed.")
                        content_parts[(index, content_index)] = part["type"]
                        if part["type"] == "refusal":
                            refused = True
                    elif event_name == "response.output_text.delta":
                        index, content_index = payload.get("output_index"), payload.get("content_index")
                        item_id, delta = payload.get("item_id"), payload.get("delta")
                        item = items.get(index) if isinstance(index, int) else None
                        if (not created or item is None or item["type"] != "message" or item["id"] != item_id
                                or not isinstance(content_index, int)
                                or content_parts.get((index, content_index)) != "output_text"
                                or not isinstance(delta, str)):
                            raise StreamProtocolError("The OpenAI response stream was malformed.")
                        if delta:
                            saw_text = True
                            streamed_message_ids.add(item["id"])
                            yield delta
                    elif event_name == "response.refusal.delta":
                        index, item_id = payload.get("output_index"), payload.get("item_id")
                        item = items.get(index) if isinstance(index, int) else None
                        if item is None or item["type"] != "message" or item["id"] != item_id or not isinstance(payload.get("delta"), str):
                            raise StreamProtocolError("The OpenAI response stream was malformed.")
                        refused = True
                    elif event_name == "response.output_item.done":
                        index, item = payload.get("output_index"), payload.get("item")
                        known = items.get(index) if isinstance(index, int) else None
                        if (known is None or not isinstance(item, dict) or item.get("id") != known["id"]
                                or item.get("type") != known["type"]):
                            raise StreamProtocolError("The OpenAI response stream was malformed.")
                        if _is_tool_item(item.get("type")):
                            raise CompletionStoppedError("tool_calls")
                        if item.get("type") == "message":
                            if item.get("role") != "assistant" or not isinstance(item.get("content"), list):
                                raise StreamProtocolError("The OpenAI response stream was malformed.")
                            if any(isinstance(part, dict) and part.get("type") == "refusal" for part in item["content"]):
                                refused = True
                    elif event_name == "response.incomplete":
                        response_obj = payload.get("response")
                        if not created or not isinstance(response_obj, dict) or response_obj.get("status") != "incomplete":
                            raise StreamProtocolError("The OpenAI response stream was malformed.")
                        details = response_obj.get("incomplete_details")
                        reason = details.get("reason") if isinstance(details, dict) else None
                        raise CompletionStoppedError("length" if reason == "max_output_tokens" else "other")
                    elif event_name == "response.failed" or event_name == "error":
                        raise StreamProtocolError("The OpenAI response stream reported an error.")
                    elif event_name == "response.completed":
                        response_obj = payload.get("response")
                        output = response_obj.get("output") if isinstance(response_obj, dict) else None
                        if (not created or completed or not isinstance(response_obj, dict)
                                or response_obj.get("status") != "completed" or not isinstance(output, list)):
                            raise StreamProtocolError("The OpenAI response stream was malformed.")
                        if refused:
                            raise CompletionStoppedError("content_filter")
                        completed_text_message_ids: set[str] = set()
                        for item in output:
                            if not isinstance(item, dict):
                                raise StreamProtocolError("The OpenAI response stream was malformed.")
                            if _is_tool_item(item.get("type")):
                                raise CompletionStoppedError("tool_calls")
                            if item.get("type") == "message":
                                item_id = item.get("id")
                                status = item.get("status")
                                content = item.get("content")
                                if (not isinstance(item_id, str) or not item_id or item.get("role") != "assistant"
                                        or not isinstance(content, list)
                                        or (status is not None and status != "completed")):
                                    raise StreamProtocolError("The OpenAI response stream was malformed.")
                                if any(isinstance(part, dict) and part.get("type") == "refusal" for part in content):
                                    raise CompletionStoppedError("content_filter")
                                if any(isinstance(part, dict) and part.get("type") == "output_text" for part in content):
                                    completed_text_message_ids.add(item_id)
                        if not streamed_message_ids.issubset(completed_text_message_ids):
                            raise StreamProtocolError("The OpenAI response did not complete a streamed text message.")
                        completed = True
                        if not saw_text:
                            raise StreamProtocolError("The OpenAI response completed without assistant text.")
                        return
                    # Other named events, including reasoning and non-text modalities, carry no output here.
                if not completed:
                    raise StreamProtocolError("The OpenAI response stream ended before completion.")
        except httpx.RequestError:
            raise StreamProtocolError("The OpenAI model service could not be reached.") from None
