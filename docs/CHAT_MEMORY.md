# Chat memory

Long chats in Converse and Scene Creation stay coherent with any model, including small local ones with short context windows. The whole chat is always saved; memory decides what each reply **gets sent**.

## How a reply is built (`backend/rag/memory.py`)

The prompt is fitted to the model's context size. Set it in Converse → "Context size"; the default comes from the `chat_context_tokens` setting, 8192. It is made of:

1. The system prompt.
2. **What you remember:** pinned memories and facts. Each has a scope:
   - about the user (every chat);
   - about the character (every chat with that character);
   - about this chat.
3. **Earlier in this conversation:** rolling summaries of older messages. They are written in the background by the chat's own model once 12 or more older messages are not yet covered (24 messages per pass). The same pass extracts short, lasting facts.
4. **Relevant earlier moments:** the older messages that best match the new message.
   - Keyword search (SQLite FTS5) always runs.
   - Meaning search runs when the model server offers an embedding model, for example LM Studio's `nomic-embed`.
     In LM Studio, **load the embedding model yourself, next to your chat model** (Developer tab → Load model). VoiceVault only uses an embedding model that is already loaded: asking LM Studio for one that isn't would unload your chat model, because LM Studio keeps one automatically loaded model by default. Without it, memory still works, using keyword search.
   - Up to 6 moments are included.
5. The most recent messages, verbatim. They take up to about 55 % of the space; the last two are always sent.

The old 100-message limit is gone; a chat now stores up to 5,000 messages. The final `done` / `scene_done` event reports what was used (`memory`: recent messages, summaries, moments, facts, search type).

Storage is `<data>/memory/memory.sqlite3`: a message index, summaries and facts. Deleting a chat removes its index, summaries and chat-scoped facts. Character and user facts stay.

## In the app

- **Chat list:**
  - search across titles and message text, with the matching line shown;
  - rename: pencil icon, or double-click a chat;
  - delete: trash icon, then confirm.
- **Memory** button in the chat header:
  - what was used for the last reply;
  - add a memory, with a scope;
  - edit, pin or forget facts;
  - read the summaries.
- Cloud providers with "name only" privacy mode never get memory or summaries, so no extra chat content is sent.

## API (`/api/converse`)

| Call | Purpose |
|---|---|
| `GET /sessions?q=` | Search chats; each hit carries `matches` snippets |
| `DELETE /sessions/{id}` | Delete a chat and its memory (refused while it is replying) |
| `GET /sessions/{id}/memory` | Summaries, facts, characters, message count |
| `POST /sessions/{id}/memory` | `{text, scope: chat\|character\|user, character?, pinned}`; near-duplicates are refused with 409 |
| `PATCH /memory/{fact_id}` | `{text?, pinned?}` |
| `DELETE /memory/{fact_id}` | Forget one fact |

Message and scene requests accept `context_tokens`.

## Verified

- **Tests:** `backend/tests/test_chat_memory.py` has 9 tests:
  - recall inside a 2,048-token budget for a 300-message chat;
  - scopes and dedupe;
  - background summaries;
  - meaning search;
  - the API, including going past the old 100-message limit.
- **Live (LM Studio `spark-x2.6-4b`, 4,096-token context):** a 160-message chat with three details planted at messages 7, 65 and 113 (dog Biscuit the beagle, sister Lena in Oslo, peanut allergy).
  - The model answered all four checks correctly.
  - The prompt used 2,037 tokens: 50 recent messages plus 6 retrieved moments, with keyword plus meaning search.
  - The background summary and fact extraction ran afterwards.
  - Search found the chat by "Oslo".

## Scale: thousands of turns, personas and agents (added 2026-11-04, later)

- **No practical length limit.**
  - A chat stores up to 100,000 messages.
  - The browser gets the newest 300; "Load earlier messages" pages back 200 at a time.
  - Replies no longer send the whole chat back.
- **Layered summaries.** Every 6 summaries roll up into a "chapter" (`consolidate`), so the whole history stays representable in the prompt.
- **Meaning search over the whole chat.**
  - 48 vectors are made per turn (newest first), and the rest are back-filled in the background after each reply.
  - Vectors are cached in memory per chat.
- **Facts by relevance.** Pinned facts come first, then facts sharing words with the new message, then the newest.
- **Personas remember across chats.**
  - Chats are linked by character name (`session_chars`).
  - A new chat with the same persona gets "From your other chats with …": matching messages and summaries.
- **Agents and tools:**
  - `GET /api/converse/memory/search?q=&character=&session_id=` returns messages, summaries and facts across chats.
  - MCP tools: `vv_chat_list`, `vv_memory_recall`, `vv_memory_remember` (adds only, never deletes).

**Live check:** LM Studio `spark-x2.6-4b`, 4,096-token context, scratch library.

| Test | Result |
|---|---|
| 2,000-message persona chat ("Mira"), five details planted at messages 31, 901 and 1501 | The model recalled all five (song, artist, cat, plan, place). The prompt used 2,145 tokens: 70 recent messages plus 6 retrieved moments plus 1 fact. Keyword and meaning search both ran. |
| A **new** chat with Mira: "Do you remember our special song?" | Answered "Moonlit Harbor by The Tidewater Band" from the other chat (4 cross-chat snippets). |
| Recall API, "cat name" for Mira | The first hit is the planted message. |

**Tests:** 14 in `test_chat_memory.py`, including chapters, a 3,000-turn budget and recall check under 3 s, persona recall, background backfill, paging and search. MCP memory tools are covered in `test_mcp_server.py`.
