"""
api/voice.py — Voice Lab: shape, transform and convert voices.

GET  /api/voice/presets                 presets (realistic / cartoon / voice changer), effect types, disclaimer
POST /api/voice/sources                 upload any audio/video to shape (kept in DATA_DIR/voice/sources)
GET  /api/voice/sources                 uploaded sources
POST /api/voice/analyze                 {source} → median pitch, gender guess, duration
POST /api/voice/shape                   {source, simple, advanced, effects} → preview WAV
POST /api/voice/convert                 {source, target_reference_id, ...shape} → voice-to-voice (Seed-VC) preview
GET  /api/voice/previews/{id}           play a preview
POST /api/voice/save                    {preview_id, as: generation|reference, name, transcript} → keep it

A source is {"kind": "generation", "name": "<file>.wav"} | {"kind": "reference", "id": "<ref id>"} |
{"kind": "upload", "id": "<upload id>"}. Shaping runs on the CPU (WORLD vocoder); conversion uses the GPU
through the shared audio.cpp server.
"""
from __future__ import annotations

import asyncio
import logging
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Literal, Optional

import numpy as np
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from core import settings as app_settings
from core.paths import DATA_DIR, DEFAULT_OUTPUTS_DIR
from music import dsp
from voice import effects as fx
from voice import shaper

router = APIRouter()
from core.library import generations_dir, voice_lab_uploads_dir, voices_dir
ROOT = DATA_DIR / "tmp" / "voice"             # working files only
SOURCES = voice_lab_uploads_dir()             # Library/Voice Lab/Uploads
PREVIEWS = ROOT / "previews"                  # unsaved results (the newest 60 are kept)
ID = re.compile(r"^[0-9a-f]{32}$")
MAX_SOURCE_S = 600.0
MAX_PREVIEWS = 60
PREVIEW_KEEP_S = 15 * 60   # previews this recent are kept even past MAX_PREVIEWS
_REAL_DATA = DATA_DIR
DISCLAIMER = ("You are responsible for what you create with VoiceVault. Only shape or convert voices you have the "
              "right to use, never to deceive, impersonate real people without consent, or make illegal content. "
              "VoiceVault does not condone illegal use. Child and elderly voices are provided for legitimate character "
              "and voice-over work.")


def _voices() -> Path:
    """Library/Voices, or DATA_DIR/references when a test points DATA_DIR elsewhere."""
    return DATA_DIR / "references" if DATA_DIR != _REAL_DATA else voices_dir()


def _outputs_dir() -> Path:
    configured = app_settings.load().get("outputs_dir") or ""
    return Path(configured) if configured else generations_dir()


class Source(BaseModel):
    kind: Literal["generation", "reference", "upload", "preview"]
    id: Optional[str] = Field(default=None, max_length=64)
    name: Optional[str] = Field(default=None, max_length=200)


class ShapeIn(BaseModel):
    source: Source
    simple: dict[str, Any] = Field(default_factory=dict)
    advanced: dict[str, Any] = Field(default_factory=dict)
    effects: list[dict[str, Any]] = Field(default_factory=list, max_length=24)
    seed: int = 0


class ConvertIn(ShapeIn):
    target_reference_id: str = Field(max_length=64)
    steps: int = Field(default=16, ge=1, le=200)
    similarity: float = Field(default=0.7)          # how strongly to match the target voice
    intelligibility: float = Field(default=0.7)     # how strongly to keep the words clear
    enhance: bool = True                            # restore to 48 kHz after conversion (Seed-VC outputs 22 kHz)


class SaveIn(BaseModel):
    preview_id: str = Field(max_length=64)
    as_: Literal["generation", "reference"] = Field(alias="as")
    name: str = Field(default="Shaped voice", max_length=120)
    transcript: Optional[str] = Field(default=None, max_length=2000)


def _resolve(src: Source) -> tuple[Path, str]:
    """Path of the source audio and a cache key that changes when the file changes."""
    if src.kind == "generation":
        from engines.audio_io import is_valid_audio_name
        if not src.name or not is_valid_audio_name(src.name):
            raise HTTPException(400, "Invalid generation name.")
        path = _outputs_dir() / src.name
    elif src.kind == "reference":
        from engines.audio_store import REFERENCE_ID, hq_reference_path
        if not src.id or not REFERENCE_ID.match(src.id):
            raise HTTPException(400, "Invalid reference id.")
        base = _voices() / f"{src.id}.wav"
        hq = hq_reference_path(base)
        path = hq if hq.is_file() else base          # shape the full-band copy when there is one
    elif src.kind == "upload":
        if not src.id or not ID.match(src.id):
            raise HTTPException(400, "Invalid upload id.")
        path = SOURCES / f"{src.id}.wav"
    else:
        if not src.id or not ID.match(src.id):
            raise HTTPException(400, "Invalid preview id.")
        path = PREVIEWS / f"{src.id}.wav"
    if not path.is_file():
        raise HTTPException(404, "That audio is no longer available.")
    return path, f"{src.kind}:{src.id or src.name}:{path.stat().st_mtime_ns}"


def _load(path: Path) -> tuple[np.ndarray, int]:
    audio, rate = dsp.read(path)
    mono = audio.mean(axis=1)
    if rate > 48000:
        mono = dsp.resample(mono[:, None], rate, 48000)[:, 0]
        rate = 48000
    return mono.astype(np.float32), rate


def _store_preview(audio: np.ndarray, rate: int, meta: dict) -> dict:
    PREVIEWS.mkdir(parents=True, exist_ok=True)
    pid = uuid.uuid4().hex
    dsp.write(PREVIEWS / f"{pid}.wav", audio[:, None], rate)
    (PREVIEWS / f"{pid}.json").write_text(json.dumps(meta, default=float), encoding="utf-8")
    olds = sorted(PREVIEWS.glob("*.wav"), key=lambda p: p.stat().st_mtime)
    cutoff = time.time() - PREVIEW_KEEP_S
    for old in olds[:-MAX_PREVIEWS]:
        if old.stat().st_mtime > cutoff:
            continue
        old.unlink(missing_ok=True)
        old.with_suffix(".json").unlink(missing_ok=True)
    return {"preview_id": pid, "url": f"/api/voice/previews/{pid}", "duration_s": round(len(audio) / rate, 2),
            "rate": rate, **meta}


def _shape_audio(audio, rate, key, body: ShapeIn) -> tuple[np.ndarray, dict]:
    try:
        out, meta = shaper.shape(audio, rate, body.simple, body.advanced, key=key, seed=body.seed)
        out = fx.apply_chain(out, rate, body.effects)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return out, meta


@router.get("/presets")
def presets():
    return {"presets": fx.PRESETS, "effects": sorted(fx.EFFECTS), "disclaimer": DISCLAIMER,
            "simple": {"age": {"min": -1.5, "max": 2.0, "note": "-1 young child, 0 as recorded, 1 elderly; any value works"},
                       "pitch": {"min": -12, "max": 12, "unit": "semitones"},
                       "gender": ["as_recorded", "male", "female"], "speed": {"min": 0.5, "max": 2.0}}}


@router.post("/sources")
async def upload_source(file: UploadFile = File(...)):
    from asr.audio import AudioDecodeError, decode_audio

    SOURCES.mkdir(parents=True, exist_ok=True)
    sid = uuid.uuid4().hex
    tmp = SOURCES / f"{sid}.upload"
    size = 0
    with open(tmp, "wb") as out:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > 300 * 1024 * 1024:
                out.close()
                tmp.unlink(missing_ok=True)
                raise HTTPException(413, "The file is larger than 300 MB.")
            out.write(chunk)
    try:
        samples = await asyncio.to_thread(decode_audio, tmp, 48000, max_seconds=MAX_SOURCE_S)
    except AudioDecodeError as exc:
        raise HTTPException(422, str(exc))
    finally:
        tmp.unlink(missing_ok=True)
    dsp.write(SOURCES / f"{sid}.wav", np.asarray(samples, np.float32)[:, None], 48000)
    (SOURCES / f"{sid}.json").write_text(json.dumps({"name": (file.filename or "audio")[:120], "created": time.time()}),
                                         encoding="utf-8")
    return {"id": sid, "name": file.filename, "duration_s": round(len(samples) / 48000, 2)}


@router.get("/sources")
def list_sources():
    out = []
    for meta in sorted(SOURCES.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if SOURCES.is_dir() else []:
        if (SOURCES / f"{meta.stem}.wav").is_file():
            info = json.loads(meta.read_text(encoding="utf-8"))
            out.append({"id": meta.stem, "name": info.get("name"), "created": info.get("created")})
    return out[:100]


@router.get("/sources/{sid}/audio")
def source_audio(sid: str):
    if not ID.match(sid) or not (SOURCES / f"{sid}.wav").is_file():
        raise HTTPException(404, "Upload not found.")
    return FileResponse(SOURCES / f"{sid}.wav", media_type="audio/wav")


@router.post("/analyze")
async def analyze(source: Source):
    path, key = _resolve(source)
    audio, rate = await asyncio.to_thread(_load, path)
    analysis = await asyncio.to_thread(shaper.analyse, audio, rate, key)
    return analysis.summary()


@router.post("/shape")
async def shape_voice(body: ShapeIn):
    path, key = _resolve(body.source)
    audio, rate = await asyncio.to_thread(_load, path)
    t = time.time()
    out, meta = await asyncio.to_thread(_shape_audio, audio, rate, key, body)
    meta = {**meta, "seconds": round(time.time() - t, 2), "source": body.source.model_dump(),
            "simple": body.simple, "advanced": body.advanced, "effects": body.effects}
    return _store_preview(out, rate, meta)


@router.post("/convert")
async def convert_voice(body: ConvertIn):
    """Voice to voice: keep the source performance (words, timing, emotion), take the target's voice."""
    from api.music import get_studio
    from engines.audio_store import REFERENCE_ID, hq_reference_path

    if not REFERENCE_ID.match(body.target_reference_id):
        raise HTTPException(400, "Invalid target voice.")
    target = _voices() / f"{body.target_reference_id}.wav"
    if hq_reference_path(target).is_file():
        target = hq_reference_path(target)
    if not target.is_file():
        raise HTTPException(404, "The target voice clip was not found.")
    path, _ = _resolve(body.source)
    studio = get_studio()
    if not studio.runtime.has("vc"):
        raise HTTPException(409, "The voice-to-voice model (Seed-VC) is not installed.")
    audio, rate = await asyncio.to_thread(_load, path)
    tmp = ROOT / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    src_wav = tmp / f"vc-src-{uuid.uuid4().hex[:8]}.wav"
    dsp.write(src_wav, dsp.resample(audio[:, None], rate, 44100), 44100)
    request = {"audio": src_wav.resolve().as_posix(), "voice_ref": target.resolve().as_posix(), "seed": body.seed,
               "options": {"route": "v2_vc", "num_inference_steps": body.steps,
                           "similarity_guidance_scale": body.similarity,
                           "intelligibility_guidance_scale": body.intelligibility, "seed": body.seed}}
    t = time.time()
    try:
        from music.runtime import MusicRuntimeError, result_audio
        try:
            result = await asyncio.to_thread(studio.run_tool, "vc", request)
        except MusicRuntimeError as exc:
            raise HTTPException(422, str(exc))
        converted, crate = dsp.decode(result_audio(result))
    finally:
        src_wav.unlink(missing_ok=True)
    mono = converted.mean(axis=1).astype(np.float32)
    enhanced = False
    enhance_error = None
    if body.enhance and crate < 44100 and studio.runtime.has("restore"):
        # Seed-VC returns 22.05 kHz audio (nothing above ~11 kHz). Sidon speech restoration rebuilds it at
        # 48 kHz (5 s for 41 s of speech). UniverSR would also work but does not run on the Vulkan backend.
        up = tmp / f"vc-up-{uuid.uuid4().hex[:8]}.wav"
        dsp.write(up, dsp.resample(mono[:, None], crate, 48000), 48000)
        from music.runtime import MusicRuntimeError, result_audio
        try:
            for attempt in (1, 2):
                try:
                    res = await asyncio.to_thread(studio.run_tool, "restore", {"audio": up.resolve().as_posix()})
                    hq, crate = dsp.decode(result_audio(res))
                    mono = hq.mean(axis=1).astype(np.float32)
                    enhanced = True
                    break
                except MusicRuntimeError as exc:
                    # After a long conversion the GPU can still hold Seed-VC's buffers and Sidon fails to load.
                    # Restarting the audio.cpp server frees them; then try once more.
                    logging.getLogger(__name__).warning("Voice-to-voice restore failed (attempt %d): %s", attempt, exc)
                    enhance_error = str(exc)
                    if attempt == 1:
                        await asyncio.to_thread(studio.runtime.stop)
        finally:
            up.unlink(missing_ok=True)
    convert_s = round(time.time() - t, 2)
    meta: dict = {"converted_with": "seed_vc v2" + (" + Sidon 48 kHz" if enhanced else ""),
                  "target_reference_id": body.target_reference_id,
                  "convert_seconds": convert_s, "source": body.source.model_dump()}
    if body.enhance and not enhanced and enhance_error:
        meta["enhance_error"] = "Kept the 22 kHz result: the 48 kHz restore step failed (" + enhance_error[:200] + ")."
    if body.simple or body.advanced or body.effects:
        mono, shaped = await asyncio.to_thread(_shape_audio, mono, crate, None, body)
        meta.update(shaped, simple=body.simple, advanced=body.advanced, effects=body.effects)
    return _store_preview(mono, crate, meta)


@router.get("/previews/{pid}")
def get_preview(pid: str):
    if not ID.match(pid) or not (PREVIEWS / f"{pid}.wav").is_file():
        raise HTTPException(404, "Preview not found.")
    return FileResponse(PREVIEWS / f"{pid}.wav", media_type="audio/wav")


@router.post("/save")
async def save_preview(body: SaveIn):
    if not ID.match(body.preview_id) or not (PREVIEWS / f"{body.preview_id}.wav").is_file():
        raise HTTPException(404, "Preview not found; render it again.")
    meta_path = PREVIEWS / f"{body.preview_id}.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        audio, rate = dsp.read(PREVIEWS / f"{body.preview_id}.wav")
    except (OSError, ValueError, RuntimeError):
        raise HTTPException(404, "Preview not found; render it again.")
    mono = audio.mean(axis=1).astype(np.float32)
    if body.as_ == "generation":
        from engines.audio_io import save_generation

        record = save_generation(_outputs_dir(), mono, rate, {
            "engine": "voice_lab", "model_id": "voice_lab", "model_family": "Voice Lab",
            "text": body.name, "voice_description": None, "reference_id": None,
            "meta": {"voice_lab": {k: meta.get(k) for k in ("simple", "advanced", "effects", "params", "source",
                                                          "converted_with", "target_reference_id")}}})
        record["url"] = f"/api/tts/audio/{record['file']}"
        return record
    seconds = len(mono) / rate
    if not 3.0 <= seconds <= 60.0:
        raise HTTPException(422, f"A cloning reference must be 3-60 seconds; this one is {seconds:.1f} s.")
    from engines import audio_store

    low = dsp.resample(mono[:, None], rate, 24000)[:, 0]
    high = dsp.resample(mono[:, None], rate, 44100)[:, 0]
    record = audio_store.save_reference(_voices(), low, 24000, body.name, high, 44100)
    ref_json = _voices() / f"{record['reference_id']}.json"
    saved = json.loads(ref_json.read_text(encoding="utf-8"))
    saved.update({"transcript": (body.transcript or "").strip(), "voice_lab": {k: meta.get(k) for k in ("simple", "advanced", "effects", "source")}})
    ref_json.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    return saved
