# Release checklist: keeping personal data out

A developer's working folder holds real keys, audio and machine details. A public release must be built from a clean copy. The root `.gitignore` lists everything that must not ship.

## Before publishing

1. **Copy without the excluded paths** (`.gitignore`). Most important:
   - `backend/voicevault_settings.json`, which holds API keys and model folders. Ship `backend/voicevault_settings.example.json` instead. The app creates a fresh settings file from defaults on first start.
   - `data/` and `Library/`: audio, chats, caches, test output, backups.
   - `backend/db/*.db`: the collections database. It is recreated on first start.
   - `Voices/`, `.claude/`, `venv/`, `node_modules/`, `frontend/release/`.
   - Internal working notes (listed under "Internal working notes" in `.gitignore`).
   - Test leftovers (`backend/.pytest*/`) and the audio.cpp binaries (`runtime/audiocpp/`; the installer bundles them, source users download them in Settings → Models).
2. **Search the copy for personal strings** and expect no hits:
   - your Windows user name;
   - your email;
   - `C:\Users\`;
   - your own drive and folder names (where your projects and model folders live);
   - any API key value.
3. **Rebuild** `frontend/dist` and the Electron package from the clean copy. Never reuse a local build.
4. If any key was ever published, **rotate** it at the provider (TMDB, IGDB/Twitch, OpenAI, Anthropic, Gemini, Fish Audio).
5. Decide whether the audio.cpp runtime is bundled (`runtime/audiocpp/v0.9.3-vulkan-portable`, Apache-2.0) or downloaded on first run.

## Built-in protections (2026-11-04)

- **API keys never reach the browser.**
  - The settings API sends a mask for every saved key and keeps the stored key when the mask comes back.
  - The Settings page shows "Saved on this PC", with a Clear button.
- **Settings updates are checked:** known names only, correct types, full folder paths. Bad updates are refused, and nothing is written.
- **Reset Settings** keeps API keys.
- **Host check:** the backend answers only requests addressed to `127.0.0.1`, `localhost` or `::1`. This blocks DNS-rebinding web pages. `VOICEVAULT_ALLOWED_HOSTS` adds names when really needed.
- **Agent tools** (`backend/mcp_server.py`) strip local paths and cannot delete anything.
- **Harvest** never reads browser cookies, and refuses links that point at this PC or the local network.

Tests: `backend/tests/test_settings_security.py`.
