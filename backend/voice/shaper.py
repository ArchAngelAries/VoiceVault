"""
voice/shaper.py — Realistic voice shaping with the WORLD vocoder (pyworld, BSD).

WORLD splits speech into pitch (F0), spectral envelope (the vocal tract's resonances, i.e. formants)
and aperiodicity (breath/noise). Changing them independently is what keeps shaping natural:
moving pitch alone keeps the same "person", moving formants changes perceived size, age and sex,
and moving both together gives the cartoon chipmunk/giant sound.

Two layers of controls:
  Simple   age (-1 young child .. 0 as recorded .. +1 elderly; beyond allowed), pitch (semitones),
           gender (as recorded / male / female, with strength), speed.
  Advanced every underlying parameter, added on top of what the simple controls derive. Nothing is
           clamped beyond what the maths needs to stay finite.

Analysis is cached per source so moving a slider only re-synthesises.
"""
from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from dataclasses import asdict, dataclass, field, fields
from typing import Optional

import numpy as np

FRAME_MS = 5.0
F0_FLOOR, F0_CEIL = 40.0, 1400.0          # covers basses through small children and falsetto
MALE_TARGET_HZ, FEMALE_TARGET_HZ = 110.0, 205.0
CHILD_TARGET_HZ = 275.0
GENDER_SPLIT_HZ = 160.0


@dataclass
class ShapeParams:
    pitch_semitones: float = 0.0     # whole-voice pitch, formants kept (same person, higher/lower)
    formant_shift: float = 0.0       # percent: + smaller vocal tract (younger/feminine/cartoon), - larger
    pitch_range: float = 1.0         # 0 monotone, 1 as recorded, >1 more melodic
    speed: float = 1.0               # speaking rate, pitch unchanged
    breathiness: float = 0.0         # 0..1+ air in the voice
    roughness: float = 0.0           # 0..1+ rasp/jitter (age, gravel, monsters)
    tremor_depth: float = 0.0        # semitones of slow wobble (elderly tremor)
    tremor_rate: float = 5.5         # Hz
    vibrato_depth: float = 0.0       # semitones (singing vibrato)
    vibrato_rate: float = 5.8        # Hz
    brightness: float = 0.0          # dB per octave above 1 kHz (+ brighter/younger, - darker/warmer)
    whisper: bool = False            # remove voicing entirely
    robot: bool = False              # fixed pitch (monotone, mechanical)
    layers: list = field(default_factory=list)   # extra voices [{semitones, formant_shift, gain}] (demon, choir)

    def scaled(self, strength: float) -> "ShapeParams":
        """The same change at a different strength: 0 = the original voice, 1 = as set, 1.5 = exaggerated.

        Additive values scale towards 0, factors (speed, pitch range) towards 1, extra layers get
        quieter. Wobble rates stay as they are; only how much wobble changes. Whisper and robot are
        on/off, so they turn off at 0 and stay on otherwise.
        """
        s = max(0.0, float(strength))
        if s == 1.0:
            return ShapeParams(**asdict(self))
        out = ShapeParams(**asdict(self))
        for f in fields(ShapeParams):
            value = getattr(self, f.name)
            if f.name in ("pitch_range", "speed"):
                setattr(out, f.name, max(0.05, 1.0 + (value - 1.0) * s))
            elif f.name in ("whisper", "robot"):
                setattr(out, f.name, bool(value) and s > 0)
            elif f.name == "layers":
                setattr(out, f.name, [{**layer, "gain": float(layer.get("gain", 0.6)) * s} for layer in value] if s > 0 else [])
            elif f.name in ("tremor_rate", "vibrato_rate"):
                continue
            else:
                setattr(out, f.name, value * s)
        return out

    def merged(self, other: "ShapeParams") -> "ShapeParams":
        """Advanced adjustments add to (or for factors multiply) the simple-derived values."""
        out = ShapeParams(**asdict(self))
        for f in fields(ShapeParams):
            a, b = getattr(self, f.name), getattr(other, f.name)
            if f.name in ("pitch_range", "speed"):
                setattr(out, f.name, a * b)
            elif f.name in ("whisper", "robot"):
                setattr(out, f.name, a or b)
            elif f.name == "layers":
                setattr(out, f.name, list(a) + list(b))
            elif f.name in ("tremor_rate", "vibrato_rate"):
                setattr(out, f.name, b if b != getattr(ShapeParams, f.name, None) else a)
            else:
                setattr(out, f.name, a + b)
        return out


@dataclass
class Analysis:
    rate: int
    f0: np.ndarray
    sp: np.ndarray
    ap: np.ndarray
    median_f0: float
    voiced_share: float

    @property
    def gender_guess(self) -> str:
        if not self.median_f0:
            return "unknown"
        return "female" if self.median_f0 >= GENDER_SPLIT_HZ else "male"

    def summary(self) -> dict:
        return {"median_f0_hz": round(self.median_f0, 1), "gender_guess": self.gender_guess,
                "voiced_share": round(self.voiced_share, 3), "duration_s": round(len(self.f0) * FRAME_MS / 1000, 2),
                "rate": self.rate}


_cache: "OrderedDict[str, Analysis]" = OrderedDict()
_cache_lock = threading.Lock()


def analyse(audio: np.ndarray, rate: int, key: Optional[str] = None) -> Analysis:
    """WORLD analysis of mono audio (cached by key, or by content hash)."""
    import pyworld

    x = np.asarray(audio, dtype=np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)
    key = key or hashlib.sha1(x.tobytes()).hexdigest()
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    x = np.ascontiguousarray(x)
    f0, t = pyworld.dio(x, rate, f0_floor=F0_FLOOR, f0_ceil=F0_CEIL, frame_period=FRAME_MS)
    f0 = pyworld.stonemask(x, f0, t, rate)
    fft_size = pyworld.get_cheaptrick_fft_size(rate, F0_FLOOR)   # same resolution for envelope and breath
    sp = pyworld.cheaptrick(x, f0, t, rate, f0_floor=F0_FLOOR, fft_size=fft_size)
    ap = pyworld.d4c(x, f0, t, rate, fft_size=fft_size)
    voiced = f0 > 0
    result = Analysis(rate=rate, f0=f0, sp=sp, ap=ap,
                      median_f0=float(np.median(f0[voiced])) if voiced.any() else 0.0,
                      voiced_share=float(voiced.mean()) if len(f0) else 0.0)
    with _cache_lock:
        _cache[key] = result
        while len(_cache) > 16:
            _cache.popitem(last=False)
    return result


# ── Simple controls → parameters ────────────────────────────────────────────

def from_simple(analysis: Analysis, age: float = 0.0, pitch: float = 0.0, gender: str = "as_recorded",
                gender_strength: float = 1.0, speed: float = 1.0) -> ShapeParams:
    """Map the easy sliders to realistic parameter sets (all ranges open-ended)."""
    p = ShapeParams(pitch_semitones=float(pitch), speed=float(speed))
    current = analysis.gender_guess
    if gender in ("male", "female") and gender != current and analysis.median_f0:
        target = FEMALE_TARGET_HZ if gender == "female" else MALE_TARGET_HZ
        p.pitch_semitones += gender_strength * 12 * np.log2(target / analysis.median_f0)
        # Adult female vocal tracts are ~15-20% shorter: formants move up together with pitch.
        p.formant_shift += gender_strength * (17.0 if gender == "female" else -14.0)
        p.pitch_range *= 1.0 + gender_strength * (0.15 if gender == "female" else -0.1)
        p.brightness += gender_strength * (1.0 if gender == "female" else -1.0)
        p.breathiness += gender_strength * (0.06 if gender == "female" else 0.0)
    # Choosing the gender the voice already has changes nothing; use pitch/formants to push it further.
    a = float(age)
    if a < 0:   # younger: shorter vocal tract, higher and livelier voice
        y = -a
        # Children's vocal tracts are ~28% shorter than adult men's and ~15% shorter than adult women's.
        voice_now = gender if gender in ("male", "female") else current
        p.formant_shift += (15.0 if voice_now == "female" else 28.0) * y
        # Young children speak around 260-300 Hz whatever the source voice: head toward that
        # (a woman's voice needs a smaller step than a man's). Past -1 keeps going.
        start = analysis.median_f0 * 2 ** (p.pitch_semitones / 12) if analysis.median_f0 else CHILD_TARGET_HZ
        p.pitch_semitones += y * 12 * np.log2(CHILD_TARGET_HZ / start) if start else 7.0 * y
        p.pitch_range *= 1.0 + 0.35 * y
        p.speed *= 1.0 + 0.05 * y
        p.brightness += 1.5 * y
    elif a > 0:  # older: shakier, rougher, breathier and slower; only slightly deeper
        # Age is heard mostly as tremor and roughness. Women's voices drop a little with age and men's
        # rise a little; a big pitch or formant drop just sounds "deeper", not older (tuned by ear 2026-11-05).
        o = a
        p.formant_shift -= 0.5 * o
        p.pitch_semitones += (0.0 if current == "female" else 0.3) * o
        p.breathiness += 0.30 * o
        p.roughness += 0.64 * o
        p.tremor_depth += 1.1 * o
        p.tremor_rate = 6.0
        p.speed *= max(0.2, 1.0 - 0.12 * o)
        p.pitch_range *= max(0.0, 1.0 - 0.15 * o)
        p.brightness -= 1.0 * o
    return p


# ── Resynthesis ─────────────────────────────────────────────────────────────

def _warp_envelope(sp: np.ndarray, factor: float) -> np.ndarray:
    """Move formants by `factor` along frequency (factor > 1 raises them)."""
    if abs(factor - 1.0) < 1e-4:
        return sp
    bins = sp.shape[1]
    src = np.clip(np.arange(bins) / factor, 0, bins - 1)
    lo = np.floor(src).astype(int)
    hi = np.minimum(lo + 1, bins - 1)
    w = (src - lo)[None, :]
    out = sp[:, lo] * (1 - w) + sp[:, hi] * w
    if factor > 1:   # bins above the old top get the last value's energy, slightly rolled off
        out[:, src >= bins - 1] *= 0.5
    return np.maximum(out, 1e-16)


def _stretch(arr: np.ndarray, n_out: int) -> np.ndarray:
    n_in = arr.shape[0]
    if n_in == n_out or n_in < 2:
        return arr
    pos = np.linspace(0, n_in - 1, n_out)
    lo = np.floor(pos).astype(int)
    hi = np.minimum(lo + 1, n_in - 1)
    w = pos - lo
    if arr.ndim == 1:
        return arr[lo] * (1 - w) + arr[hi] * w
    return arr[lo] * (1 - w)[:, None] + arr[hi] * w[:, None]


def _smooth_noise(n: int, cutoff_frames: float, rng: np.random.Generator) -> np.ndarray:
    noise = rng.standard_normal(n + 64)
    k = max(1, int(cutoff_frames))
    kernel = np.hanning(2 * k + 1)
    kernel /= kernel.sum()
    return np.convolve(noise, kernel, mode="same")[32:32 + n] * np.sqrt(k)


def render(analysis: Analysis, params: ShapeParams, seed: int = 0) -> np.ndarray:
    """Resynthesise the analysed voice with the parameters. Returns float32 mono at analysis.rate."""
    import pyworld

    rate = analysis.rate
    f0, sp, ap = analysis.f0.copy(), analysis.sp, analysis.ap.copy()
    voiced = f0 > 0
    rng = np.random.default_rng(seed)

    # Duration (speech rate) first, on all three streams.
    speed = max(1e-3, float(params.speed))
    n_out = max(2, int(round(len(f0) / speed)))
    if n_out != len(f0):
        v = _stretch(voiced.astype(float), n_out) > 0.5
        if voiced.any():
            # Fill unvoiced gaps before interpolating so pitch never glides through zero.
            filled = np.interp(np.arange(len(f0)), np.flatnonzero(voiced), f0[voiced])
            f0 = np.where(v, _stretch(filled, n_out), 0.0)
        else:
            f0 = np.zeros(n_out)
        sp = _stretch(sp, n_out)
        ap = _stretch(ap, n_out)
        voiced = v
    t = np.arange(len(f0)) * FRAME_MS / 1000.0

    if params.whisper:
        f0 = np.zeros_like(f0)
        ap = np.ones_like(ap)
    else:
        logf = np.log(np.where(voiced, f0, 1.0))
        median = np.log(analysis.median_f0) if analysis.median_f0 else 0.0
        if params.robot and voiced.any():
            logf = np.full_like(logf, median)
        else:
            logf = median + params.pitch_range * (logf - median)
        logf += params.pitch_semitones / 12.0 * np.log(2.0)
        if params.vibrato_depth:
            logf += params.vibrato_depth / 12.0 * np.log(2.0) * np.sin(2 * np.pi * params.vibrato_rate * t)
        if params.tremor_depth:
            wobble = np.sin(2 * np.pi * params.tremor_rate * t + 0.6 * _smooth_noise(len(t), 40, rng))
            logf += params.tremor_depth / 12.0 * np.log(2.0) * wobble
        if params.roughness:
            logf += params.roughness * 0.035 * _smooth_noise(len(t), 1.5, rng)   # jitter
        f0 = np.where(voiced, np.exp(logf), 0.0)
        f0 = np.clip(f0, 0, rate / 2 - 1)
        if params.breathiness:
            b = float(np.clip(params.breathiness, 0, 4))
            ap = np.where(voiced[:, None], np.clip(ap + (1 - ap) * (1 - np.exp(-b)), 0, 1), ap)

    sp = _warp_envelope(sp, 1.0 + params.formant_shift / 100.0) if params.formant_shift else sp
    if params.brightness:
        freqs = np.linspace(0, rate / 2, sp.shape[1])
        octaves = np.log2(np.maximum(freqs, 1000.0) / 1000.0)
        sp = sp * (10 ** (params.brightness * octaves / 10.0))[None, :]
    if params.roughness:
        shimmer = 1.0 + params.roughness * 0.25 * _smooth_noise(len(f0), 1.0, rng)
        sp = sp * np.clip(shimmer, 0.2, 3.0)[:, None] ** 2

    sp = np.ascontiguousarray(sp, dtype=np.float64)
    ap = np.ascontiguousarray(np.clip(ap, 0.0, 1.0), dtype=np.float64)
    y = pyworld.synthesize(np.ascontiguousarray(f0, dtype=np.float64), sp, ap, rate, frame_period=FRAME_MS)
    for layer in params.layers or []:
        st = float(layer.get("semitones", -12))
        fs_ = float(layer.get("formant_shift", 0))
        gain = float(layer.get("gain", 0.6))
        lf0 = np.where(f0 > 0, f0 * 2 ** (st / 12), 0.0)
        lsp = np.ascontiguousarray(_warp_envelope(sp, 1 + fs_ / 100.0), dtype=np.float64)
        ly = pyworld.synthesize(np.ascontiguousarray(lf0, dtype=np.float64), lsp, ap, rate, frame_period=FRAME_MS)
        n = min(len(y), len(ly))
        y = y[:n] + gain * ly[:n]
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    if peak > 0.97:
        y = y * (0.97 / peak)
    return y.astype(np.float32)


def shape(audio: np.ndarray, rate: int, simple: Optional[dict] = None, advanced: Optional[dict] = None,
          key: Optional[str] = None, seed: int = 0, strength: float = 1.0) -> tuple[np.ndarray, dict]:
    """Analyse (cached), derive parameters from the simple controls, add advanced ones, render."""
    analysis = analyse(audio, rate, key)
    simple = simple or {}
    base = from_simple(analysis, age=simple.get("age", 0.0), pitch=simple.get("pitch", 0.0),
                       gender=simple.get("gender", "as_recorded"), gender_strength=simple.get("gender_strength", 1.0),
                       speed=simple.get("speed", 1.0))
    extra = ShapeParams(**{k: v for k, v in (advanced or {}).items() if k in {f.name for f in fields(ShapeParams)}})
    final = base.merged(extra).scaled(strength)
    out = render(analysis, final, seed=seed)
    return out, {"analysis": analysis.summary(), "params": asdict(final)}
