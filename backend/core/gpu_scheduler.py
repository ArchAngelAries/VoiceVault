"""Shared lock for speech-model GPU work.

ASR can be called from inside a TTS synthesis (OmniVoice reference transcription), so
the lock must be reentrant. Every load, inference, and unload path uses this lock.
"""
from __future__ import annotations

import threading
import weakref
from typing import Callable, Optional


speech_resource_lock = threading.RLock()
_tts_release: Optional[weakref.WeakMethod] = None


def register_tts_manager(release: Callable[[], None]) -> None:
    """Remember the manager that currently owns TTS residency, without retaining it."""
    global _tts_release
    _tts_release = weakref.WeakMethod(release)  # managers provide a bound _release method


# Whisper large-v3-turbo needs about 1.6 GB in fp16; keep a margin for activations.
WHISPER_VRAM_BYTES = 3 * 1024 ** 3


def vram_fits(need_bytes: int) -> bool:
    """True when the GPU has at least need_bytes free right now (False when it cannot be measured)."""
    try:
        from engines.fish_drama import free_vram_bytes
        free = free_vram_bytes()
    except Exception:
        return False
    return free is not None and free >= need_bytes


def release_tts_for_asr() -> None:
    """Make room for Whisper. The resident voice model stays loaded when both fit, so live chat
    does not reload its voice after every spoken turn."""
    if vram_fits(WHISPER_VRAM_BYTES):
        return
    with speech_resource_lock:
        release = _tts_release() if _tts_release is not None else None
        if release is not None:
            release()
    release_gpu_users()


# Other GPU users (for example the Music Studio's audio.cpp server) register a release
# callback so speech work can evict them, and they can evict speech, before loading.
_other_users: dict[str, Callable[[], None]] = {}


def register_gpu_user(name: str, release: Callable[[], None]) -> None:
    _other_users[name] = release


def release_gpu_users(except_name: str = "") -> None:
    for name, release in list(_other_users.items()):
        if name != except_name:
            try:
                release()
            except Exception:  # a failed release must not block the caller's own work
                import logging
                logging.getLogger(__name__).exception("Releasing GPU user %s failed", name)
