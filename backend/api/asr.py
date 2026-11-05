"""
api/asr.py — Speech-to-text endpoints (Whisper large-v3-turbo, see asr/whisper_engine.py).

GET  /api/asr/status        model, cache and runtime state
POST /api/asr/transcribe    upload an audio or video file; returns text and timestamped segments
POST /api/asr/unload        release the model's GPU memory
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from asr.audio import AudioDecodeError, decode_audio
from asr.service import get_transcriber
from core.gpu_scheduler import release_tts_for_asr, speech_resource_lock
from core.paths import DATA_DIR

log = logging.getLogger(__name__)
router = APIRouter()

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
TMP_DIR = DATA_DIR / "tmp"
# Keep this module binding for route tests that substitute a fake transcriber. In production it is
# the same singleton used by TTS reference transcription.
_transcriber = get_transcriber()


@router.get("/status")
async def asr_status():
    problem = await asyncio.to_thread(_transcriber.runtime_problem)
    cached = await asyncio.to_thread(_transcriber.is_cached)
    return {
        "engine": "whisper",
        "model": _transcriber.model_id,
        "cached": cached,
        "loaded": _transcriber.loaded,
        "device": _transcriber.device,
        "dtype": _transcriber.dtype,
        "runtime_problem": problem,
    }


@router.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    language: Optional[str] = Form(None),
    timestamps: bool = Form(True),
):
    problem = await asyncio.to_thread(_transcriber.runtime_problem)
    if problem:
        raise HTTPException(503, problem)

    suffix = Path(file.filename or "").suffix.lower()[:10] or ".bin"
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = TMP_DIR / f"{uuid.uuid4().hex}{suffix}"
    try:
        written = 0
        with tmp_path.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "The file is larger than 200 MB. Split it first.")
                out.write(chunk)
        if written == 0:
            raise HTTPException(422, "The uploaded file is empty.")

        samples = await asyncio.to_thread(decode_audio, tmp_path)
        def _transcribe():
            with speech_resource_lock:
                release_tts_for_asr()
                if not _transcriber.loaded:
                    _transcriber.load()
                return _transcriber.transcribe(samples, language or None, timestamps)

        result = await asyncio.to_thread(_transcribe)
    except AudioDecodeError as exc:
        raise HTTPException(422, str(exc))
    except HTTPException:
        raise
    except Exception:
        log.exception("Transcription failed for %s", file.filename)
        raise HTTPException(500, "Transcription failed. See the backend log for details.")
    finally:
        tmp_path.unlink(missing_ok=True)  # only the temp copy this request created
    return result


@router.post("/unload")
async def unload_asr():
    def _unload():
        with speech_resource_lock:
            _transcriber.unload()

    await asyncio.to_thread(_unload)
    return {"ok": True, "loaded": False}
