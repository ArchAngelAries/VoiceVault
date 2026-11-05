"""
api/music.py — Music Studio endpoints.

GET    /api/music/status                         runtime, installed packages, queue
POST   /api/music/refresh                        rescan the model folders for music packages
GET    /api/music/projects                       songs, newest first
POST   /api/music/projects                       create {title, style, lyrics}
GET    /api/music/projects/{pid}                 one song with all takes
PATCH  /api/music/projects/{pid}                 edit title, style, lyrics, active take
DELETE /api/music/projects/{pid}                 delete the song and its audio
POST   /api/music/projects/{pid}/import          upload a song or reference (any audio/video file)
POST   /api/music/projects/{pid}/jobs            run an operation {kind, params}
GET    /api/music/jobs?project=                  recent jobs; GET /api/music/jobs/{id}; POST .../cancel
GET    /api/music/projects/{pid}/takes/{tid}/audio?stem=       WAV of a take or one of its stems
GET    /api/music/projects/{pid}/takes/{tid}/peaks?buckets=    waveform min/max pairs
POST   /api/music/projects/{pid}/takes/{tid}/export            WAV / FLAC / MP3 with optional loudness target
PATCH  /api/music/projects/{pid}/takes/{tid}                   rename a take
DELETE /api/music/projects/{pid}/takes/{tid}                   delete a take and its stems
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from core import settings as app_settings
from core.model_paths import model_roots
from core.paths import DATA_DIR
from music import dsp
from music.runtime import MusicRuntime
from music.studio import OPERATIONS, ProjectStore, Studio, StudioError, import_audio

router = APIRouter()
MAX_UPLOAD = 500 * 1024 * 1024
_studio: dict[str, Studio] = {}
_studio_lock = threading.Lock()


def get_studio() -> Studio:
    if "s" in _studio:
        return _studio["s"]
    with _studio_lock:
        return _build_studio()


def _build_studio() -> Studio:
    if "s" not in _studio:
        from core import gpu_scheduler

        runtime = MusicRuntime(lambda: model_roots(app_settings.load()), app_settings.load)

        def release_speech():
            """Free the GPU for a music job: resident TTS model and Whisper (not this server)."""
            from asr.service import unload_transcriber

            release = gpu_scheduler._tts_release() if gpu_scheduler._tts_release is not None else None
            if release is not None:
                release()
            unload_transcriber()

        from core.library import music_projects_dir
        studio = Studio(ProjectStore(music_projects_dir()), runtime, DATA_DIR / "tmp" / "music",
                        gpu_lock=gpu_scheduler.speech_resource_lock, release_others=release_speech)
        gpu_scheduler.register_gpu_user("music", runtime.stop)
        _studio["s"] = studio
    return _studio["s"]


async def shutdown_music() -> None:
    if "s" in _studio:
        await asyncio.to_thread(_studio["s"].shutdown)


def _call(fn, *args):
    try:
        return fn(*args)
    except StudioError as exc:
        raise HTTPException(404 if "not found" in str(exc).lower() else 400, str(exc))


class ProjectIn(BaseModel):
    title: str = Field(default="Untitled song", max_length=120)
    style: str = Field(default="", max_length=2000)
    lyrics: str = Field(default="", max_length=20000)


class ProjectPatch(BaseModel):
    title: Optional[str] = Field(default=None, max_length=120)
    style: Optional[str] = Field(default=None, max_length=2000)
    lyrics: Optional[str] = Field(default=None, max_length=20000)
    active_take: Optional[str] = Field(default=None, max_length=20)
    notes: Optional[str] = Field(default=None, max_length=5000)


class JobIn(BaseModel):
    kind: str = Field(max_length=20)
    params: dict[str, Any] = Field(default_factory=dict)


class TakePatch(BaseModel):
    label: str = Field(max_length=120)


class ExportIn(BaseModel):
    format: Literal["wav", "flac", "mp3"] = "wav"
    loudness: Optional[float] = Field(default=None, ge=-30, le=-6)   # LUFS target; None keeps the level
    stem: Optional[str] = Field(default=None, max_length=20)


@router.get("/status")
def status():
    s = get_studio()
    info = s.runtime.status()
    info["operations"] = sorted(OPERATIONS)
    info["queued"] = sum(1 for j in s.jobs() if j["status"] == "queued")
    info["running"] = next((j for j in s.jobs() if j["status"] == "running"), None)
    return info


@router.get("/style-vocabulary")
def style_vocabulary():
    """Words for the style builder; the same list the song analysis tags with."""
    from music import analysis

    return {"groups": analysis.STYLE_VOCAB, "tagger": analysis.CLAP_REPO, "tagger_ready": analysis.CLAP.available()}


@router.post("/refresh")
def refresh():
    s = get_studio()
    s.runtime.stop()
    s.runtime.refresh()
    return s.runtime.status()


@router.get("/projects")
def list_projects():
    return get_studio().store.list()


@router.post("/projects")
def create_project(body: ProjectIn):
    return get_studio().store.create(body.title, body.style, body.lyrics)


@router.get("/projects/{pid}")
def get_project(pid: str):
    return _call(get_studio().store.get, pid)


@router.patch("/projects/{pid}")
def patch_project(pid: str, body: ProjectPatch):
    changes = body.model_dump(exclude_none=True)

    def apply(p):
        if "active_take" in changes:
            get_studio().store.find_take(p, changes["active_take"])
        p.update(changes)
    return _call(get_studio().store.update, pid, apply)


@router.delete("/projects/{pid}")
def delete_project(pid: str):
    s = get_studio()
    if any(j["project"] == pid and j["status"] in ("queued", "running") for j in s.jobs()):
        raise HTTPException(409, "This song has work in progress. Wait for it or cancel it first.")
    _call(s.store.delete, pid)
    return {"deleted": pid}


@router.post("/projects/{pid}/import")
async def import_file(pid: str, file: UploadFile = File(...), label: str = Form(default="")):
    s = get_studio()
    _call(s.store.get, pid)
    tmp = s.tmp / f"upload-{uuid.uuid4().hex[:8]}{Path(file.filename or 'audio').suffix[:8]}"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    with open(tmp, "wb") as out:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD:
                out.close()
                tmp.unlink(missing_ok=True)
                raise HTTPException(413, "The file is larger than 500 MB.")
            out.write(chunk)
    try:
        return await asyncio.to_thread(_call, import_audio, s, pid, tmp, label or (file.filename or "Imported"))
    finally:
        tmp.unlink(missing_ok=True)


@router.post("/projects/{pid}/jobs")
def submit_job(pid: str, body: JobIn):
    job = _call(get_studio().submit, pid, body.kind, body.params)
    return job.to_dict() | {"position": get_studio().queue_position(job.id)}


@router.get("/jobs")
def list_jobs(project: Optional[str] = None):
    s = get_studio()
    return [j | {"position": s.queue_position(j["id"])} for j in s.jobs(project)]


@router.get("/jobs/{jid}")
def get_job(jid: str):
    s = get_studio()
    job = _call(s.job, jid)
    return job | {"position": s.queue_position(jid)}


@router.post("/jobs/{jid}/cancel")
def cancel_job(jid: str):
    return _call(get_studio().cancel, jid)


def _audio_path(pid: str, tid: str, stem: Optional[str]) -> Path:
    s = get_studio()
    project = _call(s.store.get, pid)
    take = _call(s.store.find_take, project, tid)
    if stem and stem not in (take.get("stems") or {}):
        raise HTTPException(404, "That stem has not been separated for this take.")
    path = s.store.take_path(pid, tid, stem)
    if not path.is_file():
        raise HTTPException(404, "Audio file missing.")
    return path


@router.get("/projects/{pid}/takes/{tid}/audio")
def take_audio(pid: str, tid: str, stem: Optional[str] = None):
    return FileResponse(_audio_path(pid, tid, stem), media_type="audio/wav")


def _peaks_cache_files(wav: Path) -> list[Path]:
    """Cached waveform peaks for one take/stem WAV: `<tid>[.<stem>].peaks.<buckets>.json`."""
    return list(wav.parent.glob(f"{wav.stem}.peaks.*.json")) if wav.parent.is_dir() else []


@router.get("/projects/{pid}/takes/{tid}/peaks")
def take_peaks(pid: str, tid: str, buckets: int = 1600, stem: Optional[str] = None):
    path = _audio_path(pid, tid, stem)
    buckets = max(100, min(buckets, 8000))
    cache = path.with_name(f"{path.stem}.peaks.{buckets}.json")
    st = path.stat()
    stamp = [st.st_mtime_ns, st.st_size]
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if cached.get("stamp") == stamp:   # invalidated whenever the WAV is rewritten
            return cached["data"]
    except (OSError, ValueError, KeyError, AttributeError):
        pass
    audio, rate = dsp.read(path)
    data = {"rate": rate, "duration": round(len(audio) / rate, 3), "peaks": dsp.peaks(audio, buckets)}
    try:
        tmp = cache.with_name(f"{cache.name}.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(json.dumps({"stamp": stamp, "data": data}), encoding="utf-8")
        os.replace(tmp, cache)
    except OSError:
        pass   # the cache is only a speed-up
    return data


@router.post("/projects/{pid}/takes/{tid}/export")
def export_take(pid: str, tid: str, body: ExportIn):
    s = get_studio()
    src = _audio_path(pid, tid, body.stem)
    project = s.store.get(pid)
    take = s.store.find_take(project, tid)
    audio, rate = dsp.read(src)
    measured = None
    if body.loudness is not None:
        audio, measured = dsp.loudness_normalize(audio, rate, body.loudness)
    stem = f"-{body.stem}" if body.stem else ""
    safe = "".join(c if c.isalnum() or c in "-_ " else "-" for c in f"{project['title']} - {take['label']}")[:80]
    safe = f"{safe} [{tid}]"  # same title and label in two songs must not overwrite each other
    from core.library import exports_dir
    out_dir = exports_dir() / "Music"
    wav = out_dir / f"{safe}{stem}.wav"
    if body.format == "flac":
        out = wav.with_suffix(".flac")
        dsp.write(out, audio, rate, fmt="FLAC")
    else:
        dsp.write(wav, audio, rate)
        out = wav
        if body.format == "mp3":
            out = wav.with_suffix(".mp3")
            try:
                dsp.export_mp3(wav, out)
            except Exception:
                raise HTTPException(500, "MP3 encoding failed (FFmpeg).")
            wav.unlink(missing_ok=True)
    media = {"wav": "audio/wav", "flac": "audio/flac", "mp3": "audio/mpeg"}[body.format]
    headers = {"X-Measured-Loudness": str(measured)} if measured is not None else None
    return FileResponse(out, media_type=media, filename=out.name, headers=headers)


@router.patch("/projects/{pid}/takes/{tid}")
def rename_take(pid: str, tid: str, body: TakePatch):
    s = get_studio()

    def apply(p):
        s.store.find_take(p, tid)["label"] = body.label.strip() or "Take"
    return _call(s.store.update, pid, apply)


@router.delete("/projects/{pid}/takes/{tid}")
def delete_take(pid: str, tid: str):
    s = get_studio()
    if any(j["project"] == pid and j["status"] in ("queued", "running") for j in s.jobs()):
        raise HTTPException(409, "This song has work in progress. Wait for it or cancel it first.")

    def apply(p):
        take = s.store.find_take(p, tid)
        p["takes"] = [t for t in p["takes"] if t["id"] != tid]
        for name in [None, *(take.get("stems") or {})]:
            wav = s.store.take_path(pid, tid, name)
            wav.unlink(missing_ok=True)
            for cached in _peaks_cache_files(wav):
                cached.unlink(missing_ok=True)
        if p.get("active_take") == tid:
            p["active_take"] = p["takes"][-1]["id"] if p["takes"] else None
    return _call(s.store.update, pid, apply)
