"""
engines/maya1.py — Maya1 expressive TTS adapter (native transformers + SNAC).

Maya1 is a 3B Llama-architecture LM that emits SNAC codec tokens. The recipe here
follows the official transformers_inference.py as implemented by the ComfyUI-Maya1_TTS
node (Apache-2.0, Saganaki22): prompt layout, control-token IDs, EOS handling, the
7-token frame packing and the 2048-sample decoder warm-up trim.

Maya1 has no reference-audio cloning. Voices come from a text description, and
expression comes from inline tags such as <laugh> or <whisper>.
"""
from __future__ import annotations

import gc
import importlib.util
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

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

# Control tokens (Maya1 vocabulary)
SOH_ID = 128259          # start of header
EOH_ID = 128260          # end of header
SOA_ID = 128261          # start of audio
SOS_ID = 128257          # start of speech codes
EOT_ID = 128009          # end of text (also a generic EOS; ignored for stopping)
SNAC_EOS_ID = 128258     # end of speech codes: the only stop token used for generation
SNAC_START = 128266
SNAC_END = 156937
SNAC_CODEBOOK = 4096
MAYA1_VOCAB_SIZE = 156960

SNAC_MODEL_ID = "hubertsiuzdak/snac_24khz"
SAMPLE_RATE = 24000
DECODER_WARMUP_SAMPLES = 2048
MIN_NEW_TOKENS = 28      # at least four 7-token SNAC frames

DEFAULT_DESCRIPTION = (
    "Realistic male voice in the 30s age with american accent. "
    "Normal pitch, warm timbre, conversational pacing."
)

EMOTION_TAGS = [
    "laugh", "laugh_harder", "giggle", "chuckle", "cry", "sigh", "gasp", "whisper",
    "angry", "scream", "snort", "yawn", "cough", "sneeze", "breathing", "humming",
    "throat_clearing",
]

_REQUIRED_PACKAGES = ("transformers", "accelerate", "snac")


def build_prompt(tokenizer: Any, text: str, description: str) -> str:
    """Assemble the prompt exactly as the official recipe does (see module docstring)."""
    formatted = f'<description="{description}"> {text}'
    return (
        tokenizer.decode([SOH_ID]) + tokenizer.bos_token + formatted
        + tokenizer.decode([EOT_ID]) + tokenizer.decode([EOH_ID])
        + tokenizer.decode([SOA_ID]) + tokenizer.decode([SOS_ID])
    )


def _snac_code(token_id: int) -> int:
    return (token_id - SNAC_START) % SNAC_CODEBOOK


def unpack_snac_tokens(token_ids: list[int]) -> tuple[list[list[int]], int]:
    """
    Turn generated token IDs into the three SNAC codebook levels.

    Each frame is 7 tokens: slot0 -> L1 (12 Hz); slot1, slot4 -> L2 (23 Hz);
    slot2, slot3, slot5, slot6 -> L3 (47 Hz). Text tokens are dropped first. A trailing
    partial frame is discarded.
    """
    snac = [t for t in token_ids if SNAC_START <= t <= SNAC_END]
    frames = len(snac) // 7
    l1: list[int] = []
    l2: list[int] = []
    l3: list[int] = []
    for f in range(frames):
        s = snac[f * 7:(f + 1) * 7]
        l1.append(_snac_code(s[0]))
        l2.extend([_snac_code(s[1]), _snac_code(s[4])])
        l3.extend([_snac_code(s[2]), _snac_code(s[3]), _snac_code(s[5]), _snac_code(s[6])])
    return [l1, l2, l3], frames


def _find_tokenizer_dir(model_dir: Path) -> Path:
    sub = model_dir / "tokenizer"
    return sub if sub.is_dir() else model_dir


class Maya1Engine(TTSEngine):
    engine_id = "maya1"
    display_name = "Maya1"
    capabilities = EngineCapabilities(
        commercial_use="yes", license_note="Apache-2.0 (code and weights). Outputs can be used commercially.",
        languages=["en"],
        voice_description=True,
        reference_audio=False,
        emotion_tags=EMOTION_TAGS,
        delivery_emotions=["angry"],
        delivery_styles=["whisper"],
        delivery_expressions=EMOTION_TAGS,
        delivery_tag_map={"emotion:angry": "angry", "style:whisper": "whisper"},
        multi_speaker=False,
        streaming=False,
        seed=True,
        supported_generation_controls=["temperature", "top_p", "repetition_penalty", "max_new_tokens", "seed"],
        notes="Voice from a text description. Expression via inline tags like <laugh>. No cloning.",
    )

    def __init__(self) -> None:
        self._lm = None
        self._tokenizer = None
        self._snac = None
        self._device = None
        self._dtype = None
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
        if cfg.get("model_type") != "llama" or cfg.get("vocab_size") != MAYA1_VOCAB_SIZE:
            return None
        shards = list(model_dir.glob("*.safetensors"))
        if not shards:
            return None
        size = sum(p.stat().st_size for p in shards)
        return ModelInfo(
            id="",  # filled in by the registry
            engine_id=self.engine_id,
            family="Maya1",
            architecture="LlamaForCausalLM (3B) + SNAC 24 kHz codec",
            path=str(model_dir),
            size_bytes=size,
            state=ModelState.IDENTIFIED,
            notes="Apache-2.0 (per ComfyUI-Maya1_TTS README). Needs the SNAC codec from the Hugging Face hub on first use.",
        )

    def runtime_problem(self) -> Optional[str]:
        missing = [p for p in _REQUIRED_PACKAGES if importlib.util.find_spec(p) is None]
        if missing:
            return f"Missing Python packages: {', '.join(missing)}. Install backend/requirements-tts.txt."
        if not torch_gpu_available():
            return "No GPU backend available to PyTorch (ROCm/CUDA). CPU runs would be very slow."
        return None

    # ── Lifecycle ───────────────────────────────────────────────────

    def load(self, model: ModelInfo, device: str = "cuda", dtype: str = "bfloat16") -> None:
        import torch
        from snac import SNAC
        from transformers import AutoModelForCausalLM, AutoTokenizer

        if dtype not in ("bfloat16", "float16", "float32"):
            raise EngineError(f"Unsupported dtype '{dtype}' for Maya1.")
        model_dir = Path(model.path)
        t0 = time.time()
        self._tokenizer = AutoTokenizer.from_pretrained(str(_find_tokenizer_dir(model_dir)))
        self._lm = AutoModelForCausalLM.from_pretrained(
            str(model_dir),
            dtype=getattr(torch, dtype),
            attn_implementation="sdpa",
            device_map=device,
        ).eval()
        try:
            self._snac = SNAC.from_pretrained(SNAC_MODEL_ID).eval().to(device)
        except Exception as exc:  # offline with no cached codec is the usual cause
            self._release()
            raise EngineError(
                f"Could not load the SNAC codec '{SNAC_MODEL_ID}'. Connect to the internet once so it can be "
                f"cached, or place it in the Hugging Face cache. Detail: {exc}"
            ) from exc
        self._device = device
        self._dtype = dtype
        self._loaded_model = model
        log.info("Maya1 loaded from %s on %s (%s) in %.1fs", model_dir, device, dtype, time.time() - t0)

    def unload(self) -> None:
        self._release()

    def _release(self) -> None:
        self._lm = None
        self._tokenizer = None
        self._snac = None
        self._loaded_model = None
        gc.collect()
        try:
            import torch

            torch.cuda.empty_cache()
        except Exception:  # torch missing or no GPU: nothing to release
            pass

    # ── Synthesis ───────────────────────────────────────────────────

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        import torch

        if self._lm is None or self._tokenizer is None or self._snac is None:
            raise EngineError("Maya1 is not loaded.")
        if request.reference_audio is not None:
            raise EngineError("Maya1 does not support reference-audio cloning. Use a voice description instead.")
        text = request.text.strip()
        if not text:
            raise EngineError("Text is empty.")

        description = (request.voice_description or "").strip() or DEFAULT_DESCRIPTION
        prompt = build_prompt(self._tokenizer, text, description)
        inputs = self._tokenizer(prompt, return_tensors="pt").to(self._device)
        n_in = inputs["input_ids"].shape[1]

        if request.seed is not None:
            torch.manual_seed(request.seed)
        t_gen = time.time()
        with torch.inference_mode():
            out = self._lm.generate(
                **inputs,
                max_new_tokens=request.max_new_tokens,
                min_new_tokens=MIN_NEW_TOKENS,
                temperature=request.temperature,
                top_p=request.top_p,
                do_sample=True,
                repetition_penalty=request.repetition_penalty,
                pad_token_id=self._tokenizer.pad_token_id,
                eos_token_id=SNAC_EOS_ID,
                use_cache=True,
            )
        gen_s = time.time() - t_gen

        new_ids = out[0, n_in:].tolist()
        stopped_on_eos = SNAC_EOS_ID in new_ids
        codes, frames = unpack_snac_tokens(new_ids)
        if frames == 0:
            raise EngineError(
                "Maya1 produced no audio tokens. Try a longer text or a different description."
            )

        t_dec = time.time()
        code_tensors = [torch.tensor(level, dtype=torch.long, device=self._device).unsqueeze(0) for level in codes]
        with torch.inference_mode():
            audio = self._snac.decode(code_tensors)
        samples = audio[0, 0].float().cpu().numpy()[DECODER_WARMUP_SAMPLES:]
        dec_s = time.time() - t_dec

        meta = {
            "engine": self.engine_id,
            "model": self._loaded_model.id if self._loaded_model else None,
            "device": self._device,
            "dtype": self._dtype,
            "attention": "sdpa",
            "prompt_tokens": n_in,
            "generated_tokens": len(new_ids),
            "snac_frames": frames,
            "stopped_on_eos": stopped_on_eos,
            "truncated": not stopped_on_eos,
            "temperature": request.temperature,
            "top_p": request.top_p,
            "repetition_penalty": request.repetition_penalty,
            "seed": request.seed,
            "timings_s": {"generate": round(gen_s, 3), "decode": round(dec_s, 3)},
        }
        return SynthesisResult(samples=samples.astype(np.float32), sample_rate=SAMPLE_RATE, meta=meta)
