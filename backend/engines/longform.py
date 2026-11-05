"""
engines/longform.py — Long text for engines that do not split it themselves, plus a
model-free quality check on every generation.

Splitting order: paragraphs, then sentence ends, then clause punctuation, then the last
space before the limit. Inline tags such as [sighs], <laugh>, (cough) and <|speaker:1|>
are never cut. Pieces without any letters or digits are folded into a neighbour so the
engine is never asked to speak bare punctuation.

Chunks are trimmed of edge silence and joined with a short equal-power crossfade plus a
small pause, so sentence joins do not click or run together.

The idea follows VoiceStudio's chunked TTS service; this is an independent implementation
(VoiceStudio is AGPL-3.0 and none of its code is copied).
"""
from __future__ import annotations

import re
from typing import Optional

import numpy as np

DEFAULT_MAX_CHARS = 400
CHUNK_THRESHOLD = 600        # shorter text goes to the engine in one piece
_TAG = re.compile(r"(\[[^\]\n]{0,60}\]|<\|[^|>\n]{0,40}\|>|<[^>\n]{0,40}>|\([^)\n]{0,40}\))")
_ABBREV = {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e", "no", "mt", "lt", "col"}
_SENTENCE_END = re.compile(r"([.!?…。！？]+[\"'”’)\]]*)(\s+)")
_CLAUSE = re.compile(r"([,;:—–、，；：])(\s+)")
_SPEAKABLE = re.compile(r"[^\W_]", re.UNICODE)
_DENSE_SCRIPT = re.compile("[฀-๿぀-ヿ㐀-鿿가-힯]")  # Thai, kana, CJK, Hangul


def _protected_spans(text: str) -> list[tuple[int, int]]:
    return [m.span() for m in _TAG.finditer(text)]


def _inside(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(a < pos < b for a, b in spans)


def _split_at(text: str, pattern: re.Pattern, spans) -> list[str]:
    pieces, start = [], 0
    for m in pattern.finditer(text):
        cut = m.end(1)
        if _inside(cut, spans):
            continue
        if pattern is _SENTENCE_END:
            word = re.search(r"([\w.]+)$", text[start:m.start(1)] + text[m.start(1):m.end(1)].rstrip(".!?…"))
            if m.group(1) == "." and word and word.group(1).lower().rstrip(".") in _ABBREV:
                continue
            if m.group(1) == "." and re.search(r"\d$", text[:m.start(1)]) and re.match(r"\s+\d", text[m.end(1):]):
                continue
        pieces.append(text[start:cut].strip())
        start = m.end()
    pieces.append(text[start:].strip())
    return [p for p in pieces if p]


def _hard_split(text: str, max_chars: int) -> list[str]:
    out = []
    while len(text) > max_chars:
        spans = _protected_spans(text)
        cut = text.rfind(" ", 0, max_chars)
        while cut > 0 and _inside(cut, spans):
            cut = text.rfind(" ", 0, cut)
        if cut <= 0:
            end_tag = next((b for a, b in spans if a < max_chars < b), max_chars)
            cut = end_tag
        out.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        out.append(text)
    return out


def _units(text: str, max_chars: int) -> list[str]:
    units = []
    for para in re.split(r"\n\s*\n", text):
        para = " ".join(para.split())
        if not para:
            continue
        for sentence in _split_at(para + " ", _SENTENCE_END, _protected_spans(para + " ")):
            if len(sentence) <= max_chars:
                units.append(sentence)
                continue
            for clause in _split_at(sentence + " ", _CLAUSE, _protected_spans(sentence + " ")):
                units.extend(_hard_split(clause, max_chars) if len(clause) > max_chars else [clause])
        units.append("\n")  # paragraph marker: never merged across
    return units


def split_text(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[str]:
    """Split text into speakable chunks of at most about max_chars characters."""
    chunks: list[str] = []
    current = ""
    for unit in _units(text, max_chars):
        if unit == "\n":
            if current:
                chunks.append(current)
            current = ""
            continue
        if current and len(current) + 1 + len(unit) > max_chars:
            chunks.append(current)
            current = unit
        else:
            current = f"{current} {unit}".strip()
    if current:
        chunks.append(current)
    # Fold unspeakable fragments (bare tags or punctuation) into a neighbour.
    merged: list[str] = []
    for chunk in chunks:
        bare = _TAG.sub("", chunk)
        if merged and not _SPEAKABLE.search(bare):
            merged[-1] = f"{merged[-1]} {chunk}"
        elif merged and not _SPEAKABLE.search(_TAG.sub("", merged[-1])):
            merged[-1] = f"{merged[-1]} {chunk}"
        else:
            merged.append(chunk)
    return merged


def needs_chunking(text: str, engine_long_form: bool) -> bool:
    return not engine_long_form and len(text) > CHUNK_THRESHOLD


# ── Joining ──────────────────────────────────────────────────────────────────

def trim_edge_silence(samples: np.ndarray, rate: int, threshold_db: float = -45.0,
                      keep_ms: float = 40.0) -> np.ndarray:
    """Remove leading/trailing near-silence, keeping a short margin so consonants are not clipped."""
    x = np.asarray(samples, dtype=np.float32)
    if x.size == 0:
        return x
    win = max(1, int(rate * 0.01))
    frames = len(x) // win
    if frames == 0:
        return x
    rms = np.sqrt(np.mean(x[: frames * win].reshape(frames, win) ** 2, axis=1) + 1e-12)
    loud = np.nonzero(20 * np.log10(rms) > threshold_db)[0]
    if loud.size == 0:
        return x
    keep = int(rate * keep_ms / 1000)
    start = max(0, loud[0] * win - keep)
    end = min(len(x), (loud[-1] + 1) * win + keep)
    return x[start:end]


def join_chunks(chunks: list[np.ndarray], rate: int, fade_ms: float = 30.0, pause_ms: float = 180.0) -> np.ndarray:
    """Trim each chunk, then join with an equal-power crossfade into a short pause."""
    parts = [trim_edge_silence(c, rate) for c in chunks if c is not None and len(c)]
    if not parts:
        return np.zeros(0, dtype=np.float32)
    out = parts[0]
    pause = np.zeros(int(rate * pause_ms / 1000), dtype=np.float32)
    for nxt in parts[1:]:
        fade = min(int(rate * fade_ms / 1000), len(out) // 2, len(nxt) // 2)
        if fade > 0:
            t = np.linspace(0, np.pi / 2, fade, dtype=np.float32)
            tail = out[-fade:] * np.cos(t)          # fade the end of one chunk out ...
            head = nxt[:fade] * np.sin(t)           # ... and the next one in, around the pause
            out = np.concatenate([out[:-fade], tail, pause, head, nxt[fade:]])
        else:
            out = np.concatenate([out, pause, nxt])
    return out.astype(np.float32, copy=False)


# ── Model-free quality check ─────────────────────────────────────────────────

def analyze(samples: np.ndarray, rate: int, text: Optional[str] = None) -> dict:
    """Level, clipping and silence figures with plain-language warnings. Not a listening test."""
    x = np.asarray(samples, dtype=np.float32)
    report: dict = {"warnings": []}
    if x.size == 0:
        report["warnings"].append("The output is empty.")
        return report
    peak = float(np.max(np.abs(x)))
    rms = float(np.sqrt(np.mean(x ** 2)))
    clipped = float(np.mean(np.abs(x) >= 0.999))
    win = max(1, int(rate * 0.05))
    frames = len(x) // win
    silent_ratio = 0.0
    longest_silence = 0.0
    if frames:
        frms = np.sqrt(np.mean(x[: frames * win].reshape(frames, win) ** 2, axis=1) + 1e-12)
        silent = 20 * np.log10(frms) < -50
        silent_ratio = float(np.mean(silent))
        run = best = 0
        for s in silent:
            run = run + 1 if s else 0
            best = max(best, run)
        longest_silence = best * win / rate
    duration = len(x) / rate
    report.update({
        "peak_dbfs": round(20 * np.log10(peak + 1e-12), 1),
        "rms_dbfs": round(20 * np.log10(rms + 1e-12), 1),
        "clipped_fraction": round(clipped, 5),
        "silence_fraction": round(silent_ratio, 3),
        "longest_silence_s": round(longest_silence, 2),
    })
    w = report["warnings"]
    if peak < 1e-4:
        w.append("The output is silent.")
    elif report["rms_dbfs"] < -40:
        w.append("The output is very quiet.")
    if clipped > 0.001:
        w.append("The output clips (distorts) in places.")
    if silent_ratio > 0.6 and duration > 2:
        w.append("Most of the output is silence.")
    if longest_silence > 4:
        w.append(f"The output contains a {longest_silence:.1f} s silent gap.")
    if text and not _DENSE_SCRIPT.search(text):  # word counts mean little for CJK/Thai scripts
        words = len(re.findall(r"[^\W_]+", _TAG.sub(" ", text)))
        speech_s = duration * (1 - silent_ratio)
        if words >= 4 and speech_s > 0:
            rate_wps = words / speech_s
            report["words_per_second"] = round(rate_wps, 2)
            if rate_wps > 6:
                w.append("The output is much shorter than the text; words may be missing.")
            elif rate_wps < 0.8:
                w.append("The output is much longer than the text; it may ramble or repeat.")
    return report
