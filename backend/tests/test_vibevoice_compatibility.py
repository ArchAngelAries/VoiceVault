"""CPU-only compatibility checks for VibeVoice checkpoint and stop metadata."""
import json
import sys
from pathlib import Path

import numpy as np

from engines.vibevoice_engine import checkpoint_shard_names, generation_completion

VENDOR_DIR = Path(__file__).resolve().parents[1] / "engines" / "vibevoice" / "vendor"
if str(VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_DIR))
from processor.vibevoice_processor import resolve_language_model_name


def test_checkpoint_shards_support_indexed_and_single_file_layouts(tmp_path):
    (tmp_path / "model.safetensors").write_bytes(b"fixture")
    assert checkpoint_shard_names(tmp_path) == ["model.safetensors"]

    (tmp_path / "model.safetensors.index.json").write_text(
        json.dumps({"weight_map": {"a": "part-2.safetensors", "b": "part-1.safetensors", "c": "part-2.safetensors"}}),
        encoding="utf-8",
    )
    assert checkpoint_shard_names(tmp_path) == ["part-1.safetensors", "part-2.safetensors"]


def test_explicit_tokenizer_path_overrides_preprocessor_config():
    kwargs = {"language_model_pretrained_name": "D:/local/qwen-tokenizer"}
    actual = resolve_language_model_name({"language_model_pretrained_name": "remote/config"}, kwargs)
    assert actual == "D:/local/qwen-tokenizer"
    assert kwargs == {}
    assert resolve_language_model_name({}, {}) == "Qwen/Qwen2.5-1.5B"


def test_completion_metadata_uses_eos_and_vendor_max_step_flags():
    sequences = np.asarray([[10, 11, 20, 2], [10, 11, 20, 21]])
    assert generation_completion(sequences, prompt_length=2, eos_token_id=2, reach_max_step_sample=np.array([False])) == (True, False)
    assert generation_completion(sequences, prompt_length=2, eos_token_id=2, reach_max_step_sample=np.array([True])) == (True, True)
    assert generation_completion(sequences, prompt_length=2, eos_token_id=None, reach_max_step_sample=None) == (None, None)
