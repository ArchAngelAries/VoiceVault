"""
engines/manager.py — Runs one speech model at a time on the GPU.

Large TTS models are loaded on first use and stay resident until a different model is
requested or unload() is called. A lock serialises GPU work, so two requests cannot
load or run models at the same time.
"""
from __future__ import annotations

import logging
from threading import Event
from typing import Callable, Optional

import numpy as np

from core.gpu_scheduler import register_tts_manager, speech_resource_lock
from engines.base import EngineError, ModelInfo, ModelState, SynthesisCancelled, SynthesisRequest, SynthesisResult, TTSEngine
from engines.registry import ModelRegistry

log = logging.getLogger(__name__)


class EngineManager:
    def __init__(self, engines: dict[str, TTSEngine], registry: ModelRegistry):
        self._engines = engines
        self._registry = registry
        # ASR and TTS share the lock because some TTS engines invoke ASR from synthesize().
        self._lock = speech_resource_lock
        self._active: Optional[tuple[str, str, str]] = None  # (model id, device, dtype)
        self._active_engine: Optional[TTSEngine] = None
        register_tts_manager(self._release)

    @property
    def loaded_model_id(self) -> Optional[str]:
        return self._active[0] if self._active else None

    def synthesize(
        self,
        model_id: str,
        request: SynthesisRequest,
        device: str = "cuda",
        dtype: str = "bfloat16",
        cancel: Event | None = None,
    ) -> SynthesisResult:
        info = self._registry.get(model_id)
        if info is not None and self._outside_gpu_slot(self._engines[info.engine_id]):
            return self._synthesize_remote(info, request, cancel)
        # A disconnected clause must not load a model after waiting for another
        # speech request. Complete-output engines can only stop between runs.
        while not self._lock.acquire(timeout=0.1):
            if cancel is not None and cancel.is_set():
                raise SynthesisCancelled("Speech canceled before loading.")
        try:
            if cancel is not None and cancel.is_set():
                raise SynthesisCancelled("Speech canceled before loading.")
            register_tts_manager(self._release)
            info = self._registry.get(model_id)
            if info is None:
                raise EngineError(f"Unknown model '{model_id}'. Refresh the model list and try again.")
            if info.state == ModelState.BLOCKED:
                raise EngineError(info.reason or "This model is blocked.")
            key = (model_id, device, dtype)
            engine = self._engines[info.engine_id]
            if self._needs_transcripts(engine, request):
                # Transcribe while the TTS model is not resident, then let the model load.
                # The shared RLock allows the service to acquire this lock in the same thread.
                self._release()
                self._fill_transcripts(request)
            if cancel is not None and cancel.is_set():
                raise SynthesisCancelled("Speech canceled before loading.")
            if self._active != key:
                from asr.service import unload_transcriber

                unload_transcriber()   # only when a model has to load; a resident voice keeps Whisper too
                self._load(info, key)
            assert self._active_engine is not None
            if cancel is not None and cancel.is_set():
                raise SynthesisCancelled("Speech canceled while loading.")
            result = self._active_engine.synthesize(request)
            if cancel is not None and cancel.is_set():
                raise SynthesisCancelled("Speech canceled during synthesis.")
            return result
        finally:
            if cancel is not None and cancel.is_set():
                self._release()
            self._lock.release()

    def unload(self) -> None:
        with self._lock:
            self._release()

    def warm(self, model_id: str) -> str:
        """Load a voice ahead of time so the first spoken reply doesn't wait for it. Returns its state."""
        info = self._registry.get(model_id)
        if info is None:
            raise EngineError(f"Unknown model '{model_id}'. Refresh the model list and try again.")
        if info.state == ModelState.BLOCKED:
            raise EngineError(info.reason or "This model is blocked.")
        engine = self._engines[info.engine_id]
        if self._outside_gpu_slot(engine):
            engine.load(info)          # its own process; nothing else is unloaded
            return "ready"
        with self._lock:
            key = (model_id, "cuda", "bfloat16")
            if self._active == key:
                return "ready"
            from asr.service import unload_transcriber

            unload_transcriber()
            register_tts_manager(self._release)
            self._load(info, key)
        return "ready"

    @staticmethod
    def _outside_gpu_slot(engine: TTSEngine) -> bool:
        caps = engine.capabilities
        return bool(getattr(caps, "remote", False) or getattr(caps, "cpu_resident", False))

    @staticmethod
    def _needs_transcripts(engine: TTSEngine, request: SynthesisRequest) -> bool:
        if not getattr(engine.capabilities, "reference_transcript", False):
            return False
        if request.reference_audio and not (request.reference_text or "").strip():
            return True
        return any(not (s.text or "").strip() for s in request.speakers)

    @staticmethod
    def _fill_transcripts(request: SynthesisRequest) -> None:
        from asr.service import prepare_reference_transcript

        if request.reference_audio and not (request.reference_text or "").strip():
            request.reference_text = prepare_reference_transcript(request.reference_audio, request.language)
        for speaker in request.speakers:
            if not (speaker.text or "").strip():
                speaker.text = prepare_reference_transcript(speaker.audio, request.language)

    def _synthesize_remote(self, info: ModelInfo, request: SynthesisRequest,
                           cancel: Event | None) -> SynthesisResult:
        """Cloud engines use no local GPU memory, so the resident local model stays loaded."""
        if info.state == ModelState.BLOCKED:
            raise EngineError(info.reason or "This model is blocked.")
        engine = self._engines[info.engine_id]
        if cancel is not None and cancel.is_set():
            raise SynthesisCancelled("Speech canceled before sending.")
        if self._needs_transcripts(engine, request):
            with self._lock:  # ASR shares the speech lock with local TTS
                self._release()
                self._fill_transcripts(request)
        engine.load(info)
        result = engine.synthesize(request)
        if cancel is not None and cancel.is_set():
            raise SynthesisCancelled("Speech canceled during synthesis.")
        return result

    def synthesize_stream(
        self, model_id: str, request: SynthesisRequest,
        on_audio: Callable[[np.ndarray, int], None], cancel: Event,
        device: str = "cuda", dtype: str = "bfloat16",
    ) -> SynthesisResult:
        info = self._registry.get(model_id)
        if info is not None and self._outside_gpu_slot(self._engines[info.engine_id]):
            # A CPU-resident voice (Kokoro) streams beside the GPU voice and the chat LLM.
            engine = self._engines[info.engine_id]
            if info.state == ModelState.BLOCKED:
                raise EngineError(info.reason or "This model is blocked.")
            if not engine.capabilities.streaming:
                raise EngineError(f"{engine.display_name} does not support incremental speech.")
            engine.load(info)
            return engine.synthesize_stream(request, on_audio, cancel)
        # Waiting streams can be canceled without waiting for another model's run.
        while not self._lock.acquire(timeout=0.1):
            if cancel.is_set():
                raise SynthesisCancelled("Speech canceled before loading.")
        try:
            if cancel.is_set():
                raise SynthesisCancelled("Speech canceled before loading.")
            info = self._registry.get(model_id)
            if info is None:
                raise EngineError(f"Unknown model '{model_id}'. Refresh the model list.")
            engine = self._engines[info.engine_id]
            if info.state == ModelState.BLOCKED:
                raise EngineError(info.reason or "This model is blocked.")
            if not engine.capabilities.streaming:
                raise EngineError(f"{engine.display_name} does not support incremental speech.")
            # Streaming engines currently use cached presets, with no ASR work in
            # this worker. Keep any future reference transcription on this thread.
            register_tts_manager(self._release)
            from asr.service import unload_transcriber, prepare_reference_transcript
            if engine.capabilities.reference_transcript and request.reference_audio and not (request.reference_text or "").strip():
                self._release()
                request.reference_text = prepare_reference_transcript(request.reference_audio, request.language)
            key = (model_id, device, dtype)
            if self._active != key:
                unload_transcriber()
                self._load(info, key)
            if cancel.is_set():
                raise SynthesisCancelled("Speech canceled while loading.")
            return engine.synthesize_stream(request, on_audio, cancel)
        except Exception:
            self._release()
            raise
        finally:
            if cancel.is_set():
                self._release()
            self._lock.release()

    def _load(self, info: ModelInfo, key: tuple[str, str, str]) -> None:
        self._release()
        from core.gpu_scheduler import release_gpu_users
        release_gpu_users()  # e.g. the Music Studio server
        engine = self._engines[info.engine_id]
        _, device, dtype = key
        log.info("Loading %s from %s", info.id, info.path)
        try:
            engine.load(info, device=device, dtype=dtype)
        except Exception:
            # Loading can fail after allocating tensors but before becoming active.
            try:
                engine.unload()
            except Exception:
                log.exception("Failed to release partially loaded %s", info.id)
            raise
        self._active = key
        self._active_engine = engine

    def _release(self) -> None:
        if self._active_engine is not None:
            log.info("Unloading %s", self._active[0] if self._active else "?")
            self._active_engine.unload()
        self._active = None
        self._active_engine = None
