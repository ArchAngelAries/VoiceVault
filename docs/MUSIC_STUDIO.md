# Music Studio

The Music Studio composes, edits and analyses songs locally, without ComfyUI. Every model runs in the same audio.cpp runtime as Drama 3 (`runtime/audiocpp/v0.9.3-vulkan`, Vulkan on the RX 7900 XT). A second, separate audio.cpp server hosts the music models and keeps at most one loaded at a time.

## Engines and packages

Packages are found anywhere under your model folders (Settings → Model Sources), or download them in Settings → Models.

| Role | Package (Hugging Face) | Used for | Licence |
|---|---|---|---|
| `yue2` | `audio-cpp/Yue2-3B-GGUF` (Q8 main + F16 VAE + sidecars, 4.5 GB) | Full songs from lyrics + style, with an optional melody/chord plan (ABC score); reuse a melody with new words | CC BY-NC 4.0 |
| `ace_step` | `audio-cpp/audio.cpp-gguf` → `ACE-Step1.5-GGUF/turbo/ace-step-1.5-turbo-q8_0.gguf` (6.2 GB, self-contained) | Fast text-to-music with BPM/key/time signature; **repaint** (regenerate a time range), **complete** (extend), **cover** (restyle a recording), **lego** (add an instrument), **extract** | Apache-2.0 |
| `stems` | `HTDemucs-6stems-GGUF/htdemucs-6stems-q8_0.gguf` (42 MB) | Vocals, drums, bass, guitar, piano, other | MIT |
| `vocals` | `Mel-Band-RoFormer-GGUF/mel-band-roformer-q8_0.gguf` (252 MB) | Clean vocal isolation before lyric alignment | MIT |
| `asr` | `Qwen3-ASR-1.7B-GGUF/qwen3-asr-1.7b-q8_0.gguf` (2.5 GB) | Transcribe the lyrics of any song, with word times | Apache-2.0 |
| `align` | `Qwen3-ForcedAligner-0.6B-GGUF/qwen3-forced-aligner-0.6b-q8_0.gguf` (1.1 GB) | Word-exact timing of known lyrics | Apache-2.0 |
| `score` | `audio-cpp/SheetSage2-GGUF` (2.7 GB) | Melody of a reference song as ABC sheet music, for YuE2 covers (the method used by YuE2 cover workflows) | CC BY-NC 4.0 |

YuE2 and SheetSage2 are non-commercial. The UI states the licences.

## How it works

- `backend/music/runtime.py` discovers packages and builds the server config. It calls `/v1/tasks/run` for generation, separation and score extraction, `/v1/audio/transcriptions/details` for transcription, and `/v1/audio/alignments` for alignment. Input audio is rewritten to each model's rate and channel layout first: ACE-Step 48 kHz stereo, separators 44.1 kHz stereo, ASR and aligner 16 kHz mono.
- `backend/music/studio.py` holds the songs and the job queue:
  - A **song** has a style, lyrics and **takes**. Every edit creates a new take recording its operation, parent, parameters and seed, so nothing is overwritten.
  - **Jobs** run one at a time on a worker thread under the shared speech lock. Before a music job, resident TTS and Whisper are released; before a TTS load, the music server is stopped (`core/gpu_scheduler.register_gpu_user`).
  - Song data lives in `DATA_DIR/music/projects/<id>/` (`project.json`, `takes/<take>.wav`, `takes/<take>.<stem>.wav`). Exports go to `DATA_DIR/music/exports/`.
- **Regenerate selection** uses ACE-Step repaint over the selected range. The result is spliced back with equal-power crossfades (80 ms by default), so audio outside the range is sample-identical to the original. When lyrics are aligned, the lines sung in the range are passed automatically.
- **Extend** uses ACE-Step complete. The original is kept exactly and the new material crossfades in after it.
- **Cover / audio reference** has two methods:
  - ACE-Step cover restyles the recording, keeping its arrangement and length.
  - YuE2 reads the reference's melody with SheetSage2 and sings the song's lyrics over it in the song's style.
- **Instruments**: lego adds a part; extract pulls one instrument out.
- **Stems and mix**:
  - Six stems via HTDemucs. You can listen to each stem on its own, download it, or set per-stem gain and mute and render the result as a new take.
- **Lyrics on the timeline**:
  - For alignment, vocals are isolated first (RoFormer, or the Demucs vocal stem if one exists). Known lyrics are then force-aligned word by word; with no lyrics, the vocals are transcribed and the lyrics are filled in.
  - Words keep their character offsets in the lyric text. Highlighting words in the lyrics panel selects that time range, and the sung word is lit during playback.
  - Section bands (Verse/Chorus…) tile the waveform; clicking a band selects that section.
- **Splice** replaces the selected range with the same range of another take, for example the best of several regenerations. **Blend** crossfades into another take at the playhead.
- **Export**: WAV 24-bit, FLAC, or MP3 320k (FFmpeg), optionally normalised to −14 LUFS.
- **Import**: any audio or video file (FFmpeg, 44.1 kHz stereo, at most 10 minutes) becomes a take. It can then be edited, separated, transcribed or used as a cover reference.

API: `/api/music/*` (see the docstring in `backend/api/music.py`). UI: `frontend/src/pages/Music.jsx`, with `components/music/Waveform.jsx` and `LyricsPanel.jsx`.

## Verification (2026-11-04)

- CPU tests: `tests/test_music.py`, 11 passing, with a fake runtime.
  - Lyrics: parsing, alignment matching and interpolation, highlight-to-time, and section ranges.
  - DSP: splice keeps the outside identical; blend and mix lengths.
  - Package discovery and stem output shapes.
  - Every operation end to end: generate, repaint, extend, stems, align, mixdown, blend, YuE2 cover via the score, and transcription filling the lyrics.
- Live, on the RX 7900 XT with Vulkan:
  - **YuE2 generate**: a 32.1 s song (48 kHz stereo) in 31 s including the model load. Whisper read back nearly all the lyrics; levels were −15 dB RMS with no clipping.
  - **HTDemucs**: six stems in 7.9 s.
  - **Lyric alignment** (RoFormer vocals, then Qwen3 forced aligner): 30/30 words in 4.6 s, with Verse/Chorus sections.
  - **Drop-in transcription** (Qwen3-ASR, then aligner): lyrics written as lines and 30/30 words timed, in 17 s.
  - **Highlight-to-select**: highlighting "Stay with the rhythm" in the UI selected 10.3–15.8 s.
  - **Regenerate section** (ACE-Step repaint): 22 s. Audio outside the selection is bit-identical; the inside is new.
  - **Extend** +20 s: 52 s. **ACE-Step cover**: 9.7 s. **YuE2 melody cover** (SheetSage2 ABC, then YuE2): 44 s. **Add strings** (lego): 129 s. **ACE-Step text2music** (30 s at 120 BPM, A minor): 41 s.
  - **MP3 export at -14 LUFS**: the song measured -15.3 LUFS before normalisation.

## Hardware limits found and handled

- The native v0.9.3 Vulkan build crashes in ACE-Step with an illegal instruction (no AVX-512 on this CPU). The portable build works and is preferred automatically.
- Vulkan cannot allocate one buffer over about 4 GB on this card:
  - **ACE-Step's VAE encoder** needs about 140 MB per second of source audio. VoiceVault therefore sends:
    - repaint: a local window of up to 14 s of region plus 4 s of context each side; long selections are processed window by window, crossfading new audio into new audio;
    - extend: the last 20 s;
    - cover, lego and extract: 20 s chunks with 2 s overlaps.
    - Text-to-music needs no encoder, and its decode is tiled (128 latent frames).
  - **SheetSage2** uses `attention_tile_rows=1024`, which brought a 32 s reference under the limit.
- Chunked ACE-Step covers of long songs restyle each 20 s part separately. For a whole-song cover that stays continuous, use the YuE2 melody method.

## Sound effects (Stable Audio 3 Medium), added 2026-11-04

Package: `audio-cpp/audio.cpp-gguf` → `Stable-Audio-3-Medium-GGUF/stable-audio-3-medium-f16.gguf` (5.8 GB), in `Models/Music`.
- It is the largest Stable Audio 3 model (about 2B parameters). The Small SFX model has 0.6B.
- VoiceVault picks Medium F16 first when several Stable Audio packages are present.
- Licence: Stability AI Community License (free below USD 1M annual revenue) plus Gemma terms for one component.

Studio → **Sound effects** tool (works with no take):
- **New sound from text**: 0.5–120 s, one to four takes with consecutive seeds.
- **Reshape the current take**: `init_audio`, with a 0–1 change amount.
- **Regenerate the selected part**: `inpaint_audio`. The rest is spliced back sample-exact.
- **Mix in**: overlay the current take into another take at a time and level. The other take is extended if the sound runs past its end, and peaks are kept below 0 dBFS.

Live checks, RX 7900 XT, Vulkan:
- 2 × 6 s door creaks in 23.2 s, including the first load.
- 10 s of thunder and rain in 4.3 s.
- A downpour variation in 7.4 s.
- A thunder crack inpainted at 4–6 s in 7.6 s, with the outside bit-identical.
- Mixing into the 32 s song.
- All levels were healthy, with no quality warnings.
- CPU tests: `test_sfx_variations_inpaint_and_overlay`, `test_stable_audio_prefers_medium_f16`.

## Song analysis and style builder, added 2026-11-04

Compose → **Style builder** (`components/music/StyleBuilder.jsx`) helps write the style prompt. **Analyse take** runs the `analyze` job (`music/analysis.py`) on the current take. It runs on the CPU, so it never waits for the GPU. The result is saved on the take as `analysis`.

| Value | How | Reliability |
|---|---|---|
| BPM, with half and double alternatives | librosa beat tracking | Good. Synthwave demo 117 vs the real 120; synthetic tests within ±3. |
| Key | Krumhansl–Schmuckler profiles on the averaged chroma | Good. A minor demo and synthetic songs exact. Relative major/minor can swap. |
| Beats per bar (3 or 4) | Accent pattern every 3 or 4 beats | Rough guess, shown with its confidence |
| Feel, mix | Tempo band; loudness, brightness, dynamics | Measured |
| Genre, mood, instruments, vocal | Zero-shot CLAP `laion/clap-htsat-unfused` (CPU, 0.6 GB, HF cache) | Suggestions. On the demos: jazz + saxophone, choir + female vocal, synth bass + synthesizer. |
| Instruments and vocals, when stems exist | Each stem's level against the mix | Measured. Wins over the tagger. |

- **Use as style** fills the style box and the ACE-Step BPM, key and beats-per-bar fields. **Use tempo + key** fills only the fields.
- The chips are grouped (genre, mood, instruments, vocal, production). A click adds or removes the word in the style box. The same list is served at `GET /api/music/style-vocabulary`.
- Tips for the selected engine sit under the chips.
- Rules in `suggest()`:
  - The tagger hears "instrumental" in sung songs, so it counts only at 0.75 or more.
  - Opposite moods (happy + sad) are never both used.
  - "Duet" and the production words were dropped from the tagger, because it picked them for almost every song. Production words come from the measurements instead.
- `laion/larger_clap_music` was tried first and rejected: in transformers 5.3 every text embeds almost the same (cosine 0.999 between "a dog barking" and "This is a jazz song."), so every song got the same tags. Its weights loaded without missing keys, so the checkpoint itself is the problem.
- Not covered: per-instrument sheet music, lyric language, and detecting sections or structure.
- Tests: `backend/tests/test_music_analysis.py` (5).
