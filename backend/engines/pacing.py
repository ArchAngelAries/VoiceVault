"""Pacing for any speech engine: exact pauses, slower or faster phrases, and the gaps between sentences.

Tags in the text (case-insensitive):
  [pause]            0.5 s of silence          [pause 0.8s] / [pause 800ms]  exactly that much
  [slow] ... [/slow] that part at 0.8× speed   [fast] ... [/fast]           that part at 1.2× speed
  [speed 0.7] ... [/speed]                     that part at any speed from 0.5× to 2×
Pace (the whole clip): "tight", "natural" (as generated), "relaxed" or "dramatic" sets the silence
between sentences. Words are never cut: only silence that is already there is shortened or lengthened.

Text with no tags at the natural pace is passed straight to the engine, unchanged.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Callable

import numpy as np

PACES = ("tight", "natural", "relaxed", "dramatic")
DEFAULT_PAUSE_S = 0.5
MAX_PAUSE_S = 10.0
SLOW, FAST = 0.8, 1.2
_TAG = re.compile(r"\[(?P<tag>/?(?:pause|slow|fast|speed))(?:\s+(?P<arg>[0-9]*\.?[0-9]+)\s*(?P<unit>ms|s|x|×)?)?\s*\]",
                  re.IGNORECASE)
# Gap rules per pace, in seconds: tight caps long pauses, relaxed and dramatic stretch short ones.
_GAP = {"tight": (None, 0.15), "relaxed": (0.45, None), "dramatic": (0.8, None)}
MIN_GAP_S = 0.12          # shorter silences are inside words (stop consonants), never touched
MIN_STRETCH_S = 0.25      # only pauses this long (sentence ends) are lengthened; comma pauses and breaths stay
EDGE_S = 0.04             # silence kept on each side of a gap so word endings aren't clipped


@dataclass
class Part:
    kind: str             # "text" or "pause"
    text: str = ""
    seconds: float = 0.0
    speed: float = 1.0


def has_tags(text: str) -> bool:
    return bool(_TAG.search(text or ""))


def parse(text: str) -> list[Part]:
    """Split text into spoken parts and pauses. Unknown or malformed tags are left as text."""
    parts: list[Part] = []
    speeds: list[float] = []
    pos = 0

    def add_text(chunk: str):
        chunk = chunk.strip()
        if not chunk:
            return
        # Sentence punctuation right after a tag ("." after [/fast]) belongs to the previous spoken part,
        # so it is never voiced on its own. Engine cues such as [laughs] are left where they are.
        lead = re.match(r"^[.,!?;:…\"”’)\-–—]+", chunk)
        if lead and parts and parts[-1].kind == "text":
            parts[-1].text += lead.group(0)
            chunk = chunk[lead.end():].strip()
        if not chunk or not any(ch.isalnum() for ch in chunk):
            if chunk and parts and parts[-1].kind == "text":
                parts[-1].text += chunk
            return
        parts.append(Part("text", chunk, speed=speeds[-1] if speeds else 1.0))

    for match in _TAG.finditer(text or ""):
        add_text(text[pos:match.start()])
        pos = match.end()
        tag, arg, unit = match.group("tag").lower(), match.group("arg"), (match.group("unit") or "").lower()
        if tag == "pause":
            seconds = DEFAULT_PAUSE_S if arg is None else float(arg) / (1000 if unit == "ms" else 1)
            parts.append(Part("pause", seconds=min(max(seconds, 0.0), MAX_PAUSE_S)))
        elif tag in ("slow", "fast", "speed"):
            value = SLOW if tag == "slow" else FAST if tag == "fast" else float(arg or 1.0)
            speeds.append(min(max(value, 0.5), 2.0))
        elif speeds:                                    # a closing tag ends the latest open speed
            speeds.pop()
    add_text(text[pos:])
    return parts


def _frames(samples: np.ndarray, rate: int, frame_s: float = 0.01) -> tuple[np.ndarray, int]:
    hop = max(1, int(rate * frame_s))
    n = len(samples) // hop
    if n == 0:
        return np.zeros(0, np.float32), hop
    rms = np.sqrt(np.mean(samples[: n * hop].reshape(n, hop) ** 2, axis=1))
    return rms, hop


def silences(samples: np.ndarray, rate: int, threshold_db: float = -40.0) -> list[tuple[int, int]]:
    """Silent stretches between speech (sample ranges), ignoring the very start and end."""
    rms, hop = _frames(samples, rate)
    if not rms.size or rms.max() <= 0:
        return []
    quiet = rms < rms.max() * 10 ** (threshold_db / 20)
    voiced = np.flatnonzero(~quiet)
    if voiced.size == 0:
        return []
    first, last = voiced[0], voiced[-1]
    gaps, start = [], None
    for i in range(first, last + 1):
        if quiet[i] and start is None:
            start = i
        elif not quiet[i] and start is not None:
            gaps.append((start * hop, i * hop))
            start = None
    return gaps


def set_gaps(samples: np.ndarray, rate: int, pace: str) -> np.ndarray:
    """Shorten or lengthen the silences between sentences; speech itself is never changed."""
    rule = _GAP.get(pace)
    if not rule:
        return samples
    low, high = rule
    edge = int(EDGE_S * rate)
    out, pos = [], 0
    for start, end in silences(samples, rate):
        length = (end - start) / rate
        if length < MIN_GAP_S:
            continue
        target = length
        if high is not None:
            target = min(length, high)
        if low is not None and length >= MIN_STRETCH_S:
            target = max(length, low)
        if abs(target - length) < 0.01:
            continue
        keep_a, keep_b = start + edge, end - edge          # silence kept next to the words
        out.append(samples[pos:keep_a])
        middle = max(0, int(target * rate) - 2 * edge)
        out.append(np.zeros(middle, np.float32))
        pos = keep_b
    out.append(samples[pos:])
    return np.concatenate(out).astype(np.float32) if out else samples


def change_speed(samples: np.ndarray, rate: int, speed: float) -> np.ndarray:
    """Faster or slower with the same pitch (Voice Lab's WORLD resynthesis)."""
    if abs(speed - 1.0) < 1e-3 or len(samples) < rate // 10:
        return samples
    from voice import shaper
    out, _ = shaper.shape(samples, rate, {"speed": speed}, None, key=None)
    return out.astype(np.float32)


def _fade(samples: np.ndarray, rate: int, ms: float = 8.0) -> np.ndarray:
    n = min(len(samples) // 2, int(rate * ms / 1000))
    if n <= 1:
        return samples
    out = samples.copy()
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
    out[:n] *= ramp
    out[-n:] *= ramp[::-1]
    return out


def needed(text: str, pace: str) -> bool:
    return has_tags(text) or (pace or "natural") != "natural"


def synthesize(run: Callable, request, pace: str = "natural"):
    """Voice `request` with pacing. `run(request)` is the normal synthesis call (returns SynthesisResult).

    Each spoken part is voiced on its own with the same voice, reference and seed, so the speaker
    stays the same. Returns a SynthesisResult with the parts joined.
    """
    from engines.base import SynthesisResult
    pace = pace if pace in PACES else "natural"
    parts = parse(request.text)
    spoken = [p for p in parts if p.kind == "text"]
    if not spoken:
        raise ValueError("There is nothing to say: the text only has pacing tags.")
    if len(parts) == 1 and spoken[0].speed == 1.0:
        result = run(replace(request, text=spoken[0].text))
        samples = set_gaps(result.samples.astype(np.float32), result.sample_rate, pace)
        meta = {**result.meta, "pacing": {"pace": pace, "parts": 1}}
        return SynthesisResult(samples=samples, sample_rate=result.sample_rate, meta=meta)
    pieces, rate, metas = [], None, []
    for part in parts:
        if part.kind == "pause":
            pieces.append(("pause", part.seconds))
            continue
        result = run(replace(request, text=part.text))
        if rate is None:
            rate = result.sample_rate
        elif result.sample_rate != rate:
            raise ValueError("The engine returned parts at different sample rates.")
        audio = set_gaps(result.samples.astype(np.float32), rate, pace)
        audio = change_speed(audio, rate, part.speed)
        pieces.append(("audio", _trim(audio, rate)))
        metas.append(result.meta)
    joined, previous = [], None
    for kind, value in pieces:
        if kind == "pause":
            joined.append(np.zeros(int(value * rate), np.float32))
        else:
            if previous == "audio":       # two spoken parts with no pause tag between them
                joined.append(np.zeros(int(_between(pace) * rate), np.float32))
            joined.append(_fade(value, rate))
        previous = kind
    meta = dict(metas[0])
    if any(m.get("truncated") for m in metas):
        meta["truncated"] = True          # a later part hit the voice model's length limit
    warnings = [w for m in metas for w in (m.get("delivery_warnings") or [])]
    if warnings:
        meta["delivery_warnings"] = list(dict.fromkeys(warnings))
    meta = {**meta, "pacing": {"pace": pace, "parts": len(spoken),
                                   "pauses_s": [p.seconds for p in parts if p.kind == "pause"],
                                   "speeds": [p.speed for p in spoken]}}
    return SynthesisResult(samples=np.concatenate(joined).astype(np.float32), sample_rate=rate, meta=meta)


def _trim(samples: np.ndarray, rate: int, threshold_db: float = -45.0) -> np.ndarray:
    """Drop leading and trailing silence of a part, so the tags alone decide the pause length."""
    rms, hop = _frames(samples, rate)
    if not rms.size or rms.max() <= 0:
        return samples
    voiced = np.flatnonzero(rms >= rms.max() * 10 ** (threshold_db / 20))
    if voiced.size == 0:
        return samples
    pad = int(0.03 * rate)
    return samples[max(0, voiced[0] * hop - pad): min(len(samples), (voiced[-1] + 1) * hop + pad)]


def _between(pace: str) -> float:
    """Silence between two spoken parts with no pause tag between them: a speed change, often mid-sentence."""
    return {"tight": 0.03, "relaxed": 0.12, "dramatic": 0.2}.get(pace, 0.06)

