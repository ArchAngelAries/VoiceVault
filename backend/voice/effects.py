"""
voice/effects.py — Voice changer effects and character presets (numpy/scipy, CPU).

Effects run after shaping, as a chain: [{"type": "reverb", "size": 0.8, "mix": 0.4}, ...].
Presets are just shaping parameters plus an effect chain, so every preset stays editable in the
advanced panel. Unknown effect types are rejected; parameter values are not capped.
"""
from __future__ import annotations

import numpy as np

# ── Building blocks ────────────────────────────────────────────────────────


def _butter(audio, rate, kind, freq, order=4):
    from scipy.signal import butter, sosfilt

    nyq = rate / 2
    if kind == "band":
        lo, hi = max(10.0, freq[0]), min(nyq * 0.99, freq[1])
        sos = butter(order, [lo / nyq, hi / nyq], btype="band", output="sos")
    else:
        sos = butter(order, min(max(freq, 10.0), nyq * 0.99) / nyq, btype=kind, output="sos")
    return sosfilt(sos, audio).astype(np.float32)


def _ir(rate, size, decay, seed=7):
    """Synthetic room: decaying stereo-free noise burst (size 0..1+ → 0.2..4 s)."""
    rng = np.random.default_rng(seed)
    seconds = 0.2 + 3.8 * max(0.0, size)
    n = int(rate * seconds)
    t = np.arange(n) / rate
    env = np.exp(-t * (3.0 + 9.0 * (1.0 - min(max(decay, 0.0), 1.0))) / max(seconds, 0.2) * 2)
    ir = rng.standard_normal(n) * env
    ir[: int(0.005 * rate)] *= np.linspace(0, 1, int(0.005 * rate))   # soft onset
    return (ir / (np.sqrt(np.sum(ir ** 2)) + 1e-9)).astype(np.float32)


def reverb(audio, rate, size=0.5, decay=0.5, mix=0.3, **_):
    from scipy.signal import fftconvolve

    wet = fftconvolve(audio, _ir(rate, size, decay))[: len(audio) + int(rate * (0.2 + 3.8 * size))]
    dry = np.concatenate([audio, np.zeros(len(wet) - len(audio), np.float32)])
    return (dry * (1 - mix) + wet * mix * 1.6).astype(np.float32)


def reverse_reverb(audio, rate, size=0.7, mix=0.5, **_):
    """The 'ghostly' swell that rises into each word."""
    tail = reverb(audio[::-1].copy(), rate, size=size, decay=0.6, mix=1.0)[::-1]
    pad = len(tail) - len(audio)
    dry = np.concatenate([np.zeros(pad, np.float32), audio])
    return (dry * (1 - mix * 0.5) + tail * mix).astype(np.float32)


def echo(audio, rate, delay=0.3, feedback=0.4, mix=0.35, repeats=6, **_):
    d = max(1, int(delay * rate))
    out = np.concatenate([audio, np.zeros(d * repeats, np.float32)])
    wet = np.zeros_like(out)
    g = 1.0
    for k in range(1, repeats + 1):
        g *= feedback
        wet[k * d:k * d + len(audio)] += audio * g
    return (out * 1.0 + wet * mix).astype(np.float32)


def _mod_delay(audio, rate, base_ms, depth_ms, lfo_hz, feedback=0.0):
    n = len(audio)
    t = np.arange(n) / rate
    delay = (base_ms + depth_ms * (0.5 + 0.5 * np.sin(2 * np.pi * lfo_hz * t))) * rate / 1000
    idx = np.arange(n) - delay
    lo = np.floor(idx).astype(int)
    frac = idx - lo
    lo = np.clip(lo, 0, n - 1)
    hi = np.clip(lo + 1, 0, n - 1)
    out = audio[lo] * (1 - frac) + audio[hi] * frac
    out[idx < 0] = 0
    if feedback:
        out = out + feedback * np.concatenate([np.zeros(int(base_ms * rate / 1000)), out])[:n]
    return out.astype(np.float32)


def chorus(audio, rate, depth=0.5, rate_hz=0.8, mix=0.5, voices=3, **_):
    wet = sum(_mod_delay(audio, rate, 15 + 7 * v, 6 * depth, rate_hz * (1 + 0.27 * v)) for v in range(int(voices)))
    return (audio * (1 - mix) + wet / max(1, voices) * mix * 1.3).astype(np.float32)


def flanger(audio, rate, depth=0.6, rate_hz=0.25, feedback=0.5, mix=0.5, **_):
    wet = _mod_delay(audio, rate, 1.0, 4.0 * depth, rate_hz, feedback=min(feedback, 0.95))
    return (audio * (1 - mix) + wet * mix).astype(np.float32)


def ring_mod(audio, rate, freq=60.0, mix=0.5, **_):
    t = np.arange(len(audio)) / rate
    return (audio * (1 - mix) + audio * np.sin(2 * np.pi * freq * t) * mix).astype(np.float32)


def tremolo(audio, rate, rate_hz=6.0, depth=0.5, **_):
    t = np.arange(len(audio)) / rate
    return (audio * (1 - depth * (0.5 + 0.5 * np.sin(2 * np.pi * rate_hz * t)))).astype(np.float32)


def distortion(audio, rate, drive=4.0, mix=1.0, **_):
    wet = np.tanh(audio * drive) / np.tanh(max(drive, 1e-3))
    return (audio * (1 - mix) + wet * mix).astype(np.float32)


def bitcrush(audio, rate, bits=8, downsample=4, **_):
    q = 2 ** max(1, int(bits) - 1)
    held = np.repeat(audio[:: max(1, int(downsample))], max(1, int(downsample)))[: len(audio)]
    return (np.round(held * q) / q).astype(np.float32)


def eq(audio, rate, low_cut=0.0, high_cut=0.0, band=None, **_):
    out = audio
    if band:
        out = _butter(out, rate, "band", band)
    if low_cut:
        out = _butter(out, rate, "high", low_cut)
    if high_cut:
        out = _butter(out, rate, "low", high_cut)
    return out


def noise(audio, rate, level=0.01, color="white", **_):
    rng = np.random.default_rng(3)
    n = rng.standard_normal(len(audio)).astype(np.float32)
    if color == "hiss":
        n = _butter(n, rate, "high", 3000)
    return (audio + n * level).astype(np.float32)


EFFECTS = {"reverb": reverb, "reverse_reverb": reverse_reverb, "echo": echo, "chorus": chorus, "flanger": flanger,
           "ring_mod": ring_mod, "tremolo": tremolo, "distortion": distortion, "bitcrush": bitcrush, "eq": eq,
           "noise": noise}


def apply_chain(audio: np.ndarray, rate: int, chain: list[dict]) -> np.ndarray:
    out = np.asarray(audio, dtype=np.float32)
    for step in chain or []:
        kind = step.get("type")
        if kind not in EFFECTS:
            raise ValueError(f"Unknown effect '{kind}'.")
        if step.get("enabled", True) is False:
            continue
        params = {k: v for k, v in step.items() if k not in ("type", "enabled")}
        out = EFFECTS[kind](out, rate, **params)
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    if peak > 0.97:
        out = out * (0.97 / peak)
    return out.astype(np.float32)


# ── Presets: shaping (simple + advanced) plus effects ────────────────────────
# "realistic" presets reshape the person; "fun" presets are voice-changer characters.

PRESETS: dict[str, dict] = {
    # Realistic
    "young_child": {"group": "realistic", "label": "Young child", "simple": {"age": -1.0}},
    "teen": {"group": "realistic", "label": "Teenager", "simple": {"age": -0.45}},
    "elder": {"group": "realistic", "label": "Elderly", "simple": {"age": 1.0}},
    "very_old": {"group": "realistic", "label": "Very old", "simple": {"age": 1.6}},
    "as_female": {"group": "realistic", "label": "Female version", "simple": {"gender": "female"}},
    "as_male": {"group": "realistic", "label": "Male version", "simple": {"gender": "male"}},
    "deeper": {"group": "realistic", "label": "Deeper, same person", "advanced": {"pitch_semitones": -3, "formant_shift": -4}},
    "brighter": {"group": "realistic", "label": "Lighter, same person", "advanced": {"pitch_semitones": 3, "formant_shift": 4}},
    "big_build": {"group": "realistic", "label": "Big, heavy build", "advanced": {"formant_shift": -10, "pitch_semitones": -1.5, "brightness": -1}},
    "small_build": {"group": "realistic", "label": "Small, slight build", "advanced": {"formant_shift": 9, "pitch_semitones": 1.5}},
    "gravelly": {"group": "realistic", "label": "Gravelly", "advanced": {"roughness": 0.8, "breathiness": 0.2, "brightness": -1}},
    "breathy": {"group": "realistic", "label": "Breathy, intimate", "advanced": {"breathiness": 0.6, "brightness": -0.5}},
    "monotone": {"group": "realistic", "label": "Flat, monotone", "advanced": {"pitch_range": 0.3}},
    "expressive": {"group": "realistic", "label": "Very expressive", "advanced": {"pitch_range": 1.6}},
    "fast_talker": {"group": "realistic", "label": "Fast talker", "simple": {"speed": 1.25}},
    "slow_drawl": {"group": "realistic", "label": "Slow drawl", "simple": {"speed": 0.8}, "advanced": {"pitch_range": 0.8}},
    "whisper": {"group": "realistic", "label": "Whisper", "advanced": {"whisper": True}},
    # Cartoon
    "chipmunk": {"group": "cartoon", "label": "Chipmunk", "advanced": {"pitch_semitones": 12, "formant_shift": 32}, "simple": {"speed": 1.08}},
    "helium": {"group": "cartoon", "label": "Helium", "advanced": {"formant_shift": 45, "pitch_semitones": 4}},
    "giant": {"group": "cartoon", "label": "Giant", "advanced": {"pitch_semitones": -12, "formant_shift": -32}, "simple": {"speed": 0.9}},
    "tiny_fairy": {"group": "cartoon", "label": "Tiny fairy", "advanced": {"pitch_semitones": 16, "formant_shift": 40, "breathiness": 0.3},
                   "effects": [{"type": "chorus", "depth": 0.3, "mix": 0.3}, {"type": "reverb", "size": 0.3, "mix": 0.2}]},
    "old_crone": {"group": "cartoon", "label": "Witch / crone", "simple": {"age": 1.8}, "advanced": {"pitch_semitones": 4, "roughness": 0.6}},
    # Voice changer
    "robot": {"group": "fx", "label": "Robot", "advanced": {"robot": True},
              "effects": [{"type": "ring_mod", "freq": 55, "mix": 0.45}, {"type": "flanger", "depth": 0.3, "mix": 0.3},
                          {"type": "bitcrush", "bits": 10, "downsample": 2}]},
    "cyborg": {"group": "fx", "label": "Cyborg (half human)", "effects": [{"type": "ring_mod", "freq": 80, "mix": 0.25},
                                                                       {"type": "flanger", "depth": 0.4, "mix": 0.35}]},
    "ghost": {"group": "fx", "label": "Ghost", "advanced": {"breathiness": 0.5, "pitch_semitones": 1},
              "effects": [{"type": "reverse_reverb", "size": 0.7, "mix": 0.6}, {"type": "chorus", "depth": 0.6, "mix": 0.4},
                          {"type": "eq", "low_cut": 180, "high_cut": 6000}, {"type": "tremolo", "rate_hz": 3, "depth": 0.25},
                          {"type": "reverb", "size": 0.9, "decay": 0.8, "mix": 0.45}]},
    "alien": {"group": "fx", "label": "Alien", "advanced": {"formant_shift": 22, "pitch_semitones": 3},
              "effects": [{"type": "ring_mod", "freq": 95, "mix": 0.5}, {"type": "flanger", "depth": 0.8, "rate_hz": 0.6, "mix": 0.5},
                          {"type": "reverb", "size": 0.3, "mix": 0.2}]},
    "demon": {"group": "fx", "label": "Demon", "advanced": {"pitch_semitones": -5, "formant_shift": -22, "roughness": 0.7,
                                                           "layers": [{"semitones": -12, "formant_shift": -12, "gain": 0.7}]},
              "effects": [{"type": "distortion", "drive": 2.5, "mix": 0.5}, {"type": "reverb", "size": 0.6, "decay": 0.6, "mix": 0.35}]},
    "monster": {"group": "fx", "label": "Monster / orc", "advanced": {"pitch_semitones": -6, "formant_shift": -15, "roughness": 1.0},
                "effects": [{"type": "distortion", "drive": 3, "mix": 0.4}]},
    "radio": {"group": "fx", "label": "Old radio", "effects": [{"type": "eq", "band": [300, 3400]}, {"type": "distortion", "drive": 2, "mix": 0.4},
                                                            {"type": "noise", "level": 0.008, "color": "hiss"}]},
    "telephone": {"group": "fx", "label": "Telephone", "effects": [{"type": "eq", "band": [350, 3200]}, {"type": "bitcrush", "bits": 12, "downsample": 3}]},
    "megaphone": {"group": "fx", "label": "Megaphone", "effects": [{"type": "eq", "band": [500, 4500]}, {"type": "distortion", "drive": 5, "mix": 0.7}]},
    "cave": {"group": "fx", "label": "Cave", "effects": [{"type": "reverb", "size": 1.0, "decay": 0.8, "mix": 0.5}, {"type": "echo", "delay": 0.35, "feedback": 0.35, "mix": 0.3}]},
    "underwater": {"group": "fx", "label": "Underwater", "effects": [{"type": "eq", "high_cut": 700}, {"type": "chorus", "depth": 1.0, "rate_hz": 0.4, "mix": 0.6}]},
    "space_helmet": {"group": "fx", "label": "Space helmet", "effects": [{"type": "eq", "band": [250, 5000]}, {"type": "reverb", "size": 0.08, "decay": 0.2, "mix": 0.35},
                                                                       {"type": "noise", "level": 0.004, "color": "hiss"}]},
}


def preset_list() -> list[dict]:
    return [{"id": k, "label": v["label"], "group": v["group"]} for k, v in PRESETS.items()]
