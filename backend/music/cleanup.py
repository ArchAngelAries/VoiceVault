"""
music/cleanup.py — Voice-clip cleaning through audio.cpp (used by Harvest).

Steps run in a fixed order, each optional:
  remove_music   Mel-Band RoFormer keeps the vocal stem (music and most effects removed)
  isolate_voice  SAM-Audio keeps only the sound described by a text prompt (default: one person
                 speaking), removing effects, crowd and other voices that music separation leaves
  denoise        FFmpeg afftdn, gentle broadband noise reduction (no model)
  restore        Sidon speech restoration to 48 kHz (noise, reverb, codec damage, muffled audio)
  bandwidth      UniverSR rebuilds high frequencies of band-limited audio (e.g. old 24 kHz clips).
                 It runs on a CPU audio.cpp server (Vulkan lacks an op it needs) and is slow, so the
                 clip is processed in short overlapping pieces that each finish well inside the timeout.

All steps work on mono audio. The result is returned at 48 kHz (the native rate of SAM-Audio
and Sidon) so nothing above 12 kHz is thrown away before cloning. The input is never modified.

Licences: RoFormer MIT, SAM-Audio Meta SAM License, Sidon MIT, UniverSR CC-BY-4.0.
SAM-Audio adds Meta's inaudible audio watermark to what it outputs, as the official model does.
"""
from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from music import dsp
from music.runtime import named_outputs, result_audio

STEPS = ("remove_music", "isolate_voice", "denoise", "restore", "bandwidth")
ROLE_FOR_STEP = {"remove_music": "vocals", "isolate_voice": "separate", "restore": "restore", "bandwidth": "bwe"}
STEP_NAMES = {
    "remove_music": "Remove music (RoFormer)",
    "isolate_voice": "Keep only the voice (SAM-Audio, text prompt)",
    "denoise": "Gentle denoise (FFmpeg)",
    "restore": "Restore speech to 48 kHz (Sidon)",
    "bandwidth": "Rebuild high frequencies (UniverSR)",
}
STEP_MODELS = {"remove_music": "RoFormer", "isolate_voice": "SAM-Audio", "restore": "Sidon", "bandwidth": "UniverSR"}
STEP_NOTES = {"bandwidth": "Slow: runs on the CPU, about a minute per second of audio."}
BWE_CHUNK_S = 8.0     # ~8 min per piece on a 24-thread CPU (measured 2026-11-04), far inside run_tool's 30 min
BWE_OVERLAP_S = 0.25  # crossfade between pieces
DEFAULT_VOICE_PROMPT = "a person speaking"
OUT_RATE = 48000


class CleanupError(RuntimeError):
    pass


def normalise_steps(value) -> list[str]:
    """Accept the legacy single value ("none" / "denoise") or a list of step ids; keep the fixed order."""
    if value in (None, "", "none"):
        return []
    items = [value] if isinstance(value, str) else list(value)
    unknown = [s for s in items if s not in STEPS and s != "none"]
    if unknown:
        raise CleanupError(f"Unknown cleanup step: {', '.join(map(str, unknown))}.")
    return [s for s in STEPS if s in items]


def available_steps(has_role: Callable[[str], bool], ffmpeg: bool) -> list[dict]:
    out = []
    for step in STEPS:
        if step == "denoise":
            ok, reason = ffmpeg, None if ffmpeg else "FFmpeg is not installed."
        else:
            role = ROLE_FOR_STEP[step]
            ok = has_role(role)
            reason = None if ok else f"The {STEP_MODELS[step]} model is not installed."
        item = {"id": step, "name": STEP_NAMES[step], "available": ok, "reason": reason}
        if step in STEP_NOTES:
            item["note"] = STEP_NOTES[step]
        out.append(item)
    return out


def _write(tmp: Path, audio: np.ndarray, rate: int, target_rate: int, channels: int, tag: str) -> Path:
    path = tmp / f"clean-{tag}-{uuid.uuid4().hex[:8]}.wav"
    dsp.write(path, dsp.match(audio, rate, target_rate, channels), target_rate)
    return path


def clean(audio: np.ndarray, rate: int, steps: list[str], run_tool: Callable[[str, dict], dict], tmp: Path,
          voice_prompt: Optional[str] = None, source_rate: Optional[int] = None,
          progress: Callable[[str], None] = lambda _m: None) -> tuple[np.ndarray, int, list[str]]:
    """Run the cleaning steps on (frames, channels) audio. Returns mono audio at 48 kHz and notes."""
    tmp.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    cur, cur_rate = dsp.match(audio, rate, OUT_RATE, 1), OUT_RATE
    for step in steps:
        progress(STEP_NAMES[step] + "…")
        if step == "denoise":
            src = _write(tmp, cur, cur_rate, cur_rate, 1, "dn-in")
            dst = src.with_name(src.stem + "-out.wav")
            try:
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-af", "afftdn=nf=-25:tn=1",
                                "-ac", "1", "-ar", str(cur_rate), str(dst)], check=True, timeout=300,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                cur, cur_rate = dsp.read(dst)
            finally:
                src.unlink(missing_ok=True)
                dst.unlink(missing_ok=True)
            continue
        if step == "bandwidth":
            band = source_rate if source_rate in (8000, 12000, 16000, 24000) else 24000
            cur = _bandwidth(cur, cur_rate, band, run_tool, tmp, progress)
            if not np.isfinite(cur).all() or float(np.max(np.abs(cur))) < 1e-5:
                raise CleanupError(f"{STEP_NAMES[step]} returned silence; try fewer steps or another prompt.")
            continue
        role = ROLE_FOR_STEP[step]
        if step == "remove_music":
            path = _write(tmp, cur, cur_rate, 44100, 2, "sep")
            request = {"audio": path.resolve().as_posix()}
        elif step == "isolate_voice":
            path = _write(tmp, cur, cur_rate, 48000, 1, "sam")
            request = {"audio": path.resolve().as_posix(), "text": (voice_prompt or DEFAULT_VOICE_PROMPT).strip()[:200],
                       "seed": 42, "options": {"seed": 42}}
        else:  # restore
            path = _write(tmp, cur, cur_rate, 48000, 1, "restore")
            request = {"audio": path.resolve().as_posix()}
        try:
            result = run_tool(role, request)
        finally:
            path.unlink(missing_ok=True)
        outputs = named_outputs(result)
        if step == "remove_music":
            key = next((k for k in outputs if "vocal" in k.lower()), None)
            if key is None:
                raise CleanupError("Music removal returned no vocal stem.")
            cur, cur_rate = dsp.decode(outputs[key])
        elif step == "isolate_voice":
            key = next((k for k in outputs if "target" in k.lower()), None)
            data = outputs[key] if key else result_audio(result)
            cur, cur_rate = dsp.decode(data)
            notes.append("SAM-Audio adds Meta's inaudible watermark to its output.")
        else:
            cur, cur_rate = dsp.decode(result_audio(result))
        cur = dsp.match(cur, cur_rate, OUT_RATE, 1)
        cur_rate = OUT_RATE
        if not np.isfinite(cur).all() or float(np.max(np.abs(cur))) < 1e-5:
            raise CleanupError(f"{STEP_NAMES[step]} returned silence; try fewer steps or another prompt.")
    peak = float(np.max(np.abs(cur))) if cur.size else 0.0
    if peak > 0.99:
        cur = cur * (0.99 / peak)
    return cur.astype(np.float32), cur_rate, notes


def _bandwidth(cur: np.ndarray, rate: int, band: int, run_tool: Callable[[str, dict], dict], tmp: Path,
               progress: Callable[[str], None]) -> np.ndarray:
    """UniverSR over (frames, 1) audio at `rate`, in overlapping pieces joined with a linear crossfade."""
    piece, overlap = int(BWE_CHUNK_S * rate), int(BWE_OVERLAP_S * rate)
    total = len(cur)
    starts = [0]
    # A short tail joins the previous piece rather than becoming a fragment of its own.
    while total - (starts[-1] + piece - overlap) >= 2 * rate:
        starts.append(starts[-1] + piece - overlap)
    out = np.zeros((total, 1), dtype=np.float32)
    for n, start in enumerate(starts):
        if len(starts) > 1:
            progress(f"{STEP_NAMES['bandwidth']} piece {n + 1} of {len(starts)}…")
        end = starts[n + 1] + overlap if n + 1 < len(starts) else total
        seg = cur[start:end]
        path = _write(tmp, seg, rate, OUT_RATE, 1, "bwe")
        try:
            result = run_tool("bwe", {"audio": path.resolve().as_posix(), "options": {"input_sample_rate": band}})
        finally:
            path.unlink(missing_ok=True)
        got, got_rate = dsp.decode(result_audio(result))
        got = dsp.match(got, got_rate, OUT_RATE, 1)[:len(seg)]
        if len(got) < len(seg):
            got = np.concatenate([got, np.zeros((len(seg) - len(got), 1), dtype=got.dtype)])
        if start > 0:
            fade = min(overlap, len(got))
            ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)[:, None]
            out[start:start + fade] = out[start:start + fade] * (1 - ramp) + got[:fade] * ramp
            out[start + fade:start + len(got)] = got[fade:]
        else:
            out[:len(got)] = got
    return out
