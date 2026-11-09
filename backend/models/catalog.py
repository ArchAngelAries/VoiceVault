"""
models/catalog.py — Every model VoiceVault can use, where its official files live, and its licence.

VoiceVault never redistributes model weights. Each entry points at the publisher's own Hugging Face
repository (or, for audio.cpp GGUF packages, the audio-cpp organisation the runtime's model specs name),
and the user downloads it onto their own PC after seeing the licence. Entries that cannot be fetched
automatically carry a link and steps instead.

Sources (kind):
  hf_files     listed files from a repo into <Models>/<dest> (repo folders kept as in the repo)
  hf_cache     a repo snapshot into the standard Hugging Face cache (loaded by repo id)
  hf_local     a repo snapshot into <Models>/<dest>
  espeak_msi   eSpeak NG's official Windows MSI, unpacked (not installed) into runtime/espeak-ng/<version>
  drama_convert Fish Audio's official Drama 3 checkpoint, converted locally to GGUF (models/drama_convert.py)
  manual       a link and steps; the app only detects it
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

AUDIOCPP = "audio-cpp/audio.cpp-gguf"


@dataclass
class Source:
    kind: str
    repo: str = ""
    files: list[str] = field(default_factory=list)       # hf_files
    allow: list[str] = field(default_factory=list)       # hf_cache / hf_local allow patterns
    dest: str = ""                                       # folder under <Models>
    revision: Optional[str] = None
    url: str = ""                                        # manual / espeak_msi
    steps: list[str] = field(default_factory=list)


@dataclass
class Entry:
    id: str
    name: str
    task: str                    # Speech, Live chat voice, Speech recognition, Music, Cleaning, Voice Lab, Analysis, Runtime
    what: str
    sources: list[Source]
    license: str
    license_url: str
    commercial: str              # yes / no / limited
    vram_gb: float = 0.0         # approximate need when it runs on the GPU (0 = CPU only or negligible)
    detect: list[str] = field(default_factory=list)   # file names that show it is installed (any model root)
    detect_cache: list[tuple[str, str]] = field(default_factory=list)  # (repo, file) in the HF cache
    recommended: bool = False
    note: str = ""
    requires: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _f(*files: str, dest: str = "", repo: str = AUDIOCPP) -> Source:
    return Source(kind="hf_files", repo=repo, files=list(files), dest=dest)


CATALOG: list[Entry] = [
    # ── Speech ─────────────────────────────────────────────────────────────────
    Entry("drama3_final", "Fish Audio Drama 3 (final; sizes Q2–Q8)", "Speech",
          "The most natural speech and voice cloning; multi-voice scenes. VoiceVault's default voice.",
          [Source(kind="drama_convert", repo="fishaudio/drama-3", dest="Drama-3-GGUF-final",
                  revision="78e5ab79e49aa9a25abe8799eff2c4d73a1c3216", url="https://huggingface.co/fishaudio/drama-3",
                  steps=["Downloads Fish Audio's official final checkpoint (41.5 GB) and converts it on this PC with "
                         "audio.cpp's own converter into the Q4 package (about 25 GB).",
                         "Optional: build other sizes too, from Q2 (about 17 GB, lower quality) to Q8 (about 42 GB, closest "
                         "to the original). Smaller sizes use less memory but can lose quality; Q4 and Q6 are the tested ones. "
                         "Keep the downloaded original to build more sizes later without downloading again.",
                         "Needs about 140 GB free while converting (about 175 GB with Q6), 32 GB of RAM or more, and "
                         "an hour or more of CPU time. Temporary files are deleted at the end."])],
          "Fish Audio Community License 1.0", "https://huggingface.co/fishaudio/drama-3", "limited", vram_gb=16,
          detect=["drama-3-final-experts-Q4_K-backbone-Q8_0-embed-F16.gguf"], recommended=True,
          note="Free for personal use; commercial use allowed under the licence's revenue limit; credit "
               "\"Built with Fish Audio\". Larger than most cards: parts run from system RAM automatically."),
    Entry("kokoro", "Kokoro 82M (multilingual)", "Live chat voice",
          "Very fast preset voices for live character chat: first audio in about half a second. 54 voices, 9 languages.",
          [_f("Kokoro-82M-GGUF/kokoro-82m-multilingual-q8_0.gguf")],
          "Apache-2.0", "https://huggingface.co/hexgrad/Kokoro-82M", "yes", vram_gb=1,
          detect=["kokoro-82m-multilingual-q8_0.gguf", "kokoro-82m-q8_0.gguf"], recommended=True,
          requires=["espeak_ng"]),
    Entry("espeak_ng", "eSpeak NG 1.52.0 (pronunciation for Kokoro)", "Live chat voice",
          "Turns text into pronunciations for Kokoro. Unpacked into VoiceVault's runtime folder; nothing is installed system-wide.",
          [Source(kind="espeak_msi", url="https://github.com/espeak-ng/espeak-ng/releases/download/1.52.0/espeak-ng.msi",
                  dest="1.52.0", steps=["Official release page: https://github.com/espeak-ng/espeak-ng/releases"])],
          "GPL-3.0", "https://github.com/espeak-ng/espeak-ng/blob/master/COPYING", "yes",
          note="A separate program VoiceVault uses; it does not affect what you can do with your audio."),
    Entry("maya1", "Maya1 (+ SNAC codec)", "Speech",
          "Design a voice by describing it in words; 17 emotion tags. No clip needed.",
          [Source(kind="hf_local", repo="maya-research/maya1", dest="maya1",
                  allow=["*.json", "*.safetensors", "*.model", "*.txt", "tokenizer*"]),
           Source(kind="hf_cache", repo="hubertsiuzdak/snac_24khz", allow=["*.json", "*.pt", "*.bin", "*.safetensors"])],
          "Apache-2.0", "https://huggingface.co/maya-research/maya1", "yes", vram_gb=7,
          detect=["model-00001-of-00002.safetensors"], detect_cache=[("hubertsiuzdak/snac_24khz", "config.json")]),
    Entry("omnivoice", "OmniVoice", "Speech",
          "600+ languages, voice design from structured words, small and fast. Non-commercial weights.",
          [Source(kind="hf_cache", repo="k2-fsa/OmniVoice")],
          "CC BY-NC 4.0 (weights)", "https://huggingface.co/k2-fsa/OmniVoice", "no", vram_gb=4,
          detect_cache=[("k2-fsa/OmniVoice", "config.json")]),
    Entry("vibevoice_large", "VibeVoice-Large (+ Qwen2.5 tokenizer)", "Speech",
          "Long-form reference cloning. Needs about 20 GB of VRAM.",
          [Source(kind="hf_cache", repo="aoi-ot/VibeVoice-Large"),
           Source(kind="hf_cache", repo="Qwen/Qwen2.5-1.5B", allow=["tokenizer*", "vocab.json", "merges.txt", "*.json"])],
          "MIT + research-use notes", "https://github.com/microsoft/VibeVoice", "limited", vram_gb=20,
          detect=["dir:models--aoi-ot--vibevoice-large", "dir:vibevoice-large"],
          detect_cache=[("aoi-ot/VibeVoice-Large", "config.json")],
          note="Check the model card before commercial use."),
    Entry("breeze", "Breeze TTS 2", "Speech",
          "Mandarin and English; voice design plus directed cloning. Non-commercial weights.",
          [Source(kind="hf_local", repo="drbaph/Breeze-TTS-2-comfyui", dest="breezetts2/drbaph_Breeze-TTS-2-comfyui",
                  allow=["Breeze-TTS-2-bf16.safetensors", "*.json", "audio_tokenizer/*", "tokenizer*", "*.txt", "*.model"])],
          "BreezeBlue research and non-commercial", "https://huggingface.co/drbaph/Breeze-TTS-2-comfyui", "no", vram_gb=12,
          detect=["Breeze-TTS-2-bf16.safetensors"]),
    # ── Recognition ────────────────────────────────────────────────────────────
    Entry("whisper", "Whisper large-v3-turbo", "Speech recognition",
          "Transcribes clips for cloning, Harvest and the chat microphone.",
          [Source(kind="hf_cache", repo="openai/whisper-large-v3-turbo",
                  allow=["*.json", "model.safetensors", "*.txt", "tokenizer*", "vocab*", "merges*"])],
          "MIT", "https://huggingface.co/openai/whisper-large-v3-turbo", "yes", vram_gb=2,
          detect_cache=[("openai/whisper-large-v3-turbo", "config.json")], recommended=True),
    Entry("whisper_cpu", "Whisper base (CPU)", "Speech recognition",
          "Small transcription model used automatically on PCs without a graphics card (about 2x real time on a 12-core CPU).",
          [Source(kind="hf_cache", repo="openai/whisper-base",
                  allow=["*.json", "model.safetensors", "*.txt", "tokenizer*", "vocab*", "merges*"])],
          "MIT", "https://huggingface.co/openai/whisper-base", "yes",
          detect_cache=[("openai/whisper-base", "config.json")]),
    Entry("qwen3_asr", "Qwen3-ASR 1.7B", "Speech recognition", "Transcribes sung lyrics in the Music Studio.",
          [_f("Qwen3-ASR-1.7B-GGUF/qwen3-asr-1.7b-q8_0.gguf")], "Apache-2.0",
          "https://huggingface.co/Qwen/Qwen3-ASR-1.7B", "yes", vram_gb=2.5, detect=["qwen3-asr-1.7b-q8_0.gguf"]),
    Entry("qwen3_aligner", "Qwen3 forced aligner 0.6B", "Speech recognition", "Times each lyric word to the audio.",
          [_f("Qwen3-ForcedAligner-0.6B-GGUF/qwen3-forced-aligner-0.6b-q8_0.gguf")], "Apache-2.0",
          "https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B", "yes", vram_gb=1,
          detect=["qwen3-forced-aligner-0.6b-q8_0.gguf"]),
    Entry("moss_diarize", "MOSS-Transcribe-Diarize", "Speech recognition",
          "Finds who speaks when, so Harvest can keep one person's lines.",
          [_f("MOSS-Transcribe-Diarize-GGUF/moss-transcribe-diarize-bf16.gguf")], "Apache-2.0",
          "https://huggingface.co/OpenMOSS-Team", "yes", vram_gb=2, detect=["moss-transcribe-diarize-bf16.gguf"]),
    # ── Music ──────────────────────────────────────────────────────────────────
    Entry("ace_step", "ACE-Step 1.5 turbo", "Music", "Fast songs with set BPM and key; editing, covers, extending.",
          [_f("ACE-Step1.5-GGUF/turbo/ace-step-1.5-turbo-q8_0.gguf")], "Apache-2.0",
          "https://huggingface.co/ACE-Step", "yes", vram_gb=7, detect=["ace-step-1.5-turbo-q8_0.gguf"], recommended=True),
    Entry("yue2", "YuE2 3B", "Music", "Full sung songs from lyrics, with a planned melody. Non-commercial.",
          [_f("sidecars/yue2-model-config.json", "sidecars/yue2-generation-config.json", "sidecars/yue2-qwen.tiktoken",
              "sidecars/yue2-vae-config.json", "yue2-3b-q8_0.gguf", "yue2-vae-f16.gguf",
              repo="audio-cpp/Yue2-3B-GGUF", dest="Yue2-3B-GGUF")],
          "CC BY-NC 4.0", "https://huggingface.co/m-a-p", "no", vram_gb=5, detect=["yue2-3b-q8_0.gguf"]),
    Entry("htdemucs", "HTDemucs 6-stem", "Music", "Splits a song into vocals, drums, bass, guitar, piano and other.",
          [_f("HTDemucs-6stems-GGUF/htdemucs-6stems-q8_0.gguf")], "MIT", "https://github.com/facebookresearch/demucs",
          "yes", vram_gb=1, detect=["htdemucs-6stems-q8_0.gguf"]),
    Entry("sheetsage2", "SheetSage2", "Music", "Reads a song's melody and chords for YuE2 covers. Non-commercial.",
          [_f("sheetsage2-orig.gguf", repo="audio-cpp/SheetSage2-GGUF", dest="SheetSage2-GGUF")], "CC BY-NC",
          "https://github.com/chrisdonahue/sheetsage", "no", vram_gb=3, detect=["sheetsage2-orig.gguf"]),
    Entry("stable_audio", "Stable Audio 3 Medium", "Music", "Sound effects from a description.",
          [_f("Stable-Audio-3-Medium-GGUF/stable-audio-3-medium-f16.gguf")], "Stability AI Community License",
          "https://huggingface.co/stabilityai/stable-audio-3-medium", "limited", vram_gb=6,
          detect=["stable-audio-3-medium-f16.gguf"], note="Free below USD 1M annual revenue; check the licence."),
    Entry("clap", "CLAP (song style tagger)", "Analysis", "Suggests genre, mood and instruments when you analyse a song.",
          [Source(kind="hf_cache", repo="laion/clap-htsat-unfused",
                  allow=["*.json", "pytorch_model.bin", "merges.txt", "vocab.json"])],
          "Check the model card", "https://huggingface.co/laion/clap-htsat-unfused", "yes",
          detect_cache=[("laion/clap-htsat-unfused", "config.json")]),
    # ── Cleaning and Voice Lab ─────────────────────────────────────────────────
    Entry("roformer", "Mel-Band RoFormer", "Cleaning", "Removes music from voice clips; also used for lyrics.",
          [_f("Mel-Band-RoFormer-GGUF/mel-band-roformer-q8_0.gguf")], "MIT", "https://huggingface.co/audio-cpp/audio.cpp-gguf",
          "yes", vram_gb=1, detect=["mel-band-roformer-q8_0.gguf"], recommended=True),
    Entry("sidon", "Sidon", "Cleaning", "Restores noisy or muffled speech to clean 48 kHz. The default restore step.",
          [_f("Sidon-GGUF/sidon-v0.1-f32.gguf")], "MIT", "https://huggingface.co/audio-cpp/audio.cpp-gguf", "yes",
          vram_gb=1.5, detect=["sidon-v0.1-f32.gguf"], recommended=True),
    Entry("sam_audio", "SAM-Audio Large", "Cleaning", "Keeps only the voice you describe (\"a man speaking\").",
          [_f("sam-audio-large-bf16.gguf", repo="audio-cpp/SAM-Audio-GGUF", dest="SAM-Audio-GGUF")],
          "Meta SAM licence", "https://huggingface.co/facebook", "limited", vram_gb=8, detect=["sam-audio-large-bf16.gguf"],
          note="Adds Meta's inaudible watermark. Check the licence before commercial use."),
    Entry("universr", "UniverSR speech", "Cleaning", "Rebuilds missing high frequencies. Slow (runs on the CPU).",
          [_f("UniverSR-GGUF/universr-speech-orig.gguf")], "CC-BY-4.0", "https://huggingface.co/audio-cpp/audio.cpp-gguf",
          "yes", detect=["universr-speech-orig.gguf"]),
    Entry("seed_vc", "Seed-VC v2", "Voice Lab", "Voice-to-voice: perform a line, hear it in a saved voice.",
          [_f("SeedVC-MLX-GGUF/seed-vc-mlx-f16.gguf")], "GPL-3.0", "https://github.com/Plachtaa/seed-vc", "yes",
          vram_gb=4, detect=["seed-vc-mlx-f16.gguf"]),
    # ── Runtime ────────────────────────────────────────────────────────────────
    Entry("audiocpp", "audio.cpp runtime (Vulkan, portable)", "Runtime",
          "Runs the GGUF models above on your GPU or CPU.",
          [Source(kind="manual", url="https://github.com/0xShug0/audio.cpp/releases", steps=[
              "Download the Windows Vulkan portable build (v0.9.3 or newer) from the official releases page.",
              "Unpack it into <VoiceVault>/runtime/audiocpp/<version>/ (or set audio.cpp folder in Settings → TTS Engines)."])],
          "Apache-2.0", "https://github.com/0xShug0/audio.cpp", "yes", detect=["audiocpp_server.exe", "audiocpp_server"],
          recommended=True),
]

BY_ID = {e.id: e for e in CATALOG}
