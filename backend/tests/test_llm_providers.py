"""Offline adapter tests for local model-service protocols."""
import asyncio
import json

import httpx
import pytest

import api.converse as converse
from llm.client import CompletionStoppedError, StreamProtocolError, list_models, normalize_base_url, stream_chat


def test_provider_base_url_normalization_and_defaults():
    assert normalize_base_url("http://localhost:11434/proxy/ollama", "ollama") == "http://localhost:11434/proxy/ollama"
    assert normalize_base_url("http://localhost:5001/proxy/kobold", "koboldcpp") == "http://localhost:5001/proxy/kobold/v1"
    assert normalize_base_url("http://localhost:1234", "lmstudio") == "http://localhost:1234/v1"
    assert normalize_base_url("https://generativelanguage.googleapis.com/v1beta/openai/", "openai_compatible") == \
        "https://generativelanguage.googleapis.com/v1beta/openai"
    assert converse._endpoint(None, "ollama") == "http://localhost:11434"
    assert converse._endpoint(None, "koboldcpp") == "http://localhost:5001/v1"
    for url in ("http://user:pw@host", "http://host/?token=x", "http://host/a/%2e%2e/private"):
        with pytest.raises(ValueError):
            normalize_base_url(url, "ollama")


def test_google_openai_compatibility_root_routes_models_and_chat_exactly():
    seen = []
    async def handler(request):
        seen.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "gemini-test"}]})
        body = (b'data: {"choices":[{"delta":{"content":"Hello"},"finish_reason":null}]}\n\n'
                b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n')
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    base = normalize_base_url("https://generativelanguage.googleapis.com/v1beta/openai/", "openai_compatible")
    async def run():
        models = await list_models(base, "request-key", transport=httpx.MockTransport(handler))
        chunks = [chunk async for chunk in stream_chat(
            base, "gemini-test", [{"role": "user", "content": "Hi"}], "request-key",
            transport=httpx.MockTransport(handler))]
        return models, chunks
    models, chunks = asyncio.run(run())
    assert models == [{"id": "gemini-test"}]
    assert chunks == ["Hello"]
    assert seen == [("GET", "/v1beta/openai/models"), ("POST", "/v1beta/openai/chat/completions")]


def test_connection_reports_configured_saved_default_provider(monkeypatch):
    settings = {"llm_default_backend": "kobold", "kobold_url": "http://kobold.example/proxy"}
    monkeypatch.setattr(converse, "get_setting", lambda key, default=None: settings.get(key, default))
    response = asyncio.run(converse.connection())
    assert response == {
        "provider": "koboldcpp",
        "default_endpoint": "http://kobold.example/proxy/v1",
        "base_url": "http://kobold.example/proxy/v1",
    }


def test_request_models_default_to_legacy_provider():
    assert converse.ModelsRequest().provider == "openai_compatible"
    assert converse.MessageRequest(text="hello", model="local").provider == "openai_compatible"
    assert converse.SceneRequest(model="local").provider == "openai_compatible"
    with pytest.raises(ValueError):
        converse.MessageRequest(text="hello", model="local", provider="unsupported")


def test_api_routing_passes_native_provider_only_when_selected(monkeypatch):
    calls = []

    async def fake_list(url, key=None, *, provider="legacy"):
        calls.append((url, key, provider))
        return [{"id": "native-model"}]

    async def fake_stream(url, model, messages, key=None, *, provider="legacy"):
        calls.append((url, model, messages, key, provider))
        yield "ok"

    monkeypatch.setattr(converse, "list_models", fake_list)
    monkeypatch.setattr(converse, "stream_chat", fake_stream)

    async def run():
        models = await converse.models(converse.ModelsRequest(provider="ollama"))
        chunks = [part async for part in converse._stream_for_provider(
            "http://localhost:11434", "local-model", [], "temporary-key", "ollama")]
        legacy_chunks = [part async for part in converse._stream_for_provider(
            "http://localhost:1234/v1", "legacy-model", [], None, "openai_compatible")]
        return models, chunks, legacy_chunks

    models, chunks, legacy_chunks = asyncio.run(run())
    assert models == {"models": [{"id": "native-model"}]}
    assert calls[0] == ("http://localhost:11434", None, "ollama")
    assert calls[1][-1] == "ollama" and calls[1][-2] == "temporary-key"
    assert calls[2][-1] == "legacy"
    assert chunks == legacy_chunks == ["ok"]


def test_ollama_model_listing_uses_native_tags_api_and_names():
    seen = []

    async def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"models": [{"name": "llama3.2:latest"}, {"name": "qwen:7b"}]})

    async def run():
        return await list_models("http://ollama.example/proxy", provider="ollama",
                                 transport=httpx.MockTransport(handler))
    assert asyncio.run(run()) == [{"id": "llama3.2:latest"}, {"id": "qwen:7b"}]
    assert str(seen[0].url) == "http://ollama.example/proxy/api/tags"


def test_ollama_ndjson_chat_requires_done_and_sanitizes_stream_errors():
    requests = []

    async def handler(request):
        requests.append(request)
        body = b'{"message":{"role":"assistant","content":"Hello"},"done":false}\n' \
               b'{"message":{"content":" world"},"done":false}\n' \
               b'{"done":true,"total_duration":10}\n'
        return httpx.Response(200, content=body, headers={"content-type": "application/x-ndjson"})

    async def run():
        return [part async for part in stream_chat("http://ollama.example/prefix", "llama3", [],
                                                    "request-only-secret", transport=httpx.MockTransport(handler),
                                                    provider="ollama")]
    assert asyncio.run(run()) == ["Hello", " world"]
    assert requests[0].url.path == "/prefix/api/chat"
    assert requests[0].headers["authorization"] == "Bearer request-only-secret"

    async def error_handler(_request):
        return httpx.Response(200, content=b'{"error":"secret model path"}\n')

    async def incomplete_handler(_request):
        return httpx.Response(200, content=b'{"message":{"content":"partial"},"done":false}\n')

    async def run_error(handler):
        return [part async for part in stream_chat("http://ollama.example", "m", [],
                                                    transport=httpx.MockTransport(handler), provider="ollama")]
    for handler in (error_handler, incomplete_handler):
        with pytest.raises(StreamProtocolError) as caught:
            asyncio.run(run_error(handler))
        assert "secret model path" not in str(caught.value)


@pytest.mark.parametrize("finish_reason", ["length", "content_filter", "tool_calls", "function_call", "unexpected"])
def test_chat_completions_non_success_finish_reasons_preserve_partial_chunks(finish_reason):
    body = (b'data: {"choices":[{"delta":{"content":"partial reply"},"finish_reason":null}]}\n\n'
            + f'data: {{"choices":[{{"delta":{{}},"finish_reason":"{finish_reason}"}}]}}\n\n'.encode())

    async def handler(_request):
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    async def run():
        received = []
        with pytest.raises(CompletionStoppedError) as caught:
            async for chunk in stream_chat("http://openai.example/v1", "model", [],
                                           transport=httpx.MockTransport(handler)):
                received.append(chunk)
        return received, caught.value

    received, stopped = asyncio.run(run())
    assert received == ["partial reply"]
    assert stopped.reason == (finish_reason if finish_reason in {"length", "content_filter", "tool_calls", "function_call"} else "other")
    assert isinstance(stopped.message, str) and stopped.message
    if finish_reason == "length":
        assert "output-token limit" in stopped.message
    assert finish_reason not in stopped.message


def test_kobold_model_list_fallback_only_for_404():
    seen = []
    statuses = [404, 503]

    async def fallback_handler(request):
        seen.append(request.url.path)
        if request.url.path.endswith("/v1/models"):
            return httpx.Response(404)
        return httpx.Response(200, json={"result": "Kobold loaded model"})

    async def run_fallback():
        return await list_models("http://kobold.example/proxy/v1", provider="koboldcpp",
                                 transport=httpx.MockTransport(fallback_handler))
    assert asyncio.run(run_fallback()) == [{"id": "Kobold loaded model"}]
    assert seen == ["/proxy/v1/models", "/proxy/api/v1/model"]

    async def unavailable_handler(request):
        seen.append(request.url.path)
        return httpx.Response(503)

    async def run_unavailable():
        return await list_models("http://kobold.example/proxy/v1", provider="koboldcpp",
                                 transport=httpx.MockTransport(unavailable_handler))
    with pytest.raises(Exception) as caught:
        asyncio.run(run_unavailable())
    assert getattr(caught.value, "status_code", None) == 503
    assert seen[-1] == "/proxy/v1/models"
