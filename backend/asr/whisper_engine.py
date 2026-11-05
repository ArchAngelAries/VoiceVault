"""
asr/whisper_engine.py — Speech-to-text with Whisper large-v3-turbo.

Why this engine:
  * Whisper large-v3-turbo (MIT, openai/whisper-large-v3-turbo): 99 languages, segment timestamps,
    automatic language detection. About 1.6 GB of fp16 weights and about 2 GB of VRAM in use.
    Its accuracy is close to large-v3, but it decodes much faster. It is transcription-only
    (no translate task); large-v3 would be the choice if translation were needed.
  * Runs through Hugging Face transformers on PyTorch, so it uses the same ROCm build as the rest
    of VoiceVault. No new packages are needed.
  * faster-whisper (CTranslate2) has no AMD GPU path, and whisper.cpp would need a native build.

Long audio: transcription uses Whisper's own sequential long-form decoding inside generate() with
timestamp tokens (return_segments=True). The high-level transformers pipeline was tried first, but
its chunk stitching misplaced segment boundaries on a 61 s test clip, so it is not used.
"""
from __future__ import annotations

import gc
import importlib.util
import logging
import shutil
import threading
import time
from typing import Any, Callable, Optional

import numpy as np

from asr.audio import WHISPER_RATE

log = logging.getLogger(__name__)

MODEL_ID = "openai/whisper-large-v3-turbo"
# Without a GPU the large model is ~40x slower than real time on a 12-core CPU (measured 2026-11-05);
# whisper-base runs about 2x real time there, fine for short cloning clips.
CPU_MODEL_ID = "openai/whisper-base"


def _gpu_available() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def segments_from_output(raw_segments: list[dict[str, Any]], decode: Callable[[Any], str]) -> list[dict[str, Any]]:
    """generate(return_segments=True) output -> [{start, end, text}]. Blank segments are dropped."""
    segments = []
    for raw in raw_segments:
        text = decode(raw["tokens"]).strip()
        if not text:
            continue
        start, end = float(raw["start"]), float(raw["end"])
        segments.append({"start": round(start, 2), "end": round(max(end, start), 2), "text": text})
    return segments


class WhisperTranscriber:
    def __init__(self, model_id: str = MODEL_ID):
        self.model_id = model_id
        self.device: Optional[str] = None
        self.dtype: Optional[str] = None
        self._model = None
        self._processor = None
        self._torch_dtype = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def runtime_problem(self) -> Optional[str]:
        missing = [p for p in ("transformers", "accelerate") if importlib.util.find_spec(p) is None]
        if missing:
            return f"Missing Python packages: {', '.join(missing)}."
        if shutil.which("ffmpeg") is None:
            return "ffmpeg is not on PATH."
        if not _gpu_available() and not self.is_cached():
            return ("No GPU found, so transcription uses the small Whisper model on the CPU. "
                    "Download \"Whisper base (CPU)\" in Settings → Models.")
        return None

    def is_cached(self) -> bool:
        from huggingface_hub import try_to_load_from_cache

        return isinstance(try_to_load_from_cache(self.effective_model_id(), "model.safetensors"), str)

    def effective_model_id(self) -> str:
        """The large model on a GPU; the small one when only the CPU is available."""
        if self.model_id == MODEL_ID and not _gpu_available():
            return CPU_MODEL_ID
        return self.model_id

    def load(self, device: str = "cuda", dtype: str = "float16") -> None:
        import torch
        from transformers import WhisperForConditionalGeneration, WhisperProcessor

        if device == "cuda" and not torch.cuda.is_available():
            device, dtype = "cpu", "float32"
        if self.effective_model_id() != self.model_id and self._model is not None:
            self.unload()
        self.model_id = self.effective_model_id()

        with self._lock:
            if self.loaded and (self.device, self.dtype) == (device, dtype):
                return
            self._release()
            t0 = time.time()
            torch_dtype = getattr(torch, dtype)
            self._model = WhisperForConditionalGeneration.from_pretrained(self.model_id, dtype=torch_dtype).to(device).eval()
            self._processor = WhisperProcessor.from_pretrained(self.model_id)
            self._torch_dtype = torch_dtype
            self.device, self.dtype = device, dtype
            log.info("Whisper %s loaded on %s (%s) in %.1fs", self.model_id, device, dtype, time.time() - t0)

    def unload(self) -> None:
        with self._lock:
            self._release()

    def _release(self) -> None:
        self._model = None
        self._processor = None
        self._torch_dtype = None
        self.device = self.dtype = None
        try:
            import torch

            gc.collect()
            torch.cuda.empty_cache()
        except Exception:  # torch missing or no GPU: nothing to release
            pass

    def transcribe(self, samples: np.ndarray, language: Optional[str] = None, timestamps: bool = True) -> dict[str, Any]:
        if not self.loaded:
            raise RuntimeError("Whisper is not loaded.")
        import torch

        duration_s = len(samples) / WHISPER_RATE
        with self._lock:
            t0 = time.time()
            if duration_s <= 30.0:
                # Language detection needs the full 30 s window; long-form input is padded to its own length.
                inputs = self._processor(samples, sampling_rate=WHISPER_RATE, return_tensors="pt",
                                         truncation=True, padding="max_length", return_attention_mask=True)
            else:
                inputs = self._processor(samples, sampling_rate=WHISPER_RATE, return_tensors="pt",
                                         truncation=False, padding="longest", return_attention_mask=True)
            inputs = inputs.to(self.device, dtype=self._torch_dtype)
            kwargs: dict[str, Any] = {"task": "transcribe", "return_timestamps": True, "return_segments": True}
            if language:
                kwargs["language"] = language
            with torch.inference_mode():
                out = self._model.generate(**inputs, **kwargs)
            elapsed = time.time() - t0

        decode = lambda tokens: self._processor.decode(tokens, skip_special_tokens=True)  # noqa: E731
        segments = segments_from_output(out["segments"][0], decode)
        text = " ".join(seg["text"] for seg in segments)
        return {
            "text": text,
            "language": language,
            "duration_s": round(duration_s, 3),
            "segments": segments if timestamps else [],
            "model": self.model_id,
            "device": self.device,
            "dtype": self.dtype,
            "transcribe_seconds": round(elapsed, 3),
            "real_time_factor": round(elapsed / duration_s, 3) if duration_s else None,
        }
