"""
engines/fish_drama.py — Fish Audio Drama 3 (35B MoE, 3B active), local and cloud.

Built with Fish Audio. Drama 3 weights are under the Fish Audio Community License 1.0
(free for personal/research use; commercial use only below USD 500k annual revenue; no
hosted speech service). Keep the LICENSE/NOTICE files next to the weights.

Two engines share the request mapping:

* FishDramaEngine runs GGUF conversions (Jmica/Drama-3-GGUF, Drama3-small) in a local
  audio.cpp server (engines/audiocpp_server.py). Vulkan works on AMD, NVIDIA and Intel
  GPUs; routed experts can be kept on the CPU (fish_drama3.cpu_expert_layers) so the
  ~16-23 GB packages fit a 16-24 GB card. The original FP8 safetensors checkpoint needs
  SGLang/vLLM on a >=48 GB NVIDIA GPU and is not run here.
* FishDramaCloudEngine sends the request to Fish Audio's API (model header drama-3).
  It needs the fish_audio_api_key setting, is billed per character, and sends the text
  and any reference clips to Fish Audio.

Text can carry speaker tags <|speaker:0|> .. <|speaker:15|> (one voice per reference, in
order) and free-form bracketed cues such as [sighs], [laughs] or [whispers].
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import struct
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import httpx
import numpy as np

from engines.audiocpp_server import AudioCppServer, find_server_executable
from engines.base import (
    SynthesisCancelled,
    EngineCapabilities,
    EngineError,
    ModelInfo,
    ModelState,
    SynthesisRequest,
    SynthesisResult,
    TTSEngine,
)

log = logging.getLogger(__name__)

SAMPLE_RATE = 44100
MAX_SPEAKERS = 16
SPEAKER_TAG = re.compile(r"<\|speaker:(\d{1,2})\|>")
SKIP_DIRS = {".cache", ".no_exist", "blobs", "refs", "__pycache__", ".git"}
MAX_DEPTH = 5
SERVER_MODEL_ID = "drama-3"
FISH_API_URL = "https://api.fish.audio/v1/tts"
ATTRIBUTION = "Built with Fish Audio. Weights: Fish Audio Community License 1.0."

_CUES = ["laughs", "sighs", "whispers", "gasps", "clears throat", "chuckles", "sobs", "pause"]


def _capabilities(*, remote: bool) -> EngineCapabilities:
    return EngineCapabilities(
        languages=["auto"], multilingual=False,  # language is detected from the text; no field to send
        reference_audio=True, reference_transcript=True, max_reference_seconds=60.0,
        multi_speaker=True, max_speakers=MAX_SPEAKERS, long_form=True, remote=remote,
        streaming=not remote,   # local audio.cpp streams PCM as it is generated (live chat)
        commercial_use="limited",
        license_note=("Fish Audio Community License 1.0: free for personal use; commercial use is allowed for "
                      "organisations under the licence's revenue limit; no hosted competing service; credit "
                      "\"Built with Fish Audio\". Check the licence for your case."
                      + (" The cloud API is billed and follows Fish Audio's own terms." if remote else "")),
        emotion_tags=list(_CUES),
        delivery_emotions=["cheerful", "sad", "angry", "calm"],
        delivery_styles=["whisper", "dramatic"],
        delivery_expressions=["laugh", "sigh", "gasp", "whisper", "cough", "clears throat", "cry"],
        delivery_reference=True,
        delivery_tag_map={
            "emotion:cheerful": "cheerful", "emotion:sad": "sad", "emotion:angry": "angry",
            "emotion:calm": "calm", "style:whisper": "whispers", "style:dramatic": "dramatic",
        },
        tag_template="[{tag}]",
        seed=not remote,
        supported_generation_controls=["temperature", "top_p"] + ([] if remote else ["seed"]),
        notes=(("Fish Audio cloud API (billed per character); text and reference clips are sent to Fish Audio. "
                if remote else
                "Local GGUF through audio.cpp (Vulkan or CPU). Experimental community port; quantized. ")
               + "Clone from a reference clip with its exact transcript, or leave the reference empty for a "
                 "model-chosen voice. Up to 16 voices with <|speaker:N|> tags. Bracketed cues such as [sighs] "
                 "are free-form and not always reliable. Long text is chunked by the runtime. " + ATTRIBUTION),
    )


# ── Shared helpers ───────────────────────────────────────────────────────────

def speaker_indices(text: str) -> list[int]:
    return sorted({int(m) for m in SPEAKER_TAG.findall(text)})


def validate_speakers(request: SynthesisRequest) -> None:
    used = speaker_indices(request.text)
    if any(i >= MAX_SPEAKERS for i in used):
        raise EngineError(f"Speaker tags run from <|speaker:0|> to <|speaker:{MAX_SPEAKERS - 1}|>.")
    voices = len(request.speakers) or (1 if request.reference_audio else 0)
    if request.speakers and used and max(used) >= voices:
        raise EngineError(f"The text uses <|speaker:{max(used)}|> but only {voices} voice(s) were given. "
                          "Add a reference clip for every speaker tag.")
    for i, spk in enumerate(request.speakers):
        if not Path(spk.audio).is_file():
            raise EngineError(f"Reference clip for speaker {i} is missing.")
        if not (spk.text or "").strip():
            raise EngineError(f"Speaker {i} needs the exact transcript of its reference clip.")


def best_reference(path: Path) -> Path:
    """Use the clip's full-band 44.1 kHz copy when it exists (Drama 3's codec runs at 44.1 kHz)."""
    from engines.audio_store import hq_reference_path

    hq = hq_reference_path(Path(path))
    return hq if hq.is_file() else Path(path)


def decode_wav_bytes(data: bytes) -> tuple[np.ndarray, int]:
    import soundfile as sf

    try:
        samples, rate = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    except Exception as exc:
        raise EngineError(f"The speech service returned audio VoiceVault could not read ({exc}).") from exc
    mono = samples.mean(axis=1) if samples.shape[1] > 1 else samples[:, 0]
    if mono.size == 0:
        raise EngineError("The speech service returned empty audio.")
    if not np.isfinite(mono).all():
        raise EngineError("The speech service returned invalid audio samples.")
    return np.ascontiguousarray(mono, dtype=np.float32), int(rate)


def _error_text(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300].strip() or f"HTTP {response.status_code}"
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict):
            return str(err.get("message") or err.get("reason") or err)[:300]
        return str(err)[:300]
    return str(body)[:300]


# ── GGUF discovery (header only, never the weights) ─────────────────────────

def _read_gguf_architecture(path: Path) -> Optional[str]:
    """Return general.architecture from a GGUF header, or None if the file is not GGUF."""
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"GGUF":
                return None
            version, = struct.unpack("<I", f.read(4))
            if version < 2:
                return None
            f.read(16)  # tensor count, kv count
            for _ in range(8):  # architecture is written first by every known converter
                klen, = struct.unpack("<Q", f.read(8))
                if klen > 256:
                    return ""
                key = f.read(klen).decode("utf-8", "replace")
                vtype, = struct.unpack("<I", f.read(4))
                if vtype != 8:  # only plain strings are expected before the architecture
                    return ""
                vlen, = struct.unpack("<Q", f.read(8))
                if vlen > 4096:
                    return ""
                value = f.read(vlen).decode("utf-8", "replace")
                if key == "general.architecture":
                    return value
    except (OSError, struct.error):
        return None
    return ""


def find_drama_ggufs(root: Path, max_depth: int = MAX_DEPTH) -> Iterable[Path]:
    root_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        depth = len(here.parts) - root_depth
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and depth < max_depth]
        for name in filenames:
            low = name.lower()
            if low.endswith(".gguf") and "drama" in low and "3" in low:
                path = here / name
                arch = _read_gguf_architecture(path)
                if arch is not None and arch in ("audiocpp", ""):
                    yield path


def _describe_package(name: str) -> str:
    low = name.lower()
    if "small" in low:
        return "Drama3-small GGUF (" + ("IQ3_XXS" if "iq3" in low else "quantized") + ")"
    experts = next((q for q in ("Q2_K", "Q3_K", "Q4_K", "Q5_K", "Q6_K", "Q8_0") if q.lower() in low), "quantized")
    backbone = "Q4 backbone" if "backbone-4bit" in low else "Q8 backbone"
    # VoiceVault's own conversion is named drama-3-final-*; the community packages came from the preview.
    weights = "final weights, Oct 29" if "final" in low else "preview weights, Oct 13"
    if "routers" in low:
        weights += "; full-precision routers and speech head, max quality, slower"
    return f"35B MoE GGUF ({experts} experts, {backbone}; {weights})"


# ── GPU / system-RAM split ───────────────────────────────────────────────────

LAYERS = 40                 # Drama 3 backbone layers, each with one routed-expert bank
NON_EXPERT_BYTES = 3.5e9    # backbone (Q8), audio tower, codec and embeddings stay on the GPU
RUNTIME_OVERHEAD = 1.5e9    # KV cache at 2048 context, graphs and codec work buffers
SAFETY_MARGIN = 0.5e9


def _windows_dedicated_used() -> Optional[int]:
    """Dedicated GPU memory in use across all processes (largest adapter), from Windows counters."""
    import subprocess

    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Counter '\\GPU Adapter Memory(*)\\Dedicated Usage').CounterSamples | "
             "Measure-Object -Property CookedValue -Maximum | Select-Object -ExpandProperty Maximum"],
            capture_output=True, text=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return int(float(out.stdout.strip())) if out.returncode == 0 and out.stdout.strip() else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def free_vram_bytes() -> Optional[int]:
    """Free memory on the first GPU, or None when it cannot be measured.

    On Windows with ROCm, torch's free figure ignores other processes (it reported 21.3 GB free
    on a 21.5 GB card while ComfyUI held 5 GB), so the free amount is the card total minus the
    Windows dedicated-usage counter.
    """
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        free, total = torch.cuda.mem_get_info(0)
    except Exception:  # no GPU stack, or the driver refused the query
        return None
    if os.name == "nt":
        used = _windows_dedicated_used()
        if used is not None:
            return max(0, int(total) - used)
    return int(free)


def auto_cpu_expert_layers(package_bytes: int, free_vram: Optional[int]) -> int:
    """Fewest expert layers to keep in system RAM so the rest fits the free VRAM.

    Calibrated against audio.cpp's published configurations: Q2 → 8 layers and Q4 → 24 on a
    16 GB card, Q4 → 4 on a 24 GB card. Unknown VRAM falls back to the 16 GB recommendation.
    """
    per_layer = max(1.0, (package_bytes - NON_EXPERT_BYTES) / LAYERS)
    budget = (free_vram if free_vram else 15.5e9) - RUNTIME_OVERHEAD - SAFETY_MARGIN
    excess = package_bytes - budget
    if excess <= 0:
        return 0
    return int(min(LAYERS, -(-excess // per_layer)))


# ── Local engine (audio.cpp) ─────────────────────────────────────────────────

class FishDramaEngine(TTSEngine):
    engine_id = "fish_drama3"
    display_name = "Fish Audio Drama 3 (local)"
    capabilities = _capabilities(remote=False)

    def __init__(self, settings_loader: Optional[Callable[[], dict]] = None,
                 server: Optional[AudioCppServer] = None):
        self._settings_loader = settings_loader
        self._server = server or AudioCppServer()
        self._model: Optional[ModelInfo] = None
        self._backend: Optional[str] = None
        self._cpu_expert_layers: Optional[int] = None
        self._loaded_at = 0.0
        self._first_request = True

    def _settings(self) -> dict:
        if self._settings_loader is not None:
            return self._settings_loader()
        from core import settings as app_settings
        return app_settings.load()

    def _exe(self) -> Optional[Path]:
        return find_server_executable(str(self._settings().get("audiocpp_dir") or ""))

    # Discovery -------------------------------------------------------------
    def identify(self, model_dir: Path) -> Optional[ModelInfo]:
        return None  # GGUF packages have no config.json; see identify_files()

    def identify_files(self, root: Path) -> list[ModelInfo]:
        found = []
        for path in find_drama_ggufs(root):
            found.append(ModelInfo(
                id="", engine_id=self.engine_id, family="Fish Audio Drama 3",
                architecture=_describe_package(path.name), path=str(path),
                size_bytes=path.stat().st_size, state=ModelState.IDENTIFIED,
                notes=ATTRIBUTION,
            ))
        return found

    def runtime_problem(self) -> Optional[str]:
        exe = self._exe()
        if exe is None:
            return ("The audio.cpp runtime was not found. Put the audio.cpp v0.9.3+ Windows build "
                    "(audiocpp_server.exe) in runtime/audiocpp/<version>/, or set audiocpp_dir in Settings.")
        if not (exe.parent / "model_specs" / "fish_drama3.json").is_file():
            return "This audio.cpp build has no fish_drama3 model spec. Use v0.9.3 or newer."
        return None

    # Lifecycle -------------------------------------------------------------
    def server_config(self, model: ModelInfo, device: str) -> dict[str, Any]:
        cfg = self._settings()
        exe = self._exe()
        wanted = str(cfg.get("drama3_backend") or "auto").lower()
        if wanted == "auto":
            gpu_build = exe is not None and "vulkan" in exe.parent.name.lower()
            backend = "vulkan" if device != "cpu" and gpu_build else "cpu"
        else:
            backend = "cpu" if device == "cpu" else wanted
        threads = int(cfg.get("drama3_threads") or max(1, (os.cpu_count() or 8) // 2))
        session: dict[str, Any] = {
            "fish_drama3.context_length": int(cfg.get("drama3_context_length") or 4096),
        }
        if backend != "cpu":
            layers = cfg.get("drama3_cpu_expert_layers", -1)
            layers = int(layers) if layers not in (None, "", "auto") else -1
            if layers < 0:
                layers = auto_cpu_expert_layers(Path(model.path).stat().st_size, free_vram_bytes())
            self._cpu_expert_layers = layers
            session["fish_drama3.cpu_expert_layers"] = layers
            if backend == "vulkan":
                # Documented fix for incomplete Vulkan voice cloning.
                session["fish_drama3.codec_encoder_f32_accumulation"] = True
        self._backend = backend
        return {
            "backend": backend, "threads": threads, "lazy_load": True, "max_loaded_models": 1,
            "ui": False,
            "models": [{
                "id": SERVER_MODEL_ID, "family": "fish_drama3", "path": Path(model.path).as_posix(),
                # Streaming mode also serves normal full renders (checked 2026-11-04), so one
                # loaded server covers Generate and live chat without a reload.
                "task": "tts", "mode": "streaming", "session_options": session,
            }],
        }

    def load(self, model: ModelInfo, device: str = "cuda", dtype: str = "bfloat16") -> None:
        exe = self._exe()
        problem = self.runtime_problem()
        if problem or exe is None:
            raise EngineError(problem or "audio.cpp runtime missing.")
        if not Path(model.path).is_file():
            raise EngineError("The Drama 3 GGUF file is missing. Refresh the model list.")
        try:
            self._server.ensure(exe, self.server_config(model, device))
        except RuntimeError as exc:
            raise EngineError(str(exc)) from exc
        self._model = model
        self._loaded_at = time.time()
        self._first_request = True

    def unload(self) -> None:
        self._server.stop()
        self._model = None

    def build_payload(self, request: SynthesisRequest) -> dict[str, Any]:
        validate_speakers(request)
        options: dict[str, Any] = {
            "temperature": float(min(max(request.temperature, 0.0), 2.0)),
            "top_p": float(min(max(request.top_p, 0.05), 1.0)),
            "max_tokens": int(min(max(request.max_new_tokens, 64), 1024)),
        }
        if request.seed is not None:
            options["seed"] = int(request.seed)
        payload: dict[str, Any] = {"model": SERVER_MODEL_ID, "input": request.text, "response_format": "wav"}
        if request.speakers:
            options["references"] = json.dumps([
                {"text": f"<|speaker:{i}|> {spk.text.strip()}", "audio_path": best_reference(spk.audio).resolve().as_posix()}
                for i, spk in enumerate(request.speakers)
            ], ensure_ascii=False)
        elif request.reference_audio:
            if not (request.reference_text or "").strip():
                raise EngineError("Drama 3 cloning needs the exact transcript of the reference clip.")
            payload["voice_ref"] = {"type": "path", "path": best_reference(request.reference_audio).resolve().as_posix()}
            payload["reference_text"] = request.reference_text.strip()
        payload["options"] = options
        return payload

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        if self._model is None or not self._server.running or not self._server.base_url:
            raise EngineError("Drama 3 is not loaded.")
        payload = self.build_payload(request)
        started = time.time()
        try:
            response = self._server.http.post(f"{self._server.base_url}/v1/audio/speech", json=payload,
                                              timeout=httpx.Timeout(1800.0, connect=5.0))
        except httpx.HTTPError as exc:
            raise EngineError(f"The local audio.cpp server did not answer ({exc.__class__.__name__}).") from exc
        if response.status_code != 200:
            detail = _error_text(response)
            if "context_length" in detail:
                raise EngineError("The reference clip plus the text is too long for Drama 3's context. Raise "
                                  "'Context length' in Settings → TTS Engines (e.g. 6144), or use a shorter reference.")
            raise EngineError(f"Drama 3 (audio.cpp) refused the request: {detail}")
        samples, rate = decode_wav_bytes(response.content)
        elapsed = time.time() - started
        first = self._first_request
        self._first_request = False
        return SynthesisResult(samples=samples, sample_rate=rate, meta={
            "runtime": "audio.cpp", "backend": self._backend, "package": Path(self._model.path).name,
            "cpu_expert_layers": self._cpu_expert_layers,
            "seconds": round(elapsed, 2), "includes_model_load": first,
            "speakers": len(request.speakers) or (1 if request.reference_audio else 0),
            "completion": "unknown",  # the HTTP response does not report EOS or the token cap
            "attribution": ATTRIBUTION,
        })


    def synthesize_stream(self, request: SynthesisRequest, on_audio: Callable[[np.ndarray, int], None],
                          cancel) -> SynthesisResult:
        """Stream PCM from audio.cpp as it is generated, so playback starts before the line is finished."""
        if self._model is None or not self._server.running or not self._server.base_url:
            raise EngineError("Drama 3 is not loaded.")
        payload = {**self.build_payload(request), "stream": True, "stream_format": "sse", "response_format": "pcm"}
        started = time.time()
        first: Optional[float] = None
        chunks: list[np.ndarray] = []
        rate = 44100
        try:
            with self._server.http.stream("POST", f"{self._server.base_url}/v1/audio/speech", json=payload,
                                          timeout=httpx.Timeout(1800.0, connect=5.0)) as response:
                if response.status_code != 200:
                    detail = _error_text(response) if response.is_closed else response.read().decode("utf-8", "replace")[:300]
                    raise EngineError(f"Drama 3 (audio.cpp) refused the request: {detail}")
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
                        raise EngineError(f"Drama 3 (audio.cpp) stopped: {event.get('error') or event.get('message')}")
                    if event.get("type") != "speech.audio.delta" or not event.get("audio"):
                        continue
                    rate = int(event.get("sample_rate") or rate)
                    pcm = np.frombuffer(base64.b64decode(event["audio"]), dtype="<i2").astype(np.float32) / 32768.0
                    if pcm.size:
                        if first is None:
                            first = time.time() - started
                        chunks.append(pcm)
                        on_audio(pcm, rate)
        except httpx.HTTPError as exc:
            raise EngineError(f"The local audio.cpp server did not answer ({exc.__class__.__name__}).") from exc
        if not chunks:
            raise EngineError("Drama 3 returned no audio.")
        samples = np.concatenate(chunks)
        load_included = self._first_request
        self._first_request = False
        return SynthesisResult(samples=samples, sample_rate=rate, meta={
            "runtime": "audio.cpp", "backend": self._backend, "package": Path(self._model.path).name,
            "cpu_expert_layers": self._cpu_expert_layers, "streamed": True,
            "first_audio_s": round(first or 0.0, 2), "seconds": round(time.time() - started, 2),
            "includes_model_load": load_included,
            "speakers": len(request.speakers) or (1 if request.reference_audio else 0),
            "completion": "unknown", "attribution": ATTRIBUTION,
        })


# ── Cloud engine (Fish Audio API) ────────────────────────────────────────────

class FishDramaCloudEngine(TTSEngine):
    engine_id = "fish_drama3_cloud"
    display_name = "Fish Audio Drama 3 (cloud)"
    capabilities = _capabilities(remote=True)
    MODEL_SUFFIX = "fish-audio-api"

    def __init__(self, settings_loader: Optional[Callable[[], dict]] = None,
                 http: Optional[httpx.Client] = None):
        self._settings_loader = settings_loader
        self._http = http
        self._loaded = False

    def _settings(self) -> dict:
        if self._settings_loader is not None:
            return self._settings_loader()
        from core import settings as app_settings
        return app_settings.load()

    def _key(self) -> str:
        return str(self._settings().get("fish_audio_api_key") or "").strip()

    def identify(self, model_dir: Path) -> Optional[ModelInfo]:
        return None

    def virtual_models(self) -> list[ModelInfo]:
        problem = self.runtime_problem()
        return [ModelInfo(
            id=f"{self.engine_id}:{self.MODEL_SUFFIX}", engine_id=self.engine_id,
            family="Fish Audio Drama 3", architecture="Cloud API (Fish Audio, model drama-3)",
            path="", size_bytes=0, state=ModelState.BLOCKED if problem else ModelState.INTEGRATED,
            reason=problem, notes="Paid cloud service: text and reference clips are sent to Fish Audio. " + ATTRIBUTION,
        )]

    def runtime_problem(self) -> Optional[str]:
        if not self._key():
            return "Add a Fish Audio API key (fish_audio_api_key) in Settings to use the cloud service."
        return None

    def load(self, model: ModelInfo, device: str = "cuda", dtype: str = "bfloat16") -> None:
        problem = self.runtime_problem()
        if problem:
            raise EngineError(problem)
        self._loaded = True

    def unload(self) -> None:
        self._loaded = False

    def build_body(self, request: SynthesisRequest) -> tuple[dict[str, Any], bool]:
        """Return the request body and whether it must be sent as MessagePack (inline audio)."""
        validate_speakers(request)
        body: dict[str, Any] = {
            "text": request.text, "format": "wav", "sample_rate": SAMPLE_RATE, "normalize": True,
            "latency": "normal",
            "temperature": float(min(max(request.temperature, 0.0), 1.0)),
            "top_p": float(min(max(request.top_p, 0.0), 1.0)),
        }
        if request.speakers:
            body["references"] = [[{"audio": best_reference(s.audio).read_bytes(), "text": s.text.strip()}]
                                  for s in request.speakers]
            body["reference_id"] = [f"voicevault-speaker-{i}" for i in range(len(request.speakers))]
            return body, True
        if request.reference_audio:
            if not (request.reference_text or "").strip():
                raise EngineError("Drama 3 cloning needs the exact transcript of the reference clip.")
            body["references"] = [{"audio": best_reference(request.reference_audio).read_bytes(),
                                   "text": request.reference_text.strip()}]
            return body, True
        return body, False

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        key = self._key()
        if not key:
            raise EngineError(self.runtime_problem() or "Missing API key.")
        body, packed = self.build_body(request)
        headers = {"Authorization": f"Bearer {key}", "model": "drama-3"}
        if packed:
            import msgpack
            content = msgpack.packb(body, use_bin_type=True)
            headers["Content-Type"] = "application/msgpack"
        else:
            content = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        client = self._http or httpx.Client(timeout=httpx.Timeout(600.0, connect=10.0))
        started = time.time()
        try:
            response = client.post(FISH_API_URL, content=content, headers=headers)
        except httpx.HTTPError as exc:
            raise EngineError(f"Fish Audio did not answer ({exc.__class__.__name__}).") from exc
        finally:
            if self._http is None:
                client.close()
        if response.status_code == 401:
            raise EngineError("Fish Audio rejected the API key.")
        if response.status_code == 402:
            raise EngineError("Fish Audio reports no remaining credit on this account.")
        if response.status_code == 503:
            raise EngineError("Fish Audio is under high load. Try again shortly.")
        if response.status_code != 200:
            raise EngineError(f"Fish Audio refused the request: {_error_text(response)}")
        samples, rate = decode_wav_bytes(response.content)
        return SynthesisResult(samples=samples, sample_rate=rate, meta={
            "runtime": "fish-audio-api", "model": "drama-3", "seconds": round(time.time() - started, 2),
            "characters_billed": len(request.text), "completion": "unknown", "attribution": ATTRIBUTION,
        })
