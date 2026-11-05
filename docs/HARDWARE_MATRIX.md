# Hardware matrix — measured 2026-11-05

The owner has one PC, so other hardware is approximated on it:
- **CPU-only:** the CPU paths are forced.
- **Smaller graphics cards:** Drama 3's automatic GPU/RAM split is set to what it would choose for that card, and the PyTorch voices are capped with `torch.cuda.set_per_process_memory_fraction`.

NVIDIA, Linux and macOS are **community-tested**: not verified here.

Reference PC: Ryzen 9 3900X (12 cores, no AVX-512), 32 GB RAM, Radeon RX 7900 XT 20 GB, Windows 11; audio.cpp v0.9.3 Vulkan portable, torch 2.9.1 + ROCm 7.2.1. Test line: about 6 s of speech. "× real time" = time taken ÷ length of the audio (lower is faster; under 1 is faster than real time).

## Speech

| Engine | 20 GB card (reference) | 12 GB card (simulated) | 8 GB card (simulated) | CPU only |
|---|---|---|---|---|
| **Drama 3 final Q4** (25 GB package) | 2.8× (15 layers in RAM) | **2.95×** (27 layers in RAM) | **3.75×** (35 layers in RAM) | Not supported: needs more than 32 GB of RAM and is very slow |
| **Kokoro** (live chat voice) | 0.05–0.3× (Vulkan; first audio 0.4–1.2 s) | same (about 1 GB of VRAM) | same | 1.8–2.6× (slower than real time with the portable build; usable for short replies, not live chat) |
| **Maya1** | fits | fits | **fits: peak 6.6 GB**, 5.9× | Not measured (slow) |
| **OmniVoice** | fits | fits | **fits: peak 2.2 GB**, 1.8× | Not measured |
| VibeVoice-Large | about 20 GB, at the limit | does not fit | does not fit | no |

## Recognition and tools

| Tool | GPU | CPU only |
|---|---|---|
| Whisper large-v3-turbo | under 1 s for a 7 s clip | **286 s for a 7 s clip (40× slower than real time)**, so effectively unusable on the CPU. CPU-only users need a smaller recogniser; see follow-ups. |
| Voice Lab shaping (WORLD) | CPU anyway: about 4 s per 40 s | same |
| Music Studio (ACE-Step, YuE2, stems), cleaning, Seed-VC | measured on the GPU in MUSIC_STUDIO.md, HARVEST_CLEANING.md and VOICE_LAB.md | not measured; expect them to be very slow |

## What this means for users (README table)

- **16 GB VRAM or more:** everything works. Drama 3 runs at about 3× real time.
- **12 GB:** everything except VibeVoice. Drama 3 runs at about 3×.
- **8 GB:** Drama 3 still works, at about 4× real time, with more of it in RAM (32 GB of RAM recommended). Maya1, OmniVoice, Kokoro, music and cleaning work. No VibeVoice.
- **No GPU:** Kokoro (slower than real time), Voice Lab shaping, transcription (small Whisper, about 2× real time), Library and chat (with an external LLM) work. Drama 3 and the music models are not practical.

## Follow-ups

1. **CPU speech recognition: done.** Without a GPU, VoiceVault uses `openai/whisper-base`: 16.6 s for a 7.2 s clip and accurate, versus 286 s for turbo. It is offered as "Whisper base (CPU)" in Settings → Models and in the setup wizard.
2. **Show these limits in the app.** Settings → Models already shows "fits / tight / bigger than your GPU" per model. Generate's engine list could show the same hint.
