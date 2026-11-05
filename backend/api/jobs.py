"""
api/jobs.py — What the app is busy with right now, in one cheap call (for the top-bar job strip).

GET /api/jobs/active returns queued and running work only:
    [{source, label, state, elapsed_s, progress?, ...}]
Sources: Music Studio jobs, Harvest jobs, model downloads, and speech-model GPU work.
Each source is read from the in-process service only if it already exists (nothing is built or
loaded here), and each is wrapped in try/except so one broken source never breaks the strip.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from fastapi import APIRouter

log = logging.getLogger(__name__)
router = APIRouter()

ACTIVE = {"queued", "running"}


def _elapsed(stamp: Any) -> Optional[float]:
    """Seconds since an ISO time (naive = local time) or a time.time() float."""
    if stamp is None or stamp == "":
        return None
    try:
        if isinstance(stamp, (int, float)):
            return max(0.0, round(time.time() - float(stamp), 1))
        dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
        return max(0.0, round((now - dt).total_seconds(), 1))
    except (TypeError, ValueError):
        return None


# ── Sources (each returns a list of active items; may raise) ────────────────

def _music_jobs() -> list[dict]:
    from api import music as music_api

    studio = music_api._studio.get("s")   # never build the studio just to look at it
    if studio is None:
        return []
    out = []
    for j in studio.jobs():
        if j.get("status") not in ACTIVE:
            continue
        kind = str(j.get("kind") or "job").replace("_", " ")
        item = {"source": "music", "id": j.get("id"), "label": f"Music Studio: {kind}", "state": j["status"],
                "elapsed_s": _elapsed(j.get("started") or j.get("created")), "route": "/music"}
        if j["status"] == "queued":
            item["position"] = studio.queue_position(j["id"])
        if j.get("message"):
            item["detail"] = str(j["message"])[:120]
        out.append(item)
    return out


def _harvest_jobs() -> list[dict]:
    from api import harvest as harvest_api

    svc = harvest_api.service   # None until Harvest has been used this session
    if svc is None:
        return []
    out = []
    for j in svc.jobs():
        if j.get("state") not in ACTIVE:
            continue
        stage = j.get("stage") or ("Waiting in queue" if j["state"] == "queued" else "Working")
        out.append({"source": "harvest", "id": j.get("id"), "label": f"Harvest: {stage}", "state": j["state"],
                    "elapsed_s": _elapsed(j.get("created_at")), "route": "/harvest"})
    return out


def _model_downloads() -> list[dict]:
    from models import manager
    from models.catalog import BY_ID

    out = []
    for j in manager.jobs():
        if j.get("state") not in ACTIVE:
            continue
        entry = BY_ID.get(j.get("entry"))
        name = entry.name if entry is not None else (j.get("entry") or "model")
        item = {"source": "models", "id": j.get("id"), "label": f"Downloading {name}", "state": j["state"],
                "elapsed_s": _elapsed(j.get("created")), "route": "/settings"}
        total, done = j.get("total_bytes") or 0, j.get("done_bytes") or 0
        if total > 0:
            item["progress"] = max(0.0, min(1.0, round(done / total, 3)))
        out.append(item)
    return out


def _speech_busy() -> list[dict]:
    """A speech/ASR model is loading or working when the shared speech lock is held by someone else."""
    from api import tts as tts_api
    from core.gpu_scheduler import speech_resource_lock

    if not speech_resource_lock.acquire(blocking=False):
        manager = tts_api._services.get("manager")
        loaded = getattr(manager, "loaded_model_id", None) if manager is not None else None
        label = f"Speech: {loaded}" if loaded else "Speech model loading"
        return [{"source": "speech", "id": "speech", "label": label, "state": "running", "elapsed_s": None,
                 "route": "/generate", "model_id": loaded}]
    speech_resource_lock.release()
    return []


SOURCES: list[tuple[str, Callable[[], list[dict]]]] = [
    ("music", _music_jobs), ("harvest", _harvest_jobs), ("models", _model_downloads), ("speech", _speech_busy),
]


def active_jobs() -> list[dict]:
    items: list[dict] = []
    for name, source in SOURCES:
        if name == "speech" and any(i["source"] == "music" and i["state"] == "running" for i in items):
            continue   # the Music Studio holds the same GPU lock while it runs; don't report it twice
        try:
            items.extend(source())
        except Exception:   # one broken source must never break the strip
            log.debug("Active-jobs source %s failed", name, exc_info=True)
    return items


@router.get("/active")
def get_active_jobs():
    # Plain def: FastAPI runs it in a worker thread, so the lock probe never blocks the event loop.
    return active_jobs()
