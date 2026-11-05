"""
engines/vibevoice_engine.py — VibeVoice adapter (Microsoft VibeVoice-Large; English, voice from a reference clip).

The modeling code is vendored under engines/vibevoice/vendor (MIT, Microsoft; the ComfyUI wrapper's notice is kept
in engines/vibevoice/LICENSE.vibevoice-comfyui). It was written for transformers 4.x. The vendored copy carries
small edits for transformers 5.3, each marked "VoiceVault patch". After from_pretrained this adapter also copies
every checkpoint tensor into the live parameter of the same name: on transformers 5.3 the LM head and the speech
connectors were left randomly initialised, and generation was unintelligible until they were reloaded.

Requirements: transformers 5.3.x (the patches were validated on 5.3.0), diffusers (scheduler), a GPU.
The Qwen2.5-1.5B tokenizer is resolved from a configured folder, adjacent/shared model roots,
or its existing Hugging Face cache. Discovery never downloads it.
Model weights: MIT per the model card, which limits use to research and lists out-of-scope uses.
"""
from __future__ import annotations

import gc
import importlib.util
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

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
DIFFUSION_STEPS = 20
CFG_SCALE = 1.3
VENDOR_DIR = Path(__file__).resolve().parent / "vibevoice" / "vendor"
VALIDATED_TRANSFORMERS = "5.3."   # the vendored patches were validated on transformers 5.3.0


def _vendor_on_path() -> None:
    # The upstream code imports 'modular' and 'processor' as top-level packages, so its folder goes on sys.path.
    if str(VENDOR_DIR) not in sys.path:
        sys.path.insert(0, str(VENDOR_DIR))


def _tokenizer_dir(model_dir: Path) -> Optional[Path]:
    """Resolve a complete local Qwen tokenizer; never download it implicitly."""
    from core import settings
    from core.model_paths import huggingface_cache, model_roots

    cfg = settings.load()
    override = os.environ.get("VOICEVAULT_VIBEVOICE_TOKENIZER_DIR") or cfg.get("vibevoice_tokenizer_dir")
    if override:
        candidate = Path(os.path.expandvars(str(override))).expanduser()
        return candidate if (candidate / "tokenizer.json").is_file() and (candidate / "tokenizer_config.json").is_file() else None
    candidates = [model_dir / "tokenizer"]
    candidates += [parent / "vibevoice" / "tokenizer" for parent in model_dir.parents if len(parent.parts) > 1]
    candidates += [parent / "tokenizer" for parent in model_dir.parents if parent.name.lower() == "vibevoice"]
    candidates += [root / "vibevoice" / "tokenizer" for root in model_roots(cfg)]
    repo = huggingface_cache() / "models--Qwen--Qwen2.5-1.5B"
    ref = repo / "refs" / "main"
    if ref.is_file():
        try:
            revision = ref.read_text(encoding="utf-8").strip()
            if revision and Path(revision).name == revision and revision not in {".", ".."}:
                candidates.append(repo / "snapshots" / revision)
        except OSError:
            pass
    snapshots = repo / "snapshots"
    if snapshots.is_dir():
        candidates += sorted(p for p in snapshots.iterdir() if p.is_dir())
    for candidate in candidates:
        if (candidate / "tokenizer.json").is_file() and (candidate / "tokenizer_config.json").is_file():
            return candidate
    return None


def checkpoint_shard_names(model_dir: Path) -> list[str]:
    """List checkpoint files for sharded-index and single-file safetensors layouts."""
    index_path = model_dir / "model.safetensors.index.json"
    if index_path.is_file():
        index = json.loads(index_path.read_text(encoding="utf-8"))["weight_map"]
        return sorted(set(index.values()))
    if (model_dir / "model.safetensors").is_file():
        return ["model.safetensors"]
    raise EngineError(f"No supported safetensors checkpoint found in {model_dir}.")


def generation_completion(sequences, prompt_length: int, eos_token_id, reach_max_step_sample):
    """Read actual EOS and max-step flags from the vendored generation result."""
    generated = sequences[:, prompt_length:]
    stopped_on_eos = bool((generated == eos_token_id).any().item()) if eos_token_id is not None else None
    truncated = bool(reach_max_step_sample[0].item()) if reach_max_step_sample is not None else None
    return stopped_on_eos, truncated


def reload_checkpoint_tensors(model, model_dir: Path) -> list[str]:
    """Copy every checkpoint tensor into the live parameter of the same name. Returns the names that differed."""
    import torch
    from safetensors import safe_open

    shards = checkpoint_shard_names(model_dir)
    params = dict(model.named_parameters())
    corrected: list[str] = []
    for shard in shards:
        with safe_open(str(model_dir / shard), framework="pt", device="cpu") as handle:
            for key in handle.keys():
                live = params.get(key)
                if live is None:
                    continue
                tensor = handle.get_tensor(key).to(device=live.device, dtype=live.dtype)
                if not torch.equal(live.detach(), tensor):
                    live.data.copy_(tensor)
                    corrected.append(key)
    return corrected


class VibeVoiceEngine(TTSEngine):
    engine_id = "vibevoice"
    display_name = "VibeVoice"
    capabilities = EngineCapabilities(
        commercial_use="limited", license_note="Microsoft VibeVoice: MIT code; the model card adds research-use restrictions. Check the model card before commercial use.",
        languages=["en"],
        long_form=True,  # designed for long-form speech; VoiceVault does not split its text
        voice_description=False,
        reference_audio=True,
        reference_required=True,
        multi_speaker=False,
        streaming=False,
        seed=True,
        supported_generation_controls=["max_new_tokens", "seed"],
        notes=("Voice from a 3-25 s reference clip. English tested (the model card also lists Chinese, not tested). "
               "Decoding is fixed (no temperature or top-p): diffusion steps 20, CFG 1.3. "
               "Research-use model card."),
    )

    def __init__(self) -> None:
        self._model = None
        self._processor = None
        self._device: Optional[str] = None
        self._dtype: Optional[str] = None
        self._loaded_model: Optional[ModelInfo] = None
        self._reloaded_count = 0
        self._q8_linear_count = 0

    # ── Discovery ───────────────────────────────────────────────────

    def identify(self, model_dir: Path) -> Optional[ModelInfo]:
        cfg_path = model_dir / "config.json"
        if not cfg_path.is_file():
            return None
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if cfg.get("model_type") != "vibevoice":
            return None
        has_weights = (model_dir / "model.safetensors.index.json").is_file() or (model_dir / "model.safetensors").is_file()
        if not has_weights:
            return None
        size = sum(p.stat().st_size for p in model_dir.rglob("*.safetensors") if p.is_file())
        base = dict(
            id="",
            engine_id=self.engine_id,
            family="VibeVoice",
            architecture="VibeVoice-Large (acoustic and semantic tokenizers, Qwen2 backbone, diffusion head)",
            path=str(model_dir),
            size_bytes=size,
            notes="Modeling code: MIT (Microsoft), vendored from ComfyUI-VibeVoice. Weights: MIT, research-use limit.",
        )
        # The Q8 route accepts only the inspected rowwise bitsandbytes layout.
        if "quantization_config" in cfg:
            from engines.vibevoice_q8 import supports_q8_config

            if not supports_q8_config(cfg):
                return ModelInfo(**base, state=ModelState.BLOCKED,
                                 reason="This quantized VibeVoice layout is unsupported; only the inspected rowwise bitsandbytes int8 format is integrated.")
            base["notes"] += (" Q8 uses plain PyTorch rowwise dequantization for each linear call; it does not use "
                              "bitsandbytes activation quantization or fused kernels. Performance and GPU output are unvalidated.")
        if _tokenizer_dir(model_dir) is None:
            return ModelInfo(**base, state=ModelState.BLOCKED,
                             reason="The Qwen2.5-1.5B tokenizer is missing. Configure its folder, place it at "
                                    "vibevoice/tokenizer in a model root, or use a cached Qwen2.5-1.5B tokenizer.")
        return ModelInfo(**base, state=ModelState.INTEGRATED)

    def runtime_problem(self) -> Optional[str]:
        import transformers

        if not transformers.__version__.startswith(VALIDATED_TRANSFORMERS):
            return (f"VibeVoice is validated on transformers 5.3.x; this environment has {transformers.__version__}.")
        if importlib.util.find_spec("diffusers") is None:
            return "Missing Python package: diffusers (pip install diffusers==0.41.0)."
        if not torch_gpu_available():
            return "No GPU backend available to PyTorch (ROCm/CUDA). CPU runs are not supported here."
        return None

    # ── Lifecycle ───────────────────────────────────────────────────

    def load(self, model: ModelInfo, device: str = "cuda", dtype: str = "bfloat16") -> None:
        import torch

        problem = self.runtime_problem()
        if problem:
            raise EngineError(problem)
        if dtype not in ("bfloat16", "float16", "float32"):
            raise EngineError(f"Unsupported dtype '{dtype}' for VibeVoice.")
        model_dir = Path(model.path)
        tokenizer = _tokenizer_dir(model_dir)
        if tokenizer is None:
            raise EngineError("The Qwen2.5-1.5B tokenizer folder is missing (vibevoice/tokenizer).")

        _vendor_on_path()
        from modular.modeling_vibevoice_inference import VibeVoiceForConditionalGenerationInference
        from processor.vibevoice_processor import VibeVoiceProcessor

        t0 = time.time()
        q8_linear_count = 0
        processor = VibeVoiceProcessor.from_pretrained(
            str(model_dir), language_model_pretrained_name=str(tokenizer), trust_remote_code=True, local_files_only=True,
        )
        raw_config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
        if "quantization_config" in raw_config:
            from engines.vibevoice_q8 import load_vibevoice_q8
            from modular.configuration_vibevoice import VibeVoiceConfig

            net, quantized_count = load_vibevoice_q8(
                model_dir, device=device, dtype=dtype,
                model_cls=VibeVoiceForConditionalGenerationInference,
                config_cls=VibeVoiceConfig,
            )
            corrected = []
            q8_linear_count = quantized_count
            log.info("VibeVoice Q8: loaded %d rowwise int8 linear matrices using torch dequantization", quantized_count)
        else:
            net = VibeVoiceForConditionalGenerationInference.from_pretrained(
                str(model_dir), dtype=getattr(torch, dtype), device_map=device, attn_implementation="sdpa",
                local_files_only=True,
            ).eval()
            corrected = reload_checkpoint_tensors(net, model_dir)
            if corrected:
                log.warning("VibeVoice: transformers left %d tensors unloaded; reloaded them from the checkpoint (%s)",
                            len(corrected), ", ".join(corrected[:12]))
        self._model, self._processor = net, processor
        self._device, self._dtype, self._loaded_model = device, dtype, model
        self._reloaded_count = len(corrected)
        self._q8_linear_count = q8_linear_count
        log.info("VibeVoice loaded from %s on %s (%s) in %.1fs", model.path, device, dtype, time.time() - t0)

    def unload(self) -> None:
        self._model = None
        self._processor = None
        self._loaded_model = None
        self._reloaded_count = 0
        self._q8_linear_count = 0
        gc.collect()
        try:
            import torch

            torch.cuda.empty_cache()
        except Exception:  # torch missing or no GPU: nothing to release
            pass

    # ── Synthesis ───────────────────────────────────────────────────

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        import soundfile as sf
        import torch

        if self._model is None or self._processor is None:
            raise EngineError("VibeVoice is not loaded.")
        text = request.text.strip()
        if not text:
            raise EngineError("Text is empty.")
        if request.reference_audio is None:
            raise EngineError("VibeVoice needs a reference clip (3-25 seconds) for the voice. Upload one first.")
        seconds = sf.info(str(request.reference_audio)).duration
        if not (MIN_REFERENCE_SECONDS <= seconds <= MAX_REFERENCE_SECONDS):
            raise EngineError(f"The reference clip is {seconds:.1f} s. Use {MIN_REFERENCE_SECONDS:.0f}-"
                              f"{MAX_REFERENCE_SECONDS:.0f} seconds of clean, single-speaker speech.")
        reference, reference_rate = sf.read(str(request.reference_audio), dtype="float32")
        if reference.ndim > 1:
            reference = reference.mean(axis=1)
        if reference_rate != SAMPLE_RATE:
            raise EngineError(f"The reference clip is {reference_rate} Hz. Upload it again; VoiceVault stores 24 kHz.")

        # The model expects a speaker label; a single speaker is "Speaker 1".
        if not text.lower().startswith("speaker "):
            text = f"Speaker 1: {text}"
        inputs = self._processor([text], voice_samples=[[reference]], return_tensors="pt", return_attention_mask=True)
        device = next(self._model.parameters()).device
        inputs = {k: (v.to(device) if isinstance(v, torch.Tensor) else v) for k, v in inputs.items()}
        self._model.set_ddpm_inference_steps(DIFFUSION_STEPS)
        if request.seed is not None:
            torch.manual_seed(request.seed)
            torch.cuda.manual_seed_all(request.seed)

        t0 = time.time()
        with torch.no_grad():
            output = self._model.generate(
                **inputs, tokenizer=self._processor.tokenizer, cfg_scale=CFG_SCALE,
                max_new_tokens=request.max_new_tokens, do_sample=False,
            )
        elapsed = time.time() - t0

        speech = output.speech_outputs
        pieces = [p for p in (speech if isinstance(speech, list) else [speech]) if p is not None]
        if not pieces:
            raise EngineError("VibeVoice returned no audio for this input.")
        samples = torch.cat(pieces, dim=-1).float().cpu().numpy().reshape(-1).astype(np.float32)
        eos_id = getattr(self._processor.tokenizer, "eos_id", None)
        if eos_id is None:
            eos_id = getattr(self._processor.tokenizer, "eos_token_id", None)
        stopped_on_eos, reached_limit = generation_completion(
            output.sequences,
            inputs["input_ids"].shape[-1],
            eos_id,
            getattr(output, "reach_max_step_sample", None),
        )
        meta = {
            "engine": self.engine_id,
            "model": self._loaded_model.id if self._loaded_model else None,
            "device": self._device,
            "dtype": self._dtype,
            "mode": "clone",
            "language": "en",
            "instruct": None,
            "reference_text": None,
            "seed": request.seed,
            "diffusion_steps": DIFFUSION_STEPS,
            "cfg_scale": CFG_SCALE,
            "tensors_reloaded_after_load": self._reloaded_count,
            "duration_s": round(len(samples) / SAMPLE_RATE, 3),
            "stopped_on_eos": stopped_on_eos,
            "truncated": reached_limit,
            "timings_s": {"generate": round(elapsed, 3)},
        }
        if self._q8_linear_count:
            meta["quantized_linear_count"] = self._q8_linear_count
            meta["quantization_compute_path"] = "torch_rowwise_dequant_per_linear"
        return SynthesisResult(samples=samples, sample_rate=SAMPLE_RATE, meta=meta)
