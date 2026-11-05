"""Bounded incremental PCM events, with cooperative cancellation on disconnect."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from queue import Empty, Full, Queue
from threading import Event

import numpy as np
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from engines.base import EngineError, ModelState, SynthesisCancelled, SynthesisRequest
from engines.delivery import resolve_delivery

log = logging.getLogger(__name__)


def pcm_event(samples: np.ndarray, rate: int) -> dict:
    samples = np.asarray(samples, dtype=np.float32).reshape(-1)
    if not samples.size or not np.isfinite(samples).all() or not 1 <= rate <= 192000:
        raise EngineError("The speech engine emitted an invalid audio chunk.")
    pcm = (np.clip(samples, -1, 1) * 32767).astype('<i2').tobytes()
    return {"sample_rate": rate, "samples": samples.size,
            "pcm_s16le": base64.b64encode(pcm).decode('ascii')}


async def stream_speech(request: Request, body, *, allow_complete: bool = False):
    # Imported after tts.py defines its request schema and registers this router.
    from api.tts import REFERENCE_DIR, _outputs_dir, _services_async
    from engines import audio_store
    from engines.audio_io import save_generation

    registry, manager = await _services_async()
    info = registry.get(body.model_id)
    if info is None:
        raise HTTPException(404, "Unknown model. Refresh the model list.")
    if info.state == ModelState.BLOCKED:
        raise HTTPException(422, info.reason or "This model is blocked.")
    engine = manager._engines[info.engine_id]
    incremental = bool(engine.capabilities.streaming)
    if not incremental and not allow_complete:
        raise HTTPException(422, "This model does not support incremental speech.")
    reference = None
    if body.reference_id:
        if not audio_store.REFERENCE_ID.fullmatch(body.reference_id):
            raise HTTPException(400, "Invalid reference id.")
        reference = REFERENCE_DIR / f'{body.reference_id}.wav'
        if not reference.is_file():
            raise HTTPException(404, "Reference clip not found.")
    text, voice_description, delivery_meta, warnings = resolve_delivery(
        body.delivery, engine, reference_mode=reference is not None,
        text=body.text, voice_description=body.voice_description,
    )
    speech = SynthesisRequest(text=text, voice_description=voice_description,
        reference_audio=reference, reference_text=body.reference_text, language=body.language,
        temperature=body.temperature, top_p=body.top_p, repetition_penalty=body.repetition_penalty,
        max_new_tokens=body.max_new_tokens, seed=body.seed)
    cancel = Event()
    packets: Queue = Queue(maxsize=8)

    def emit(kind, payload):
        while not cancel.is_set():
            try:
                packets.put((kind, payload), timeout=0.1)
                return
            except Full:
                continue
        raise SynthesisCancelled("Speech stream closed.")

    def produce():
        try:
            with audio_store.reference_in_use(body.reference_id):
                emit('start', {"model_id": info.id, "incremental": incremental})
                if incremental:
                    result = manager.synthesize_stream(body.model_id, speech,
                        lambda samples, rate: emit('audio', pcm_event(samples, rate)), cancel,
                        body.device, body.dtype)
                else:
                    result = manager.synthesize(body.model_id, speech, body.device, body.dtype, cancel=cancel)
                    if cancel.is_set():
                        return
                    chunk_samples = max(1, result.sample_rate)
                    for offset in range(0, len(result.samples), chunk_samples):
                        emit('audio', pcm_event(result.samples[offset:offset + chunk_samples], result.sample_rate))
                if delivery_meta is not None:
                    result.meta["delivery"] = delivery_meta
                if warnings:
                    result.meta["delivery_warnings"] = warnings
                if cancel.is_set():
                    return
                record = save_generation(_outputs_dir(), result.samples, result.sample_rate, {
                    "engine": info.engine_id, "model_id": info.id, "model_family": info.family,
                    "text": body.text, "voice_description": voice_description,
                    "reference_id": body.reference_id, "meta": result.meta,
                })
                record['url'] = f"/api/tts/audio/{record['file']}"
                emit('done', record)
        except SynthesisCancelled:
            pass
        except Exception as exc:
            if not isinstance(exc, EngineError):
                log.exception("Streamed speech failed for %s", info.id)
            if not cancel.is_set():
                try:
                    emit('error', {"message": str(exc) if isinstance(exc, EngineError)
                        else "Speech generation failed. See the backend log for details."})
                except SynthesisCancelled:
                    pass

    async def events():
        worker = asyncio.create_task(asyncio.to_thread(produce))
        try:
            while not worker.done() or not packets.empty():
                if await request.is_disconnected():
                    break
                try:
                    kind, payload = packets.get_nowait()
                except Empty:
                    await asyncio.sleep(0.03)
                    continue
                yield f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
        finally:
            # Never force-unload a model that is still executing. Its worker owns
            # the lock until the engine observes this event and releases safely.
            cancel.set()
            try:
                await asyncio.wait_for(asyncio.shield(worker), timeout=30)
            except asyncio.TimeoutError:
                log.warning("Speech cancellation is waiting for the current decode step.")

    return StreamingResponse(events(), media_type='text/event-stream', headers={
        'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no',
    })
