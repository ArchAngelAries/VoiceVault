"""
music/dsp.py — Local audio editing for the Music Studio (numpy only, stereo-aware).

Everything here is deterministic and runs on the CPU: reading/writing takes, waveform
peaks for the editor, splicing a region from one take into another with crossfades,
blending (crossfading) two takes, mixing stems with gains/mutes, and loudness
normalisation for export.
"""
from __future__ import annotations

import io
import os
import subprocess
from pathlib import Path
from typing import Optional

import numpy as np


def read(path: Path) -> tuple[np.ndarray, int]:
    """Return (frames, channels) float32 and the sample rate."""
    import soundfile as sf

    data, rate = sf.read(str(path), dtype="float32", always_2d=True)
    return data, int(rate)


def decode(data: bytes) -> tuple[np.ndarray, int]:
    import soundfile as sf

    audio, rate = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    return audio, int(rate)


def write(path: Path, audio: np.ndarray, rate: int, fmt: str = "WAV") -> None:
    import soundfile as sf

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    subtype = "PCM_24" if fmt.upper() in ("WAV", "FLAC") else None
    sf.write(str(tmp), np.clip(audio, -1.0, 1.0), rate, format=fmt, subtype=subtype)
    os.replace(tmp, path)


def resample(audio: np.ndarray, src: int, dst: int) -> np.ndarray:
    """Linear-phase resampling via polyphase filtering (scipy), or linear interpolation."""
    if src == dst:
        return audio
    try:
        from math import gcd

        from scipy.signal import resample_poly

        g = gcd(src, dst)
        return resample_poly(audio, dst // g, src // g, axis=0).astype(np.float32)
    except ImportError:
        n = int(round(len(audio) * dst / src))
        x = np.linspace(0, len(audio) - 1, n)
        return np.stack([np.interp(x, np.arange(len(audio)), audio[:, c]) for c in range(audio.shape[1])],
                        axis=1).astype(np.float32)


def match(audio: np.ndarray, rate: int, target_rate: int, channels: int) -> np.ndarray:
    audio = resample(audio, rate, target_rate)
    if audio.shape[1] == channels:
        return audio
    if channels == 1:
        return audio.mean(axis=1, keepdims=True)
    return np.repeat(audio[:, :1], channels, axis=1)


def peaks(audio: np.ndarray, buckets: int = 2000) -> list[list[float]]:
    """Min/max pairs per bucket of the mono mix, for drawing the waveform."""
    mono = audio.mean(axis=1) if audio.ndim == 2 else audio
    buckets = max(1, min(buckets, len(mono)))
    edges = np.linspace(0, len(mono), buckets + 1, dtype=int)
    out = []
    for a, b in zip(edges[:-1], edges[1:]):
        seg = mono[a:max(b, a + 1)]
        out.append([round(float(seg.min()), 4), round(float(seg.max()), 4)])
    return out


def _fade(n: int) -> tuple[np.ndarray, np.ndarray]:
    t = np.linspace(0, np.pi / 2, n, dtype=np.float32)[:, None]
    return np.cos(t), np.sin(t)  # equal-power out/in


def splice(base: np.ndarray, insert: np.ndarray, rate: int, start: float, end: float,
           fade: float = 0.08, fade_in: bool = True, fade_out: bool = True) -> np.ndarray:
    """Replace base[start:end] with insert[start:end] (same timeline), crossfading both edges.

    Used to keep only the edited region of a repaint/variation and the original elsewhere.
    """
    n = min(len(base), len(insert))
    a, b = int(start * rate), int(end * rate)
    a, b = max(0, min(a, n)), max(0, min(b, n))
    if b <= a:
        return base.copy()
    f = max(1, min(int(fade * rate), (b - a) // 2))
    out = base.copy()
    fo, fi = _fade(f)
    out[a:b] = insert[a:b]
    if fade_in:
        out[a:a + f] = base[a:a + f] * fo + insert[a:a + f] * fi
    if fade_out:
        out[b - f:b] = insert[b - f:b] * fo + base[b - f:b] * fi
    return out


def blend(first: np.ndarray, second: np.ndarray, rate: int, at: float, second_from: float = 0.0,
          fade: float = 2.0) -> np.ndarray:
    """Play `first` until `at`, then crossfade over `fade` seconds into `second` from `second_from`."""
    a = max(0, min(int(at * rate), len(first)))
    s = max(0, min(int(second_from * rate), len(second)))
    if s > 0:
        # Same timeline (e.g. a continuation that repeats the original): the fade ends at
        # `second_from`, so the result is exactly first[:at] + second[second_from:] long.
        f = max(1, min(int(fade * rate), a, s))
        lead = second[s - f:s]
        tail = second[s:]
    else:
        f = max(1, min(int(fade * rate), a, len(second)))
        lead = second[:f]
        tail = second[f:]
    fo, fi = _fade(f)
    head = first[:a - f]
    mixed = first[a - f:a] * fo + lead * fi
    return np.concatenate([head, mixed, tail]).astype(np.float32)


def append(first: np.ndarray, second: np.ndarray, rate: int, fade: float = 0.5) -> np.ndarray:
    """Join two clips end to start with a short crossfade (extensions rendered separately)."""
    return blend(first, second, rate, at=len(first) / rate, second_from=0.0, fade=fade)


def mix(stems: dict[str, np.ndarray], gains_db: dict[str, float], muted: set[str]) -> np.ndarray:
    """Sum stems with per-stem gain; muted stems are left out. Peaks are kept below 0 dBFS."""
    live = [(name, a) for name, a in stems.items() if name not in muted]
    if not live:
        n = max((len(a) for a in stems.values()), default=0)
        ch = next(iter(stems.values())).shape[1] if stems else 1
        return np.zeros((n, ch), dtype=np.float32)
    n = max(len(a) for _, a in live)
    ch = max(a.shape[1] for _, a in live)
    out = np.zeros((n, ch), dtype=np.float32)
    for name, a in live:
        g = 10 ** (gains_db.get(name, 0.0) / 20)
        a = a if a.shape[1] == ch else np.repeat(a[:, :1], ch, axis=1)
        out[:len(a)] += a * g
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    if peak > 0.98:
        out *= 0.98 / peak
    return out


def loudness_normalize(audio: np.ndarray, rate: int, target_lufs: float = -14.0) -> tuple[np.ndarray, Optional[float]]:
    """Gain to an integrated loudness target (pyloudnorm when present, else RMS), peak-safe."""
    try:
        import pyloudnorm as pyln

        meter = pyln.Meter(rate)
        current = float(meter.integrated_loudness(audio))
    except Exception:
        rms = float(np.sqrt(np.mean(audio ** 2))) if audio.size else 0.0
        current = 20 * np.log10(rms + 1e-12) - 0.7  # rough RMS → LUFS for full-band music
    if not np.isfinite(current):
        return audio, None
    gain = 10 ** ((target_lufs - current) / 20)
    out = audio * gain
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    if peak > 0.97:
        out *= 0.97 / peak
    return out.astype(np.float32), round(current, 1)


def export_mp3(wav_path: Path, mp3_path: Path, bitrate: str = "320k") -> None:
    """Encode with FFmpeg (already a VoiceVault dependency for Harvest)."""
    tmp = mp3_path.with_name(mp3_path.name + ".tmp.mp3")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav_path), "-codec:a", "libmp3lame",
                    "-b:a", bitrate, str(tmp)], check=True, timeout=300,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    os.replace(tmp, mp3_path)
