"""HTTPX contract and safe streaming behavior for the native Responses API client."""
import asyncio
import json

import httpx
import pytest

from llm.client import CompletionStoppedError, StreamProtocolError, UpstreamError
from llm import openai_native


def _sse(event, payload):
    return f"event: {event}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n".encode()


def _created():
    return _sse("response.created", {"type": "response.created", "response": {"status": "in_progress", "id": "resp_1"}})


def _message_started(index=0, item_id="msg_1", part_type="output_text"):
    return b"".join((
        _sse("response.output_item.added", {"type": "response.output_item.added", "output_index": index,
                                               "item": {"id": item_id, "type": "message", "role": "assistant"}}),
        _sse("response.content_part.added", {"type": "response.content_part.added", "output_index": index,
                                                "item_id": item_id, "content_index": 0,
                                                "part": {"type": part_type, "text": ""}}),
    ))


def _completed(output=None, status="completed", **extra):
    return _sse("response.completed", {"type": "response.completed", "response": {
        "status": status, "output": output if output is not None else [
            {"id": "msg_1", "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Hello"}]}
        ], **extra,
    }})


def _run(coro):
    return asyncio.run(coro)


async def _texts(stream):
    return [text async for text in stream]


def test_list_models_uses_bearer_auth_limit_and_filters_safe_ids():
    captured = []

    async def handler(request):
        captured.append(request)
        rows = [{"id": f"model-{i}"} for i in range(100)]
        rows.insert(1, {"id": "bad model"})
        rows.append({"id": "\nsecret"})
        rows.append({"id": "model-100"})
        return httpx.Response(200, json={"data": rows})

    result = _run(openai_native.list_models("https://proxy.example/openai/v1/", "sk-test",
                                             httpx.MockTransport(handler)))

    assert len(result) == 100
    assert result[0] == {"id": "model-0"}
    assert result[-1] == {"id": "model-99"}
    assert captured[0].url.path == "/openai/v1/models"
    assert str(captured[0].url.params) == ""
    assert captured[0].headers["authorization"] == "Bearer sk-test"
    assert captured[0].headers["accept"] == "application/json"


@pytest.mark.parametrize("key", [None, "", "  ", "key with spaces", "line\nbreak", "snowman-\u2603", "k" * 4097])
def test_invalid_api_key_fails_before_any_request(key):
    async def handler(_request):
        pytest.fail("invalid API key must be rejected before network I/O")

    with pytest.raises(ValueError, match="valid OpenAI API key") as error:
        _run(openai_native.list_models("https://api.openai.com/v1", key, httpx.MockTransport(handler)))
    assert not key or key not in str(error.value)


def test_stream_posts_responses_payload_and_yields_only_assistant_output_text():
    captured = []
    output = b"".join((
        _created(),
        _sse("response.output_item.added", {"type": "response.output_item.added", "output_index": 0,
                                               "item": {"id": "reason_1", "type": "reasoning"}}),
        _sse("response.reasoning_summary_text.delta", {"type": "response.reasoning_summary_text.delta", "delta": "private reasoning"}),
        _sse("response.output_item.added", {"type": "response.output_item.added", "output_index": 1,
                                               "item": {"id": "msg_1", "type": "message", "role": "assistant"}}),
        _sse("response.content_part.added", {"type": "response.content_part.added", "output_index": 1,
                                                "item_id": "msg_1", "content_index": 0,
                                                "part": {"type": "output_text", "text": ""}}),
        _sse("response.output_text.delta", {"type": "response.output_text.delta", "output_index": 1,
                                               "item_id": "msg_1", "content_index": 0, "delta": "Hello"}),
        _sse("response.output_item.done", {"type": "response.output_item.done", "output_index": 1,
                                              "item": {"id": "msg_1", "type": "message", "role": "assistant",
                                                       "content": [{"type": "output_text", "text": "Hello"}]}}),
        _completed(output=[{"id": "reason_1", "type": "reasoning", "summary": [{"type": "summary_text", "text": "private reasoning"}]},
                           {"id": "msg_1", "type": "message", "role": "assistant",
                            "content": [{"type": "output_text", "text": "Hello"}]}]),
    ))

    async def handler(request):
        captured.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=output)

    values = _run(_texts(openai_native.stream_chat("https://proxy.example/prefix/v1/", "gpt-test", [
        {"role": "system", "content": "System instruction"}, {"role": "user", "content": "First"},
        {"role": "assistant", "content": "Earlier answer"}, {"role": "tool", "content": "discard"},
    ], "sk-test", httpx.MockTransport(handler))))

    assert values == ["Hello"]
    request = captured[0]
    assert request.url.path == "/prefix/v1/responses"
    assert request.headers["authorization"] == "Bearer sk-test"
    body = json.loads(request.content)
    assert body == {
        "model": "gpt-test", "instructions": "System instruction",
        "input": [{"role": "user", "content": "First"}, {"role": "assistant", "content": "Earlier answer"}],
        "stream": True, "store": False, "max_output_tokens": 2048,
    }
    assert "private reasoning" not in "".join(values)


def test_text_delta_for_non_output_text_content_part_is_rejected_without_emission():
    events = _created() + _message_started(part_type="audio") + _sse("response.output_text.delta", {
        "type": "response.output_text.delta", "output_index": 0, "item_id": "msg_1", "content_index": 0,
        "delta": "must not leak"})

    async def handler(_request):
        return httpx.Response(200, content=events)

    async def run():
        streamed = []
        with pytest.raises(StreamProtocolError):
            async for text in openai_native.stream_chat("https://api.openai.com/v1", "gpt-test",
                    [{"role": "user", "content": "hello"}], "sk-test", httpx.MockTransport(handler)):
                streamed.append(text)
        return streamed

    assert _run(run()) == []


def test_completed_response_must_contain_the_exact_streamed_assistant_message():
    events = _created() + _message_started() + _sse("response.output_text.delta", {
        "type": "response.output_text.delta", "output_index": 0, "item_id": "msg_1", "content_index": 0,
        "delta": "partial"}) + _completed(output=[{
        "id": "msg_2", "type": "message", "role": "assistant", "status": "completed",
        "content": [{"type": "output_text", "text": "different message"}],
    }])

    async def handler(_request):
        return httpx.Response(200, content=events)

    async def run():
        streamed = []
        with pytest.raises(StreamProtocolError):
            async for text in openai_native.stream_chat("https://api.openai.com/v1", "gpt-test",
                    [{"role": "user", "content": "hello"}], "sk-test", httpx.MockTransport(handler)):
                streamed.append(text)
        return streamed

    assert _run(run()) == ["partial"]


@pytest.mark.parametrize(("events", "expected_reason"), [
    (_created() + _message_started() + _sse("response.output_text.delta", {
        "type": "response.output_text.delta", "output_index": 0, "item_id": "msg_1", "content_index": 0,
        "delta": "partial"}) + _sse("response.output_item.added", {
        "type": "response.output_item.added", "output_index": 1,
        "item": {"id": "call_1", "type": "function_call", "name": "do_thing", "arguments": "{}"}}) +
        _sse("response.output_item.done", {"type": "response.output_item.done", "output_index": 1,
                                             "item": {"id": "call_1", "type": "function_call", "name": "do_thing", "arguments": "{}"}}), "tool_calls"),
    (_created() + _message_started(part_type="refusal") + _sse("response.refusal.delta", {
        "type": "response.refusal.delta", "output_index": 0, "item_id": "msg_1", "content_index": 0,
        "delta": "I cannot help with that"}) + _completed(output=[{
        "id": "msg_1", "type": "message", "role": "assistant", "content": [{"type": "refusal", "refusal": "no"}]}]), "content_filter"),
    (_created() + _sse("response.incomplete", {"type": "response.incomplete", "response": {
        "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}}), "length"),
])
def test_known_nonsuccess_terminal_states_raise_safe_completion_errors(events, expected_reason):
    async def handler(_request):
        return httpx.Response(200, content=events)

    async def run():
        result = []
        with pytest.raises(CompletionStoppedError) as caught:
            async for text in openai_native.stream_chat("https://api.openai.com/v1", "gpt-test",
                    [{"role": "user", "content": "hello"}], "sk-test", httpx.MockTransport(handler)):
                result.append(text)
        return result, caught.value

    streamed, error = _run(run())
    assert error.reason == expected_reason
    assert "do_thing" not in error.message and "cannot help" not in error.message
    if expected_reason == "tool_calls":
        assert streamed == ["partial"]  # Any already streamed text is assistant text; tool data stays hidden.


@pytest.mark.parametrize("events", [
    _created() + _sse("response.failed", {"type": "response.failed", "response": {
        "status": "failed", "error": {"message": "secret upstream detail"}}}),
    _created() + _sse("error", {"type": "error", "message": "secret upstream detail"}),
    _created() + _message_started() + _sse("response.output_text.delta", {
        "type": "response.output_text.delta", "output_index": 0, "item_id": "msg_1", "content_index": 0,
        "delta": "partial"}),  # EOF before completed
    b"event: response.created\ndata: {bad json}\n\n",
])
def test_failed_malformed_or_premature_streams_raise_redacted_protocol_error(events):
    async def handler(_request):
        return httpx.Response(200, content=events)

    with pytest.raises(StreamProtocolError) as caught:
        _run(_texts(openai_native.stream_chat("https://api.openai.com/v1", "gpt-test",
                    [{"role": "user", "content": "hello"}], "sk-test", httpx.MockTransport(handler))))
    assert "secret upstream detail" not in str(caught.value)
    assert "bad json" not in str(caught.value)


def test_http_errors_do_not_echo_provider_response_body():
    async def handler(_request):
        return httpx.Response(401, text="secret upstream key echo")

    with pytest.raises(UpstreamError) as caught:
        _run(openai_native.list_models("https://api.openai.com/v1", "sk-test", httpx.MockTransport(handler)))
    assert caught.value.status_code == 401
    assert "secret upstream" not in str(caught.value)


def test_closing_stream_closes_underlying_http_response():
    class BlockingBody(httpx.AsyncByteStream):
        def __init__(self):
            self.closed = False
            self.started = asyncio.Event()

        async def __aiter__(self):
            yield _created()
            yield _message_started()
            yield _sse("response.output_text.delta", {"type": "response.output_text.delta", "output_index": 0,
                                                        "item_id": "msg_1", "content_index": 0, "delta": "hi"})
            self.started.set()
            await asyncio.Event().wait()

        async def aclose(self):
            self.closed = True

    body = BlockingBody()

    async def handler(_request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body)

    async def run():
        iterator = openai_native.stream_chat("https://api.openai.com/v1", "gpt-test",
                    [{"role": "user", "content": "hello"}], "sk-test", httpx.MockTransport(handler))
        first = await anext(iterator)
        await iterator.aclose()
        return first

    assert _run(run()) == "hi"
    assert body.closed is True
