# VoiceVault agent manual

How an AI agent (a local LLM in LM Studio, Claude, or any MCP client) uses VoiceVault safely. Humans may find the "Workflows" section useful too.

## 1. Connect

VoiceVault must be running (the backend listens on `http://127.0.0.1:7763`). Then start the MCP server, which talks to it:

```
<VoiceVault folder>/venv/Scripts/python.exe <VoiceVault folder>/backend/mcp_server.py
```

- It needs only the Python standard library and talks to VoiceVault over HTTP.
- To point it at another address, set `VOICEVAULT_URL`.
- The transport is stdio: one JSON-RPC message per line.

**LM Studio** (Program → Edit `mcp.json`), or **Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "voicevault": {
      "command": "<VoiceVault folder>/venv/Scripts/python.exe",
      "args": ["<VoiceVault folder>/backend/mcp_server.py"]
    }
  }
}
```

Replace `<VoiceVault folder>` with the install folder; use forward slashes, or doubled backslashes on Windows.

## 2. Rules the tools enforce

- **Nothing deletes, moves or overwrites.** Composing adds a new take. Voice Lab makes previews, and only `vv_voice_save` keeps one. Library edits change metadata only, and labels are merged.
- **Names are resolved, never guessed.** For example, "Nova Reyes" or "drama q4" are accepted when they match exactly one item. When a name matches several items or none, the reply lists the closest matches, so retry with one of those names or its id.
- **Strict arguments.**
  - Unknown argument names, wrong types and out-of-range values are rejected, and the error says what is allowed.
  - Numbers and booleans sent as strings are accepted.
  - A comma-separated string is accepted where a list is expected.
- **No local paths** are returned, only names, ids, keys and `listen` URLs.
- **Long work returns a `job_id`.** This applies to music composing and analysis. Poll `vv_job_status` every 20–60 s.
- **Every error says what to do next.** When VoiceVault is not running, the error says so; ask the user to start it.

## 3. Tools

| Tool | What it does | Changes data |
|---|---|---|
| `vv_status` | Is VoiceVault up; ready speech models; music tools. **Call first.** | no |
| `vv_list_speech_models` | Ready speech models, and whether each can clone, describe a voice, languages, emotion tags | no |
| `vv_list_voices` | Saved voices (cloning references), filter by words | no |
| `vv_speak` | Text → speech, saved to Library/Generations; clone a saved `voice` or use a `voice_description`. `pace` (tight/natural/relaxed/dramatic) and tags in the text (`[pause 0.8s]`, `[slow]…[/slow]`, `[fast]…[/fast]`) set the rhythm | adds a file |
| `vv_library_search` | Search all audio by words, type, character, label | no |
| `vv_library_tag` | Add/remove labels, set category, notes, title, character/actor | metadata only |
| `vv_voice_presets` | Voice Lab presets (age, gender, characters, effects) | no |
| `vv_voice_shape` | Apply a preset to a voice or generation → preview; `strength` and `effects_strength` (0–2) dial it from subtle to exaggerated | preview only |
| `vv_voice_convert` | Voice-to-voice: delivery of one, sound of another saved voice → preview (slow: ~5–7× clip length) | preview only |
| `vv_voice_save` | Keep a preview as a generation or as a new voice | adds a file |
| `vv_music_list_songs` | Songs, or one song in detail (style, lyrics, takes, analysis) | no |
| `vv_music_create_song` | New song with title, style, lyrics | adds a song |
| `vv_music_style_words` | Style vocabulary and per-engine tips | no |
| `vv_music_compose` | New take with YuE2 or ACE-Step → `job_id` | adds a take |
| `vv_music_analyze` | Tempo, key, beats per bar, style tags of a take → `job_id` | adds analysis to the take |
| `vv_job_status` | Progress or result of a music job | no |
| `vv_chat_list` | Saved chats and scenes, optionally searched | no |
| `vv_memory_recall` | Search what chats remember (messages, summaries, facts) | no |
| `vv_memory_remember` | Save a lasting memory about the user, a character or a chat | adds a memory |
| `vv_cast_list` | The Cast, plus favourite characters that have no persona yet | no |
| `vv_cast_get` | One cast member: persona, voice, sources, open questions | no |
| `vv_cast_research` | Wikipedia excerpts for an established character, or article choices when the name is ambiguous | no |
| `vv_cast_craft` | Build a persona with the user's chat model and save it as a draft: lore-checked for established characters, from an `idea` for original ones (a few minutes) | saves a draft; the previous version is kept |
| `vv_cast_save` | Write persona fields yourself; only the fields you pass change | saves a draft; the previous version is kept |
| `vv_persona_list` | The user's role-play personas and the default one | no |
| `vv_persona_save` | Create or update a persona; optionally make it the default | adds or edits a persona |

The tool annotations mark which tools are read-only (`readOnlyHint`); none is marked destructive.

## 4. Workflows

**Say a line in a character's voice**
1. `vv_list_voices` with a query to find the voice.
2. `vv_speak` with `text` and `voice`. Leave `model` empty: a model that clones is picked automatically, the Drama 3 Q4 build first.
3. Give the user the `listen` URL.

**"Make a song like this one"**
1. The song must already be in the Music Studio: the user imports it as a take, or picks an existing take.
2. `vv_music_analyze`, then `vv_job_status`. The result's `analysis.suggestion` holds a style line, bpm, key and beats per bar.
3. `vv_music_create_song` with that style and new lyrics. Section headers go on their own lines.
4. `vv_music_compose` with `engine: ace_step` and the bpm, key and beats per bar, or `engine: yue2` for a full sung song.

**Older / younger / other-gender version of a voice**
1. `vv_voice_presets`.
2. `vv_voice_shape` with the preset and a `voice`.
3. The user listens to the preview.
4. `vv_voice_save` with `save_as: voice` makes it available for cloning.

**"Craft personas for my favourite characters"**
1. `vv_cast_list`. Everyone under `favourites_not_in_cast` needs a persona.
2. `vv_cast_craft` with `character` set to each name, one at a time. A favourite is added to the Cast automatically.
3. If a result has `choices` (for example "The Doctor" matches many articles), call again with `article` set to the right title, or with `version` (for example "Twelfth Doctor").
4. Tell the user the personas are saved as drafts in Cast, and pass on any `unconfirmed` items.

**Writing a persona yourself (agents with their own research)**
- `vv_cast_research` gives sourced Wikipedia excerpts.
- `vv_cast_save` stores your text. For an established character, include only facts you confirmed in a source. Pass those sources in `sources_json`, and put anything uncertain in `open_questions`. Never present guesses as lore.
- Voices are designed from a description, never copied from an actor. The user auditions them on the Cast page.

**"Make me a character" or "make me a persona"**
- `vv_cast_craft` with `kind: original`, the name and an `idea`.
- `vv_persona_save` for who the user plays: name, address, description, notes, optionally `make_default`.

**Organise**
- `vv_library_search` to find items.
- `vv_library_tag` to add labels and a character.
- Music and sound effects take labels and categories, but no character.

## 5. Limits worth knowing

- Speech: up to 5000 characters per call (longer text is split automatically by the engine).
- Music:
  - ACE-Step: 10–600 s takes; BPM 40–240; key such as `C major` or `F# minor`.
  - YuE2 writes full songs from lyrics, but has no BPM or key fields.
- Song analysis: tempo and key are measured and reliable. Genre, mood and instrument tags are suggestions from a listening model.
- Sheet music: SheetSage2 melody + chords only. No per-instrument parts.
- Voice Lab conversion holds the GPU. Only one GPU job runs at a time, and others queue.
- Harvest (fetching audio from links) and deleting anything are deliberately **not** agent tools. Ask the user to use the app for those.

## 6. Raw HTTP API (for agents without MCP)

All routes are under `http://127.0.0.1:7763/api`. Interactive docs: `http://127.0.0.1:7763/docs`.

| Area | Main routes |
|---|---|
| Speech | `GET /tts/models`, `GET /tts/engines`, `GET /tts/references`, `POST /tts/synthesize` `{model_id, text, reference_id?, reference_text?, voice_description?, language?, seed?}`, `GET /tts/audio/{file}` |
| Library | `GET /library/items?q=&type=&character=&label=`, `PATCH /library/items` `{key, labels?, category?, notes?, title?, character?}` |
| Voice Lab | `GET /voice/presets`, `POST /voice/shape`, `POST /voice/convert`, `POST /voice/save`, `GET /voice/previews/{id}` |
| Music | `GET/POST /music/projects`, `POST /music/projects/{id}/jobs` `{kind, params}`, `GET /music/jobs/{id}`, `GET /music/style-vocabulary` |

Music job kinds:
- `generate`, `cover`, `repaint`, `extend`, `layer`;
- `stems`, `align`, `analyze`;
- `splice`, `blend`, `mixdown`;
- `sfx`, `overlay`.

See MUSIC_STUDIO.md.

The raw API also has delete routes. An agent must not call them unless the user explicitly asks for that one deletion.

## 7. Testing

- `backend/tests/test_mcp_server.py` covers the protocol, argument checking, name resolution, path scrubbing and the no-delete guarantee, using a fake API.
- A live check with a local model in LM Studio is described in [§8](#8-live-check-with-a-local-model).

## 8. Live check with a local model

2026-11-04: a small 4B tool-calling model (`spark-x2.6-4b`, Q4_K_M) in LM Studio was given VoiceVault's tools through LM Studio's OpenAI-compatible API, working on a scratch Library. It ran four everyday requests, each in plain words, with no hints about tool names.

| Request | Tool calls | Errors |
|---|---|---|
| "Which of my saved voices are <character name>?" | 1 | 0 |
| "Make the <voice name> voice sound elderly, then save it as a generation called '<new name>'" | 4 | 0 |
| "Analyse the jazz demo song and tell me its tempo, key and a style prompt" | 3 | 0 |
| "Tag the <voice name> voice with 'narrator' in category 'Test'" | 2 | 0 |

- In the last request, the model picked the voice item rather than a harvested clip with the same title.
- Names are replaced with placeholders here. The test script lives outside the repo. To repeat it, point an OpenAI-compatible tool loop at `mcp_server.TOOLS` and `mcp_server.call`.
