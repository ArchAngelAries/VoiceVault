r"""
OmniVoice on the GPU. Skipped unless VV_GPU_TESTS=1 and the model is in the Hugging Face cache.

Run from backend/:  set VV_GPU_TESTS=1 && ..\venv\Scripts\python.exe -m pytest tests/test_omnivoice_gpu.py -s
"""
import os
from pathlib import Path

import numpy as np
import pytest

from core.paths import VOICEVAULT_ROOT

REFERENCE = VOICEVAULT_ROOT / "data" / "test_audio" / "long_form_61s.wav"

pytestmark = pytest.mark.skipif(
    os.environ.get("VV_GPU_TESTS") != "1",
    reason="GPU test: set VV_GPU_TESTS=1",
)


def _snapshot_dir() -> Path:
    from huggingface_hub import try_to_load_from_cache

    cached = try_to_load_from_cache("k2-fsa/OmniVoice", "config.json")
    if not isinstance(cached, str):
        pytest.skip("k2-fsa/OmniVoice is not in the Hugging Face cache")
    return Path(cached).parent


def test_omnivoice_runs_all_three_modes_on_the_gpu(tmp_path):
    import torch

    from asr.service import transcribe_file
    from engines.audio_io import write_wav
    from engines.base import SynthesisRequest
    from engines.omnivoice import OmniVoiceEngine

    assert torch.cuda.is_available(), "PyTorch cannot see the GPU; this is not a ROCm run"
    engine = OmniVoiceEngine(transcribe_reference=transcribe_file)
    info = engine.identify(_snapshot_dir())
    assert info is not None
    info.id = "omnivoice:test"
    engine.load(info, device="cuda", dtype="float16")
    try:
        auto = engine.synthesize(SynthesisRequest(text="Hello from OmniVoice.", seed=0))
        design = engine.synthesize(SynthesisRequest(
            text="This voice was designed from a description.",
            voice_description="female, young adult, high pitch, british accent", seed=0))

        clone_ref = tmp_path / "ref_10s.wav"
        from asr.audio import decode_audio
        samples = decode_audio(REFERENCE, sample_rate=24000)[: 10 * 24000]
        write_wav(clone_ref, samples, 24000)
        clone = engine.synthesize(SynthesisRequest(
            text="Thank you for waiting. This sentence uses a cloned voice.",
            reference_audio=clone_ref, seed=1))
    finally:
        engine.unload()

    for result, mode in [(auto, "automatic"), (design, "design"), (clone, "clone")]:
        seconds = len(result.samples) / result.sample_rate
        rms = float(np.sqrt(np.mean(result.samples ** 2)))
        print(f"\n{mode}: {seconds:.2f}s | rms {rms:.4f} | gen {result.meta['timings_s']['generate']}s")
        assert result.sample_rate == 24000
        assert result.meta["mode"] == mode
        assert 1.0 < seconds < 30.0
        assert rms > 0.01
        assert result.meta["device"] == "cuda"
    assert clone.meta["reference_text"], "the clone should use a transcript from Whisper"
