"""
music/studio.py — Song projects, takes and the job queue of the Music Studio.

A project holds the style prompt, lyrics and a list of takes. Every edit makes a new take
that records its operation, parameters and parent, so nothing is overwritten and any
version can be compared, exported or edited again.

Operations (one GPU job at a time, under the shared speech lock):
  generate   YuE2 (full song, optional melody plan) or ACE-Step text2music
  cover      a reference recording restyled: ACE-Step cover, or YuE2 sung over the
             SheetSage2 melody score of the reference
  repaint    regenerate a time range (ACE-Step repaint); the result is spliced so the
             audio outside the range stays sample-identical
  extend     continue the song (ACE-Step complete), keeping the original intact
  layer      add or replace one instrument track (ACE-Step lego)
  stems      split into drums, bass, vocals, other, guitar and piano (HTDemucs 6-stem)
  analyze    tempo, key, metre and style tags of a take, for prompting (CPU; music/analysis.py)
  align      word-timed lyrics: vocals isolated, then forced alignment of known lyrics,
             or transcription when there are none (Qwen3)
Local, CPU-only edits: splice (region from another take), blend (crossfade two takes),
mixdown (stems with gains/mutes), import, export (WAV / FLAC / MP3, loudness target).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from music import dsp, lyrics as lyr
from music.runtime import MusicRuntime, MusicRuntimeError, artifact_text, named_outputs, result_audio

log = logging.getLogger(__name__)

PROJECT_ID = re.compile(r"^[a-f0-9]{12}$")
TAKE_ID = re.compile(r"^[a-f0-9]{10}$")
STEM_NAMES = ("drums", "bass", "vocals", "other", "guitar", "piano")
INPUT_FORMAT = {"sfx": (44100, 2), "ace_step": (48000, 2), "stems": (44100, 2), "vocals": (44100, 2), "score": (44100, 1),
                "asr": (16000, 1), "align": (16000, 1)}
MAX_SECONDS = 600.0


class StudioError(ValueError):
    pass


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── Projects ────────────────────────────────────────────────────────────────

class ProjectStore:
    def __init__(self, root: Path):
        self.root = root
        self._lock = threading.RLock()

    def _dir(self, pid: str) -> Path:
        if not PROJECT_ID.match(pid or ""):
            raise StudioError("Invalid project id.")
        return self.root / pid

    def list(self) -> list[dict]:
        out = []
        if self.root.is_dir():
            for d in self.root.iterdir():
                f = d / "project.json"
                if f.is_file():
                    try:
                        p = json.loads(f.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        continue
                    out.append({k: p.get(k) for k in ("id", "title", "created", "updated", "style")} |
                               {"takes": len(p.get("takes", []))})
        return sorted(out, key=lambda p: p.get("updated") or "", reverse=True)

    def create(self, title: str, style: str = "", lyrics: str = "") -> dict:
        pid = uuid.uuid4().hex[:12]
        project = {"id": pid, "title": title.strip()[:120] or "Untitled song", "style": style, "lyrics": lyrics,
                   "created": _now(), "updated": _now(), "takes": [], "active_take": None}
        self.save(project)
        return project

    def get(self, pid: str) -> dict:
        f = self._dir(pid) / "project.json"
        if not f.is_file():
            raise StudioError("Project not found.")
        return json.loads(f.read_text(encoding="utf-8"))

    def save(self, project: dict) -> dict:
        with self._lock:
            d = self._dir(project["id"])
            d.mkdir(parents=True, exist_ok=True)
            project["updated"] = _now()
            tmp = d / "project.json.tmp"
            tmp.write_text(json.dumps(project, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, d / "project.json")
            return project

    def update(self, pid: str, fn: Callable[[dict], None]) -> dict:
        with self._lock:
            project = self.get(pid)
            fn(project)
            return self.save(project)

    def delete(self, pid: str) -> None:
        d = self._dir(pid)
        if d.is_dir():
            shutil.rmtree(d)

    def take_path(self, pid: str, tid: str, name: Optional[str] = None) -> Path:
        if not TAKE_ID.match(tid or ""):
            raise StudioError("Invalid take id.")
        base = self._dir(pid) / "takes"
        return base / (f"{tid}.wav" if name is None else f"{tid}.{name}.wav")

    def find_take(self, project: dict, tid: str) -> dict:
        for t in project["takes"]:
            if t["id"] == tid:
                return t
        raise StudioError("Take not found.")

    def add_take(self, pid: str, audio: np.ndarray, rate: int, *, op: str, label: str,
                 parent: Optional[str] = None, params: Optional[dict] = None, extra: Optional[dict] = None) -> dict:
        tid = uuid.uuid4().hex[:10]
        dsp.write(self.take_path(pid, tid), audio, rate)
        take = {"id": tid, "label": label[:120], "op": op, "parent": parent, "params": params or {},
                "rate": rate, "channels": int(audio.shape[1]), "duration": round(len(audio) / rate, 3),
                "created": _now(), "stems": {}, "alignment": None, **(extra or {})}

        def add(p):
            p["takes"].append(take)
            p["active_take"] = tid
        self.update(pid, add)
        return take

    def load_take(self, pid: str, tid: str) -> tuple[np.ndarray, int]:
        return dsp.read(self.take_path(pid, tid))


# ── Jobs ────────────────────────────────────────────────────────────────────

@dataclass
class Job:
    id: str
    project: str
    kind: str
    params: dict
    status: str = "queued"      # queued, running, done, failed, canceled
    message: str = ""
    error: Optional[str] = None
    take: Optional[str] = None
    created: str = field(default_factory=_now)
    started: Optional[str] = None
    finished: Optional[str] = None
    seconds: Optional[float] = None

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("id", "project", "kind", "params", "status", "message", "error",
                                              "take", "created", "started", "finished", "seconds")}


class Studio:
    def __init__(self, store: ProjectStore, runtime: MusicRuntime, tmp_dir: Path,
                 gpu_lock=None, release_others: Optional[Callable[[], None]] = None):
        self.store = store
        self.runtime = runtime
        self.tmp = tmp_dir
        self._gpu_lock = gpu_lock or threading.RLock()
        self._release_others = release_others or (lambda: None)
        self._jobs: dict[str, Job] = {}
        self._queue: deque[str] = deque()
        self._cv = threading.Condition()
        self._worker: Optional[threading.Thread] = None
        self._stopping = False

    # Queue ------------------------------------------------------------------
    def submit(self, project: str, kind: str, params: dict) -> Job:
        self.store.get(project)
        if kind not in OPERATIONS:
            raise StudioError(f"Unknown operation '{kind}'.")
        job = Job(id=uuid.uuid4().hex[:12], project=project, kind=kind, params=params)
        with self._cv:
            if self._stopping:
                raise StudioError("The studio is shutting down.")
            self._jobs[job.id] = job
            self._queue.append(job.id)
            self._cv.notify()
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._run, name="music-studio", daemon=True)
                self._worker.start()
        return job

    def jobs(self, project: Optional[str] = None) -> list[dict]:
        items = [j for j in self._jobs.values() if project is None or j.project == project]
        return [j.to_dict() for j in sorted(items, key=lambda j: j.created, reverse=True)[:100]]

    def job(self, jid: str) -> dict:
        if jid not in self._jobs:
            raise StudioError("Job not found.")
        return self._jobs[jid].to_dict()

    def cancel(self, jid: str) -> dict:
        with self._cv:
            job = self._jobs.get(jid)
            if job is None:
                raise StudioError("Job not found.")
            if job.status == "queued":
                self._queue.remove(jid)
                job.status, job.finished = "canceled", _now()
            return job.to_dict()

    def run_tool(self, role: str, request: dict, timeout: float = 1800) -> dict:
        """Run one audio.cpp call for another feature (e.g. Harvest cleaning) with exclusive GPU use."""
        with self._gpu_lock:
            self._release_others()
            return self.runtime.run_task(role, request, timeout=timeout)

    def run_details(self, role: str, audio_path: Path, language: Optional[str] = None) -> dict:
        """Transcript and speaker details from an ASR/diarization model, with exclusive GPU use."""
        with self._gpu_lock:
            self._release_others()
            return self.runtime.details(role, audio_path, language)

    def queue_position(self, jid: str) -> Optional[int]:
        try:
            return list(self._queue).index(jid) + 1
        except ValueError:
            return None

    def shutdown(self) -> None:
        with self._cv:
            self._stopping = True
            for jid in list(self._queue):
                self._jobs[jid].status = "canceled"
            self._queue.clear()
            self._cv.notify_all()
        self.runtime.stop()

    def _run(self) -> None:
        while True:
            with self._cv:
                while not self._queue and not self._stopping:
                    if not self._cv.wait(timeout=60):
                        self._worker = None
                        return
                if self._stopping:
                    return
                job = self._jobs[self._queue.popleft()]
                job.status, job.started = "running", _now()
            started = time.time()
            try:
                local = OPERATIONS[job.kind][1]
                if local:
                    take = OPERATIONS[job.kind][0](self, job)
                else:
                    with self._gpu_lock:
                        self._release_others()
                        take = OPERATIONS[job.kind][0](self, job)
                job.take = take["id"] if isinstance(take, dict) else None
                job.status = "done"
            except (StudioError, MusicRuntimeError) as exc:
                job.status, job.error = "failed", str(exc)
            except Exception as exc:  # report, keep the worker alive
                log.exception("Music job %s failed", job.id)
                job.status, job.error = "failed", f"{exc.__class__.__name__}: {exc}"[:400]
            finally:
                job.finished = _now()
                job.seconds = round(time.time() - started, 1)

    # Helpers ----------------------------------------------------------------
    def _input_wav(self, audio: np.ndarray, rate: int, role: str, name: str) -> Path:
        target_rate, channels = INPUT_FORMAT.get(role, (rate, audio.shape[1]))
        path = self.tmp / f"{name}-{uuid.uuid4().hex[:6]}.wav"
        dsp.write(path, dsp.match(audio, rate, target_rate, channels), target_rate)
        return path

    def _take_input(self, pid: str, tid: str, role: str, start: Optional[float] = None,
                    end: Optional[float] = None) -> tuple[Path, np.ndarray, int]:
        audio, rate = self.store.load_take(pid, tid)
        if start is not None or end is not None:
            a = int((start or 0) * rate)
            b = int(end * rate) if end is not None else len(audio)
            audio = audio[a:b]
        return self._input_wav(audio, rate, role, f"{tid}-{role}"), audio, rate

    @staticmethod
    def _seed(params: dict) -> int:
        seed = params.get("seed")
        return int(seed) if seed not in (None, "", -1) else int.from_bytes(os.urandom(4), "little")

    def _style_lyrics(self, job: Job) -> tuple[str, str]:
        project = self.store.get(job.project)
        style = (job.params.get("style") if job.params.get("style") is not None else project.get("style")) or ""
        text = (job.params.get("lyrics") if job.params.get("lyrics") is not None else project.get("lyrics")) or ""
        return style.strip(), text


# ── Operations ──────────────────────────────────────────────────────────────

def _ace_request(text: str, lyrics_text: str, seed: int, options: dict, audio: Optional[Path] = None) -> dict:
    opts = {k: v for k, v in options.items() if v is not None and v != ""}
    opts.setdefault("lyrics", lyrics_text)
    opts["seed"] = seed
    req: dict[str, Any] = {"text": text or "music", "lyrics": lyrics_text, "seed": seed, "options": opts}
    if audio is not None:
        req["audio"] = audio.resolve().as_posix()
    return req


def _save_result(studio: Studio, job: Job, result: dict, label: str, parent: Optional[str], extra=None) -> dict:
    audio, rate = dsp.decode(result_audio(result))
    return studio.store.add_take(job.project, audio, rate, op=job.kind, label=label, parent=parent,
                                 params=job.params, extra=extra)


def op_generate(studio: Studio, job: Job) -> dict:
    p = job.params
    style, text = studio._style_lyrics(job)
    seed = studio._seed(p)
    engine = p.get("engine") or ("yue2" if studio.runtime.has("yue2") else "ace_step")
    job.message = f"Composing with {engine}…"
    if engine == "yue2":
        opts = {"style": style or "pop, clear vocals", "lyrics": text, "cot": p.get("plan") or "full",
                "seed": seed, "num_inference_steps": int(p.get("steps") or 8)}
        if p.get("guidance") is not None:
            opts["guidance_scale"] = float(p["guidance"])
        if p.get("abc"):
            opts["abc"] = p["abc"]
            if opts["cot"] == "off":
                opts["cot"] = "melody"
        result = studio.runtime.run_task("yue2", {"text": text, "lyrics": text, "seed": seed, "options": opts},
                                         include_artifacts=True)
        score = artifact_text(result)
        return _save_result(studio, job, result, p.get("label") or f"YuE2 · seed {seed}", None,
                            extra={"engine": "yue2", "seed": seed, "abc": score})
    duration = float(p.get("duration") or 120)
    opts = {"route": "text2music", "duration_sec": min(duration, MAX_SECONDS), "bpm": p.get("bpm"),
            "keyscale": p.get("key"), "timesignature": p.get("time_signature"),
            "num_inference_steps": p.get("steps"), "guidance_scale": p.get("guidance"),
            "language": p.get("language"), "negative_prompt": p.get("negative")}
    result = studio.runtime.run_task("ace_step", _ace_request(style, text, seed, opts))
    return _save_result(studio, job, result, p.get("label") or f"ACE-Step · seed {seed}", None,
                        extra={"engine": "ace_step", "seed": seed})


# ACE-Step's VAE encoder needs ~140 MB of GPU buffer per second of source audio in one allocation;
# past ~28 s it exceeds the Vulkan per-allocation limit on a 20 GB RX 7900 XT. Source audio is
# therefore sent in windows: repaint/extend use a local window, cover/instrument routes use chunks.
ACE_MAX_INPUT_S = 22.0
ACE_REPAINT_REGION_S = 14.0
ACE_REPAINT_CONTEXT_S = 4.0
ACE_CHUNK_S, ACE_CHUNK_OVERLAP_S = 20.0, 2.0


def _ace_on_audio(studio: Studio, audio: np.ndarray, rate: int, name: str, text: str, lyrics_text: str,
                  seed: int, opts: dict) -> np.ndarray:
    """Run one ACE-Step route on a short clip; return the result at the clip's rate/channels."""
    path = studio._input_wav(audio, rate, "ace_step", name)
    try:
        result = studio.runtime.run_task("ace_step", _ace_request(text, lyrics_text, seed, opts, path))
    finally:
        path.unlink(missing_ok=True)
    out, out_rate = dsp.decode(result_audio(result))
    return dsp.match(out, out_rate, rate, audio.shape[1])


def _overlap_join(first: np.ndarray, second: np.ndarray, rate: int, overlap: float) -> np.ndarray:
    """Join two clips whose first `overlap` seconds of `second` repeat the last ones of `first`."""
    f = min(int(overlap * rate), len(first), len(second))
    t = np.linspace(0, np.pi / 2, f, dtype=np.float32)[:, None]
    mixed = first[len(first) - f:] * np.cos(t) + second[:f] * np.sin(t)
    return np.concatenate([first[:len(first) - f], mixed, second[f:]]).astype(np.float32)


def _ace_chunked(studio: Studio, job: Job, audio: np.ndarray, rate: int, text: str, lyrics_text: str,
                 seed: int, opts: dict, label: str) -> tuple[np.ndarray, int]:
    """Apply a whole-source route to the song in overlapping chunks, joined with equal-power crossfades."""
    total = len(audio) / rate
    if total <= ACE_MAX_INPUT_S:
        job.message = f"{label}…"
        return _ace_on_audio(studio, audio, rate, "src", text, lyrics_text, seed, opts), 1
    hop = ACE_CHUNK_S - ACE_CHUNK_OVERLAP_S
    count = int(np.ceil((total - ACE_CHUNK_OVERLAP_S) / hop))
    out: Optional[np.ndarray] = None
    for n in range(count):
        job.message = f"{label}: part {n + 1} of {count}…"
        a = int(n * hop * rate)
        b = min(len(audio), a + int(ACE_CHUNK_S * rate))
        piece = _ace_on_audio(studio, audio[a:b], rate, f"chunk{n}", text, lyrics_text, seed, opts)
        if len(piece) < b - a:
            piece = np.concatenate([piece, np.zeros((b - a - len(piece), piece.shape[1]), np.float32)])
        piece = piece[: b - a]
        out = piece if out is None else _overlap_join(out, piece, rate, ACE_CHUNK_OVERLAP_S)
    return out, count


def op_cover(studio: Studio, job: Job) -> dict:
    p = job.params
    style, text = studio._style_lyrics(job)
    seed = studio._seed(p)
    src_pid = p.get("source_project") or job.project
    tid = p.get("source_take")
    if not tid:
        raise StudioError("Choose the reference take or imported song to cover.")
    engine = p.get("engine") or "ace_step"
    if engine == "yue2":
        job.message = "Reading the melody (SheetSage2)…"
        path, _, _ = studio._take_input(src_pid, tid, "score")
        score = studio.runtime.run_task("score", {"audio": path.resolve().as_posix()}, include_artifacts=True)
        abc = artifact_text(score) or score.get("text")
        if not abc:
            raise StudioError("SheetSage2 returned no melody score for this recording.")
        job.message = "Singing over the melody (YuE2)…"
        opts = {"style": style, "lyrics": text, "cot": "melody", "abc": abc, "seed": seed,
                "num_inference_steps": int(p.get("steps") or 8)}
        result = studio.runtime.run_task("yue2", {"text": text, "lyrics": text, "seed": seed, "options": opts})
        return _save_result(studio, job, result, p.get("label") or "Cover (YuE2 melody)", tid,
                            extra={"engine": "yue2", "seed": seed, "abc": abc})
    audio, rate = studio.store.load_take(src_pid, tid)
    opts = {"route": "cover-nofsq" if p.get("loose") else "cover",
            "audio_cover_strength": p.get("strength"), "cover_noise_strength": p.get("noise"),
            "num_inference_steps": p.get("steps")}
    out, parts = _ace_chunked(studio, job, audio, rate, style, text, seed, opts, "Restyling (ACE-Step cover)")
    return studio.store.add_take(job.project, out, rate, op="cover", parent=tid, params=p,
                                 label=p.get("label") or "Cover (ACE-Step)",
                                 extra={"engine": "ace_step", "seed": seed, "chunks": parts})


def op_repaint(studio: Studio, job: Job) -> dict:
    p = job.params
    tid = p["take"]
    start, end = float(p["start"]), float(p["end"])
    if end - start < 0.5:
        raise StudioError("Select at least half a second to regenerate.")
    project = studio.store.get(job.project)
    take = studio.store.find_take(project, tid)
    style, text = studio._style_lyrics(job)
    if p.get("lyrics") is None and take.get("alignment"):
        parsed = lyr.parse(project.get("lyrics") or "")
        lyr.apply_alignment(parsed, take["alignment"].get("words", []))
        text = lyr.lyrics_slice(parsed, start, end) or text
    seed = studio._seed(p)
    original, rate = studio.store.load_take(job.project, tid)
    duration = len(original) / rate
    end = min(end, duration)
    windows = []
    t = start
    while t < end - 1e-6:
        windows.append((t, min(end, t + ACE_REPAINT_REGION_S)))
        t = windows[-1][1]
    fade = float(p.get("fade") or 0.08)
    current = original
    for n, (ws, we) in enumerate(windows, 1):
        part = f" (part {n} of {len(windows)})" if len(windows) > 1 else ""
        job.message = f"Regenerating {ws:.1f}–{we:.1f} s{part}…"
        c0, c1 = max(0.0, ws - ACE_REPAINT_CONTEXT_S), min(duration, we + ACE_REPAINT_CONTEXT_S)
        a, b = int(c0 * rate), int(c1 * rate)
        clip = current[a:b]
        opts = {"route": "repaint", "repainting_start": round(ws - c0, 3), "repainting_end": round(we - c0, 3),
                "repaint_mode": p.get("mode") or "balanced", "repaint_strength": p.get("strength"),
                "num_inference_steps": p.get("steps")}
        new = _ace_on_audio(studio, clip, rate, f"{tid}-repaint{n}", style or project.get("style", ""), text, seed, opts)
        if len(new) < len(clip):
            new = np.concatenate([new, clip[len(new):]])
        # The outer edges of the selection crossfade with the original; a later window starts one
        # fade early so it crossfades from the previous window's new audio (no original leaks in).
        lead = 0.0 if n == 1 else min(fade, ws - c0)
        window = dsp.splice(clip, new[:len(clip)], rate, ws - c0 - lead, we - c0, fade=fade,
                            fade_in=True, fade_out=n == len(windows))
        current = np.concatenate([current[:a], window, current[b:]])
    return studio.store.add_take(job.project, current, rate, op="repaint", parent=tid, params=p,
                                 label=p.get("label") or f"Repaint {start:.1f}–{end:.1f} s · seed {seed}",
                                 extra={"engine": "ace_step", "seed": seed, "region": [start, end]})


def op_extend(studio: Studio, job: Job) -> dict:
    p = job.params
    tid = p["take"]
    seconds = max(5.0, min(float(p.get("seconds") or 30), 240.0))
    style, text = studio._style_lyrics(job)
    seed = studio._seed(p)
    original, rate = studio.store.load_take(job.project, tid)
    ctx_s = min(ACE_MAX_INPUT_S - 2, len(original) / rate)
    ctx = original[len(original) - int(ctx_s * rate):]
    job.message = f"Extending by {seconds:.0f} s…"
    opts = {"route": "complete", "duration_sec": round(ctx_s + seconds, 2), "num_inference_steps": p.get("steps"),
            "complete_track_classes": p.get("tracks")}
    new = _ace_on_audio(studio, ctx, rate, f"{tid}-tail", style, p.get("lyrics") or text, seed, opts)
    if len(new) > len(ctx) + rate:
        # The model returns the context plus the continuation: keep the original exactly and
        # crossfade into the new material over the last second of the context.
        tail = new[len(ctx) - int(1.0 * rate):]
        merged = _overlap_join(original, tail, rate, 1.0)
    else:
        merged = dsp.append(original, new, rate, fade=0.75)
    return studio.store.add_take(job.project, merged, rate, op="extend", parent=tid, params=p,
                                 label=p.get("label") or f"Extended +{seconds:.0f} s · seed {seed}",
                                 extra={"engine": "ace_step", "seed": seed})


def op_layer(studio: Studio, job: Job) -> dict:
    p = job.params
    tid, track = p["take"], (p.get("track") or "guitar")
    style, _ = studio._style_lyrics(job)
    seed = studio._seed(p)
    route = "extract" if p.get("extract") else "lego"
    audio, rate = studio.store.load_take(job.project, tid)
    opts = {"route": route, "track_name": track, "num_inference_steps": p.get("steps")}
    out, parts = _ace_chunked(studio, job, audio, rate, p.get("prompt") or style, "", seed, opts,
                              f"{'Extracting' if route == 'extract' else 'Adding'} {track}")
    return studio.store.add_take(job.project, out, rate, op="layer", parent=tid, params=p,
                                 label=p.get("label") or f"{route.title()} {track}",
                                 extra={"engine": "ace_step", "seed": seed, "chunks": parts})


def op_stems(studio: Studio, job: Job) -> dict:
    p = job.params
    tid = p["take"]
    job.message = "Separating stems (HTDemucs 6-stem)…"
    path, original, rate = studio._take_input(job.project, tid, "stems")
    result = studio.runtime.run_task("stems", {"audio": path.resolve().as_posix()})
    outputs = named_outputs(result)
    if not outputs:
        raise StudioError("The separator returned no stems.")
    stems = {}
    for name, data in outputs.items():
        audio, r = dsp.decode(data)
        key = next((s for s in STEM_NAMES if s in name.lower()), re.sub(r"[^a-z0-9]+", "-", name.lower())[:20])
        dsp.write(studio.store.take_path(job.project, tid, key), audio, r)
        stems[key] = {"rate": r, "duration": round(len(audio) / r, 3)}

    def save(project):
        studio.store.find_take(project, tid)["stems"] = stems
    studio.store.update(job.project, save)
    return studio.store.find_take(studio.store.get(job.project), tid)


def op_analyze(studio: Studio, job: Job) -> dict:
    from music import analysis

    tid = job.params["take"]
    project = studio.store.get(job.project)
    take = studio.store.find_take(project, tid)
    job.message = "Measuring tempo, key and metre…"
    audio, rate = studio.store.load_take(job.project, tid)
    stems = {}
    for name in take.get("stems") or {}:
        path = studio.store.take_path(job.project, tid, name)
        if path.is_file():
            s, r = dsp.read(path)
            stems[name] = dsp.match(s, r, rate, audio.shape[1])[:len(audio)]
    if job.params.get("tags", True):
        job.message = "Measuring, then listening for genre, mood and instruments…"
    try:
        result = analysis.analyze(audio, rate, stems or None, use_tags=job.params.get("tags", True))
    except ValueError as exc:
        raise StudioError(str(exc)) from exc
    result["analyzed"] = _now()

    def save(p):
        studio.store.find_take(p, tid)["analysis"] = result
    studio.store.update(job.project, save)
    return studio.store.find_take(studio.store.get(job.project), tid)


def op_align(studio: Studio, job: Job) -> dict:
    p = job.params
    tid = p["take"]
    project = studio.store.get(job.project)
    take = studio.store.find_take(project, tid)
    text = p.get("lyrics") if p.get("lyrics") is not None else project.get("lyrics") or ""
    audio, rate = studio.store.load_take(job.project, tid)
    vocals_file = studio.store.take_path(job.project, tid, "vocals")
    if vocals_file.is_file():
        vocals, vrate = dsp.read(vocals_file)
    elif studio.runtime.has("vocals"):
        job.message = "Isolating vocals…"
        path = studio._input_wav(audio, rate, "vocals", f"{tid}-mix")
        result = studio.runtime.run_task("vocals", {"audio": path.resolve().as_posix()})
        outs = named_outputs(result)
        key = next((k for k in outs if "vocal" in k.lower()), None)
        vocals, vrate = dsp.decode(outs[key]) if key else (audio, rate)
    else:
        vocals, vrate = audio, rate
    vpath = studio._input_wav(vocals, vrate, "align", f"{tid}-vocals16k")
    language = p.get("language") or "English"
    source = "align"
    if lyr.sung_text(lyr.parse(text)).strip() and studio.runtime.has("align"):
        job.message = "Aligning lyrics to the vocals…"
        raw = studio.runtime.align(vpath, lyr.sung_text(lyr.parse(text)), language)
    else:
        job.message = "Transcribing the vocals…"
        source = "asr"
        raw = studio.runtime.transcribe(vpath, None if language == "auto" else language)
        if not text.strip():
            text = _lyrics_from_transcript(raw)
        if not _words_seconds(raw) and studio.runtime.has("align") and lyr.sung_text(lyr.parse(text)).strip():
            # The recogniser gave text without word times: time the transcript with the aligner.
            job.message = "Timing the transcribed lyrics…"
            detected = raw.get("language") or "English"
            name = {"en": "English", "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "fr": "French", "de": "German",
                    "es": "Spanish", "it": "Italian", "pt": "Portuguese", "ru": "Russian"}.get(str(detected)[:2].lower(), detected)
            raw = studio.runtime.align(vpath, lyr.sung_text(lyr.parse(text)), name if language == "auto" else language)
            source = "asr+align"
    words = _words_seconds(raw)
    parsed = lyr.parse(text)
    matched = lyr.apply_alignment(parsed, words)
    alignment = {"source": source, "language": language, "matched": matched, "total": len(parsed.words),
                 "words": [{"word": w.text, "start": w.start, "end": w.end, "char_start": w.char_start,
                           "char_end": w.char_end, "line": w.line} for w in parsed.words if w.start is not None],
                 "lyrics": text,
                 "raw_words": words[:5000], "sections": lyr.section_ranges(parsed, take["duration"])}

    def save(project):
        if source.startswith("asr") and not (project.get("lyrics") or "").strip():
            project["lyrics"] = text
        studio.store.find_take(project, tid)["alignment"] = alignment
    studio.store.update(job.project, save)
    return studio.store.find_take(studio.store.get(job.project), tid)


def _words_seconds(raw: dict) -> list[dict]:
    rate = float(raw.get("sample_rate") or 0)
    out = []
    for w in raw.get("words") or raw.get("word_timestamps") or []:
        word = w.get("word") or w.get("text") or ""
        if "start" in w and "end" in w:
            s, e = float(w["start"]), float(w["end"])
        elif rate:
            s, e = w["start_sample"] / rate, w["end_sample"] / rate
        else:
            continue
        out.append({"word": word, "start": round(s, 3), "end": round(e, 3)})
    return out


def _lyrics_from_transcript(raw: dict) -> str:
    words = _words_seconds(raw)
    if not words:
        # Text only: one lyric line per sentence or clause.
        text = (raw.get("text") or "").strip()
        lines = [l.strip() for l in re.split(r"(?<=[.!?;])\s+|\n+", text) if l.strip()]
        return "[Verse]\n" + "\n".join(lines) if lines else ""
    lines, current, last_end = [], [], None
    for w in words:
        if last_end is not None and w["start"] - last_end > 0.9 and current:
            lines.append(" ".join(current))
            current = []
        current.append(w["word"].strip())
        last_end = w["end"]
    if current:
        lines.append(" ".join(current))
    return "[Verse]\n" + "\n".join(lines)


# Local edits -----------------------------------------------------------------

def op_splice(studio: Studio, job: Job) -> dict:
    p = job.params
    base, rate = studio.store.load_take(job.project, p["take"])
    other, orate = studio.store.load_take(job.project, p["from_take"])
    other = dsp.match(other, orate, rate, base.shape[1])
    start, end = float(p["start"]), float(p["end"])
    merged = dsp.splice(base, other, rate, start, end, fade=float(p.get("fade") or 0.08))
    return studio.store.add_take(job.project, merged, rate, op="splice", parent=p["take"], params=p,
                                 label=p.get("label") or f"Spliced {start:.1f}–{end:.1f} s")


def op_blend(studio: Studio, job: Job) -> dict:
    p = job.params
    first, rate = studio.store.load_take(job.project, p["take"])
    second, srate = studio.store.load_take(job.project, p["into_take"])
    second = dsp.match(second, srate, rate, first.shape[1])
    at = float(p["at"])
    merged = dsp.blend(first, second, rate, at=at, second_from=float(p.get("from", at)), fade=float(p.get("fade") or 2))
    return studio.store.add_take(job.project, merged, rate, op="blend", parent=p["take"], params=p,
                                 label=p.get("label") or f"Blend at {at:.1f} s")


def op_mixdown(studio: Studio, job: Job) -> dict:
    p = job.params
    tid = p["take"]
    take = studio.store.find_take(studio.store.get(job.project), tid)
    stems = {}
    for name in take.get("stems") or {}:
        stems[name], rate = dsp.read(studio.store.take_path(job.project, tid, name))
    if not stems:
        raise StudioError("Separate the stems of this take first.")
    out = dsp.mix(stems, {k: float(v) for k, v in (p.get("gains") or {}).items()}, set(p.get("muted") or []))
    return studio.store.add_take(job.project, out, rate, op="mixdown", parent=tid, params=p,
                                 label=p.get("label") or "Stem mix")


# Sound effects (Stable Audio 3) ---------------------------------------------

SFX_MAX_SECONDS = 120.0
SFX_MAX_VARIATIONS = 4


def op_sfx(studio: Studio, job: Job) -> dict:
    """Generate sound effects from text, or reshape existing audio (variation or inpaint).

    mode "generate": text only. mode "variation": the source take guides the result; strength 0..1
    (low keeps the original, high follows the prompt). mode "inpaint": only the selected range of the
    source is regenerated; the rest is kept exactly (spliced back with short crossfades).
    Each variation becomes its own take with its own seed.
    """
    p = job.params
    prompt = (p.get("prompt") or "").strip()
    if not prompt:
        raise StudioError("Describe the sound you want.")
    mode = p.get("mode") or "generate"
    count = max(1, min(int(p.get("variations") or 1), SFX_MAX_VARIATIONS))
    base_seed = studio._seed(p)
    source = audio = None
    rate = 44100
    if mode in ("variation", "inpaint"):
        if not p.get("take"):
            raise StudioError("Choose the take to reshape.")
        source = p["take"]
        audio, rate = studio.store.load_take(job.project, source)
        duration = len(audio) / rate
    else:
        duration = float(p.get("duration") or 6)
    if not 0.5 <= duration <= SFX_MAX_SECONDS:
        raise StudioError(f"Sound effects can be 0.5 to {SFX_MAX_SECONDS:.0f} seconds long.")
    opts: dict[str, Any] = {"duration_sec": round(duration, 3), "num_inference_steps": p.get("steps"),
                            "guidance_scale": p.get("guidance"), "negative_prompt": p.get("negative") or None}
    if mode == "variation":
        opts.update(audio_input_kind="init_audio", init_noise_level=float(p.get("strength", 0.6)))
    elif mode == "inpaint":
        start, end = float(p["start"]), float(p["end"])
        if end - start < 0.1:
            raise StudioError("Select the part of the sound to regenerate.")
        opts.update(audio_input_kind="inpaint_audio", inpaint_mask_start_sec=f"{start:.3f}",
                    inpaint_mask_end_sec=f"{end:.3f}")
    opts = {k: v for k, v in opts.items() if v not in (None, "")}
    path = studio._input_wav(audio, rate, "sfx", f"{source}-sfx") if audio is not None else None
    last = None
    try:
        for n in range(count):
            seed = (base_seed + n) % (2 ** 31)
            job.message = f"Sound {n + 1} of {count}…" if count > 1 else "Making the sound…"
            req: dict[str, Any] = {"text": prompt, "seed": seed, "options": {**opts, "prompt": prompt, "seed": seed}}
            if path is not None:
                req["audio"] = path.resolve().as_posix()
            result = studio.runtime.run_task("sfx", req)
            out, out_rate = dsp.decode(result_audio(result))
            if mode == "inpaint" and audio is not None:
                out = dsp.match(out, out_rate, rate, audio.shape[1])
                if len(out) < len(audio):
                    out = np.concatenate([out, audio[len(out):]])
                out = dsp.splice(audio, out[:len(audio)], rate, float(p["start"]), float(p["end"]), fade=0.03)
                out_rate = rate
            short = prompt if len(prompt) <= 40 else prompt[:39] + "…"
            label = p.get("label") or f"SFX · {short}" + (f" · {n + 1}" if count > 1 else "")
            last = studio.store.add_take(job.project, out, out_rate, op="sfx", parent=source,
                                         params={**p, "seed": seed}, label=label,
                                         extra={"engine": "stable_audio", "seed": seed, "sfx": True})
    finally:
        if path is not None:
            path.unlink(missing_ok=True)
    return last


def op_overlay(studio: Studio, job: Job) -> dict:
    """Mix one take (e.g. a sound effect) into another at a position, with gain and fades. Local."""
    p = job.params
    base, rate = studio.store.load_take(job.project, p["take"])
    layer, lrate = studio.store.load_take(job.project, p["layer_take"])
    layer = dsp.match(layer, lrate, rate, base.shape[1])
    at = max(0.0, float(p.get("at") or 0))
    gain = 10 ** (float(p.get("gain_db") or 0) / 20)
    fade = max(1, int(float(p.get("fade") or 0.01) * rate))
    if len(layer) > 2 * fade:
        ramp = np.linspace(0, 1, fade, dtype=np.float32)[:, None]
        layer = layer.copy()
        layer[:fade] *= ramp
        layer[-fade:] *= ramp[::-1]
    a = int(at * rate)
    need = a + len(layer)
    out = base if need <= len(base) else np.concatenate([base, np.zeros((need - len(base), base.shape[1]), np.float32)])
    out = out.copy()
    out[a:need] += layer * gain
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    if peak > 0.98:
        out *= 0.98 / peak
    return studio.store.add_take(job.project, out, rate, op="overlay", parent=p["take"], params=p,
                                 label=p.get("label") or f"Mixed in at {at:.1f} s")


OPERATIONS: dict[str, tuple[Callable[[Studio, Job], Any], bool]] = {
    # name: (function, runs locally without the GPU)
    "generate": (op_generate, False), "cover": (op_cover, False), "repaint": (op_repaint, False),
    "extend": (op_extend, False), "layer": (op_layer, False), "stems": (op_stems, False),
    "align": (op_align, False), "splice": (op_splice, True), "blend": (op_blend, True),
    "mixdown": (op_mixdown, True), "sfx": (op_sfx, False), "overlay": (op_overlay, True),
    "analyze": (op_analyze, True),
}


def import_audio(studio: Studio, pid: str, source: Path, label: str) -> dict:
    """Decode any audio/video file with FFmpeg to 44.1 kHz stereo and add it as a take."""
    out = studio.tmp / f"import-{uuid.uuid4().hex[:8]}.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(source), "-vn", "-ac", "2", "-ar", "44100",
                        "-t", str(int(MAX_SECONDS)), str(out)], check=True, timeout=300,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise StudioError("This file could not be decoded as audio.") from exc
    audio, rate = dsp.read(out)
    out.unlink(missing_ok=True)
    return studio.store.add_take(pid, audio, rate, op="import", label=label or "Imported audio")
