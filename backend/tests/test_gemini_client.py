from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from llm.client import CompletionStoppedError, StreamProtocolError, UpstreamError
from llm.gemini import list_models, stream_chat


def _sse(payload: dict, *, multiline: bool = False) -> bytes:
    raw = json.dumps(payload, indent=2 if multiline else None,
                     separators=None if multiline else (",", ":"))
    if multiline:
        data = "\n".join(f"data: {line}" for line in raw.splitlines())
    else:
        data = f"data: {raw}"
    return (data + "\n\n").encode()


def _chunk(text: str | None = None, *, finish: str | None = None, parts=None) -> dict:
    content_parts = list(parts) if parts is not None else ([{"text": text}] if text is not None else [])
    candidate = {"index": 0, "content": {"role": "model", "parts": content_parts}}
    if finish is not None:
        candidate["finishReason"] = finish
    return {"candidates": [candidate]}


def _input_messages():
    return [
        {"role": "system", "content": "system one"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "system", "content": "system two"},
        {"role": "tool", "content": "drop"},
        {"role": "user", "content": [{"type": "image", "source": {}}, {"type": "text", "text": "again"}]},
    ]


@pytest.mark.asyncio
async def test_stream_native_path_headers_payload_role_mapping_and_text():
    seen = []

    async def handler(request):
        seen.append(request)
        return httpx.Response(200, content=b"".join((
            _sse(_chunk("Hello "), multiline=True),
            _sse(_chunk("world", finish="STOP")),
        )), headers={"content-type": "text/event-stream"})

    result = [text async for text in stream_chat(
        "https://proxy.example/prefix/v1beta", "models/gemini-test", _input_messages(), "fixture-key",
        httpx.MockTransport(handler))]
    request = seen[0]
    body = json.loads(request.content)
    assert request.method == "POST"
    assert request.url.path == "/prefix/v1beta/models/gemini-test:streamGenerateContent"
    assert dict(request.url.params) == {"alt": "sse"}
    assert request.headers["x-goog-api-key"] == "fixture-key"
    assert "authorization" not in request.headers
    assert "key" not in request.url.params
    assert body == {
        "systemInstruction": {"parts": [{"text": "system one\nsystem two"}]},
        "contents": [
            {"role": "user", "parts": [{"text": "hello"}]},
            {"role": "model", "parts": [{"text": "hi"}]},
            {"role": "user", "parts": [{"text": "again"}]},
        ],
        "generationConfig": {"maxOutputTokens": 2048, "candidateCount": 1},
    }
    assert result == ["Hello ", "world"]


@pytest.mark.asyncio
async def test_models_filters_supported_methods_caps_and_uses_single_page():
    seen = []
    values = [
        {"name": "models/gemini-a", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/embedding", "supportedGenerationMethods": ["embedContent"]},
        {"name": "models/unsafe/path", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-b", "supportedGenerationMethods": ["generateContent"]},
    ] + [{"name": f"models/gemini-{index}", "supportedGenerationMethods": ["generateContent"]}
         for index in range(120)]

    async def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"models": values, "nextPageToken": "do-not-follow"})

    models = await list_models("https://generativelanguage.googleapis.com/v1beta", "fixture-key",
                               httpx.MockTransport(handler))
    assert models[:2] == [{"id": "models/gemini-a"}, {"id": "models/gemini-b"}]
    assert len(models) == 100
    assert len(seen) == 1
    assert seen[0].url.path == "/v1beta/models"
    assert dict(seen[0].url.params) == {"pageSize": "100"}
    assert seen[0].headers["x-goog-api-key"] == "fixture-key"
    assert "key" not in seen[0].url.params


@pytest.mark.asyncio
@pytest.mark.parametrize("call", ["models", "stream"])
@pytest.mark.parametrize("key", [None, "", "fixture key", "bad\nkey", "é" * 10, "x" * 4097])
async def test_invalid_keys_fail_before_contact(call, key):
    contacted = False

    async def handler(request):
        nonlocal contacted
        contacted = True
        return httpx.Response(200, json={"models": []})

    transport = httpx.MockTransport(handler)
    with pytest.raises(ValueError, match="Gemini API key"):
        if call == "models":
            await list_models("https://example.test/v1beta", key, transport)
        else:
            _ = [chunk async for chunk in stream_chat(
                "https://example.test/v1beta", "models/gemini-test", [{"role": "user", "content": "hi"}],
                key, transport)]
    assert contacted is False


@pytest.mark.asyncio
async def test_model_name_traversal_fails_before_contact():
    contacted = False

    async def handler(request):
        nonlocal contacted
        contacted = True
        return httpx.Response(200)

    with pytest.raises(ValueError, match="Gemini model"):
        _ = [chunk async for chunk in stream_chat(
            "https://example.test/v1beta", "models/../../admin", [{"role": "user", "content": "hi"}],
            "fixture-key", httpx.MockTransport(handler))]
    assert contacted is False


@pytest.mark.asyncio
@pytest.mark.parametrize("reason,expected", [
    ("MAX_TOKENS", "length"), ("SAFETY", "content_filter"), ("RECITATION", "content_filter"),
    ("MALFORMED_FUNCTION_CALL", "tool_calls"), ("OTHER_REASON", "other"),
])
async def test_non_success_finish_reasons_fail_safely(reason, expected):
    async def handler(request):
        return httpx.Response(200, content=_sse(_chunk("partial", finish=reason)),
                              headers={"content-type": "text/event-stream"})

    chunks = []
    with pytest.raises(CompletionStoppedError) as error:
        async for chunk in stream_chat("https://example.test/v1beta", "models/gemini-test",
                                       [{"role": "user", "content": "hi"}], "fixture-key",
                                       httpx.MockTransport(handler)):
            chunks.append(chunk)
    assert error.value.reason == expected
    assert error.value.message


@pytest.mark.asyncio
async def test_thought_multimodal_and_tool_parts_are_never_yielded():
    async def thought_handler(request):
        return httpx.Response(200, content=_sse(_chunk(parts=[
            {"text": "private thought", "thought": True}, {"text": "public"},
            {"inlineData": {"mimeType": "text/plain", "data": "hidden"}},
        ], finish="STOP")), headers={"content-type": "text/event-stream"})

    yielded = [chunk async for chunk in stream_chat("https://example.test/v1beta", "models/gemini-test",
                                                   [{"role": "user", "content": "hi"}], "fixture-key",
                                                   httpx.MockTransport(thought_handler))]
    assert yielded == ["public"]

    async def tool_handler(request):
        return httpx.Response(200, content=_sse(_chunk(parts=[{"functionCall": {"name": "go"}}], finish="STOP")),
                              headers={"content-type": "text/event-stream"})

    with pytest.raises(CompletionStoppedError) as error:
        _ = [chunk async for chunk in stream_chat("https://example.test/v1beta", "models/gemini-test",
                                                  [{"role": "user", "content": "hi"}], "fixture-key",
                                                  httpx.MockTransport(tool_handler))]
    assert error.value.reason == "tool_calls"


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"candidates": [{"index": 0, "content": {"role": "model", "parts": [{"text": "text", "thought": "true"}]}, "finishReason": "STOP"}]},
    {"candidates": [{"index": 0, "content": {"role": "model", "parts": [{"text": 7}]}, "finishReason": "STOP"}]},
    {"candidates": [{"index": 1, "content": {"role": "model", "parts": [{"text": "text"}]}, "finishReason": "STOP"}]},
    {"candidates": [{"index": 0, "content": {"role": "user", "parts": [{"text": "text"}]}, "finishReason": "STOP"}]},
])
async def test_malformed_chunk_fields_fail_safely(payload):
    async def handler(request):
        return httpx.Response(200, content=_sse(payload), headers={"content-type": "text/event-stream"})

    with pytest.raises(StreamProtocolError):
        _ = [chunk async for chunk in stream_chat("https://example.test/v1beta", "models/gemini-test",
                                                  [{"role": "user", "content": "hi"}], "fixture-key",
                                                  httpx.MockTransport(handler))]


@pytest.mark.asyncio
@pytest.mark.parametrize("parts", [
    [{"text": "private thought", "thought": True}],
    [{"inlineData": {"mimeType": "audio/wav", "data": "hidden"}}],
    [{"text": ""}],
    [],
])
async def test_stop_without_public_text_is_not_success(parts):
    async def handler(request):
        return httpx.Response(200, content=_sse(_chunk(parts=parts, finish="STOP")),
                              headers={"content-type": "text/event-stream"})

    with pytest.raises(StreamProtocolError):
        _ = [chunk async for chunk in stream_chat("https://example.test/v1beta", "models/gemini-test",
                                                  [{"role": "user", "content": "hi"}], "fixture-key",
                                                  httpx.MockTransport(handler))]


@pytest.mark.asyncio
async def test_prompt_safety_block_is_safe_completion_error():
    async def handler(request):
        return httpx.Response(200, content=_sse({"promptFeedback": {"blockReason": "SAFETY"}}),
                              headers={"content-type": "text/event-stream"})

    with pytest.raises(CompletionStoppedError) as error:
        _ = [chunk async for chunk in stream_chat("https://example.test/v1beta", "models/gemini-test",
                                                  [{"role": "user", "content": "hi"}], "fixture-key",
                                                  httpx.MockTransport(handler))]
    assert error.value.reason == "content_filter"


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [b"data: {bad json}\n\n", b"data: []\n\n", b"data: {}\n\n"])
async def test_malformed_or_premature_stream_fails_safely(payload):
    async def handler(request):
        return httpx.Response(200, content=payload, headers={"content-type": "text/event-stream"})

    with pytest.raises(StreamProtocolError):
        _ = [chunk async for chunk in stream_chat("https://example.test/v1beta", "models/gemini-test",
                                                  [{"role": "user", "content": "hi"}], "fixture-key",
                                                  httpx.MockTransport(handler))]


@pytest.mark.asyncio
async def test_http_error_is_redacted():
    async def handler(request):
        return httpx.Response(403, text="echo fixture-key and private prompt")

    with pytest.raises(UpstreamError) as error:
        await list_models("https://example.test/v1beta", "fixture-key", httpx.MockTransport(handler))
    assert str(error.value) == "Upstream returned HTTP 403"
    assert "fixture-key" not in str(error.value)


@pytest.mark.asyncio
async def test_cancelling_stream_closes_response():
    closed = False

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield _sse(_chunk("first"))
            await asyncio.Event().wait()

        async def aclose(self):
            nonlocal closed
            closed = True

    async def handler(request):
        return httpx.Response(200, stream=SlowStream(), headers={"content-type": "text/event-stream"})

    stream = stream_chat("https://example.test/v1beta", "models/gemini-test",
                         [{"role": "user", "content": "hi"}], "fixture-key", httpx.MockTransport(handler))
    assert await anext(stream) == "first"
    await stream.aclose()
    assert closed is True
