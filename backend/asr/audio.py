"""
asr/audio.py — Decode any audio or video container to the 16 kHz mono float32 that Whisper expects.

ffmpeg does the decoding, so mp3, m4a, flac, ogg and video files with audio tracks all work.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np

WHISPER_RATE = 16000
MAX_DECODE_SECONDS = 20 * 60
DECODE_TIMEOUT_SECONDS = 120


class AudioDecodeError(RuntimeError):
    pass


def decode_audio(path: Path, sample_rate: int = WHISPER_RATE, *, max_seconds: float = MAX_DECODE_SECONDS) -> np.ndarray:
    if not 0 < max_seconds <= MAX_DECODE_SECONDS or not 8000 <= sample_rate <= 48000:
        raise AudioDecodeError("Unsupported audio decoding duration or sample rate.")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AudioDecodeError("ffmpeg is not on PATH, so audio cannot be decoded.")
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
           "-i", str(path), "-t", str(max_seconds + 1), "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "f32le", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                              timeout=DECODE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise AudioDecodeError("Audio decoding timed out. Try a shorter clip.") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise AudioDecodeError(detail or "ffmpeg could not read this file.")
    if len(proc.stdout) > max_seconds * sample_rate * 4:
        raise AudioDecodeError(f"Audio exceeds {max_seconds:g} seconds. Split it into shorter clips.")
    samples = np.frombuffer(proc.stdout, dtype="<f4").copy()
    if samples.size == 0:
        raise AudioDecodeError("The file has no audio.")
    return samples
