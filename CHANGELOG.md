# Changelog

## 0.2.1 — 2026-11-09

### Converse
- **Replay any spoken reply.** Every reply that was spoken has a Replay button, and it keeps working after you switch chats or restart. Older replies offer Speak, which voices them now and keeps the audio for next time.
- **Pace between sentences** in each character's voice settings (Natural, Relaxed, Dramatic) adds breathing room to live replies.

### Generate
- **Pacing that works with every voice model.** Pace sets the silence between sentences. Tags in the text give exact control: `[pause 0.8s]` (or `[pause 800ms]`), `[slow]…[/slow]`, `[fast]…[/fast]` and `[speed 0.7]…[/speed]`. Agents can use them too (`vv_speak` with `pace`).

### Voice Lab
- **Strength controls.** Voice change strength and Effects strength (0–150%) dial any preset up or down, so you can have "Elderly" at 60% or a softer robot. Each effect also has its own Amount slider. Agents get the same (`vv_voice_shape` with `strength`).

### Models
- **Choose Drama 3's size.** Build any of Q2, Q3, Q4, Q5, Q6 and Q8 on your PC, and keep the downloaded original to build more later without downloading again. The app says plainly that smaller sizes can lose quality: every size uses the same proven method, Q4 and Q6 are the ones tested on our PC, sizes below Q4 are labelled "lower quality" and need a confirmation, and model pickers show each size. See [docs/QUANTIZATION.md](docs/QUANTIZATION.md).

### Setup
- **NVIDIA graphics cards: first-run setup no longer fails.** Setup installed PyTorch 2.6 from the CUDA 12.4 channel, which clashed with VoiceVault's other packages (a sympy version conflict), so "Install the voice engine packages" stopped. Every card now gets the same PyTorch 2.9.1: CUDA 12.8 on NVIDIA (CUDA 12.6 when the driver is older than 570), ROCm 7.2 on AMD, and the CPU build otherwise. If an earlier setup left a different PyTorch version, setup now replaces it instead of keeping it. Thanks to @DeeplyParallel for the report and diagnosis ([#1](https://github.com/ArchAngelAries/VoiceVault/issues/1)).

## 0.2.0 — 2026-11-07

### Cast (new page)
- **Set a character up once and use them anywhere.** A cast member keeps a portrait, a full persona (personality, way of speaking, background, relationships, things they'd never do, example lines) and a voice, and fills any chat or scene in one click. Add favourites from Discover, or create your own characters.
- **Craft with AI, lore-checked.** For an established character, VoiceVault reads their Wikipedia article and drafts the persona from it with your chat model. A second pass must find an exact supporting quote for every statement, and VoiceVault confirms the quote is really in the source. Guesses ("likely", "may") and anything unconfirmed are left out and listed under Open questions; a near-match can never flip a "never" or a "not", and spin-off media (novels, comics, games) is never used as evidence. Sources are shown with links. Set a version (for example Twelfth Doctor) for characters with several versions.
- **Add your own sources.** Paste lore from any page you can read, such as a fan wiki; it is checked the same way. VoiceVault doesn't fetch fan wikis itself, because Fandom's terms don't allow automated access.
- **Original characters from an idea.** Describe a character and Craft with AI writes the full persona and a voice description.
- **Voice auditions.** Maya1 designs three voices from the description, and Whisper listens to each take so garbled or rambling ones are flagged and the best match is marked. The one you pick is saved to your voices with its exact transcript, and Drama 3 performs it in chats.
- **Craft personas for all** builds every favourite in one go, saved as drafts to review. Every save keeps the previous version, and History restores it.

### Converse
- **Your personas.** Choose who you play in each chat or scene (name, avatar, description, how characters address you), or set a default. Characters respond to your persona, and your avatar shows next to your messages.
- **Your Cast comes first** in the character picker. Load from Cast and Save to Cast work in chats and scenes.

### Updates
- **VoiceVault tells you when a new version is out.** Once a day it asks GitHub for the newest release. A banner shows what's new and offers to download the installer inside the app, then checks the file against GitHub's SHA-256 fingerprint. The desktop app checks it again just before running it. Install now closes VoiceVault and runs the installer, which keeps your library, voices, chats and settings. Skip this version, or turn the check off in Settings → General → Updates. Only the "what's the newest release?" question goes to GitHub; nothing about you is sent.

### For agents
- New MCP tools: `vv_cast_list`, `vv_cast_get`, `vv_cast_research`, `vv_cast_craft`, `vv_cast_save`, `vv_persona_list` and `vv_persona_save`. An agent can now craft personas for all your favourites, make original characters, or create personas. Like every VoiceVault tool, none of them can delete anything.

## 0.1.1 — 2026-11-05

Fixes found while making VoiceVault's own release trailer, made entirely with VoiceVault. Watch it from the README.

### Chat
- **No more silent "thinking" before character replies.** Reasoning models in LM Studio used to think quietly for 5–20 seconds before the first word. VoiceVault now asks LM Studio to answer directly for chat replies, so characters respond much sooner.

### Voices
- **Drama 3 handles low memory gracefully.** Before loading, it checks that your PC has enough free system RAM for the part of the model that doesn't fit on the graphics card, and explains what to do if not: close programs, unload other models, or pick the smaller Q4 build. If Drama 3 runs out of memory or crashes, it now recovers on the next request instead of staying "not loaded" until VoiceVault restarts.
- **Voice Lab ageing sounds like age, not just depth.** Elderly, Very old and the Age slider now add a natural shake and roughness and slow the voice, with little or no drop in pitch. Women's voices barely deepen with age and men's rise slightly, as in real life.

## 0.1.0 — 2026-11-05

First public release.
