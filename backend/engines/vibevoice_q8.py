"""Narrow loader for the local VibeVoice bitsandbytes rowwise-int8 checkpoint.

This keeps int8 matrices resident and dequantizes one matrix to the model's
compute dtype for each linear call. It intentionally does not implement
bitsandbytes activation quantization or its fused kernels. The serialized
weight rule follows bitsandbytes' vectorwise dequantization in
https://github.com/bitsandbytes-foundation/bitsandbytes/blob/main/bitsandbytes/functional.py:
signed int8 values are multiplied by the row SCB scale and divided by 127.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import torch
from torch import nn
from torch.nn import functional as F

from engines.base import EngineError


SUPPORTED_WEIGHT_FORMAT = 0
SUPPORTED_COMPUTE_DTYPE = "bfloat16"


def supports_q8_config(config: dict[str, Any]) -> bool:
    """Return true only for the known 8-bit bnb config, never 4-bit variants."""
    quant = config.get("quantization_config")
    if not isinstance(quant, dict):
        return False
    try:
        threshold_ok = float(quant.get("llm_int8_threshold", -1)) == 3.0
    except (TypeError, ValueError):
        return False
    skip_raw = quant.get("llm_int8_skip_modules")
    if not isinstance(skip_raw, list) or any(not isinstance(item, str) for item in skip_raw):
        return False
    skip_modules = set(skip_raw)
    required_skips = {"prediction_head", "acoustic_connector", "semantic_connector", "acoustic_tokenizer", "semantic_tokenizer"}
    return (
        quant.get("quant_method") == "bitsandbytes"
        and quant.get("load_in_8bit") is True
        and quant.get("_load_in_8bit") is True
        and quant.get("load_in_4bit") is False
        and quant.get("_load_in_4bit") is False
        and threshold_ok
        and quant.get("llm_int8_has_fp16_weight") is False
        and quant.get("bnb_8bit_compute_dtype") == SUPPORTED_COMPUTE_DTYPE
        and required_skips.issubset(skip_modules)
    )


class RowwiseInt8Linear(nn.Module):
    """Int8 matrix plus bnb-style per-output scales, with plain torch math."""

    def __init__(self, linear: nn.Linear):
        super().__init__()
        self.in_features = linear.in_features
        self.out_features = linear.out_features
        self.register_buffer("weight", torch.empty((self.out_features, self.in_features), dtype=torch.int8, device="meta"))
        self.register_buffer("SCB", torch.empty((self.out_features,), dtype=torch.float32, device="meta"))
        self.bias = linear.bias

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        # bnb's vectorwise convention is qweight * SCB[:, None] / 127.
        weight = self.weight.to(device=inputs.device, dtype=inputs.dtype)
        scale = self.SCB.to(device=inputs.device, dtype=inputs.dtype).unsqueeze(1) / 127
        weight.mul_(scale)
        bias = self.bias
        if bias is not None:
            bias = bias.to(device=inputs.device, dtype=inputs.dtype)
        return F.linear(inputs, weight, bias)


def dequantize_rowwise(qweight: torch.Tensor, scb: torch.Tensor, dtype: torch.dtype) -> torch.Tensor:
    """Expose the checkpoint's exact rowwise scale convention for CPU fixtures."""
    if qweight.dtype != torch.int8 or qweight.ndim != 2:
        raise EngineError("Unsupported Q8 matrix: expected a two-dimensional signed int8 weight.")
    if scb.dtype != torch.float32 or scb.shape != (qweight.shape[0],):
        raise EngineError("Unsupported Q8 scales: expected one float32 SCB value per output row.")
    return qweight.to(dtype=dtype) * (scb.to(dtype=dtype).unsqueeze(1) / 127)


def _checkpoint_layout(model_dir: Path) -> tuple[list[str], dict[str, str], dict[str, tuple[int, ...]], set[str]]:
    """Read only safetensors headers and scalar format tags; weight matrices stay unopened."""
    from safetensors import safe_open

    index_path = model_dir / "model.safetensors.index.json"
    if index_path.is_file():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        if not isinstance(index.get("weight_map"), dict):
            raise EngineError("VibeVoice Q8 checkpoint has an invalid safetensors index.")
        indexed = index["weight_map"]
        shards = sorted(set(indexed.values()))
    elif (model_dir / "model.safetensors").is_file():
        shards = ["model.safetensors"]
        indexed = None
    else:
        raise EngineError("No supported safetensors checkpoint was found for VibeVoice Q8.")

    dtypes: dict[str, str] = {}
    shapes: dict[str, tuple[int, ...]] = {}
    quantized: set[str] = set()
    formats: dict[str, int] = {}
    for shard in shards:
        shard_path = (model_dir / shard).resolve()
        if model_dir.resolve() not in shard_path.parents:
            raise EngineError("VibeVoice checkpoint index points outside its model folder.")
        with safe_open(str(shard_path), framework="pt", device="cpu") as handle:
            for key in handle.keys():
                if key in dtypes:
                    raise EngineError(f"Duplicate tensor across VibeVoice Q8 shards: {key}.")
                if indexed is not None and indexed.get(key) != shard:
                    raise EngineError(f"VibeVoice Q8 index maps {key} to the wrong shard.")
                shape = tuple(handle.get_slice(key).get_shape())
                tensor_dtype = handle.get_slice(key).get_dtype()
                dtypes[key] = tensor_dtype
                shapes[key] = shape
                if key.endswith(".weight_format"):
                    value = handle.get_tensor(key)
                    if value.numel() != 1 or value.item() != SUPPORTED_WEIGHT_FORMAT:
                        raise EngineError(f"Unsupported VibeVoice Q8 weight_format at {key}.")
                    formats[key[:-len(".weight_format")]] = int(value.item())
                elif key.endswith(".weight") and tensor_dtype == "I8":
                    if len(shape) != 2:
                        raise EngineError(f"Unsupported non-matrix Q8 tensor: {key}.")
                    quantized.add(key[:-len(".weight")])

    if indexed is not None and set(indexed) != set(dtypes):
        raise EngineError("VibeVoice Q8 safetensors index does not match the shard headers.")
    if not quantized:
        raise EngineError("The checkpoint declares 8-bit weights but contains no recognized int8 matrices.")

    for name in quantized:
        weight_key, scale_key, format_key = name + ".weight", name + ".SCB", name + ".weight_format"
        if dtypes.get(scale_key) != "F32" or scale_key not in dtypes:
            raise EngineError(f"Q8 matrix {weight_key} is missing float32 SCB scales.")
        if dtypes.get(format_key) != "U8" or name not in formats:
            raise EngineError(f"Q8 matrix {weight_key} is missing its scalar weight_format tag.")
        if not (name.startswith("model.language_model.") or name == "lm_head"):
            raise EngineError(f"Q8 matrix outside the supported language model: {weight_key}.")
        weight_shape, scale_shape = shapes[weight_key], shapes[scale_key]
        if scale_shape != (weight_shape[0],):
            raise EngineError(f"SCB scale shape does not match the output rows for {weight_key}.")

    # Other integer encodings and alternate float dtypes are not inferred.
    for key, dtype in dtypes.items():
        if key.endswith(".SCB"):
            if key[:-len(".SCB")] not in quantized:
                raise EngineError(f"Orphan Q8 SCB tensor: {key}.")
        elif key.endswith(".weight_format"):
            if key[:-len(".weight_format")] not in quantized:
                raise EngineError(f"Orphan Q8 weight_format tensor: {key}.")
        elif dtype not in {"BF16", "F16", "F32", "I8"}:
            raise EngineError(f"Unsupported VibeVoice Q8 checkpoint tensor dtype {dtype} at {key}.")
        elif dtype == "I8" and not key.endswith(".weight"):
            raise EngineError(f"Unsupported int8 tensor outside a linear weight: {key}.")
    return shards, dtypes, shapes, quantized


def _get_parent_module(model: nn.Module, dotted_name: str) -> tuple[nn.Module, str]:
    parts = dotted_name.split(".")
    parent = model
    for part in parts[:-1]:
        child = parent._modules.get(part)
        if child is None:
            raise EngineError(f"Checkpoint key does not map to a model module: {dotted_name}.")
        parent = child
    return parent, parts[-1]


def _install_q8_linears(model: nn.Module, quantized: set[str]) -> None:
    modules = dict(model.named_modules())
    for name in quantized:
        linear = modules.get(name)
        if not isinstance(linear, nn.Linear):
            raise EngineError(f"Q8 checkpoint target is not a Linear module: {name}.")
        parent, leaf = _get_parent_module(model, name)
        parent._modules[leaf] = RowwiseInt8Linear(linear)


def _materialize_nonpersistent_buffers(model: nn.Module, device: torch.device) -> None:
    """Recreate the architecture's config-derived buffers that are absent from checkpoints."""
    for module_name, module in model.named_modules():
        nonpersistent = getattr(module, "_non_persistent_buffers_set", set())
        for buffer_name in nonpersistent:
            value = module._buffers.get(buffer_name)
            if value is None or value.device.type != "meta":
                continue
            if buffer_name in {"inv_freq", "original_inv_freq"} and hasattr(module, "compute_default_rope_parameters"):
                inv_freq, scaling = module.compute_default_rope_parameters(module.config, device=device)
                module._buffers["inv_freq"] = inv_freq
                module._buffers["original_inv_freq"] = inv_freq.clone()
                module.attention_scaling = scaling
            elif buffer_name == "fix_std" and hasattr(module, "config"):
                module._buffers[buffer_name] = torch.tensor(module.config.fix_std, device=device, dtype=value.dtype)
            else:
                full_name = f"{module_name}.{buffer_name}" if module_name else buffer_name
                raise EngineError(f"Unsupported meta-only model buffer while loading Q8: {full_name}.")


def load_vibevoice_q8(
    model_dir: Path,
    *,
    device: str,
    dtype: str,
    model_cls: type[nn.Module],
    config_cls: type,
) -> tuple[nn.Module, int]:
    """Build on meta, strictly load known checkpoint tensors, and return the Q8 model."""
    if dtype != SUPPORTED_COMPUTE_DTYPE:
        raise EngineError("This VibeVoice Q8 checkpoint is configured for bfloat16 compute.")
    model_dir = Path(model_dir)
    raw_config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
    if not supports_q8_config(raw_config):
        raise EngineError("This quantized VibeVoice layout is not the supported bitsandbytes rowwise-int8 format.")
    shards, dtypes, shapes, quantized = _checkpoint_layout(model_dir)

    # Remove only the known bnb metadata before constructing the unquantized architecture.
    config_data = dict(raw_config)
    config_data.pop("quantization_config", None)
    config_data["torch_dtype"] = dtype
    config = config_cls.from_dict(config_data)
    config._attn_implementation = "sdpa"
    if hasattr(config, "decoder_config"):
        config.decoder_config._attn_implementation = "sdpa"
    with torch.device("meta"):
        model = model_cls(config)
    _install_q8_linears(model, quantized)

    expected_state = model.state_dict()
    expected = set(expected_state)
    supplied = set(dtypes) - {key for key in dtypes if key.endswith(".weight_format")}
    missing, unexpected = expected - supplied, supplied - expected
    wrong_shapes = [key for key in expected & supplied if tuple(expected_state[key].shape) != shapes[key]]
    if missing or unexpected:
        raise EngineError(
            "VibeVoice Q8 checkpoint keys do not exactly match the model "
            f"(missing: {sorted(missing)[:8]}, unexpected: {sorted(unexpected)[:8]})."
        )
    if wrong_shapes:
        examples = [(key, tuple(expected_state[key].shape), shapes[key]) for key in sorted(wrong_shapes)[:8]]
        raise EngineError(f"VibeVoice Q8 checkpoint tensor shapes do not match the model: {examples}.")

    target = torch.device(device)
    _materialize_nonpersistent_buffers(model, target)
    from safetensors import safe_open

    seen: set[str] = set()
    for shard in shards:
        with safe_open(str(model_dir / shard), framework="pt", device="cpu") as handle:
            for key in handle.keys():
                if key.endswith(".weight_format"):
                    continue
                value = handle.get_tensor(key)
                if key.endswith(".SCB"):
                    if not torch.isfinite(value).all() or (value < 0).any():
                        raise EngineError(f"Invalid non-finite or negative Q8 scale at {key}.")
                    load_dtype = torch.float32
                elif value.is_floating_point():
                    load_dtype = getattr(torch, dtype)
                else:
                    load_dtype = value.dtype
                value = value.to(device=target, dtype=load_dtype)
                parent, leaf = _get_parent_module(model, key)
                if leaf in parent._parameters:
                    old = parent._parameters[leaf]
                    parent._parameters[leaf] = nn.Parameter(value, requires_grad=old.requires_grad)
                elif leaf in parent._buffers:
                    parent._buffers[leaf] = value
                else:
                    raise EngineError(f"Checkpoint tensor does not map to a parameter or buffer: {key}.")
                seen.add(key)

    if seen != expected:
        raise EngineError("VibeVoice Q8 load was incomplete; refusing to use a partially initialized model.")
    if any(parameter.device.type == "meta" for parameter in model.parameters()):
        raise EngineError("VibeVoice Q8 load left a trainable parameter uninitialized.")
    if any(buffer.device.type == "meta" for buffer in model.buffers()):
        raise EngineError("VibeVoice Q8 load left a model buffer uninitialized.")
    return model.eval(), len(quantized)
