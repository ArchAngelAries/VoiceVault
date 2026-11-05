"""
engines/kokoro.py — Kokoro 82M through audio.cpp: the fast voice for live chat.

- 54 preset voices in 9 languages (no cloning). Pick one by putting its id (e.g. "af_heart") in the voice
  description; the Generate and Converse voice chips list them.
- Runs in its own small audio.cpp process (Vulkan when available, about 1 GB of VRAM; CPU otherwise), so it
  never unloads the main voice (Drama 3) or the chat LLM. Streams PCM as it is generated.
- Needs eSpeak-ng (installed by the user from its official release; found automatically).
- Weights: hexgrad/Kokoro-82M (Apache-2.0), GGUF from audio-cpp/audio.cpp-gguf. Not redistributed by VoiceVault.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import httpx
import numpy as np

from engines.audiocpp_server import AudioCppServer, find_server_executable
from engines.base import (EngineCapabilities, EngineError, ModelInfo, ModelState, SynthesisCancelled,
                          SynthesisRequest, SynthesisResult, TTSEngine)
from engines.fish_drama import SKIP_DIRS, _error_text, _read_gguf_architecture, decode_wav_bytes

SERVER_MODEL_ID = "kokoro"
MAX_DEPTH = 4
LICENSE = "Apache-2.0 (hexgrad/Kokoro-82M)"

VOICES: dict[str, list[str]] = {
    "American English, female": ["af_heart", "af_bella", "af_nicole", "af_aoede", "af_kore", "af_sarah", "af_nova",
                                 "af_sky", "af_alloy", "af_jessica", "af_river"],
    "American English, male": ["am_michael", "am_fenrir", "am_puck", "am_echo", "am_eric", "am_liam", "am_onyx",
                               "am_santa", "am_adam"],
    "British English": ["bf_emma", "bf_isabella", "bf_alice", "bf_lily", "bm_george", "bm_fable", "bm_lewis", "bm_daniel"],
    "Japanese": ["jf_alpha", "jf_gongitsune", "jf_nezumi", "jf_tebukuro", "jm_kumo"],
    "Mandarin Chinese": ["zf_xiaobei", "zf_xiaoni", "zf_xiaoxiao", "zf_xiaoyi", "zm_yunjian", "zm_yunxi", "zm_yunxia",
                         "zm_yunyang"],
    "Spanish": ["ef_dora", "em_alex", "em_santa"],
    "French": ["ff_siwis"],
    "Hindi": ["hf_alpha", "hf_beta", "hm_omega", "hm_psi"],
    "Italian": ["if_sara", "im_nicola"],
    "Brazilian Portuguese": ["pf_dora", "pm_alex", "pm_santa"],
}
ALL_VOICES = [v for group in VOICES.values() for v in group]
DEFAULT_VOICE = "af_heart"
_VOICE_RE = re.compile(r"\b(" + "|".join(ALL_VOICES) + r")\b")


def find_kokoro_ggufs(root: Path, max_depth: int = MAX_DEPTH) -> Iterable[Path]:
    root_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and len(here.parts) - root_depth < max_depth]
        for name in filenames:
            low = name.lower()
            if low.endswith(".gguf") and "kokoro" in low:
                path = here / name
                if _read_gguf_architecture(path) in ("kokoro_tts", "audiocpp", ""):
                    yield path


ESPEAK_URL = "https://github.com/espeak-ng/espeak-ng/releases"


def find_espeak() -> Optional[tuple[Path, Path]]:
    """(libespeak-ng library, espeak-ng-data folder) from the environment, VoiceVault's runtime folder or a
    normal install. eSpeak-ng (GPL-3.0) is installed by the user from its official release, never bundled."""
    lib = os.environ.get("AUDIOCPP_ESPEAK_LIBRARY")
    if lib and Path(lib).is_file():
        data = os.environ.get("AUDIOCPP_ESPEAK_DATA") or str(Path(lib).parent / "espeak-ng-data")
        return Path(lib), Path(data)
    from core.paths import runtime_dir

    candidates = sorted((runtime_dir() / "espeak-ng").glob("*/eSpeak NG"), reverse=True)
    for base in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
        if os.environ.get(base):
            candidates.append(Path(os.environ[base]) / "eSpeak NG")
    for folder in candidates:
        for name in ("libespeak-ng.dll", "libespeak-ng.so", "libespeak-ng.dylib"):
            if (folder / name).is_file() and (folder / "espeak-ng-data" / "phontab").is_file():
                return folder / name, folder / "espeak-ng-data"
    return None


def voice_for(request: SynthesisRequest) -> str:
    m = _VOICE_RE.search(request.voice_description or "")
    return m.group(1) if m else DEFAULT_VOICE


class KokoroEngine(TTSEngine):
    engine_id = "kokoro"
    display_name = "Kokoro 82M (fast chat voice)"
    capabilities = EngineCapabilities(
        languages=["en", "es", "fr", "hi", "it", "ja", "pt", "zh"], multilingual=True,
        voice_description=True, voice_terms=VOICES, long_form=True, streaming=True, seed=True,
        supported_generation_controls=["seed"], cpu_resident=True, commercial_use="yes",
        notes=("Very fast preset voices for live chat (about 15x faster than real time on the GPU). Runs in its own "
               "small process (about 1 GB of VRAM, or the CPU), so it never unloads your main voice or chat model. Choose one of the 54 voices below (it goes in the description). "
               "No cloning. " + LICENSE + "."),
    )

    def __init__(self, server: Optional[AudioCppServer] = None, settings_loader: Optional[Callable[[], dict]] = None):
        from core.paths import DATA_DIR

        self._server = server or AudioCppServer(work_dir=DATA_DIR / "tmp" / "audiocpp-kokoro")
        self._settings_loader = settings_loader
        self._model: Optional[ModelInfo] = None
        self._backend = "cpu"

    def _settings(self) -> dict:
        if self._settings_loader is not None:
            return self._settings_loader()
        from core import settings as app_settings
        return app_settings.load()

    def _exe(self) -> Optional[Path]:
        return find_server_executable(self._settings().get("audiocpp_dir") or "")

    # Discovery ---------------------------------------------------------------
    def identify(self, model_dir: Path) -> Optional[ModelInfo]:
        return None

    def identify_files(self, root: Path) -> list[ModelInfo]:
        return [ModelInfo(id="", engine_id=self.engine_id, family="Kokoro 82M",
                          architecture="Kokoro 82M GGUF (audio.cpp, CPU)", path=str(p), size_bytes=p.stat().st_size,
                          state=ModelState.IDENTIFIED, notes=LICENSE) for p in find_kokoro_ggufs(root)]

    def runtime_problem(self) -> Optional[str]:
        exe = self._exe()
        if exe is None:
            return "The audio.cpp runtime was not found (runtime/audiocpp/<version>/, or audiocpp_dir in Settings)."
        if not (exe.parent / "model_specs" / "kokoro_tts.json").is_file():
            return "This audio.cpp build has no Kokoro model spec. Use v0.9.3 or newer."
        if find_espeak() is None:
            return ("Kokoro needs eSpeak-ng for pronunciation. Install it from " + ESPEAK_URL +
                    " (or unpack it into runtime/espeak-ng/<version>/), then refresh the model list.")
        return None

    # Lifecycle ----------------------------------------------------------------
    def server_config(self, model: ModelInfo) -> dict[str, Any]:
        threads = max(2, min(8, (os.cpu_count() or 8) // 2))
        exe = self._exe()
        # Measured 2026-11-04: a 7 s line takes 0.45 s on Vulkan (RX 7900 XT) but 12.9 s on this CPU with the
        # portable build, so the GPU is used when the build has it (about 1 GB of VRAM, its own process).
        wanted = str(self._settings().get("kokoro_backend") or "auto").lower()
        backend = wanted if wanted in ("cpu", "vulkan") else ("vulkan" if exe is not None and "vulkan" in exe.parent.name.lower() else "cpu")
        self._backend = backend
        return {"backend": backend, "threads": threads, "lazy_load": False, "max_loaded_models": 1, "ui": False,
                "models": [{"id": SERVER_MODEL_ID, "family": "kokoro_tts", "path": Path(model.path).as_posix(),
                            "task": "tts", "mode": "streaming"}]}

    def load(self, model: ModelInfo, device: str = "cpu", dtype: str = "float32") -> None:
        exe = self._exe()
        problem = self.runtime_problem()
        if problem or exe is None:
            raise EngineError(problem or "audio.cpp runtime missing.")
        if not Path(model.path).is_file():
            raise EngineError("The Kokoro GGUF file is missing. Refresh the model list.")
        lib, data = find_espeak()
        os.environ["AUDIOCPP_ESPEAK_LIBRARY"] = str(lib)   # inherited by the audio.cpp process
        os.environ["AUDIOCPP_ESPEAK_DATA"] = str(data)
        try:
            self._server.ensure(exe, self.server_config(model))
        except RuntimeError as exc:
            raise EngineError(str(exc)) from exc
        self._model = model

    def unload(self) -> None:
        self._server.stop()
        self._model = None

    def build_payload(self, request: SynthesisRequest) -> dict[str, Any]:
        options: dict[str, Any] = {"voice_id": voice_for(request), "speed": 1.0}
        if request.seed is not None:
            options["seed"] = int(request.seed)
        return {"model": SERVER_MODEL_ID, "input": request.text, "response_format": "wav", "options": options}

    def _ready(self) -> str:
        if self._model is None or not self._server.running or not self._server.base_url:
            raise EngineError("Kokoro is not loaded.")
        return self._server.base_url

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        base = self._ready()
        started = time.time()
        try:
            r = self._server.http.post(f"{base}/v1/audio/speech", json=self.build_payload(request),
                                       timeout=httpx.Timeout(600.0, connect=5.0))
        except httpx.HTTPError as exc:
            raise EngineError(f"The Kokoro audio.cpp server did not answer ({exc.__class__.__name__}).") from exc
        if r.status_code != 200:
            raise EngineError(f"Kokoro refused the request: {_error_text(r)}")
        samples, rate = decode_wav_bytes(r.content)
        return SynthesisResult(samples=samples, sample_rate=rate, meta={
            "runtime": "audio.cpp", "backend": self._backend, "voice": voice_for(request),
            "seconds": round(time.time() - started, 2), "license": LICENSE})

    def synthesize_stream(self, request: SynthesisRequest, on_audio: Callable[[np.ndarray, int], None],
                          cancel) -> SynthesisResult:
        base = self._ready()
        payload = {**self.build_payload(request), "stream": True, "stream_format": "sse", "response_format": "pcm"}
        started, first, rate, chunks = time.time(), None, 24000, []
        try:
            with self._server.http.stream("POST", f"{base}/v1/audio/speech", json=payload,
                                          timeout=httpx.Timeout(600.0, connect=5.0)) as response:
                if response.status_code != 200:
                    raise EngineError(f"Kokoro refused the request: {response.read().decode('utf-8', 'replace')[:300]}")
                for line in response.iter_lines():
                    if cancel is not None and cancel.is_set():
                        raise SynthesisCancelled("Speech canceled during synthesis.")
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except ValueError:
                        continue
                    if event.get("type") == "error" or event.get("error"):
                        raise EngineError(f"Kokoro stopped: {event.get('error') or event.get('message')}")
                    if event.get("type") != "speech.audio.delta" or not event.get("audio"):
                        continue
                    rate = int(event.get("sample_rate") or rate)
                    pcm = np.frombuffer(base64.b64decode(event["audio"]), dtype="<i2").astype(np.float32) / 32768.0
                    if pcm.size:
                        first = first if first is not None else time.time() - started
                        chunks.append(pcm)
                        on_audio(pcm, rate)
        except httpx.HTTPError as exc:
            raise EngineError(f"The Kokoro audio.cpp server did not answer ({exc.__class__.__name__}).") from exc
        if not chunks:
            raise EngineError("Kokoro returned no audio.")
        return SynthesisResult(samples=np.concatenate(chunks), sample_rate=rate, meta={
            "runtime": "audio.cpp", "backend": self._backend, "voice": voice_for(request), "streamed": True,
            "first_audio_s": round(first or 0.0, 2), "seconds": round(time.time() - started, 2), "license": LICENSE})
