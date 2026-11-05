from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from llm.client import (CompletionStoppedError, StreamProtocolError, UpstreamError,
                        list_models, normalize_base_url, stream_chat)


def _event(name: str, payload: dict) -> bytes:
    return f"event: {name}\ndata: {json.dumps(payload)}\n\n".encode()


def _successful_stream(text: str = "Hello") -> bytes:
    return b"".join((
        _event("message_start", {"type": "message_start", "message": {"content": []}}),
        _event("content_block_start", {"type": "content_block_start", "index": 0,
                                        "content_block": {"type": "text", "text": ""}}),
        _event("content_block_delta", {"type": "content_block_delta", "index": 0,
                                       "delta": {"type": "text_delta", "text": text}}),
        _event("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _event("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}),
        _event("message_stop", {"type": "message_stop"}),
    ))


def test_anthropic_base_url_uses_safe_v1_path_and_preserves_proxy_prefix():
    assert normalize_base_url("https://api.anthropic.com", "anthropic") == "https://api.anthropic.com/v1"
    assert normalize_base_url("https://proxy.example/claude/v1/", "anthropic") == "https://proxy.example/claude/v1"
    assert normalize_base_url("https://proxy.example/v1beta/openai", "anthropic") == \
        "https://proxy.example/v1beta/openai/v1"


@pytest.mark.asyncio
async def test_native_messages_headers_payload_path_and_text_stream():
    seen = []

    async def handler(request):
        seen.append(request)
        return httpx.Response(200, content=_successful_stream(), headers={"content-type": "text/event-stream"})

    chunks = [chunk async for chunk in stream_chat(
        "https://proxy.example/prefix/v1", "claude-test",
        [{"role": "system", "content": "system one"}, {"role": "user", "content": "hello"},
         {"role": "assistant", "content": "hi"}, {"role": "system", "content": "system two"},
         {"role": "tool", "content": "drop"}, {"role": "user", "content": [
             {"type": "image", "source": {}}, {"type": "text", "text": "again"}]}],
        "secret-key", transport=httpx.MockTransport(handler), provider="anthropic")]

    request = seen[0]
    body = json.loads(request.content)
    assert request.url.path == "/prefix/v1/messages"
    assert request.headers["x-api-key"] == "secret-key"
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in request.headers
    assert body == {"model": "claude-test", "max_tokens": 2048, "stream": True,
                    "system": "system one\nsystem two",
                    "messages": [{"role": "user", "content": "hello"},
                                 {"role": "assistant", "content": "hi"},
                                 {"role": "user", "content": "again"}]}
    assert chunks == ["Hello"]


@pytest.mark.asyncio
async def test_models_use_native_bounded_endpoint_and_safe_ids():
    seen = []

    async def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"data": [{"id": "claude-a"}, {"id": ""}, {"id": "bad\nmodel"},
                                                {"id": "x" * 201}], "has_more": True})

    models = await list_models("https://api.anthropic.com/v1", "secret", httpx.MockTransport(handler),
                               provider="anthropic")
    assert models == [{"id": "claude-a"}]
    assert seen[0].url.path == "/v1/models"
    assert str(seen[0].url.params) == "limit=100"
    assert seen[0].headers["x-api-key"] == "secret"
    assert "authorization" not in seen[0].headers


@pytest.mark.asyncio
async def test_missing_key_fails_before_contact_for_models_and_chat():
    async def handler(_request):
        pytest.fail("request must not be sent without a key")

    transport = httpx.MockTransport(handler)
    with pytest.raises(ValueError, match="API key"):
        await list_models("https://api.anthropic.com/v1", transport=transport, provider="anthropic")
    with pytest.raises(ValueError, match="API key"):
        async for _ in stream_chat("https://api.anthropic.com/v1", "m", [{"role": "user", "content": "hi"}],
                                   transport=transport, provider="anthropic"):
            pass


@pytest.mark.parametrize("invalid_key", ["key with space", "key\nvalue", "bad-\u2603", "\tkey"])
@pytest.mark.asyncio
async def test_invalid_header_keys_fail_safely_before_contact(invalid_key):
    async def handler(_request):
        pytest.fail("request must not be sent with an invalid header value")

    transport = httpx.MockTransport(handler)
    with pytest.raises(ValueError) as caught:
        await list_models("https://api.anthropic.com/v1", invalid_key, transport, provider="anthropic")
    assert invalid_key not in str(caught.value)
    with pytest.raises(ValueError) as caught:
        async for _ in stream_chat("https://api.anthropic.com/v1", "m", [{"role": "user", "content": "hi"}],
                                   invalid_key, transport, provider="anthropic"):
            pass
    assert invalid_key not in str(caught.value)


@pytest.mark.parametrize(("stop_reason", "expected"), [
    ("max_tokens", "length"), ("tool_use", "tool_calls"), ("refusal", "other"), ("pause_turn", "other"),
    ("unknown-provider-reason", "other"),
])
@pytest.mark.asyncio
async def test_non_success_reasons_never_complete_successfully(stop_reason, expected):
    body = b"".join((
        _event("message_start", {"type": "message_start", "message": {"content": []}}),
        _event("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop_reason}}),
        _event("message_stop", {"type": "message_stop"}),
    ))

    async def handler(_request):
        return httpx.Response(200, content=body)

    with pytest.raises(CompletionStoppedError) as caught:
        async for _ in stream_chat("https://api.anthropic.com/v1", "m", [{"role": "user", "content": "hi"}],
                                   "key", httpx.MockTransport(handler), provider="anthropic"):
            pass
    assert caught.value.reason == expected


@pytest.mark.asyncio
async def test_non_text_blocks_never_leak_thinking_signatures_or_tool_input():
    body = b"".join((
        _event("message_start", {"type": "message_start", "message": {"content": []}}),
        _event("content_block_start", {"type": "content_block_start", "index": 0,
                                        "content_block": {"type": "thinking", "thinking": ""}}),
        _event("content_block_delta", {"type": "content_block_delta", "index": 0,
                                       "delta": {"type": "thinking_delta", "thinking": "secret thoughts"}}),
        _event("content_block_delta", {"type": "content_block_delta", "index": 0,
                                       "delta": {"type": "signature_delta", "signature": "secret signature"}}),
        _event("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _event("content_block_start", {"type": "content_block_start", "index": 1,
                                        "content_block": {"type": "tool_use", "input": {}}}),
        _event("content_block_delta", {"type": "content_block_delta", "index": 1,
                                       "delta": {"type": "input_json_delta", "partial_json": "secret tool args"}}),
        _event("content_block_stop", {"type": "content_block_stop", "index": 1}),
        _event("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"}}),
        _event("message_stop", {"type": "message_stop"}),
    ))

    async def handler(_request):
        return httpx.Response(200, content=body)

    received = []
    with pytest.raises(CompletionStoppedError):
        async for text in stream_chat("https://api.anthropic.com/v1", "m", [{"role": "user", "content": "hi"}],
                                      "key", httpx.MockTransport(handler), provider="anthropic"):
            received.append(text)
    assert received == []


@pytest.mark.asyncio
async def test_multiline_sse_data_is_parsed_and_incomplete_or_malformed_streams_fail():
    valid = (b"event: message_start\ndata: {\ndata: \"type\":\"message_start\",\ndata: \"message\":{\"content\":[]}}\n\n"
             + _event("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}})
             + _event("message_stop", {"type": "message_stop"}))
    malformed = b"event: message_start\ndata: nope\n\n"
    incomplete = _event("message_start", {"type": "message_start", "message": {"content": []}})

    async def run(body):
        async def handler(_request):
            return httpx.Response(200, content=body)
        return [chunk async for chunk in stream_chat("https://api.anthropic.com/v1", "m",
                [{"role": "user", "content": "hi"}], "key", httpx.MockTransport(handler), provider="anthropic")]

    assert await run(valid) == []
    for body in (malformed, incomplete):
        with pytest.raises(StreamProtocolError):
            await run(body)


@pytest.mark.asyncio
async def test_http_and_stream_errors_are_redacted():
    async def http_error(_request):
        return httpx.Response(401, text="secret api key echoed")

    with pytest.raises(UpstreamError) as caught:
        async for _ in stream_chat("https://api.anthropic.com/v1", "m", [{"role": "user", "content": "hi"}],
                                   "secret", httpx.MockTransport(http_error), provider="anthropic"):
            pass
    assert "secret api key" not in str(caught.value)

    async def stream_error(_request):
        return httpx.Response(200, content=_event("error", {"type": "error", "error": {"message": "secret body"}}))

    with pytest.raises(StreamProtocolError) as caught:
        async for _ in stream_chat("https://api.anthropic.com/v1", "m", [{"role": "user", "content": "hi"}],
                                   "key", httpx.MockTransport(stream_error), provider="anthropic"):
            pass
    assert "secret body" not in str(caught.value)


@pytest.mark.asyncio
async def test_closing_outer_generator_closes_native_stream():
    closed = asyncio.Event()

    class BlockingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield (_event("message_start", {"type": "message_start", "message": {"content": []}})
                   + _event("content_block_start", {"type": "content_block_start", "index": 0,
                                                     "content_block": {"type": "text", "text": ""}})
                   + _event("content_block_delta", {"type": "content_block_delta", "index": 0,
                                                     "delta": {"type": "text_delta", "text": "chunk"}}))
            await asyncio.Event().wait()

        async def aclose(self):
            closed.set()

    async def handler(_request):
        return httpx.Response(200, stream=BlockingStream())

    stream = stream_chat("https://api.anthropic.com/v1", "m", [{"role": "user", "content": "hi"}],
                         "key", httpx.MockTransport(handler), provider="anthropic")
    assert await asyncio.wait_for(anext(stream), 1) == "chunk"
    await stream.aclose()
    assert closed.is_set()
