"""
Real Maya1 generation on the GPU. Skipped unless VV_GPU_TESTS=1 and the model folder exists.

Run from backend/:  set VV_GPU_TESTS=1 && ..\\venv\\Scripts\\python.exe -m pytest tests/test_maya1_gpu.py -s
"""
import os
from pathlib import Path

import numpy as np
import pytest

MODEL_DIR = Path(os.environ["VV_MAYA1_DIR"]) if os.environ.get("VV_MAYA1_DIR") else None

pytestmark = pytest.mark.skipif(
    os.environ.get("VV_GPU_TESTS") != "1" or MODEL_DIR is None or not MODEL_DIR.is_dir(),
    reason="GPU test: set VV_GPU_TESTS=1 and VV_MAYA1_DIR to the Maya1 model folder",
)


def test_maya1_generates_audible_speech_on_the_gpu():
    import torch

    from engines.base import SynthesisRequest
    from engines.maya1 import Maya1Engine

    assert torch.cuda.is_available(), "PyTorch cannot see the GPU; this is not a ROCm run"

    engine = Maya1Engine()
    info = engine.identify(MODEL_DIR)
    assert info is not None, "config.json did not identify as Maya1"
    info.id = "maya1:test"
    try:
        engine.load(info, device="cuda", dtype="bfloat16")
        result = engine.synthesize(SynthesisRequest(
            text="VoiceVault speech check. <laugh> That worked.",
            seed=0,
            max_new_tokens=1200,
        ))
    finally:
        engine.unload()

    seconds = len(result.samples) / result.sample_rate
    rms = float(np.sqrt(np.mean(result.samples ** 2)))
    print(f"\naudio {seconds:.2f}s rms {rms:.4f} meta {result.meta}")

    assert result.sample_rate == 24000
    assert 1.0 < seconds < 30.0
    assert rms > 0.01
    assert result.meta["device"] == "cuda"
