from __future__ import annotations

import gc
import importlib.util
import json
import logging
import time
from pathlib import Path
from typing import Optional

import numpy as np

from engines.gpu_probe import torch_bf16_supported, torch_gpu_available
from engines.base import EngineCapabilities, EngineError, ModelInfo, ModelState, SynthesisRequest, SynthesisResult, TTSEngine

log = logging.getLogger(__name__)
SAMPLE_RATE = 24_000
WEIGHT_NAMES = ("Breeze-TTS-2-bf16.safetensors", "model.safetensors")


def checkpoint_path(model_dir: Path) -> Optional[Path]:
    index = model_dir / "model.safetensors.index.json"
    if index.is_file():
        return model_dir
    return next((model_dir / name for name in WEIGHT_NAMES if (model_dir / name).is_file()), None)


def _build_codec(model_dir: Path):
    from engines.breeze_runtime import native
    codec_dir = model_dir / "audio_tokenizer"
    cfg_path = codec_dir / "config.json"
    if not cfg_path.is_file():
        raise EngineError("Breeze audio_tokenizer/config.json is missing.")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    for key in ("architectures", "transformers_version"):
        cfg.pop(key, None)
    from engines.breeze_runtime.vendor.codec_config import Qwen3TTSTokenizerV2Config
    from engines.breeze_runtime.vendor.codec_model import Qwen3TTSTokenizerV2Model

    codec_config = Qwen3TTSTokenizerV2Config(**cfg)
    codec_config._attn_implementation = "sdpa"
    codec = Qwen3TTSTokenizerV2Model(codec_config)
    # Codec is small enough to initialize on CPU without meta loading.
    state = {}
    from safetensors.torch import load_file
    codec_weights = codec_dir / "model.safetensors"
    if codec_weights.is_file():
        state = load_file(str(codec_weights), device="cpu")
    else:
        state = dict(native.iter_checkpoint_items(codec_dir))
    missing, unexpected = codec.load_state_dict(state, strict=False)
    if unexpected:
        log.debug("Ignored %d extra Breeze codec tensors", len(unexpected))
    if missing:
        raise EngineError(f"Breeze codec checkpoint is missing {len(missing)} tensors (first: {missing[:5]}).")
    return codec.eval()


class BreezeEngine(TTSEngine):
    engine_id = "breeze"
    display_name = "Breeze TTS 2"
    capabilities = EngineCapabilities(
        commercial_use="no", license_note="Breeze TTS 2 weights: BreezeBlue research and non-commercial licence. Runtime Apache-2.0.",
        languages=["en", "zh"], voice_description=True, reference_audio=True,
        reference_transcript=True, reference_direction=True, multilingual=True, seed=True,
        max_reference_seconds=60.0,
        emotion_tags=["laugh", "sigh", "cough", "clears throat"],
        delivery_emotions=["cheerful", "sad", "angry", "calm"],
        delivery_styles=["whisper", "dramatic"],
        delivery_expressions=["laugh", "sigh", "cough", "clears throat"],
        delivery_reference=True,
        tag_template="({tag})",
        supported_generation_controls=["temperature", "top_p", "repetition_penalty", "max_new_tokens", "seed"],
        notes=("BF16 model weights with SDPA attention. Supports voice design/direction and reference cloning "
               "with an exact transcript. Depth-decoder sampling is fixed at temperature 0.9, top-k 50, top-p 1.0; "
               "CFG is 1.0 for cloning and 4.0 for design/direction. "
               "No multi-speaker output or streaming. Breeze weights are non-commercial.")
    )

    def __init__(self):
        self._model = self._codec = self._tokenizer = None
        self._device = self._dtype = None
        self._loaded_model: Optional[ModelInfo] = None

    def identify(self, model_dir: Path) -> Optional[ModelInfo]:
        cfg_path = model_dir / "config.json"
        if not cfg_path.is_file():
            return None
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if cfg.get("model_type") != "breeze":
            return None
        weight = checkpoint_path(model_dir)
        tokenizer = (model_dir / "tokenizer.json").is_file() and (model_dir / "tokenizer_config.json").is_file()
        codec = (model_dir / "audio_tokenizer" / "config.json").is_file() and (model_dir / "audio_tokenizer" / "model.safetensors").is_file()
        size = sum(p.stat().st_size for p in model_dir.glob("*.safetensors") if p.is_file())
        size += sum(p.stat().st_size for p in (model_dir / "audio_tokenizer").glob("*.safetensors") if p.is_file()) if (model_dir / "audio_tokenizer").is_dir() else 0
        base = dict(id="", engine_id=self.engine_id, family="Breeze TTS 2", architecture="Breeze + Qwen3-TTS 12 Hz codec", path=str(model_dir), size_bytes=size)
        if weight is None:
            return ModelInfo(**base, state=ModelState.BLOCKED, reason="Breeze BF16 safetensors checkpoint is missing.")
        if not tokenizer:
            return ModelInfo(**base, state=ModelState.BLOCKED, reason="Breeze local tokenizer files are missing.")
        if not codec:
            return ModelInfo(**base, state=ModelState.BLOCKED, reason="Breeze audio_tokenizer files are missing.")
        notes = "Apache-2.0 runtime; BreezeBlue Research and Non-Commercial weights."
        return ModelInfo(**base, state=ModelState.INTEGRATED, notes=notes)

    def runtime_problem(self) -> Optional[str]:
        for package in ("torch", "transformers", "accelerate", "safetensors"):
            if importlib.util.find_spec(package) is None:
                return f"Missing Python package: {package}."
        import transformers
        if not transformers.__version__.startswith("5."):
            return f"Breeze runtime requires transformers 5.x; found {transformers.__version__}."
        if not torch_gpu_available():
            return "Breeze BF16 inference needs a PyTorch CUDA/ROCm GPU backend."
        if not torch_bf16_supported():
            return "Breeze BF16 inference needs GPU bfloat16 support."
        return None

    def load(self, model: ModelInfo, device: str = "cuda", dtype: str = "bfloat16") -> None:
        import torch
        from engines.breeze_runtime import native
        problem = self.runtime_problem()
        if problem:
            raise EngineError(problem)
        if dtype != "bfloat16":
            raise EngineError("Breeze currently supports bfloat16 weights only.")
        if device == "cpu":
            raise EngineError("Breeze BF16 CPU inference is not supported by this adapter.")
        model_dir = Path(model.path)
        weight = checkpoint_path(model_dir)
        if weight is None:
            raise EngineError("Breeze BF16 checkpoint is missing.")
        from transformers import AutoTokenizer
        t0 = time.time()
        model_cfg = native.read_config(model_dir)
        net = native.build_breeze_model(model_cfg, "sdpa")
        def dtype_policy(name):
            if name.endswith("weight_scale"):
                return None
            if name in ("lm_head.weight", "depth_decoder.codebooks_head.weight"):
                return torch.float32
            return torch.bfloat16
        native.load_breeze_weights(net, weight, dtype_policy=dtype_policy)
        codec = _build_codec(model_dir)
        tokenizer = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True, trust_remote_code=False)
        net.to(device).eval()
        codec.to(device).eval()
        self._model, self._codec, self._tokenizer = net, codec, tokenizer
        self._device, self._dtype, self._loaded_model = device, dtype, model
        log.info("Breeze loaded from %s on %s in %.1fs", model.path, device, time.time() - t0)

    def unload(self) -> None:
        runners = getattr(self._model, "_breeze_depth_runners", None)
        if runners is not None:
            runners.clear()
        self._model = self._codec = self._tokenizer = None
        self._device = self._dtype = None
        self._loaded_model = None
        gc.collect()
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        import torch
        # Prompt embeddings and reference encoding also need inference-only allocation.
        with torch.inference_mode():
            return self._synthesize(request)

    def _synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        import soundfile as sf
        import torch
        from engines.breeze_runtime import runtime
        if self._model is None or self._codec is None or self._tokenizer is None:
            raise EngineError("Breeze is not loaded.")
        text = request.text.strip()
        if not text:
            raise EngineError("Text is empty.")
        ref_codes = None
        ref_text = (request.reference_text or "").strip()
        if request.reference_audio is not None:
            if not ref_text:
                raise EngineError("Breeze reference audio requires a transcript.")
            info = sf.info(str(request.reference_audio))
            if info.duration > runtime.MAX_REFERENCE_SECONDS:
                raise EngineError(f"Reference audio exceeds {runtime.MAX_REFERENCE_SECONDS:.0f} seconds.")
            wav, rate = sf.read(str(request.reference_audio), dtype="float32")
            if wav.ndim > 1:
                wav = wav.mean(axis=1)
            if not len(wav):
                raise EngineError("Reference audio is empty.")
            if not np.isfinite(wav).all():
                raise EngineError("Reference audio contains non-finite samples.")
            ref_codes = runtime.encode_reference_audio(self._codec, torch.from_numpy(wav), rate)
            if ref_codes.shape[0] / runtime.FRAMES_PER_SECOND > runtime.MAX_REFERENCE_SECONDS:
                raise EngineError(f"Reference audio exceeds {runtime.MAX_REFERENCE_SECONDS:.0f} seconds.")
        temperature = float(request.temperature)
        top_p = float(request.top_p)
        repetition_penalty = float(request.repetition_penalty)
        if not np.isfinite(temperature) or temperature <= 0:
            raise EngineError("Breeze temperature must be greater than zero.")
        if not np.isfinite(top_p) or not 0 < top_p <= 1:
            raise EngineError("Breeze top_p must be greater than zero and at most 1.")
        if not np.isfinite(repetition_penalty) or repetition_penalty <= 0:
            raise EngineError("Breeze repetition_penalty must be greater than zero.")
        if int(request.max_new_tokens) < 1:
            raise EngineError("Breeze max_new_tokens must be at least 1.")
        instruction = (request.voice_description or "").strip()
        if ref_codes is not None:
            mode = "direction" if instruction else "clone"
            cond = runtime.ref_segments(ref_text, text, instruction, with_instruction=bool(instruction))
            negative = runtime.ref_segments(ref_text, text, "", with_instruction=False) if instruction else None
        else:
            mode = "design"
            cond = runtime.design_segments(text, instruction or "Speak clearly and naturally.")
            negative = runtime.design_negative_segments(text)
        cfg_scale = 1.0 if mode == "clone" else 4.0
        runtime.fix_seed(request.seed)
        embeds, mask, positions, prefill = runtime.build_generation_batch(
            self._model, self._tokenizer, cond_segments=cond,
            negative_segments=negative, ref_codes=ref_codes, cfg_scale=cfg_scale, device=self._device,
        )
        max_frames = min(int(request.max_new_tokens), runtime.MAX_SEQ_LEN - 1 - prefill)
        if max_frames < 1:
            raise EngineError("Breeze prompt leaves no room for generated audio. Shorten the reference or text.")
        params = runtime.GenerationParams(max_new_tokens=max_frames, temperature=temperature,
            top_k=50, top_p=top_p,
            repetition_penalty=repetition_penalty)
        started = time.time()
        with torch.inference_mode():
            codes = runtime.generate_codes(self._model, inputs_embeds=embeds, attention_mask=mask,
                base_positions=positions, prefill_len=prefill, cfg_scale=cfg_scale, params=params,
                decode_mode="eager", return_metadata=True)
            codes, completion = codes
            audio = runtime.decode_codes(self._codec, codes)
        if not audio.numel() or not bool(torch.isfinite(audio).all()):
            raise EngineError("Breeze returned empty or non-finite audio.")
        samples = audio.numpy().reshape(-1).astype(np.float32)
        return SynthesisResult(samples=samples, sample_rate=SAMPLE_RATE, meta={
            "engine": self.engine_id, "model": self._loaded_model.id if self._loaded_model else None,
            "device": self._device, "dtype": self._dtype, "mode": mode,
            "language": request.language, "reference_text": ref_text or None, "seed": request.seed,
            "cfg_scale": cfg_scale, "duration_s": round(len(samples) / SAMPLE_RATE, 3),
            "stopped_on_eos": completion["stopped_on_eos"], "truncated": completion["truncated"],
            "timings_s": {"generate": round(time.time() - started, 3)},
        })
