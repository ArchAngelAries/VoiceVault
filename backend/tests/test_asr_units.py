"""Speech-to-text helpers that need no GPU: audio decoding and segment shaping."""
import numpy as np
import pytest

from asr.audio import AudioDecodeError, decode_audio
from asr.whisper_engine import segments_from_output
from engines.audio_io import write_wav


def test_decode_resamples_to_16k_mono_float32(tmp_path):
    t = np.arange(24000) / 24000
    write_wav(tmp_path / "tone.wav", (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), 24000)

    samples = decode_audio(tmp_path / "tone.wav")

    assert samples.dtype == np.float32
    assert abs(len(samples) - 16000) <= 2  # one second at 16 kHz
    assert np.max(np.abs(samples)) > 0.1


def test_a_file_without_audio_is_a_clear_error(tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("not audio", encoding="utf-8")

    with pytest.raises(AudioDecodeError):
        decode_audio(notes)


def test_generate_segments_keep_their_timing_and_drop_blank_ones():
    # Token lists stand in for Whisper's output; decode() turns them into text.
    raw = [
        {"start": 0.0, "end": 2.5, "tokens": ["Hello", " there"]},
        {"start": 2.5, "end": 3.0, "tokens": ["   "]},            # blank: dropped
        {"start": 3.0, "end": 5.25, "tokens": ["Second", " line"]},
    ]

    segments = segments_from_output(raw, decode=lambda tokens: "".join(tokens))

    assert segments == [
        {"start": 0.0, "end": 2.5, "text": "Hello there"},
        {"start": 3.0, "end": 5.25, "text": "Second line"},
    ]


def test_segment_end_is_never_before_its_start():
    raw = [{"start": 4.0, "end": 3.5, "tokens": ["odd"]}]

    segments = segments_from_output(raw, decode=lambda tokens: "".join(tokens))

    assert segments[0]["end"] == segments[0]["start"] == 4.0


def test_decode_rejects_duration_without_silent_truncation(tmp_path):
    path = tmp_path / "too_long.wav"
    write_wav(path, np.zeros(32000, dtype=np.float32), 16000)
    with pytest.raises(AudioDecodeError, match="Split"):
        decode_audio(path, max_seconds=1)


def test_decode_timeout_is_bounded_and_hidden(monkeypatch, tmp_path):
    import subprocess
    import asr.audio as audio
    monkeypatch.setattr(audio.shutil, "which", lambda name: "ffmpeg")
    def run(cmd, **kwargs):
        assert kwargs["timeout"] == 120
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
        assert "-t" in cmd
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
    monkeypatch.setattr(audio.subprocess, "run", run)
    with pytest.raises(AudioDecodeError, match="timed out"):
        decode_audio(tmp_path / "file.wav")
