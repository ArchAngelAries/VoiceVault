"""Harvest API: import audio, build voice clips, transcribe and promote references."""
from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from core.paths import DATA_DIR
from harvest.service import MAX_UPLOAD_BYTES, HarvestError, HarvestService

log = logging.getLogger(__name__)
router = APIRouter()
service: HarvestService | None = None
_service_lock = threading.Lock()
_service_stopping = False


def startup_harvest_jobs() -> None:
    """Allow a subsequent application lifespan after the previous worker exited."""
    global service, _service_stopping
    with _service_lock:
        if _service_stopping:
            if service is not None and service._worker.is_alive():
                raise RuntimeError("The previous Harvest worker is still shutting down.")
            service = None
            _service_stopping = False


async def shutdown_harvest_jobs() -> None:
    # Retain a stopped singleton so a late request cannot start another worker.
    global _service_stopping
    with _service_lock:
        _service_stopping = True
        current = service
    if current is not None:
        await asyncio.to_thread(current.shutdown)


def _service() -> HarvestService:
    global service
    with _service_lock:
        if _service_stopping:
            raise HTTPException(503, "Harvest is shutting down.")
        if service is None:
            try:
                service = HarvestService()
            except RuntimeError as exc:
                raise HTTPException(503, str(exc)) from exc
        return service


class DownloadRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)   # any public video/audio page yt-dlp supports
    start_s: float = Field(default=0, ge=0, le=1200)
    end_s: float = Field(gt=0, le=1200)
    keep_video: bool = False                          # keep the video file (delete it later from the source)


class ClipRequest(BaseModel):
    start_s: float = Field(ge=0, le=1200)
    end_s: float = Field(gt=0, le=1200)
    cleanup: str | list[str] = "none"   # legacy "none"/"denoise", or steps from /capabilities
    voice_prompt: Optional[str] = Field(default=None, max_length=200)   # for isolate_voice
    segments: Optional[list[tuple[float, float]]] = Field(default=None, max_length=200)  # keep only these lines
    name: Optional[str] = Field(default=None, max_length=120)


class ReferenceRequest(BaseModel):
    transcript: Optional[str] = Field(default=None, max_length=2000)


def _call(fn, *args):
    try:
        return fn(*args)
    except HarvestError as exc:
        message = str(exc)
        status = 404 if "unavailable" in message.lower() else (429 if "queue is full" in message.lower() else 400)
        raise HTTPException(status, message) from exc


@router.get("/capabilities")
async def capabilities():
    return await asyncio.to_thread(_service().capabilities)


@router.get("/sources")
async def sources():
    return {"items": await asyncio.to_thread(_service().sources)}


@router.post("/import")
async def import_audio(file: UploadFile = File(...)):
    # Stage only the bytes owned by this request. The worker removes this exact file on completion.
    temp = DATA_DIR / "harvest" / f"upload_{uuid.uuid4().hex}.bin"
    temp.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with temp.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "The file is larger than 200 MB.")
                out.write(chunk)
        if written == 0:
            raise HTTPException(422, "The uploaded file is empty.")
        try:
            job = await asyncio.to_thread(_service().add_source_job, operation="import",
                                           payload={"path": str(temp), "name": file.filename or "Imported audio"})
        except HarvestError as exc:
            raise HTTPException(429 if "queue is full" in str(exc) else 400, str(exc)) from exc
        return job
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@router.post("/download")
async def download_audio(request: DownloadRequest):
    try:
        url = _service().validate_video_url(request.url)
        return await asyncio.to_thread(_service().add_source_job, operation="download",
                                       payload={"url": url, "start_s": request.start_s, "end_s": request.end_s,
                                                "keep_video": request.keep_video})
    except HarvestError as exc:
        raise HTTPException(429 if "queue is full" in str(exc) else 400, str(exc)) from exc


@router.post("/sources/{source_id}/clips")
async def create_clip(source_id: str, request: ClipRequest):
    try:
        return await asyncio.to_thread(_service().add_source_job, operation="clip",
                                       payload={"source_id": source_id, **request.model_dump()})
    except HarvestError as exc:
        raise HTTPException(429 if "queue is full" in str(exc) else 400, str(exc)) from exc


@router.post("/sources/{source_id}/speakers")
async def find_speakers(source_id: str):
    """Queue speaker analysis: transcribed lines with suggested speaker labels."""
    try:
        return await asyncio.to_thread(_service().add_source_job, operation="speakers", payload={"source_id": source_id})
    except HarvestError as exc:
        raise HTTPException(429 if "queue is full" in str(exc) else 400, str(exc)) from exc


@router.post("/sources/{source_id}/variants/{variant_id}/transcribe")
async def transcribe_variant(source_id: str, variant_id: str):
    caps = await asyncio.to_thread(_service().capabilities)
    if not caps["asr"]["available"]:
        raise HTTPException(503, caps["asr"].get("reason") or "Speech recognition is unavailable.")
    source = _call(_service().get_source, source_id)
    if not any(v["id"] == variant_id for v in source["variants"]):
        raise HTTPException(404, "This voice clip is unavailable.")
    try:
        return await asyncio.to_thread(_service().add_source_job, operation="transcribe",
                                       payload={"source_id": source_id, "variant_id": variant_id})
    except HarvestError as exc:
        raise HTTPException(429 if "queue is full" in str(exc) else 400, str(exc)) from exc


@router.post("/sources/{source_id}/variants/{variant_id}/reference")
async def promote_reference(source_id: str, variant_id: str, request: Optional[ReferenceRequest] = None):
    source = _call(_service().get_source, source_id)
    if not any(v["id"] == variant_id for v in source["variants"]):
        raise HTTPException(404, "This voice clip is unavailable.")
    try:
        return await asyncio.to_thread(_service().add_source_job, operation="reference",
                                       payload={"source_id": source_id, "variant_id": variant_id,
                                                "transcript": request.transcript if request else None})
    except HarvestError as exc:
        raise HTTPException(429 if "queue is full" in str(exc) else 400, str(exc)) from exc


@router.delete("/sources/{source_id}/video")
async def drop_source_video(source_id: str):
    """Delete a kept video, keeping its audio losslessly (FLAC) as the source original."""
    try:
        return await asyncio.to_thread(_service().drop_video, source_id)
    except HarvestError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/jobs")
async def jobs():
    return {"items": await asyncio.to_thread(_service().jobs)}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    return _call(_service().get_job, job_id)


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    return _call(_service().cancel, job_id)


@router.get("/audio/{source_id}/{filename}")
async def audio(source_id: str, filename: str):
    path = _call(_service().source_file, source_id, filename)
    return FileResponse(path, media_type="audio/wav" if path.suffix == ".wav" else "application/octet-stream",
                        filename=path.name)

