"""
rag/memory.py — Long-term memory for chats, so long conversations stay coherent within any model's context.

Each turn the prompt is built to fit the model's context size:
    system prompt
  + "What you remember": pinned memories and facts (about the user, the character, this chat)
  + "Earlier in this conversation": rolling summaries of turns that no longer fit
  + "Relevant earlier moments": the older messages that best match the new message
  + the most recent turns, verbatim
Nothing is ever cut from the saved chat; only what is *sent* is chosen.

Storage: one SQLite file (`<data>/memory/memory.sqlite3`).
  messages  every chat message, with full-text search (FTS5, always available)
  summaries rolling summaries of older message ranges, written by the chat's own model
  facts     durable facts and pinned memories, scoped to a chat, a character, or the user
Meaning-based search is added when the model server offers an embedding model (for example
LM Studio's nomic-embed); keyword search works without it.

Built for chats of many thousands of turns:
  - summaries roll up: every few summaries become one "chapter", so the whole history fits;
  - meaning vectors are added a few per turn and back-filled in the background, cached in memory;
  - facts are chosen by relevance to the new message (pinned ones always);
  - a persona's chat can recall its other chats (same character name), and agents can search all of it.
"""
from __future__ import annotations

import asyncio
import difflib
import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import numpy as np

log = logging.getLogger(__name__)

SUMMARY_BLOCK = 24          # messages summarised per background pass
SUMMARY_TRIGGER = 12        # summarise once this many older messages are not yet covered
MAX_FACTS_IN_PROMPT = 40
MAX_SNIPPETS = 6
MAX_CROSS_SNIPPETS = 4
CHAPTER_EVERY = 6           # this many unconsumed summaries roll up into one chapter
EMBED_PER_TURN = 48         # newest missing vectors computed inline; the rest in the background
SCOPES = ("chat", "character", "user")


def est_tokens(text: str) -> int:
    """Rough token count (about 3.6 characters per token for English, plus message overhead)."""
    return int(len(text or "") / 3.6) + 4


def character_key(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()[:80]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ── Store ───────────────────────────────────────────────────────────────────

class MemoryStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS messages(
                session_id TEXT NOT NULL, idx INTEGER NOT NULL, role TEXT, speaker TEXT, text TEXT NOT NULL,
                digest TEXT, embedding BLOB, embed_model TEXT, PRIMARY KEY(session_id, idx));
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(text, session_id UNINDEXED, idx UNINDEXED);
            CREATE TABLE IF NOT EXISTS summaries(
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL, from_idx INTEGER, to_idx INTEGER, text TEXT, created TEXT);
            CREATE TABLE IF NOT EXISTS facts(
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, session_id TEXT, character TEXT, text TEXT NOT NULL,
                pinned INTEGER DEFAULT 0, source TEXT, created TEXT, updated TEXT);
            CREATE INDEX IF NOT EXISTS facts_scope ON facts(scope, session_id, character);
            CREATE INDEX IF NOT EXISTS summaries_session ON summaries(session_id, to_idx);
            CREATE TABLE IF NOT EXISTS session_chars(session_id TEXT NOT NULL, character TEXT NOT NULL,
                PRIMARY KEY(session_id, character));
            CREATE INDEX IF NOT EXISTS session_chars_char ON session_chars(character);
            """)
            cols = {r[1] for r in db.execute("PRAGMA table_info(summaries)")}
            if "level" not in cols:
                db.execute("ALTER TABLE summaries ADD COLUMN level INTEGER DEFAULT 1")
            if "consumed" not in cols:
                db.execute("ALTER TABLE summaries ADD COLUMN consumed INTEGER DEFAULT 0")
        self._vectors: dict[str, tuple[str, np.ndarray, np.ndarray]] = {}   # session -> (model, idxs, unit rows)

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    # Messages ------------------------------------------------------------
    def index_session(self, session_id: str, messages: list[dict], characters: Optional[list[str]] = None) -> int:
        """Add or update every message of a chat in the search index. Returns how many changed."""
        changed = 0
        with self._lock, self._db() as db:
            for c in characters or []:
                key = character_key(c)
                if key:
                    db.execute("INSERT OR IGNORE INTO session_chars VALUES(?,?)", (session_id, key))
            known = {r["idx"]: r["digest"] for r in db.execute("SELECT idx, digest FROM messages WHERE session_id=?", (session_id,))}
            for i, m in enumerate(messages):
                text = m.get("content")
                if m.get("role") not in ("user", "assistant") or not isinstance(text, str) or not text.strip():
                    continue
                digest = f"{len(text)}:{hash(text) & 0xffffffff:x}"
                if known.get(i) == digest:
                    continue
                speaker = "user" if m["role"] == "user" else (m.get("speaker_name") or "assistant")
                db.execute("INSERT OR REPLACE INTO messages(session_id, idx, role, speaker, text, digest, embedding, embed_model)"
                           " VALUES(?,?,?,?,?,?,NULL,NULL)", (session_id, i, m["role"], speaker, text, digest))
                db.execute("DELETE FROM messages_fts WHERE session_id=? AND idx=?", (session_id, i))
                db.execute("INSERT INTO messages_fts(text, session_id, idx) VALUES(?,?,?)", (f"{speaker}: {text}", session_id, i))
                changed += 1
            stale = [i for i in known if i >= len(messages)]
            for i in stale:
                db.execute("DELETE FROM messages WHERE session_id=? AND idx=?", (session_id, i))
                db.execute("DELETE FROM messages_fts WHERE session_id=? AND idx=?", (session_id, i))
        if changed and stale:
            self._vectors.pop(session_id, None)
        return changed

    def search(self, query: str, session_id: Optional[str] = None, before_idx: Optional[int] = None,
               limit: int = 20, session_ids: Optional[list[str]] = None) -> list[dict]:
        text = re.sub(r"['’]s\b", "", (query or "").lower())
        terms = list(dict.fromkeys(t for t in re.findall(r"[a-z0-9]{2,}", text) if t not in _STOP))[:24]
        if not terms:
            return []
        match = " OR ".join(f"{t}*" for t in terms)   # prefix match: dog → dogs, remember → remembered
        sql = ("SELECT m.session_id, m.idx, m.speaker, m.text, bm25(messages_fts) AS rank FROM messages_fts "
               "JOIN messages m ON m.session_id = messages_fts.session_id AND m.idx = messages_fts.idx "
               "WHERE messages_fts MATCH ?")
        args: list[Any] = [match]
        if session_id:
            sql += " AND messages_fts.session_id = ?"
            args.append(session_id)
        if session_ids is not None:
            if not session_ids:
                return []
            sql += f" AND messages_fts.session_id IN ({','.join('?' * len(session_ids))})"
            args.extend(session_ids)
        if before_idx is not None:
            sql += " AND m.idx < ?"
            args.append(before_idx)
        sql += " ORDER BY rank LIMIT ?"
        args.append(limit)
        with self._lock, self._db() as db:
            try:
                return [dict(r) for r in db.execute(sql, args)]
            except sqlite3.OperationalError:
                return []

    def message_texts(self, session_id: str, idxs: list[int]) -> list[dict]:
        if not idxs:
            return []
        with self._lock, self._db() as db:
            q = ",".join("?" * len(idxs))
            return [dict(r) for r in db.execute(f"SELECT idx, speaker, text, embedding, embed_model FROM messages "
                                                f"WHERE session_id=? AND idx IN ({q})", [session_id, *idxs])]

    def set_embeddings(self, session_id: str, rows: list[tuple[int, np.ndarray]], model: str) -> None:
        with self._lock, self._db() as db:
            for idx, vec in rows:
                db.execute("UPDATE messages SET embedding=?, embed_model=? WHERE session_id=? AND idx=?",
                           (np.asarray(vec, np.float32).tobytes(), model, session_id, idx))
        self._vectors.pop(session_id, None)

    def missing_embeddings(self, session_id: str, limit: int, newest_first: bool = True) -> list[dict]:
        order = "DESC" if newest_first else "ASC"
        with self._lock, self._db() as db:
            return [dict(r) for r in db.execute(
                f"SELECT idx, speaker, text FROM messages WHERE session_id=? AND embedding IS NULL ORDER BY idx {order} LIMIT ?",
                (session_id, limit))]

    def vectors(self, session_id: str, model: str) -> tuple[np.ndarray, np.ndarray]:
        """(message indices, unit vectors) of every embedded message, cached in memory."""
        cached = self._vectors.get(session_id)
        if cached and cached[0] == model:
            return cached[1], cached[2]
        with self._lock, self._db() as db:
            rows = db.execute("SELECT idx, embedding FROM messages WHERE session_id=? AND embed_model=? "
                              "AND embedding IS NOT NULL ORDER BY idx", (session_id, model)).fetchall()
        if not rows:
            return np.zeros(0, np.int64), np.zeros((0, 1), np.float32)
        idxs = np.array([r[0] for r in rows], np.int64)
        mat = np.stack([np.frombuffer(r[1], np.float32) for r in rows])
        mat = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
        self._vectors[session_id] = (model, idxs, mat)
        return idxs, mat

    def related_sessions(self, session_id: str, characters: list[str]) -> list[str]:
        """Other chats that share one of these character names (a persona's earlier chats)."""
        keys = [character_key(c) for c in characters if character_key(c)]
        if not keys:
            return []
        with self._lock, self._db() as db:
            rows = db.execute(f"SELECT DISTINCT session_id FROM session_chars WHERE character IN "
                              f"({','.join('?' * len(keys))}) AND session_id != ?", [*keys, session_id])
            return [r[0] for r in rows]

    def all_message_rows(self, session_id: str, before_idx: int) -> list[dict]:
        with self._lock, self._db() as db:
            return [dict(r) for r in db.execute("SELECT idx, speaker, text, embedding, embed_model FROM messages "
                                                "WHERE session_id=? AND idx<? ORDER BY idx", (session_id, before_idx))]

    # Summaries -----------------------------------------------------------
    def summaries(self, session_id: str, active_only: bool = False) -> list[dict]:
        """Summaries in order. active_only: chapters plus summaries not yet rolled into a chapter."""
        sql = "SELECT * FROM summaries WHERE session_id=?"
        if active_only:
            sql += " AND COALESCE(consumed,0)=0"
        with self._lock, self._db() as db:
            return [dict(r) for r in db.execute(sql + " ORDER BY from_idx, level DESC", (session_id,))]

    def summaries_for(self, session_ids: list[str], query: str, limit: int = 3) -> list[dict]:
        """Best-matching summaries from other chats, by shared words (small sets, scored in Python)."""
        if not session_ids:
            return []
        terms = set(t for t in re.findall(r"[a-z0-9]{3,}", (query or "").lower()) if t not in _STOP)
        if not terms:
            return []
        with self._lock, self._db() as db:
            rows = [dict(r) for r in db.execute(
                f"SELECT * FROM summaries WHERE COALESCE(consumed,0)=0 AND session_id IN ({','.join('?' * len(session_ids))})",
                session_ids)]
        scored = [(len(terms & set(re.findall(r"[a-z0-9]{3,}", r["text"].lower()))), r) for r in rows]
        return [r for score, r in sorted(scored, key=lambda x: -x[0]) if score > 0][:limit]

    def covered_until(self, session_id: str) -> int:
        """Index after the last summarised message (0 when nothing is summarised)."""
        with self._lock, self._db() as db:
            row = db.execute("SELECT MAX(to_idx) FROM summaries WHERE session_id=?", (session_id,)).fetchone()
        return (row[0] + 1) if row and row[0] is not None else 0

    def add_summary(self, session_id: str, from_idx: int, to_idx: int, text: str, level: int = 1) -> dict:
        item = {"id": uuid.uuid4().hex[:12], "session_id": session_id, "from_idx": from_idx, "to_idx": to_idx,
                "text": text.strip(), "created": _now(), "level": level, "consumed": 0}
        with self._lock, self._db() as db:
            db.execute("INSERT INTO summaries(id, session_id, from_idx, to_idx, text, created, level, consumed) "
                       "VALUES(:id,:session_id,:from_idx,:to_idx,:text,:created,:level,:consumed)", item)
        return item

    def mark_consumed(self, ids: list[str]) -> None:
        with self._lock, self._db() as db:
            db.executemany("UPDATE summaries SET consumed=1 WHERE id=?", [(i,) for i in ids])

    # Facts and pinned memories ------------------------------------------
    def facts(self, session_id: Optional[str] = None, character: Optional[str] = None,
              include_user: bool = True) -> list[dict]:
        clauses, args = [], []
        if session_id:
            clauses.append("(scope='chat' AND session_id=?)")
            args.append(session_id)
        if character:
            clauses.append("(scope='character' AND character=?)")
            args.append(character_key(character))
        if include_user:
            clauses.append("scope='user'")
        if not clauses:
            return []
        with self._lock, self._db() as db:
            rows = db.execute(f"SELECT * FROM facts WHERE {' OR '.join(clauses)} ORDER BY pinned DESC, updated DESC", args)
            return [dict(r) for r in rows]

    def add_fact(self, text: str, scope: str, session_id: Optional[str] = None, character: Optional[str] = None,
                 pinned: bool = False, source: str = "user") -> Optional[dict]:
        text = re.sub(r"\s+", " ", (text or "")).strip()[:600]
        if not text or scope not in SCOPES:
            return None
        ckey = character_key(character) if scope == "character" else None
        if scope == "character" and not ckey:
            return None
        sid = session_id if scope == "chat" else None
        for existing in self.facts(sid, ckey if scope == "character" else None, include_user=scope == "user"):
            if existing["scope"] == scope and difflib.SequenceMatcher(None, existing["text"].lower(), text.lower()).ratio() > 0.88:
                if pinned and not existing["pinned"]:
                    self.update_fact(existing["id"], pinned=True)
                return None  # already known
        item = {"id": uuid.uuid4().hex[:12], "scope": scope, "session_id": sid, "character": ckey, "text": text,
                "pinned": int(pinned), "source": source, "created": _now(), "updated": _now()}
        with self._lock, self._db() as db:
            db.execute("INSERT INTO facts VALUES(:id,:scope,:session_id,:character,:text,:pinned,:source,:created,:updated)", item)
        return item

    def update_fact(self, fact_id: str, text: Optional[str] = None, pinned: Optional[bool] = None) -> Optional[dict]:
        with self._lock, self._db() as db:
            row = db.execute("SELECT * FROM facts WHERE id=?", (fact_id,)).fetchone()
            if row is None:
                return None
            item = dict(row)
            if text is not None and text.strip():
                item["text"] = re.sub(r"\s+", " ", text).strip()[:600]
            if pinned is not None:
                item["pinned"] = int(pinned)
            item["updated"] = _now()
            db.execute("UPDATE facts SET text=?, pinned=?, updated=? WHERE id=?", (item["text"], item["pinned"], item["updated"], fact_id))
            return item

    def delete_fact(self, fact_id: str) -> bool:
        with self._lock, self._db() as db:
            return db.execute("DELETE FROM facts WHERE id=?", (fact_id,)).rowcount > 0

    def forget_session(self, session_id: str) -> None:
        """Remove everything tied to one chat. Character and user facts stay."""
        with self._lock, self._db() as db:
            db.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            db.execute("DELETE FROM messages_fts WHERE session_id=?", (session_id,))
            db.execute("DELETE FROM summaries WHERE session_id=?", (session_id,))
            db.execute("DELETE FROM facts WHERE scope='chat' AND session_id=?", (session_id,))
            db.execute("DELETE FROM session_chars WHERE session_id=?", (session_id,))
        self._vectors.pop(session_id, None)

    def search_sessions(self, query: str, limit: int = 50) -> dict[str, list[dict]]:
        """Chats whose messages match, with the best snippets of each."""
        out: dict[str, list[dict]] = {}
        for row in self.search(query, limit=limit * 3):
            out.setdefault(row["session_id"], [])
            if len(out[row["session_id"]]) < 3:
                out[row["session_id"]].append({"idx": row["idx"], "speaker": row["speaker"], "text": row["text"][:240]})
        return out


_STOP = set("the a an and or but of to in on at for with is are was were be been it this that you your i me my we "
            "our they them he she his her its as by from so do does did not no yes what who how why when where "
            "can could would should will just like have has had there here then than".split())


# ── Embeddings (optional) ───────────────────────────────────────────────────

Embedder = Callable[[list[str]], Awaitable[Optional[tuple[str, list[np.ndarray]]]]]


def openai_embedder(base_url: str, api_key: Optional[str] = None, timeout: float = 20, transport=None) -> Embedder:
    """Embeddings from an OpenAI-compatible server (LM Studio). Picks a model with 'embed' in its name.

    LM Studio keeps one auto-loaded model by default, so asking for an embedding model that is not
    loaded would unload the chat model mid-conversation (the next reply then fails or reloads it).
    When the server is LM Studio, only an embedding model that is already loaded is used; otherwise
    memory falls back to keyword search. Other servers list only what they serve, so any match is used.
    """
    import time

    import httpx

    state: dict[str, Any] = {}
    api = base_url.rstrip("/")
    root = api[:-3] if api.endswith("/v1") else api

    async def pick(client, headers) -> Optional[str]:
        if state.get("checked", 0) > time.monotonic() - 30:
            return state.get("model")
        model = None
        try:
            r = await client.get(f"{root}/api/v0/models", headers=headers)   # LM Studio only: has load state
            rows = r.json().get("data") if r.status_code == 200 else None
        except Exception:
            rows = None
        if isinstance(rows, list) and rows and all(isinstance(m, dict) and "state" in m for m in rows):
            model = next((m.get("id") for m in rows if m.get("state") == "loaded"
                          and (m.get("type") == "embeddings" or "embed" in str(m.get("id", "")).lower())), None)
        else:
            try:
                r = await client.get(f"{api}/models", headers=headers)
                ids = [m.get("id", "") for m in r.json().get("data", [])]
                model = next((i for i in ids if "embed" in i.lower()), None)
            except Exception:
                model = None
        state.update(model=model, checked=time.monotonic())
        return model

    async def embed(texts: list[str]):
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            model = await pick(client, headers)
            if not model:
                return None
            r = await client.post(f"{api}/embeddings", headers=headers,
                                  json={"model": model, "input": [t[:2000] for t in texts]})
            r.raise_for_status()
            data = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
            return model, [np.asarray(d["embedding"], np.float32) for d in data]
    return embed


# ── Prompt building ─────────────────────────────────────────────────────────

def _fit(lines: list[str], budget: int) -> list[str]:
    out, used = [], 0
    for line in lines:
        t = est_tokens(line)
        if used + t > budget:
            break
        out.append(line)
        used += t
    return out


async def build_messages(store: MemoryStore, session_id: str, system_prompt: str, history: list[dict],
                         history_idx: list[int], query: str, context_tokens: int,
                         characters: list[str] | None = None, embedder: Optional[Embedder] = None,
                         reply_tokens: Optional[int] = None) -> tuple[list[dict], dict]:
    """Messages to send: the system prompt with a memory block, then as many recent turns as fit.

    history      formatted turns (role/content) of the whole chat, oldest first
    history_idx  the session message index of each history entry (for retrieval and summaries)
    """
    context_tokens = max(1024, int(context_tokens or 8192))
    reply = reply_tokens or min(2048, max(256, context_tokens // 5))
    available = context_tokens - reply - est_tokens(system_prompt)
    # Recent turns verbatim: up to about 55 % of what is left, always the latest two.
    recent: list[dict] = []
    used = 0
    for msg in reversed(history):
        t = est_tokens(msg["content"])
        if len(recent) >= 2 and used + t > available * 0.55:
            break
        recent.insert(0, msg)
        used += t
    first_recent = history_idx[len(history) - len(recent)] if recent else (history_idx[-1] + 1 if history_idx else 0)
    memory_budget = max(0, available - used)
    info = {"recent_messages": len(recent), "older_messages": len(history) - len(recent), "facts": 0,
            "summaries": 0, "snippets": 0, "search": "keyword", "context_tokens": context_tokens}

    facts = []
    for c in characters or [None]:
        for f in store.facts(session_id, c):
            if f["id"] not in {x["id"] for x in facts}:
                facts.append(f)
    facts = rank_facts(facts, query)
    fact_lines = [f"- {f['text']}" for f in facts[:MAX_FACTS_IN_PROMPT]]
    summaries = [s for s in store.summaries(session_id, active_only=True) if s["from_idx"] < first_recent]
    summary_lines = [("[Chapter] " if (s.get("level") or 1) > 1 else "") + s["text"] for s in summaries]
    snippets: list[dict] = []
    if len(history) > len(recent):
        snippets = await _retrieve(store, session_id, query, first_recent, embedder, info)
    cross_lines: list[str] = []
    related = store.related_sessions(session_id, characters or []) if characters else []
    if related:
        for s_ in store.summaries_for(related, query, limit=2):
            cross_lines.append(f"- (summary) {s_['text'][:600]}")
        for h in store.search(query, session_ids=related, limit=MAX_CROSS_SNIPPETS):
            cross_lines.append(f"- {h['speaker']}: {h['text'][:500]}")

    blocks = []
    fb = _fit(fact_lines, int(memory_budget * 0.25))
    if fb:
        blocks.append("What you remember (keep these consistent):\n" + "\n".join(fb))
        info["facts"] = len(fb)
    # Newest summaries matter most; keep as many as fit, in order.
    sb = list(reversed(_fit(list(reversed(summary_lines)), int(memory_budget * 0.4))))
    if sb:
        blocks.append("Earlier in this conversation (summary):\n" + "\n".join(sb))
        info["summaries"] = len(sb)
    remaining = memory_budget - sum(est_tokens(b) for b in blocks)
    snip_lines = _fit([f"- {s['speaker']}: {s['text'][:700]}" for s in snippets], max(0, int(remaining * 0.7) - 50))
    if snip_lines:
        blocks.append("Relevant earlier moments (quoted from this chat):\n" + "\n".join(snip_lines))
        info["snippets"] = len(snip_lines)
    remaining = memory_budget - sum(est_tokens(b) for b in blocks)
    cl = _fit(cross_lines, max(0, remaining - 50))
    if cl:
        names = ", ".join(characters or [])
        blocks.append(f"From your other chats with {names} (same persona, earlier conversations):\n" + "\n".join(cl))
        info["other_chats"] = len(cl)
    system = system_prompt
    if blocks:
        system += ("\n\n## Memory\nThe conversation is longer than what is shown below. Use this memory as part of "
                   "the conversation; do not mention that it is a summary.\n\n" + "\n\n".join(blocks))
    info["prompt_tokens"] = est_tokens(system) + used
    return [{"role": "system", "content": system}, *recent], info


async def _retrieve(store: MemoryStore, session_id: str, query: str, before_idx: int,
                    embedder: Optional[Embedder], info: dict) -> list[dict]:
    hits = store.search(query, session_id=session_id, before_idx=before_idx, limit=20)
    scores: dict[int, float] = {}
    for rank, h in enumerate(hits):
        scores[h["idx"]] = scores.get(h["idx"], 0) + 1.0 / (rank + 2)
    if embedder is not None:
        try:
            missing = store.missing_embeddings(session_id, EMBED_PER_TURN)   # newest first; the rest backfill later
            if missing:
                got = await embedder([f"{r['speaker']}: {r['text']}" for r in missing])
                if got:
                    store.set_embeddings(session_id, [(r["idx"], v) for r, v in zip(missing, got[1])], got[0])
            q = await embedder([query])
            if q:
                qv = q[1][0] / (np.linalg.norm(q[1][0]) + 1e-9)
                idxs, mat = store.vectors(session_id, q[0])
                if len(idxs) and mat.shape[1] == qv.shape[0]:
                    sims = mat @ qv
                    sims[idxs >= before_idx] = -1.0
                    top = np.argsort(-sims)[:20]
                    for rank, j in enumerate(top):
                        if sims[j] > 0:
                            scores[int(idxs[j])] = scores.get(int(idxs[j]), 0) + 1.0 / (rank + 2)
                info["search"] = "keyword + meaning"
        except Exception as exc:  # meaning search is a bonus
            log.info("Embedding search unavailable: %s", exc)
    best = sorted(scores, key=lambda i: -scores[i])[:MAX_SNIPPETS]
    rows = {r["idx"]: r for r in store.message_texts(session_id, sorted(best))}
    return [{"idx": i, "speaker": rows[i]["speaker"], "text": rows[i]["text"]} for i in sorted(best) if i in rows]


def rank_facts(facts: list[dict], query: str) -> list[dict]:
    """Pinned first, then facts sharing words with the new message, then the most recent."""
    terms = set(t for t in re.findall(r"[a-z0-9]{3,}", (query or "").lower()) if t not in _STOP)

    def score(f):
        overlap = len(terms & set(re.findall(r"[a-z0-9]{3,}", f["text"].lower())))
        return (1 if f.get("pinned") else 0, overlap, f.get("updated") or "")
    return sorted(facts, key=score, reverse=True)


async def backfill_embeddings(store: MemoryStore, session_id: str, embedder: Optional[Embedder],
                              batch: int = 128, max_batches: int = 16) -> int:
    """Add meaning vectors for older messages in the background, oldest first. Returns how many were added."""
    if embedder is None:
        return 0
    done = 0
    for _ in range(max_batches):
        rows = store.missing_embeddings(session_id, batch, newest_first=False)
        if not rows:
            break
        got = await embedder([f"{r['speaker']}: {r['text']}" for r in rows])
        if not got:
            break
        store.set_embeddings(session_id, [(r["idx"], v) for r, v in zip(rows, got[1])], got[0])
        done += len(rows)
    return done


CHAPTER_PROMPT = """Merge these consecutive summaries of one long conversation into ONE chapter summary of 5-10
sentences. Keep names, relationships, promises, decisions, places, ongoing plans and emotional turning points;
drop small talk. Past tense. Reply with the chapter text only."""


async def consolidate(store: MemoryStore, session_id: str, complete: Callable[[list[dict]], Awaitable[str]]) -> Optional[dict]:
    """Roll the oldest unconsumed summaries into a chapter once enough have piled up."""
    level1 = [s for s in store.summaries(session_id, active_only=True) if (s.get("level") or 1) == 1]
    if len(level1) < CHAPTER_EVERY + 2:      # keep the newest summaries detailed
        return None
    block = level1[:CHAPTER_EVERY]
    text = await complete([{"role": "system", "content": CHAPTER_PROMPT},
                           {"role": "user", "content": "\n\n".join(s["text"] for s in block)}])
    text = (text or "").strip()
    if not text:
        return None
    item = store.add_summary(session_id, block[0]["from_idx"], block[-1]["to_idx"], text[:4000], level=2)
    store.mark_consumed([s["id"] for s in block])
    return item


def recall(store: MemoryStore, query: str, character: Optional[str] = None, session_id: Optional[str] = None,
           limit: int = 10) -> dict:
    """Search all chat memory for agents and tools: matching messages, summaries and facts."""
    sessions = None
    if character:
        key = character_key(character)
        with store._lock, store._db() as db:
            sessions = [r[0] for r in db.execute("SELECT session_id FROM session_chars WHERE character=?", (key,))]
    if session_id:
        sessions = [session_id]
    hits = store.search(query, limit=limit, session_ids=sessions)
    facts = rank_facts(store.facts(session_id, character, include_user=True) if (session_id or character)
                       else store.facts(None, None, include_user=True), query)
    terms = set(t for t in re.findall(r"[a-z0-9]{3,}", (query or "").lower()) if t not in _STOP)
    facts = [f for f in facts if f.get("pinned") or terms & set(re.findall(r"[a-z0-9]{3,}", f["text"].lower()))][:limit]
    summaries = store.summaries_for(sessions if sessions is not None else _all_sessions(store), query, limit=3)
    return {"messages": [{"chat": h["session_id"], "message": h["idx"] + 1, "speaker": h["speaker"],
                          "text": h["text"][:600]} for h in hits],
            "summaries": [{"chat": s["session_id"], "messages": f"{s['from_idx'] + 1}-{s['to_idx'] + 1}", "text": s["text"]}
                          for s in summaries],
            "facts": [{"id": f["id"], "scope": f["scope"], "text": f["text"], "pinned": bool(f["pinned"])} for f in facts]}


def _all_sessions(store: MemoryStore) -> list[str]:
    with store._lock, store._db() as db:
        return [r[0] for r in db.execute("SELECT DISTINCT session_id FROM summaries")]


# ── Background summaries and fact extraction ────────────────────────────────

SUMMARY_PROMPT = """You maintain the long-term memory of a conversation.
Read the conversation part below and reply with ONLY a JSON object:
{"summary": "3-8 sentences: what happened, decisions, promises, open threads, emotional state, in past tense",
 "facts": [{"text": "a durable fact worth remembering", "about": "user|character|chat"}]}
Facts are short (under 25 words), specific and lasting: names, relationships, preferences, allergies, plans, places,
promises, established story events. Never describe the conversation itself or which topics were discussed.
"about": "user" for facts about the human, "character" for facts about the AI character, "chat" for the story/situation.
No more than 8 facts; return an empty list when nothing lasting was said. Do not invent anything."""


def parse_summary(text: str) -> tuple[str, list[dict]]:
    m = re.search(r"\{.*\}", text or "", re.S)
    if m:
        try:
            data = json.loads(m.group(0))
            facts = [f for f in data.get("facts") or [] if isinstance(f, dict) and isinstance(f.get("text"), str)]
            return str(data.get("summary") or "").strip(), facts
        except ValueError:
            pass
    return (text or "").strip()[:2000], []


_summarising: set[str] = set()


def needs_summary(store: MemoryStore, session_id: str, messages: list[dict], keep_recent: int = 16) -> Optional[tuple[int, int]]:
    """The next block of older messages to summarise, or None."""
    start = store.covered_until(session_id)
    end_limit = len(messages) - keep_recent
    if end_limit - start < SUMMARY_TRIGGER:
        return None
    return start, min(end_limit, start + SUMMARY_BLOCK) - 1


async def summarise(store: MemoryStore, session_id: str, messages: list[dict], span: tuple[int, int],
                    complete: Callable[[list[dict]], Awaitable[str]], character: Optional[str] = None) -> Optional[dict]:
    """Summarise messages[span] with the chat's own model and store the summary and facts."""
    if session_id in _summarising:
        return None
    _summarising.add(session_id)
    try:
        a, b = span
        part = []
        for m in messages[a:b + 1]:
            if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str):
                who = "User" if m["role"] == "user" else (m.get("speaker_name") or "Assistant")
                part.append(f"{who}: {m['content'][:2500]}")
        if not part:
            return store.add_summary(session_id, a, b, "(no spoken turns)")
        previous = store.summaries(session_id)[-1:]
        context = f"Summary so far: {previous[0]['text']}\n\n" if previous else ""
        reply = await complete([{"role": "system", "content": SUMMARY_PROMPT},
                                {"role": "user", "content": context + "Conversation part:\n" + "\n".join(part)}])
        summary, facts = parse_summary(reply)
        if not summary:
            return None
        item = store.add_summary(session_id, a, b, summary)
        for f in facts[:8]:
            if len(f["text"]) > 220 or re.search(r"(conversation|discuss|topics?|asked the assistant)", f["text"], re.I):
                continue   # not a lasting fact
            about = f.get("about")
            if about == "character" and character:
                store.add_fact(f["text"], "character", character=character, source="auto")
            elif about == "user":
                store.add_fact(f["text"], "user", source="auto")
            else:
                store.add_fact(f["text"], "chat", session_id=session_id, source="auto")
        return item
    except Exception as exc:
        log.warning("Chat summary failed for %s: %s", session_id, exc)
        return None
    finally:
        _summarising.discard(session_id)


_STORE: dict[str, MemoryStore] = {}
_STORE_LOCK = threading.Lock()


def get_store(data_dir: Path) -> MemoryStore:
    key = str(data_dir)
    with _STORE_LOCK:
        if key not in _STORE:
            _STORE[key] = MemoryStore(data_dir / "memory" / "memory.sqlite3")
        return _STORE[key]
