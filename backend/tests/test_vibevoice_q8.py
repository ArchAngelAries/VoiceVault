"""CPU fixtures for the narrowly supported VibeVoice rowwise-int8 path."""
import json

import pytest
import torch
from safetensors.torch import save_file

from engines.base import EngineError
from engines.vibevoice_q8 import (
    RowwiseInt8Linear,
    dequantize_rowwise,
    load_vibevoice_q8,
    supports_q8_config,
)


QUANT_CONFIG = {
    "quant_method": "bitsandbytes",
    "_load_in_8bit": True,
    "load_in_8bit": True,
    "_load_in_4bit": False,
    "load_in_4bit": False,
    "llm_int8_threshold": 3.0,
    "llm_int8_has_fp16_weight": False,
    "bnb_8bit_compute_dtype": "bfloat16",
    "llm_int8_skip_modules": ["prediction_head", "acoustic_connector", "semantic_connector", "acoustic_tokenizer", "semantic_tokenizer"],
}


class FakeConfig:
    def __init__(self, values):
        self.values = values

    @classmethod
    def from_dict(cls, raw):
        return cls(raw)


class FakeModel(torch.nn.Module):
    def __init__(self, _config):
        super().__init__()
        self.model = torch.nn.Module()
        self.model.language_model = torch.nn.Module()
        self.model.language_model.proj = torch.nn.Linear(2, 2, bias=True)
        self.model.language_model.offset = torch.nn.Parameter(torch.empty(2))

    def forward(self, value):
        return self.model.language_model.proj(value) + self.model.language_model.offset


def _fixture(tmp_path, *, include_bias=True, format_value=0, wrong_offset_shape=False, split_scale=False, wrong_index_mapping=False):
    (tmp_path / "config.json").write_text(json.dumps({"quantization_config": QUANT_CONFIG}), encoding="utf-8")
    tensors = {
        "model.language_model.proj.weight": torch.tensor([[127, -127], [64, 0]], dtype=torch.int8),
        "model.language_model.proj.SCB": torch.tensor([1.0, 1.984375], dtype=torch.float32),
        "model.language_model.proj.weight_format": torch.tensor(format_value, dtype=torch.uint8),
        "model.language_model.offset": torch.tensor([0.5], dtype=torch.float32) if wrong_offset_shape else torch.tensor([0.5, -1.0], dtype=torch.float32),
    }
    if include_bias:
        tensors["model.language_model.proj.bias"] = torch.tensor([1.0, 2.0], dtype=torch.float32)
    if split_scale:
        scale_key = "model.language_model.proj.SCB"
        save_file({key: value for key, value in tensors.items() if key != scale_key}, str(tmp_path / "model-00001-of-00002.safetensors"))
        save_file({scale_key: tensors[scale_key]}, str(tmp_path / "model-00002-of-00002.safetensors"))
        shard_for = {key: ("model-00002-of-00002.safetensors" if key == scale_key else "model-00001-of-00002.safetensors") for key in tensors}
        if wrong_index_mapping:
            shard_for[scale_key] = "model-00001-of-00002.safetensors"
        (tmp_path / "model.safetensors.index.json").write_text(json.dumps({"weight_map": shard_for}), encoding="utf-8")
    else:
        save_file(tensors, str(tmp_path / "model.safetensors"))
        (tmp_path / "model.safetensors.index.json").write_text(
            json.dumps({"weight_map": {key: "model.safetensors" for key in tensors}}), encoding="utf-8"
        )
    return tmp_path


def test_config_accepts_only_known_bnb_int8_shape():
    assert supports_q8_config({"quantization_config": QUANT_CONFIG})
    assert not supports_q8_config({"quantization_config": {**QUANT_CONFIG, "load_in_4bit": True}})
    assert not supports_q8_config({"quantization_config": {**QUANT_CONFIG, "llm_int8_threshold": 6.0}})


def test_dequantization_uses_per_output_scale_and_divides_by_127():
    qweight = torch.tensor([[127, -127], [64, 0]], dtype=torch.int8)
    scales = torch.tensor([1.0, 1.984375], dtype=torch.float32)
    expected = torch.tensor([[1, -1], [1, 0]], dtype=torch.float32)
    torch.testing.assert_close(dequantize_rowwise(qweight, scales, torch.float32), expected)


def test_rowwise_linear_matches_plain_torch_linear_on_cpu():
    original = torch.nn.Linear(2, 2, bias=True)
    layer = RowwiseInt8Linear(original)
    layer.weight = torch.tensor([[127, -127], [64, 0]], dtype=torch.int8)
    layer.SCB = torch.tensor([1.0, 1.984375], dtype=torch.float32)
    layer.bias = torch.nn.Parameter(torch.tensor([1.0, 2.0]))
    x = torch.tensor([[2.0, 3.0]])
    expected = torch.nn.functional.linear(x, torch.tensor([[1.0, -1.0], [1.0, 0.0]]), layer.bias)
    torch.testing.assert_close(layer(x), expected)
    torch.testing.assert_close(layer(x), expected)
    torch.testing.assert_close(layer.SCB, torch.tensor([1.0, 1.984375]))


def test_loader_assigns_every_checkpoint_key_and_runs_cpu_forward(tmp_path):
    model_dir = _fixture(tmp_path)
    model, quantized = load_vibevoice_q8(
        model_dir, device="cpu", dtype="bfloat16", model_cls=FakeModel, config_cls=FakeConfig
    )
    assert quantized == 1
    assert isinstance(model.model.language_model.proj, RowwiseInt8Linear)
    result = model(torch.tensor([[2.0, 3.0]], dtype=torch.bfloat16))
    torch.testing.assert_close(result.float(), torch.tensor([[0.5, 3.0]]))


def test_loader_rejects_weight_format_outside_inspected_layout(tmp_path):
    model_dir = _fixture(tmp_path, format_value=1)
    with pytest.raises(EngineError, match="weight_format"):
        load_vibevoice_q8(model_dir, device="cpu", dtype="bfloat16", model_cls=FakeModel, config_cls=FakeConfig)


def test_loader_rejects_missing_trainable_checkpoint_key(tmp_path):
    model_dir = _fixture(tmp_path, include_bias=False)
    with pytest.raises(EngineError, match="missing"):
        load_vibevoice_q8(model_dir, device="cpu", dtype="bfloat16", model_cls=FakeModel, config_cls=FakeConfig)


def test_loader_accepts_scale_in_a_different_indexed_shard(tmp_path):
    model_dir = _fixture(tmp_path, split_scale=True)
    model, _ = load_vibevoice_q8(
        model_dir, device="cpu", dtype="bfloat16", model_cls=FakeModel, config_cls=FakeConfig
    )
    assert tuple(model.model.language_model.proj.SCB.shape) == (2,)


def test_loader_rejects_wrong_index_shard_and_wrong_full_precision_shape(tmp_path):
    model_dir = _fixture(tmp_path, split_scale=True, wrong_index_mapping=True)
    with pytest.raises(EngineError, match="index does not match"):
        load_vibevoice_q8(model_dir, device="cpu", dtype="bfloat16", model_cls=FakeModel, config_cls=FakeConfig)

    other = tmp_path / "wrong_shape"
    other.mkdir()
    model_dir = _fixture(other, wrong_offset_shape=True)
    with pytest.raises(EngineError, match="shapes do not match"):
        load_vibevoice_q8(model_dir, device="cpu", dtype="bfloat16", model_cls=FakeModel, config_cls=FakeConfig)
