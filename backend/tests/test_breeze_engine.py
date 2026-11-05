import json
from pathlib import Path

import numpy as np
import torch
import pytest
import soundfile as sf

from engines.base import EngineError, ModelState, SynthesisRequest
from engines.breeze import BreezeEngine
from engines.breeze_runtime import runtime


def _breeze_dir(path: Path):
    path.mkdir()
    (path / "config.json").write_text(json.dumps({"model_type": "breeze"}), encoding="utf-8")
    (path / "tokenizer.json").write_text("{}", encoding="utf-8")
    (path / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    (path / "Breeze-TTS-2-bf16.safetensors").write_bytes(b"fake")
    codec = path / "audio_tokenizer"
    codec.mkdir()
    (codec / "config.json").write_text("{}", encoding="utf-8")
    (codec / "model.safetensors").write_bytes(b"fake")


def test_breeze_identifies_complete_local_checkpoint(tmp_path):
    model_dir = tmp_path / "breeze"
    _breeze_dir(model_dir)
    info = BreezeEngine().identify(model_dir)
    assert info is not None
    assert info.state == ModelState.INTEGRATED
    assert "Non-Commercial" in info.notes


@pytest.mark.parametrize("mode,instruction,cfg", [("design", "Warm alto", 4.0), ("clone", "   ", 1.0), ("direction", "Warm alto", 4.0)])
def test_breeze_fake_design_generation_wires_supported_controls(monkeypatch, tmp_path, mode, instruction, cfg):
    engine = BreezeEngine()
    engine._model, engine._codec, engine._tokenizer = object(), object(), object()
    engine._device, engine._dtype = "cpu", "bfloat16"
    observed = {}

    monkeypatch.setattr(runtime, "fix_seed", lambda seed: observed.setdefault("seed", seed))
    def build_batch(model, tokenizer, **kwargs):
        assert torch.is_inference_mode_enabled()
        observed["negative"] = kwargs["negative_segments"]
        observed["segments"] = kwargs["cond_segments"]
        observed["cfg"] = kwargs["cfg_scale"]
        return torch.zeros((1, 2, 4)), torch.ones((1, 2), dtype=torch.long), torch.tensor([2]), 2
    monkeypatch.setattr(runtime, "build_generation_batch", build_batch)
    def generate(model, **kwargs):
        assert kwargs["cfg_scale"] == cfg
        observed["params"] = kwargs["params"]
        observed["decode_mode"] = kwargs["decode_mode"]
        assert kwargs["return_metadata"] is True
        return torch.zeros((2, 16), dtype=torch.long), {"stopped_on_eos": True, "truncated": False}
    monkeypatch.setattr(runtime, "generate_codes", generate)
    monkeypatch.setattr(runtime, "decode_codes", lambda codec, codes: torch.zeros(2400))

    reference = tmp_path / "ref.wav"
    sf.write(reference, np.zeros(24000, dtype=np.float32), 24000)
    def encode(*args):
        assert torch.is_inference_mode_enabled()
        return torch.zeros((12, 16), dtype=torch.int16)
    monkeypatch.setattr(runtime, "encode_reference_audio", encode)
    result = engine.synthesize(SynthesisRequest(text="Hello there.", voice_description=instruction,
        reference_audio=reference if mode != "design" else None, reference_text="Reference words.", temperature=0.6,
        top_p=0.8, repetition_penalty=1.2, max_new_tokens=512, seed=0))
    assert result.sample_rate == 24000 and result.samples.dtype == np.float32
    assert result.meta["stopped_on_eos"] is True and result.meta["truncated"] is False
    assert observed["seed"] == 0
    assert observed["cfg"] == cfg and result.meta["cfg_scale"] == cfg
    assert result.meta["mode"] == mode
    segment_text = " ".join(segment.get("text", "") for segment in observed["segments"])
    if mode == "clone":
        assert observed["negative"] is None and "<ins_bos>" not in segment_text
    else:
        assert "Warm alto" in segment_text and observed["negative"] is not None
    assert observed["params"].temperature == 0.6
    assert observed["params"].top_p == 0.8
    assert observed["params"].repetition_penalty == 1.2
    assert observed["params"].max_new_tokens == 512
    assert observed["decode_mode"] == "eager"




def test_breeze_attention_dispatches_to_transformers_sdpa():
    from engines.breeze_runtime.native import BreezeAttention, BreezeConfig

    config = BreezeConfig(hidden_size=8, intermediate_size=16, num_attention_heads=2,
        num_key_value_heads=1, num_hidden_layers=1, vocab_size=16, text_vocab_size=16,
        _attn_implementation="sdpa")
    attention = BreezeAttention(config, layer_idx=0).eval()
    hidden = torch.randn(1, 3, 8)
    position_embeddings = (torch.ones(1, 3, 4), torch.zeros(1, 3, 4))
    output, weights = attention(hidden, position_embeddings, attention_mask=None)
    assert output.shape == hidden.shape
    assert weights is None
    assert torch.isfinite(output).all()


def test_breeze_completion_metadata_reports_eos_and_token_limit():
    from engines.breeze_runtime.runtime import generation_completion

    assert generation_completion(torch.tensor([128]), 128, 128, 128) == {
        "stopped_on_eos": True, "truncated": False,
    }
    assert generation_completion(torch.tensor([42]), 128, 128, 128) == {
        "stopped_on_eos": False, "truncated": True,
    }


def test_reference_duration_rejected_before_codec(monkeypatch, tmp_path):
    engine = BreezeEngine()
    engine._model = engine._codec = engine._tokenizer = object()
    reference = tmp_path / "long.wav"
    sf.write(reference, np.zeros(int((runtime.MAX_REFERENCE_SECONDS + 1) * 8000)), 8000)
    def unexpected(*args):
        pytest.fail("Oversized reference reached codec")
    monkeypatch.setattr(runtime, "encode_reference_audio", unexpected)
    with pytest.raises(EngineError, match="exceeds"):
        engine.synthesize(SynthesisRequest(text="Hello", reference_audio=reference, reference_text="Hello"))


def test_unload_clears_runner_cycle():
    from types import SimpleNamespace
    engine = BreezeEngine()
    model = SimpleNamespace(_breeze_depth_runners={1: object()})
    engine._model = model
    engine._device = "cuda"
    engine.unload()
    assert model._breeze_depth_runners == {}
    assert engine._model is None and engine._device is None
