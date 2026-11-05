# Changelog

## 0.1.1 — 2026-11-05

Fixes found while making VoiceVault's own release trailer, made entirely with VoiceVault. Watch it from the README.

### Chat
- **No more silent "thinking" before character replies.** Reasoning models in LM Studio used to think quietly for 5–20 seconds before the first word. VoiceVault now asks LM Studio to answer directly for chat replies, so characters respond much sooner.

### Voices
- **Drama 3 handles low memory gracefully.** Before loading, it checks that your PC has enough free system RAM for the part of the model that doesn't fit on the graphics card, and explains what to do if not: close programs, unload other models, or pick the smaller Q4 build. If Drama 3 runs out of memory or crashes, it now recovers on the next request instead of staying "not loaded" until VoiceVault restarts.
- **Voice Lab ageing sounds like age, not just depth.** Elderly, Very old and the Age slider now add a natural shake and roughness and slow the voice, with little or no drop in pitch. Women's voices barely deepen with age and men's rise slightly, as in real life.

## 0.1.0 — 2026-11-05

First public release.
