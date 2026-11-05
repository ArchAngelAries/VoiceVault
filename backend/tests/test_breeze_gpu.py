"""One bounded local Breeze BF16 request; opt-in and always offline.

Set VV_GPU_TESTS=1 and VV_BREEZE_DIR to the existing model directory.
Listening/voice likeness is separate from this short intelligibility check.
"""
import json
import os
import re
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(os.environ.get('VV_GPU_TESTS') != '1', reason='GPU check is opt-in')
SPOKEN = 'Hello. This is a short Breeze compatibility check.'


def test_breeze_bf16_speaks_short_local_request(tmp_path):
    import torch
    from asr.service import transcribe_file, unload_transcriber
    from engines.audio_io import write_wav
    from engines.base import SynthesisRequest
    from engines.breeze import BreezeEngine

    folder = os.environ.get('VV_BREEZE_DIR')
    if not folder or not Path(folder).is_dir():
        pytest.skip('Set VV_BREEZE_DIR to a local complete checkpoint')
    assert torch.cuda.is_available()
    reference_audio = os.environ.get('VV_REFERENCE_AUDIO')
    reference_text = os.environ.get('VV_REFERENCE_TEXT')
    assert bool(reference_audio) == bool(reference_text), 'Set both VV_REFERENCE_AUDIO and VV_REFERENCE_TEXT'
    mode = 'clone' if reference_audio else 'design'
    reference_path = Path(reference_audio) if reference_audio else None
    if reference_path is not None:
        assert reference_path.is_file(), 'VV_REFERENCE_AUDIO must point to an existing local audio file'

    engine = BreezeEngine()
    info = engine.identify(Path(folder))
    assert info and not info.reason
    info.id = 'breeze:local-check'
    started = time.monotonic()
    try:
        engine.load(info)
        load_seconds = time.monotonic() - started
        result = engine.synthesize(SynthesisRequest(
            text=SPOKEN,
            voice_description=('Preserve the reference speaker voice while speaking English naturally.'
                              if mode == 'clone' else 'A clear, warm adult voice speaking English naturally.'),
            reference_audio=reference_path,
            reference_text=reference_text,
            temperature=0.8, top_p=0.95, repetition_penalty=1.1, max_new_tokens=128, seed=0,
        ))
    finally:
        engine.unload()

    seconds = len(result.samples) / result.sample_rate
    rms = float(np.sqrt(np.mean(result.samples ** 2)))
    assert result.sample_rate == 24000 and 1 < seconds < 12 and rms > 0.01
    assert np.isfinite(result.samples).all()
    output = tmp_path / 'breeze.wav'
    write_wav(output, result.samples, result.sample_rate)
    try:
        heard = transcribe_file(output, 'en')
    finally:
        unload_transcriber()
    want = Counter(re.findall(r"[a-z']+", SPOKEN.lower()))
    got = Counter(re.findall(r"[a-z']+", heard.lower()))
    overlap = sum((want & got).values()) / sum(want.values())
    evidence = {**result.meta, 'load_seconds': round(load_seconds, 3), 'duration_s': round(seconds, 3),
                'rms': round(rms, 4), 'requested_text': SPOKEN, 'heard_text': heard,
                'word_overlap': overlap, 'mode': mode,
                'reference_audio': str(reference_path) if reference_path else None,
                'reference_text_supplied': reference_text if reference_path else None,
                'listening_validated': False}
    (tmp_path / 'breeze.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    print(json.dumps(evidence, indent=2))
    assert overlap >= 0.8, f'Whisper heard {heard!r}'
