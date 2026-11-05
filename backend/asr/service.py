"""
asr/service.py — The one Whisper instance VoiceVault shares.

The /api/asr routes and the TTS engines (OmniVoice needs a transcript for cloning) use the same
loaded model, so the ~2 GB of weights are held once.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from asr.audio import decode_audio
from core.gpu_scheduler import release_tts_for_asr, speech_resource_lock
from asr.whisper_engine import WhisperTranscriber

_transcriber = WhisperTranscriber()


def get_transcriber() -> WhisperTranscriber:
    return _transcriber


def transcribe_file(path: Path, language: Optional[str] = None) -> str:
    """Load Whisper if needed, transcribe a file, return the plain text. Blocking; call from a worker thread."""
    with speech_resource_lock:
        transcriber = get_transcriber()
        if not transcriber.loaded:
            release_tts_for_asr()   # only evicts when Whisper and the voice model do not both fit
            transcriber.load()
        return transcriber.transcribe(decode_audio(path), language=language, timestamps=False)["text"]


def prepare_reference_transcript(path: Path, language: Optional[str] = None) -> str:
    """Transcribe a TTS reference, then release Whisper before a large TTS model loads."""
    with speech_resource_lock:
        transcriber = get_transcriber()
        try:
            if not transcriber.loaded:
                release_tts_for_asr()
                transcriber.load()
            text = transcriber.transcribe(decode_audio(path), language=language, timestamps=False)["text"]
        finally:
            transcriber.unload()
    from engines.audio_store import remember_transcript
    remember_transcript(path, text)   # never transcribe the same voice twice
    return text


def unload_transcriber() -> None:
    """Release Whisper before TTS runs, if it is currently resident."""
    with speech_resource_lock:
        transcriber = get_transcriber()
        if transcriber.loaded:
            transcriber.unload()
