# Models and licences

VoiceVault is the app. The **models** are separate: large files made by other people and companies, each with its own licence. VoiceVault does **not** include or redistribute any model weights. You download each model yourself from its official page, and by doing so you accept its licence.

**Commercial use** means using the output to make money, for example in a paid product, an ad, a monetised video or a client job. "No" means personal, hobby and research use only.

> This table is a summary to help you find the right page. It is **not legal advice** and licences can change. The model's own page (its "model card") and licence file always win. Where we are not sure, the table says "check the model card".

Many models run in **audio.cpp**, a separate program that VoiceVault uses to run models efficiently. Its ready-to-run model files (GGUF format, a single-file model package) are published by the **`audio-cpp`** organisation on Hugging Face: <https://huggingface.co/audio-cpp>. Those packages are conversions; the original authors' licences still apply.

## Speech (text to speech and voice cloning)

| Model | What it does | Licence | Commercial use | Official page |
|---|---|---|---|---|
| **Fish Audio Drama 3** (local) | The default voice engine. Natural cloned voices, up to 16 speakers, emotion cues | Fish Audio Community License 1.0 | **Limited**: free for personal and research use; commercial use only below USD 500,000 yearly revenue; no hosted speech services | <https://huggingface.co/fishaudio/drama-3> |
| Maya1 | Describe a new voice in words | Apache-2.0 | Yes | <https://huggingface.co/maya-research/maya1> |
| SNAC 24 kHz codec | Used by Maya1 to turn its output into sound (downloaded on first use) | Check the model card | Check the model card | <https://huggingface.co/hubertsiuzdak/snac_24khz> |
| OmniVoice | 600+ languages, voice design and cloning; the training target | Code Apache-2.0; **weights CC-BY-NC**; audio tokenizer under the Boson Higgs Audio 2 Community License | **No** | <https://huggingface.co/k2-fsa/OmniVoice> |
| VibeVoice-Large and VibeVoice Q8 (8-bit) | Long-form speech from a reference voice | Code MIT (Microsoft); weights listed as MIT plus research-use and out-of-scope restrictions | Check the model card | <https://github.com/microsoft/VibeVoice> |
| Breeze TTS 2 | Voice design and directed cloning | Runtime Apache-2.0; **weights BreezeBlue Research and Non-Commercial License** | **No** | <https://huggingface.co/BreezeBlue/Breeze-TTS-2> |
| Kokoro-82M (optional) | Fast, light chat voice with preset voices; no cloning | Apache-2.0 | Yes | <https://huggingface.co/hexgrad/Kokoro-82M> |
| Fish Audio cloud (optional, experimental) | Drama 3 on Fish Audio's servers, with your own paid key | Fish Audio's service terms | See Fish Audio's terms | <https://fish.audio/> |

**Drama 3 notes**
- Show **"Built with Fish Audio"** when you share work made with Drama 3. VoiceVault shows it in the app.
- VoiceVault runs Drama 3 as a GGUF package. The final Drama 3 weights are converted to GGUF **by you**, on your own PC, with audio.cpp's converter. Keep the Fish Audio `LICENSE` and a `NOTICE` next to the converted file.

## Voice changing, cleaning and transcription

| Model | What it does | Licence | Commercial use | Official page |
|---|---|---|---|---|
| Whisper large-v3-turbo (OpenAI) | Speech to text: transcripts for cloning and the microphone | MIT | Yes | <https://huggingface.co/openai/whisper-large-v3-turbo> |
| Seed-VC v2 | Voice to voice in Voice Lab. **Downloaded separately, not bundled**; run by audio.cpp | GPL-3.0 | Yes, under GPL terms | <https://github.com/Plachtaa/seed-vc> |
| Mel-Band RoFormer | Removes music from a voice | MIT | Yes | <https://huggingface.co/audio-cpp/audio.cpp-gguf> |
| SAM-Audio Large (Meta) | Keeps only the sound you describe ("a man speaking"). Adds Meta's inaudible watermark | Meta SAM License | Check the model card | <https://huggingface.co/facebook> |
| Sidon | Restores speech to 48 kHz (noise, echo, muffled audio) | MIT | Yes | <https://huggingface.co/audio-cpp> |
| UniverSR (speech) | Rebuilds missing high frequencies | CC-BY-4.0 (credit the authors) | Yes, with credit | <https://huggingface.co/audio-cpp> |
| MOSS-Transcribe-Diarize | Finds who speaks when in Harvest | Apache-2.0 | Yes | <https://huggingface.co/audio-cpp> |

## Music and sound effects

| Model | What it does | Licence | Commercial use | Official page |
|---|---|---|---|---|
| YuE2 (3B) | Full sung songs from lyrics and a style | CC BY-NC 4.0 | **No** | <https://huggingface.co/audio-cpp/Yue2-3B-GGUF> |
| ACE-Step 1.5 (turbo) | Fast music with set tempo and key; regenerate, extend, cover, add instruments | Apache-2.0 | Yes | <https://huggingface.co/audio-cpp/audio.cpp-gguf> |
| HTDemucs (6 stems) | Splits a song into vocals, drums, bass, guitar, piano and other | MIT | Yes | <https://huggingface.co/audio-cpp/audio.cpp-gguf> |
| Qwen3-ASR 1.7B and Qwen3 Forced Aligner 0.6B | Song lyrics to text, and word timing | Apache-2.0 | Yes | <https://huggingface.co/audio-cpp/audio.cpp-gguf> |
| SheetSage2 | Reads a song's melody as sheet music, for YuE2 covers | CC BY-NC 4.0 | **No** | <https://huggingface.co/audio-cpp/SheetSage2-GGUF> |
| Stable Audio 3 Medium | Sound effects from text | Stability AI Community License (plus Gemma terms for one part) | **Limited**: free below USD 1M yearly revenue; above that you need a Stability AI licence | <https://huggingface.co/audio-cpp/audio.cpp-gguf> |
| CLAP (`laion/clap-htsat-unfused`) | Suggests genre, mood and instrument words for a song | Check the model card | Check the model card | <https://huggingface.co/laion/clap-htsat-unfused> |

## Runtime

| Component | What it does | Licence | Commercial use | Official page |
|---|---|---|---|---|
| audio.cpp | The program that runs the GGUF models above, on the GPU (Vulkan) or CPU | Apache-2.0 | Yes | <https://github.com/0xShug0/audio.cpp> |

## Things to remember

- **Your voice clones inherit the model's licence.** A voice trained with OmniVoice is non-commercial, like OmniVoice.
- **Non-commercial models** in this list: OmniVoice, Breeze TTS 2, YuE2, SheetSage2. Drama 3 and Stable Audio 3 are free only up to a revenue limit.
- **The voices you clone belong to real people.** The licence of a model is separate from the rights of the person whose voice or song you use. Those are your responsibility; see [TERMS_OF_USE.md](TERMS_OF_USE.md).


## eSpeak-ng (needed by Kokoro)

Kokoro uses eSpeak-ng, GPL-3.0, for pronunciation. VoiceVault does not bundle it.
- Install it from the official release page: https://github.com/espeak-ng/espeak-ng/releases
- Or unpack it into `<VoiceVault folder>/runtime/espeak-ng/<version>/`.

VoiceVault finds it automatically.
