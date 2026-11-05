r"""
VibeVoice-Large on the GPU. Skipped unless VV_GPU_TESTS=1 and the model and the reference clip are present.

The check is intelligibility, not only length: Whisper must hear the requested words. On transformers 5.3 an
earlier port produced audio that Whisper could not read at all (word overlap 0.08), so the test fails on that.

Run from backend/:  set VV_GPU_TESTS=1 && ..\venv\Scripts\python.exe -m pytest tests/test_vibevoice_gpu.py -s
"""
import os
import re
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

SNAPSHOT = Path(os.environ["VV_VIBEVOICE_DIR"]) if os.environ.get("VV_VIBEVOICE_DIR") else None
REFERENCE = Path(os.environ["VV_REFERENCE_AUDIO"]) if os.environ.get("VV_REFERENCE_AUDIO") else None
SPOKEN = "Hello. This is a short compatibility check, and the voice should sound natural."

pytestmark = pytest.mark.skipif(
    os.environ.get("VV_GPU_TESTS") != "1",
    reason="GPU test: set VV_GPU_TESTS=1",
)


def test_vibevoice_speaks_the_requested_words_on_the_gpu(tmp_path):
    import torch

    from asr.service import transcribe_file
    from engines.base import SynthesisRequest
    from engines.vibevoice_engine import VibeVoiceEngine

    if SNAPSHOT is None or REFERENCE is None or not SNAPSHOT.is_dir() or not REFERENCE.is_file():
        pytest.skip("Set VV_VIBEVOICE_DIR and VV_REFERENCE_AUDIO to existing model and reference paths")
    assert torch.cuda.is_available(), "PyTorch cannot see the GPU; this is not a ROCm run"

    engine = VibeVoiceEngine()
    info = engine.identify(SNAPSHOT)
    assert info is not None and info.reason is None
    info.id = "vibevoice:test"
    engine.load(info, device="cuda", dtype="bfloat16")
    try:
        result = engine.synthesize(SynthesisRequest(text=SPOKEN, reference_audio=REFERENCE, seed=0, max_new_tokens=4000))
    finally:
        engine.unload()

    seconds = len(result.samples) / result.sample_rate
    rms = float(np.sqrt(np.mean(result.samples ** 2)))
    print(f"\nvibevoice: {seconds:.2f}s | rms {rms:.4f} | gen {result.meta['timings_s']['generate']}s")
    assert result.sample_rate == 24000
    assert 1.0 < seconds < 30.0
    assert rms > 0.01
    assert result.meta["device"] == "cuda"
    assert result.meta["mode"] == "clone"

    out = tmp_path / "vibevoice_gpu_test.wav"
    from engines.audio_io import write_wav
    write_wav(out, result.samples, result.sample_rate)
    heard = transcribe_file(out, None)
    want = Counter(re.findall(r"[a-z']+", SPOKEN.lower()))
    got = Counter(re.findall(r"[a-z']+", heard.lower()))
    overlap = sum((want & got).values()) / sum(want.values())
    print(f"whisper heard: {heard!r} | overlap {overlap:.2f}")
    assert overlap >= 0.8, f"Whisper heard {heard!r}; the requested words are not intelligible"
