"""
api/tts.py — Speech generation endpoints.

GET  /api/tts/engines          engines, capabilities and runtime status
GET  /api/tts/models           models found (shared models folder and Hugging Face cache), with state
POST /api/tts/models/refresh   rescan
GET  /api/tts/references       stored reference clips, newest first
GET  /api/tts/references/{id}/audio   play a stored reference clip
DELETE /api/tts/references/{id}       delete one reference clip (refused with 409 while it is in use)
POST /api/tts/references/bulk-delete  delete selected clips, or all with the typed confirmation
POST /api/tts/synthesize       generate speech (runs in a worker thread)
GET  /api/tts/audio/{name}     download a generated WAV
DELETE /api/tts/audio/{name}   delete one generation (WAV and its record)
POST /api/tts/audio/bulk-delete  delete selected generations, or all with the typed confirmation
GET  /api/tts/history          recent generations, newest first
POST /api/tts/unload           release the loaded model's GPU memory
"""
from __future__ import annotations

import asyncio
import threading
import dataclasses
import logging
import uuid
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from asr.audio import AudioDecodeError, decode_audio
from asr.service import transcribe_file
from core import settings as app_settings
from core.model_paths import model_roots
from core.paths import DATA_DIR, DEFAULT_OUTPUTS_DIR, VERIFICATION_FILE
from engines import audio_store
from engines.audio_io import is_valid_audio_name, list_generations, save_generation
from engines import longform, pacing
from engines.base import EngineError, SpeakerReference, SynthesisRequest, SynthesisResult
from engines.delivery import Delivery, resolve_delivery
from engines.breeze import BreezeEngine
from engines.kokoro import KokoroEngine
from engines.fish_drama import FishDramaCloudEngine, FishDramaEngine
from engines.maya1 import Maya1Engine
from engines.manager import EngineManager
from engines.omnivoice import MIN_REFERENCE_SECONDS, OmniVoiceEngine
MAX_REFERENCE_SECONDS = 60.0   # storage limit; each engine declares its own (OmniVoice/VibeVoice 25 s)
from engines.registry import ModelRegistry
from engines.vibevoice_engine import VibeVoiceEngine
from engines import voice_presets

log = logging.getLogger(__name__)
router = APIRouter()

REFERENCE_RATE = 24000
REFERENCE_HQ_RATE = 44100   # kept alongside for engines that clone at 44.1 kHz (Drama 3)
from core.library import generations_dir, voices_dir
REFERENCE_DIR = voices_dir()   # Library/Voices
TMP_DIR = DATA_DIR / "tmp"
MAX_REFERENCE_BYTES = 50 * 1024 * 1024

_ENGINES = [Maya1Engine(), OmniVoiceEngine(transcribe_reference=transcribe_file), VibeVoiceEngine(), BreezeEngine(),
            FishDramaEngine(), FishDramaCloudEngine(), KokoroEngine()]
_services: dict[str, object] = {}
_services_lock = threading.Lock()


def _get_services() -> tuple[ModelRegistry, EngineManager]:
    if "manager" in _services:
        return _services["registry"], _services["manager"]  # type: ignore[return-value]
    with _services_lock:
        return _build_services()


def _build_services() -> tuple[ModelRegistry, EngineManager]:
    if "manager" not in _services:
        cfg = app_settings.load()
        roots = model_roots(cfg)
        registry = ModelRegistry(_ENGINES, roots, VERIFICATION_FILE)
        registry.discover()
        _services["registry"] = registry
        _services["manager"] = EngineManager({e.engine_id: e for e in _ENGINES}, registry)
    return _services["registry"], _services["manager"]  # type: ignore[return-value]


async def _services_async() -> tuple[ModelRegistry, EngineManager]:
    """Services for async endpoints. The first build runs model discovery (which can import
    torch for several seconds), so it runs in a worker thread and never blocks the event loop."""
    if "manager" in _services:
        return _get_services()
    return await asyncio.to_thread(_get_services)


_background_discovery: Optional[threading.Thread] = None


def start_background_discovery() -> None:
    """Build the model registry in a background thread at app startup, so the first
    Generate/engines request finds discovery already done. Safe to call more than once;
    `_services_lock` makes it race-free with request-time builds."""
    global _background_discovery
    if "manager" in _services or (_background_discovery is not None and _background_discovery.is_alive()):
        return

    def run():
        try:
            _get_services()
        except Exception:
            log.exception("Background model discovery failed; it will retry on first request")

    _background_discovery = threading.Thread(target=run, name="voicevault-model-discovery", daemon=True)
    _background_discovery.start()


def _outputs_dir() -> Path:
    configured = app_settings.load().get("outputs_dir") or ""
    return Path(configured) if configured else generations_dir()


class BulkDeleteRequest(BaseModel):
    names: Optional[list[str]] = Field(default=None, max_length=audio_store.MAX_BULK)  # file names or reference ids
    all: bool = False
    confirm: Optional[str] = Field(default=None, max_length=40)  # must read DELETE ALL when all is true


class SpeakerVoice(BaseModel):
    """One voice of a multi-speaker request. Its position is the <|speaker:N|> number."""
    reference_id: str = Field(max_length=80)
    reference_text: Optional[str] = Field(default=None, max_length=2000)


class SynthesizeRequest(BaseModel):
    model_id: str
    text: str = Field(min_length=1, max_length=5000)
    voice_description: Optional[str] = Field(default=None, max_length=1000)
    reference_id: Optional[str] = None      # from POST /api/tts/references (cloning)
    reference_text: Optional[str] = Field(default=None, max_length=2000)
    language: Optional[str] = Field(default=None, max_length=40)
    temperature: float = Field(default=0.4, ge=0.1, le=2.0)
    top_p: float = Field(default=0.9, ge=0.1, le=1.0)
    repetition_penalty: float = Field(default=1.1, ge=1.0, le=2.0)
    max_new_tokens: int = Field(default=4000, ge=100, le=16000)
    seed: Optional[int] = Field(default=0, ge=0, le=2**32 - 1)
    device: Literal["cuda", "cpu"] = "cuda"
    dtype: Literal["bfloat16", "float16", "float32"] = "bfloat16"
    delivery: Optional[Delivery] = None
    speakers: Optional[list[SpeakerVoice]] = Field(default=None, max_length=16)
    # Silence between sentences: tight / natural (as generated) / relaxed / dramatic. Pacing tags in the
    # text ([pause 0.8s], [slow]…[/slow], [fast]…[/fast], [speed 0.7]…[/speed]) work with every engine.
    pacing: Literal["tight", "natural", "relaxed", "dramatic"] = "natural"


@router.get("/engines")
async def list_engines():
    registry, manager = await _services_async()
    out = []
    for engine in _ENGINES:
        models = [m for m in registry.models() if m.engine_id == engine.engine_id]
        out.append({
            "id": engine.engine_id,
            "name": engine.display_name,
            "capabilities": dataclasses.asdict(engine.capabilities),
            "runtime_problem": engine.runtime_problem(),
            "model_count": len(models),
            "usable_model_count": sum(m.state.value in ("integrated", "tested") for m in models),
        })
    return {"engines": out, "loaded_model_id": manager.loaded_model_id}  # type: ignore[attr-defined]


@router.get("/presets")
def list_voice_presets():
    try:
        return {"items": voice_presets.list_presets()}
    except voice_presets.CorruptPresetStore as exc:
        raise HTTPException(500, str(exc))


@router.post("/presets")
def create_voice_preset(body: voice_presets.VoicePresetInput):
    try:
        return voice_presets.create_preset(body)
    except voice_presets.PresetLimitReached as exc:
        raise HTTPException(409, str(exc))
    except voice_presets.CorruptPresetStore as exc:
        raise HTTPException(500, str(exc))


@router.put("/presets/{preset_id}")
def replace_voice_preset(preset_id: str, body: voice_presets.VoicePresetInput):
    try:
        return voice_presets.replace_preset(preset_id, body)
    except voice_presets.PresetNotFound as exc:
        raise HTTPException(404, str(exc))
    except voice_presets.CorruptPresetStore as exc:
        raise HTTPException(500, str(exc))


@router.delete("/presets/{preset_id}")
def delete_voice_preset(preset_id: str):
    try:
        voice_presets.delete_preset(preset_id)
        return {"ok": True}
    except voice_presets.PresetNotFound as exc:
        raise HTTPException(404, str(exc))
    except voice_presets.CorruptPresetStore as exc:
        raise HTTPException(500, str(exc))


@router.get("/models")
async def list_models():
    registry, manager = await _services_async()
    return {
        "models": [m.to_dict() for m in registry.models()],
        "roots": [str(r) for r in registry.roots],
        "last_scan": registry.last_scan,
        "loaded_model_id": manager.loaded_model_id,  # type: ignore[attr-defined]
    }


@router.post("/models/refresh")
async def refresh_models():
    registry, _ = await _services_async()
    registry.set_roots(model_roots(app_settings.load()))
    found = await asyncio.to_thread(registry.discover)
    return {"count": len(found), "last_scan": registry.last_scan}


@router.post("/references")
async def upload_reference(file: UploadFile = File(...)):
    """Store a cloning reference (3-60 s): 24 kHz mono for every engine plus a 44.1 kHz copy."""
    suffix = Path(file.filename or "").suffix.lower()[:10] or ".bin"
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = TMP_DIR / f"{uuid.uuid4().hex}{suffix}"
    try:
        written = 0
        with tmp_path.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_REFERENCE_BYTES:
                    raise HTTPException(413, "The reference file is larger than 50 MB.")
                out.write(chunk)
        if written == 0:
            raise HTTPException(422, "The uploaded file is empty.")
        try:
            samples = await asyncio.to_thread(decode_audio, tmp_path, REFERENCE_RATE)
        except AudioDecodeError as exc:
            raise HTTPException(422, str(exc))
        seconds = len(samples) / REFERENCE_RATE
        if not (MIN_REFERENCE_SECONDS <= seconds <= MAX_REFERENCE_SECONDS):
            raise HTTPException(422, f"The clip is {seconds:.1f} s. Use {MIN_REFERENCE_SECONDS:.0f}-"
                                     f"{MAX_REFERENCE_SECONDS:.0f} seconds of clean, single-speaker speech.")
        try:
            hq = await asyncio.to_thread(decode_audio, tmp_path, REFERENCE_HQ_RATE)
        except AudioDecodeError:
            hq = None  # the 24 kHz clip is enough for every engine; the full-band copy is a bonus
        record = await asyncio.to_thread(
            audio_store.save_reference, REFERENCE_DIR, samples, REFERENCE_RATE, file.filename or "",
            hq, REFERENCE_HQ_RATE if hq is not None else None)
    finally:
        tmp_path.unlink(missing_ok=True)
    return {"reference_id": record["reference_id"], "duration_s": record["duration_s"], "sample_rate": REFERENCE_RATE}


@router.get("/references")
async def list_reference_clips():
    items = audio_store.list_references(REFERENCE_DIR)
    for item in items:
        item["url"] = f"/api/tts/references/{item['reference_id']}/audio"
    return {"items": items}


@router.get("/references/{reference_id}/audio")
async def get_reference_audio(reference_id: str):
    path = _store_call(lambda: audio_store.reference_path(REFERENCE_DIR, reference_id))
    if path is None:
        raise HTTPException(404, "Reference clip not found.")
    return FileResponse(path, media_type="audio/wav", filename=f"{reference_id}.wav")


@router.delete("/references/{reference_id}")
async def delete_reference(reference_id: str):
    result = _store_call(lambda: audio_store.delete_references(REFERENCE_DIR, [reference_id]))
    return _single_result(result, "Reference clip")


@router.post("/references/bulk-delete")
async def bulk_delete_references(body: BulkDeleteRequest):
    return _store_call(lambda: audio_store.delete_references(
        REFERENCE_DIR, body.names, delete_all=body.all, confirm=body.confirm))


@router.post("/synthesize")
async def synthesize(body: SynthesizeRequest):
    ids = [body.reference_id] + [s.reference_id for s in body.speakers or []]
    if any(i and not audio_store.REFERENCE_ID.match(i) for i in ids):
        raise HTTPException(400, "Invalid reference id.")
    # Every clip stays held for the whole run, so none can be deleted while the engine reads it.
    from contextlib import ExitStack
    with ExitStack() as stack:
        for ref in dict.fromkeys(i for i in ids if i):
            stack.enter_context(audio_store.reference_in_use(ref))
        return await _run_synthesis(body)


@router.post("/stream")
async def stream_speech(request: Request, body: SynthesizeRequest):
    from api.tts_stream import stream_speech as run_stream
    return await run_stream(request, body)


@router.post("/segment")
async def synthesize_segment(request: Request, body: SynthesizeRequest):
    """Synthesize one bounded chat segment through the shared SSE audio protocol."""
    from api.tts_stream import stream_speech as run_stream
    return await run_stream(request, body, allow_complete=True)


async def _run_synthesis(body: SynthesizeRequest):
    registry, manager = await _services_async()
    info = registry.get(body.model_id)
    if info is None:
        raise HTTPException(404, f"Unknown model '{body.model_id}'. Refresh the model list.")

    reference_path = None
    if body.reference_id:
        reference_path = REFERENCE_DIR / f"{body.reference_id}.wav"
        if not reference_path.is_file():
            raise HTTPException(404, "Reference clip not found. Upload it again.")

    engine = manager._engines[info.engine_id]  # type: ignore[attr-defined]
    speakers: list[SpeakerReference] = []
    if body.speakers:
        if not engine.capabilities.multi_speaker:
            raise HTTPException(422, f"{engine.display_name} does not support multiple speakers.")
        if len(body.speakers) > engine.capabilities.max_speakers:
            raise HTTPException(422, f"{engine.display_name} supports at most {engine.capabilities.max_speakers} voices.")
        for spk in body.speakers:
            path = REFERENCE_DIR / f"{spk.reference_id}.wav"
            if not path.is_file():
                raise HTTPException(404, "A speaker's reference clip was not found. Upload it again.")
            speakers.append(SpeakerReference(audio=path, text=spk.reference_text))
    text, voice_description, delivery_meta, warnings = resolve_delivery(
        body.delivery, engine, reference_mode=reference_path is not None,
        text=body.text, voice_description=body.voice_description,
    )

    request = SynthesisRequest(
        text=text,
        voice_description=voice_description,
        reference_audio=reference_path,
        reference_text=body.reference_text,
        language=body.language,
        temperature=body.temperature,
        top_p=body.top_p,
        repetition_penalty=body.repetition_penalty,
        max_new_tokens=body.max_new_tokens,
        seed=body.seed,
        speakers=speakers,
    )
    try:
        if pacing.needed(request.text, body.pacing):
            if body.speakers:
                raise EngineError("Pacing tags and the pace setting work with one voice at a time, not multi-speaker scenes.")
            run = lambda req: synthesize_text(manager, body.model_id, req, engine, body.device, body.dtype)  # noqa: E731
            result = await asyncio.to_thread(pacing.synthesize, run, request, body.pacing)
        else:
            result = await asyncio.to_thread(
                synthesize_text, manager, body.model_id, request, engine, body.device, body.dtype
            )
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except EngineError as exc:
        raise HTTPException(422, str(exc))
    except Exception:
        log.exception("Speech generation failed for %s", body.model_id)
        raise HTTPException(500, "Speech generation failed. See the backend log for details.")

    result.meta["quality"] = longform.analyze(result.samples, result.sample_rate, body.text)
    if delivery_meta is not None:
        result.meta["delivery"] = delivery_meta
    if warnings:
        result.meta["delivery_warnings"] = warnings

    record = save_generation(
        _outputs_dir(),
        result.samples,
        result.sample_rate,
        {
            "engine": info.engine_id,
            "model_id": info.id,
            "model_family": info.family,
            "text": body.text,
            "voice_description": voice_description,
            "reference_id": body.reference_id,
            "speaker_reference_ids": [s.reference_id for s in body.speakers or []] or None,
            "meta": result.meta,
        },
    )
    record["url"] = f"/api/tts/audio/{record['file']}"
    return record


def synthesize_text(manager: EngineManager, model_id: str, request: SynthesisRequest, engine,
                    device: str = "cuda", dtype: str = "bfloat16"):
    """Run one request, splitting long text for engines that cannot take it in one piece."""
    if not longform.needs_chunking(request.text, engine.capabilities.long_form):
        return manager.synthesize(model_id, request, device, dtype)
    pieces = longform.split_text(request.text)
    results = []
    for piece in pieces:
        # Same voice, reference and seed for every chunk keeps the speaker consistent.
        results.append(manager.synthesize(model_id, dataclasses.replace(request, text=piece), device, dtype))
    rate = results[0].sample_rate
    if any(r.sample_rate != rate for r in results):
        raise EngineError("The engine returned chunks at different sample rates.")
    joined = longform.join_chunks([r.samples for r in results], rate)
    meta = dict(results[0].meta)
    meta["chunks"] = {
        "count": len(pieces),
        "characters": [len(p) for p in pieces],
        "durations_s": [round(len(r.samples) / rate, 2) for r in results],
        "completion": [r.meta.get("completion") or r.meta.get("finish_reason") for r in results],
    }
    return SynthesisResult(samples=joined, sample_rate=rate, meta=meta)


@router.get("/audio/{name}")
async def get_audio(name: str):
    if not is_valid_audio_name(name):
        raise HTTPException(400, "Invalid file name")
    path = _outputs_dir() / name
    if not path.is_file():
        raise HTTPException(404, "Audio file not found")
    return FileResponse(path, media_type="audio/wav", filename=name)


@router.delete("/audio/{name}")
async def delete_audio(name: str):
    result = _store_call(lambda: audio_store.delete_generations(_outputs_dir(), [name]))
    return _single_result(result, "Generated file")


@router.post("/audio/bulk-delete")
async def bulk_delete_audio(body: BulkDeleteRequest):
    return _store_call(lambda: audio_store.delete_generations(
        _outputs_dir(), body.names, delete_all=body.all, confirm=body.confirm))


def _store_call(action):
    """Run a store action and turn its refusals into HTTP errors."""
    try:
        return action()
    except audio_store.StoreError as exc:
        raise HTTPException(400, str(exc))


    except audio_store.InUseError as exc:
        raise HTTPException(409, str(exc))


def _single_result(result: dict, label: str) -> dict:
    """A single delete that did not happen is an error, not an empty success."""
    if result["failed"]:
        raise HTTPException(409, result["failed"][0]["reason"])
    if result["missing"]:
        raise HTTPException(404, f"{label} not found.")
    return result


@router.get("/history")
async def history(limit: int = 50):
    items = list_generations(_outputs_dir(), limit=min(max(limit, 1), 200))
    for item in items:  # same shape as a fresh /synthesize response
        item["url"] = f"/api/tts/audio/{item['file']}"
    return {"items": items}


class WarmIn(BaseModel):
    model_id: str = Field(max_length=500)


_warming: set[str] = set()


@router.post("/warm")
async def warm_model(body: WarmIn):
    """Load a voice in the background (live chat calls this when a chat opens). Returns at once."""
    _, manager = await _services_async()
    if body.model_id in _warming:
        return {"state": "loading"}
    _warming.add(body.model_id)

    async def run():
        try:
            await asyncio.to_thread(manager.warm, body.model_id)  # type: ignore[attr-defined]
        except Exception as exc:
            log.info("Warming %s failed: %s", body.model_id, exc)
        finally:
            _warming.discard(body.model_id)
    asyncio.create_task(run())
    return {"state": "loading"}


@router.post("/unload")
async def unload_model():
    _, manager = await _services_async()
    await asyncio.to_thread(manager.unload)  # type: ignore[attr-defined]
    return {"ok": True, "loaded_model_id": None}
