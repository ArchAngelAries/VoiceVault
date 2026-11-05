"""Chat memory: long chats stay coherent inside a small context, summaries and facts, chat delete and search."""
import asyncio
import json

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.converse as converse
from rag import memory as mem


@pytest.fixture
def store(tmp_path):
    return mem.MemoryStore(tmp_path / "memory.sqlite3")


def _chat(n, planted_at=3):
    msgs = []
    for i in range(n):
        if i == planted_at:
            msgs.append({"role": "user", "content": "By the way, my dog is called Biscuit and she is a beagle."})
        elif i % 2 == 0:
            msgs.append({"role": "user", "content": f"Let's keep talking about the weather on day {i}. " * 3})
        else:
            msgs.append({"role": "assistant", "content": f"Sure, the weather on day {i} was mild and grey. " * 3})
    return msgs


def test_long_chat_fits_budget_and_recalls_an_early_detail(store):
    msgs = _chat(300)
    store.index_session("s1", msgs)
    history = [{"role": m["role"], "content": m["content"]} for m in msgs]
    out, info = asyncio.run(mem.build_messages(store, "s1", "You are helpful.", history, list(range(300)),
                                               "What is my dog's name again?", 2048))
    total = sum(mem.est_tokens(m["content"]) for m in out)
    assert total <= 2048, total
    assert info["older_messages"] > 250 and info["recent_messages"] >= 2
    assert "Biscuit" in out[0]["content"] and info["snippets"] >= 1
    assert out[-1]["content"] == msgs[-1]["content"]          # the newest turn is always sent verbatim


def test_short_chat_is_sent_as_is(store):
    msgs = _chat(6)
    history = [{"role": m["role"], "content": m["content"]} for m in msgs]
    out, info = asyncio.run(mem.build_messages(store, "s2", "Sys", history, list(range(6)), "hi", 8192))
    assert out[0]["content"] == "Sys" and out[1:] == history and info["older_messages"] == 0


def test_facts_scopes_dedupe_edit_delete(store):
    a = store.add_fact("The user lives in Oslo.", "user")
    assert store.add_fact("The user lives in Oslo", "user") is None            # near-duplicate ignored
    c = store.add_fact("Mira is afraid of heights.", "character", character="Mira")
    store.add_fact("They are travelling to the coast.", "chat", session_id="s1")
    assert {f["text"] for f in store.facts("s1", "mira")} == {"The user lives in Oslo.", "Mira is afraid of heights.",
                                                              "They are travelling to the coast."}
    assert [f["text"] for f in store.facts("s2", "Other")] == ["The user lives in Oslo."]
    assert store.update_fact(c["id"], text="Mira fears heights.", pinned=True)["pinned"] == 1
    assert store.delete_fact(a["id"]) and not store.delete_fact(a["id"])
    store.forget_session("s1")
    assert [f["text"] for f in store.facts("s1", "Mira")] == ["Mira fears heights."]   # character facts stay


def test_summaries_and_facts_from_the_chat_model(store):
    msgs = _chat(60)
    span = mem.needs_summary(store, "s1", msgs)
    assert span == (0, mem.SUMMARY_BLOCK - 1)

    async def fake_complete(prompt):
        assert "Conversation part" in prompt[1]["content"]
        return 'Sure! {"summary": "They chatted about the weather; the user mentioned their beagle Biscuit.",' \
               ' "facts": [{"text": "The user has a beagle called Biscuit.", "about": "user"},' \
               ' {"text": "Mira prefers grey skies.", "about": "character"}]}'
    item = asyncio.run(mem.summarise(store, "s1", msgs, span, fake_complete, character="Mira"))
    assert item and "Biscuit" in item["text"]
    assert store.covered_until("s1") == mem.SUMMARY_BLOCK
    texts = {f["text"]: f["scope"] for f in store.facts("s1", "Mira")}
    assert texts == {"The user has a beagle called Biscuit.": "user", "Mira prefers grey skies.": "character"}
    history = [{"role": m["role"], "content": m["content"]} for m in msgs]
    out, info = asyncio.run(mem.build_messages(store, "s1", "Sys", history, list(range(60)), "hello", 1500,
                                               characters=["Mira"]))
    assert info["summaries"] == 1 and info["facts"] == 2 and "Earlier in this conversation" in out[0]["content"]


def test_meaning_search_is_used_when_available(store):
    msgs = _chat(200)
    store.index_session("s1", msgs)
    calls = []

    async def embedder(texts):
        calls.append(len(texts))
        return "fake-embed", [np.array([1.0, 0.0]) if "dog" in t.lower() or "biscuit" in t.lower() else np.array([0.0, 1.0])
                              for t in texts]
    history = [{"role": m["role"], "content": m["content"]} for m in msgs]
    out, info = asyncio.run(mem.build_messages(store, "s1", "Sys", history, list(range(200)),
                                               "tell me about my pet dog", 2048, embedder=embedder))
    assert info["search"] == "keyword + meaning" and "Biscuit" in out[0]["content"] and calls


def test_parse_summary_tolerates_extra_text():
    s, f = mem.parse_summary('```json\n{"summary": "A.", "facts": [{"text": "B", "about": "chat"}, 3]}\n```')
    assert s == "A." and f == [{"text": "B", "about": "chat"}]
    assert mem.parse_summary("just prose")[0] == "just prose"


# ── API ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(converse, "STORE_DIR", tmp_path / "conversations")
    monkeypatch.setattr(converse, "_active_turns", set())
    monkeypatch.setattr(converse, "_embedder", lambda *a: None)
    app = FastAPI()
    app.include_router(converse.router, prefix="/api/converse")
    return TestClient(app)


def _send(session_id, text, monkeypatch, reply="Noted.", captured=None, **extra):
    async def model(base_url, model, messages, api_key=None, **kw):
        if captured is not None:
            captured.append(messages)
        yield reply
    monkeypatch.setattr(converse, "stream_chat", model)

    async def run():
        response = await converse.send_message(session_id, converse.MessageRequest(text=text, model="local", **extra))
        return [p async for p in response.body_iterator]
    return asyncio.run(run())


def test_chat_goes_past_the_old_limit_and_sends_memory(client, monkeypatch):
    session = client.post("/api/converse/sessions", json={"kind": "chat"}).json()
    sid = session["id"]
    data = json.loads(converse._session_path(sid).read_text())
    data["messages"] = _chat(140)
    converse._save(data)
    captured = []
    packets = _send(sid, "What is my dog's name?", monkeypatch, captured=captured, context_tokens=2048)
    done = next(p for p in packets if b"event: done" in p)
    payload = json.loads(done.decode().split("data: ", 1)[1])
    assert payload["memory"]["older_messages"] > 100 and payload["memory"]["snippets"] >= 1
    sent = captured[0]
    assert "Biscuit" in sent[0]["content"] and sum(mem.est_tokens(m["content"]) for m in sent) <= 2048
    assert len(client.get(f"/api/converse/sessions/{sid}").json()["messages"]) == 142


def test_delete_search_and_memory_endpoints(client, monkeypatch):
    a = client.post("/api/converse/sessions", json={"kind": "chat", "title": "Dog chat"}).json()
    b = client.post("/api/converse/sessions", json={"kind": "chat", "title": "Other"}).json()
    _send(b["id"], "I planted tomatoes in the greenhouse today.", monkeypatch, reply="Lovely tomatoes!")
    found = client.get("/api/converse/sessions", params={"q": "tomatoes"}).json()["sessions"]
    assert [s["id"] for s in found] == [b["id"]] and found[0]["matches"]
    assert [s["id"] for s in client.get("/api/converse/sessions", params={"q": "dog"}).json()["sessions"]] == [a["id"]]
    m = client.post(f"/api/converse/sessions/{b['id']}/memory", json={"text": "The user grows tomatoes.", "scope": "user"})
    assert m.status_code == 200
    assert client.post(f"/api/converse/sessions/{b['id']}/memory", json={"text": "The user grows tomatoes",
                                                                          "scope": "user"}).status_code == 409
    view = client.get(f"/api/converse/sessions/{b['id']}/memory").json()
    assert [f["text"] for f in view["facts"]] == ["The user grows tomatoes."] and view["messages"] == 2
    fid = m.json()["id"]
    assert client.patch(f"/api/converse/memory/{fid}", json={"text": "The user grows cherry tomatoes."}).json()["text"].startswith("The user grows cherry")
    assert client.delete(f"/api/converse/memory/{fid}").status_code == 200
    assert client.delete(f"/api/converse/sessions/{b['id']}").status_code == 200
    assert client.get(f"/api/converse/sessions/{b['id']}").status_code == 404
    assert client.get("/api/converse/sessions", params={"q": "tomatoes"}).json()["sessions"] == []
    assert client.delete(f"/api/converse/sessions/{b['id']}").status_code == 404


def test_background_summary_runs_after_long_chats(client, monkeypatch):
    session = client.post("/api/converse/sessions", json={"kind": "chat"}).json()
    sid = session["id"]
    data = json.loads(converse._session_path(sid).read_text())
    data["messages"] = _chat(60)
    converse._save(data)
    prompts = []

    async def model(base_url, model, messages, api_key=None, **kw):
        prompts.append(messages)
        if "long-term memory" in messages[0]["content"]:
            yield '{"summary": "Weather talk; the user has a beagle named Biscuit.", "facts": [{"text": "The user has a beagle named Biscuit.", "about": "user"}]}'
        else:
            yield "Okay."
    monkeypatch.setattr(converse, "stream_chat", model)

    async def run():
        response = await converse.send_message(sid, converse.MessageRequest(text="Hi again", model="local"))
        _ = [p async for p in response.body_iterator]
        await asyncio.gather(*list(converse._background))
    asyncio.run(run())
    view = client.get(f"/api/converse/sessions/{sid}/memory").json()
    assert view["summaries"] and "Biscuit" in view["summaries"][0]["text"]
    assert any("Biscuit" in f["text"] for f in view["facts"])


# ── Scale: thousands of turns, chapters, other chats, paging ────────────────

def test_summaries_roll_up_into_chapters(store):
    for i in range(mem.CHAPTER_EVERY + 3):
        store.add_summary("s1", i * 24, i * 24 + 23, f"Part {i}: they talked about topic {i}.")

    async def chapter(prompt):
        return "Chapter: the early parts covered topics 0 to 5 and a promise to visit Oslo."
    item = asyncio.run(mem.consolidate(store, "s1", chapter))
    assert item and item["level"] == 2 and item["from_idx"] == 0
    active = store.summaries("s1", active_only=True)
    assert active[0]["level"] == 2 and len(active) == 4         # one chapter + the 3 newest detailed parts
    assert asyncio.run(mem.consolidate(store, "s1", chapter)) is None   # not enough new parts yet


def test_three_thousand_turns_stay_within_budget_and_recall(store):
    msgs = _chat(3000, planted_at=41)
    store.index_session("big", msgs)
    for i in range(0, 2900, 24):
        store.add_summary("big", i, i + 23, f"Weather chat block {i}.")
    history = [{"role": m["role"], "content": m["content"]} for m in msgs]
    import time as _t
    t = _t.time()
    out, info = asyncio.run(mem.build_messages(store, "big", "Sys", history, list(range(3000)),
                                               "remind me what my dog is called", 4096))
    assert _t.time() - t < 3.0
    assert sum(mem.est_tokens(m["content"]) for m in out) <= 4096
    assert "Biscuit" in out[0]["content"] and info["older_messages"] > 2900


def test_persona_recalls_other_chats(store):
    store.index_session("old", [{"role": "user", "content": "Mira, remember that our song is Moonlit Harbor."},
                                {"role": "assistant", "content": "Always. Moonlit Harbor is ours.", "speaker_name": "Mira"}],
                        ["Mira"])
    store.index_session("other", [{"role": "user", "content": "Our song is Neon Rain."}], ["Zed"])
    store.index_session("new", [{"role": "user", "content": "Hi again"}], ["Mira"])
    assert store.related_sessions("new", ["Mira"]) == ["old"]
    history = [{"role": "user", "content": "Hi again"}, {"role": "user", "content": "What is our song again?"}]
    out, info = asyncio.run(mem.build_messages(store, "new", "Sys", history, [0, 1], "what is our song again", 4096,
                                               characters=["Mira"]))
    assert "Moonlit Harbor" in out[0]["content"] and "Neon Rain" not in out[0]["content"]
    assert info["other_chats"] >= 1
    found = mem.recall(store, "song", character="Mira")
    assert {m["chat"] for m in found["messages"]} == {"old"}


def test_vectors_backfill_in_background(store):
    msgs = _chat(300)
    store.index_session("v", msgs)

    async def embedder(texts):
        return "fake", [np.array([1.0, float(len(t) % 7)]) for t in texts]
    added = asyncio.run(mem.backfill_embeddings(store, "v", embedder, batch=100, max_batches=5))
    assert added == 300 and not store.missing_embeddings("v", 10)
    idxs, mat = store.vectors("v", "fake")
    assert len(idxs) == 300 and mat.shape == (300, 2)


def test_api_pages_long_chats_and_searches_memory(client, monkeypatch):
    session = client.post("/api/converse/sessions", json={"kind": "chat", "title": "Long"}).json()
    sid = session["id"]
    data = json.loads(converse._session_path(sid).read_text())
    data["messages"] = _chat(1200)
    converse._save(data)
    got = client.get(f"/api/converse/sessions/{sid}").json()
    assert len(got["messages"]) == converse.WIRE_TAIL and got["messages_total"] == 1200
    assert got["messages_offset"] == 1200 - converse.WIRE_TAIL
    page = client.get(f"/api/converse/sessions/{sid}/messages", params={"before": got["messages_offset"], "limit": 200}).json()
    assert len(page["messages"]) == 200 and page["offset"] == got["messages_offset"] - 200
    listed = client.get("/api/converse/sessions").json()["sessions"][0]
    assert listed["messages"] == [] and listed["messages_total"] == 1200
    packets = _send(sid, "Is my dog still called Biscuit?", monkeypatch, context_tokens=4096)
    done = json.loads(next(p for p in packets if b"event: done" in p).decode().split("data: ", 1)[1])
    assert len(done["session"]["messages"]) == converse.WIRE_TAIL and done["session"]["messages_total"] == 1202
    found = client.get("/api/converse/memory/search", params={"q": "beagle"}).json()
    assert any("Biscuit" in m["text"] for m in found["messages"])


def _lmstudio_transport(states, calls):
    import httpx

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/v0/models":
            return httpx.Response(200, json={"data": [
                {"id": "chat-model", "type": "llm", "state": "loaded"},
                {"id": "text-embedding-nomic", "type": "embeddings", "state": states["embed"]}]})
        if request.url.path == "/v1/embeddings":
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def test_lmstudio_embedder_never_loads_an_unloaded_embedding_model():
    import asyncio
    calls = []
    embed = mem.openai_embedder("http://localhost:1234/v1", transport=_lmstudio_transport({"embed": "not-loaded"}, calls))
    assert asyncio.run(embed(["hi"])) is None
    assert "/v1/embeddings" not in calls     # asking would make LM Studio swap out the chat model


def test_lmstudio_embedder_uses_a_loaded_embedding_model():
    import asyncio
    calls = []
    embed = mem.openai_embedder("http://localhost:1234/v1", transport=_lmstudio_transport({"embed": "loaded"}, calls))
    model, vectors = asyncio.run(embed(["hi"]))
    assert model == "text-embedding-nomic" and len(vectors) == 1


def test_other_openai_servers_use_any_listed_embedding_model():
    import asyncio
    import httpx

    def handler(request):
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "my-embed-model"}]})
        if request.url.path == "/v1/embeddings":
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})
        return httpx.Response(404)
    embed = mem.openai_embedder("http://localhost:8080/v1", transport=httpx.MockTransport(handler))
    assert asyncio.run(embed(["x"]))[0] == "my-embed-model"
