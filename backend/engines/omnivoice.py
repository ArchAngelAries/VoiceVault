"""
engines/omnivoice.py — OmniVoice adapter (k2-fsa/OmniVoice, 600+ languages, Apache-2.0 code).

Modes, as OmniVoice.generate() defines them:
  * automatic     no description and no reference: the model chooses a voice
  * voice design  `voice_description` is passed as `instruct`. It must use the model's fixed
                  vocabulary, for example "female, young adult, british accent". Unknown terms
                  are rejected with a message that names the term.
  * cloning       a reference clip of 3-25 seconds of clean, single-speaker speech. The clip's
                  transcript comes from `reference_text`, or from Whisper when it is not given.

Inline non-verbal tags such as [laughter] or [sigh] are passed through in the text.
The package needs transformers >= 5.3 (the audio tokenizer uses HiggsAudioV2TokenizerModel).
The pretrained weights are CC-BY-NC according to the model card; the code is Apache-2.0.
"""
from __future__ import annotations

import gc
import importlib.util
import json
import logging
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from engines.gpu_probe import torch_gpu_available
from engines.base import (
    EngineCapabilities,
    EngineError,
    ModelInfo,
    ModelState,
    SynthesisRequest,
    SynthesisResult,
    TTSEngine,
)

log = logging.getLogger(__name__)

SAMPLE_RATE = 24000
MIN_REFERENCE_SECONDS = 3.0
MAX_REFERENCE_SECONDS = 25.0

NONVERBAL_TAGS = [
    "laughter", "sigh", "sniff", "confirmation-en", "question-en", "question-ah", "question-oh",
    "question-ei", "question-yi", "surprise-ah", "surprise-oh", "surprise-wa", "surprise-yo",
    "dissatisfaction-hnn",
]

VOICE_DESIGN_TERMS = {
    "gender": ["male", "female"],
    "age": ["child", "teenager", "young adult", "middle-aged", "elderly"],
    "pitch": ["very low pitch", "low pitch", "moderate pitch", "high pitch", "very high pitch"],
    "style": ["whisper"],
    "accent": ["american accent", "british accent", "australian accent", "chinese accent",
               "canadian accent", "indian accent", "korean accent", "portuguese accent",
               "russian accent", "japanese accent"],
}


def _package_problem() -> Optional[str]:
    missing = [p for p in ("omnivoice", "librosa", "soundfile") if importlib.util.find_spec(p) is None]
    if missing:
        return f"Missing Python packages: {', '.join(missing)}."
    import transformers

    major, minor = (int(x) for x in transformers.__version__.split(".")[:2])
    if (major, minor) < (5, 3):
        return f"transformers {transformers.__version__} is too old for OmniVoice; 5.3 or newer is needed."
    return None


class OmniVoiceEngine(TTSEngine):
    engine_id = "omnivoice"
    display_name = "OmniVoice"
    capabilities = EngineCapabilities(
        commercial_use="no", license_note="Weights CC BY-NC 4.0: non-commercial use only. Code Apache-2.0.",
        languages=["600+ languages (per the model card)"],
        voice_description=True,
        voice_terms=VOICE_DESIGN_TERMS,
        reference_audio=True,
        reference_transcript=True,
        emotion_tags=NONVERBAL_TAGS,
        delivery_styles=["whisper"],
        delivery_expressions=NONVERBAL_TAGS,
        multi_speaker=False,
        streaming=False,
        seed=True,
        supported_generation_controls=["seed"],
        multilingual=True,
        tag_template="[{tag}]",
        notes=("Voice design takes comma-separated terms (gender, age, pitch, whisper, accent). "
               "Cloning takes a 3-25 s reference clip. Pretrained weights are CC-BY-NC."),
    )

    def __init__(self, transcribe_reference: Optional[Callable[[Path], str]] = None):
        self._transcribe_reference = transcribe_reference
        self._model = None
        self._device: Optional[str] = None
        self._dtype: Optional[str] = None
        self._loaded_model: Optional[ModelInfo] = None

    # ── Discovery ────────────────────────────────────────────────────

    def identify(self, model_dir: Path) -> Optional[ModelInfo]:
        cfg_path = model_dir / "config.json"
        if not cfg_path.is_file():
            return None
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if cfg.get("model_type") != "omnivoice":
            return None
        if not (model_dir / "audio_tokenizer").is_dir() or not (model_dir / "model.safetensors").is_file():
            return None
        size = sum(p.stat().st_size for p in model_dir.rglob("*.safetensors") if p.is_file())
        return ModelInfo(
            id="",
            engine_id=self.engine_id,
            family="OmniVoice",
            architecture="OmniVoice diffusion language model + Higgs audio tokenizer",
            path=str(model_dir),
            size_bytes=size,
            state=ModelState.IDENTIFIED,
            notes="Code Apache-2.0; pretrained weights CC-BY-NC (model card).",
        )

    def runtime_problem(self) -> Optional[str]:
        problem = _package_problem()
        if problem:
            return problem
        if not torch_gpu_available():
            return "No GPU backend available to PyTorch (ROCm/CUDA). CPU runs would be very slow."
        return None

    # ── Lifecycle ───────────────────────────────────────────────────

    def load(self, model: ModelInfo, device: str = "cuda", dtype: str = "float16") -> None:
        import torch
        from omnivoice import OmniVoice

        if dtype not in ("bfloat16", "float16", "float32"):
            raise EngineError(f"Unsupported dtype '{dtype}' for OmniVoice.")
        t0 = time.time()
        self._model = OmniVoice.from_pretrained(model.path, device_map=device, dtype=getattr(torch, dtype))
        self._device, self._dtype, self._loaded_model = device, dtype, model
        log.info("OmniVoice loaded from %s on %s (%s) in %.1fs", model.path, device, dtype, time.time() - t0)

    def unload(self) -> None:
        self._model = None
        self._loaded_model = None
        gc.collect()
        try:
            import torch

            torch.cuda.empty_cache()
        except Exception:  # torch missing or no GPU: nothing to release
            pass

    # ── Synthesis ───────────────────────────────────────────────────

    def _reference_text(self, request: SynthesisRequest) -> str:
        if request.reference_text and request.reference_text.strip():
            return request.reference_text.strip()
        if self._transcribe_reference is None:
            raise EngineError("Cloning needs the reference transcript (reference_text).")
        text = self._transcribe_reference(request.reference_audio).strip()
        if not text:
            raise EngineError("Could not transcribe the reference clip. Type its transcript and try again.")
        return text

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        import soundfile as sf
        import torch

        if self._model is None:
            raise EngineError("OmniVoice is not loaded.")
        text = request.text.strip()
        if not text:
            raise EngineError("Text is empty.")

        kwargs: dict = {}
        mode = "automatic"
        if request.reference_audio is not None:
            seconds = sf.info(str(request.reference_audio)).duration
            if not (MIN_REFERENCE_SECONDS <= seconds <= MAX_REFERENCE_SECONDS):
                raise EngineError(f"The reference clip is {seconds:.1f} s. Use {MIN_REFERENCE_SECONDS:.0f}-"
                                  f"{MAX_REFERENCE_SECONDS:.0f} seconds of clean, single-speaker speech.")
            kwargs["ref_audio"] = str(request.reference_audio)
            kwargs["ref_text"] = self._reference_text(request)
            mode = "clone"
        elif request.voice_description and request.voice_description.strip():
            kwargs["instruct"] = request.voice_description.strip()
            mode = "design"
        if request.language:
            kwargs["language"] = request.language

        if request.seed is not None:
            torch.manual_seed(request.seed)
            torch.cuda.manual_seed_all(request.seed)
        t0 = time.time()
        try:
            with torch.inference_mode():
                audio = self._model.generate(text=text, **kwargs)
        except ValueError as exc:  # unknown voice-design term, bad language, and similar
            raise EngineError(str(exc))
        elapsed = time.time() - t0
        if not audio or len(audio[0]) == 0:
            raise EngineError("OmniVoice returned no audio for this input.")

        samples = np.asarray(audio[0], dtype=np.float32).reshape(-1)
        meta = {
            "engine": self.engine_id,
            "model": self._loaded_model.id if self._loaded_model else None,
            "device": self._device,
            "dtype": self._dtype,
            "mode": mode,
            "language": request.language,
            "instruct": kwargs.get("instruct"),
            "reference_text": kwargs.get("ref_text"),
            "seed": request.seed,
            # OmniVoice returns waveform data only, so it does not expose stop reason.
            "stopped_on_eos": None,
            "truncated": None,
            "timings_s": {"generate": round(elapsed, 3)},
        }
        return SynthesisResult(samples=samples, sample_rate=SAMPLE_RATE, meta=meta)
