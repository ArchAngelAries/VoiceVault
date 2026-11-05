"""
api/settings.py — Settings read/write endpoints.

API keys are never sent to the browser: GET returns a mask for every key that is set, and a
POST that sends the mask back keeps the stored key. Updates are checked against the known
settings (names, types, full paths) before anything is written.
"""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core import settings as store

router = APIRouter()


class SettingsPayload(BaseModel):
    data: dict[str, Any]


@router.get("/")
async def get_settings():
    return store.public(store.load())


@router.post("/")
async def update_settings(payload: SettingsPayload):
    current = store.load()
    try:
        changes = store.validate_update(payload.data, current)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    try:
        saved = store.save({**current, **changes})
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Settings could not be written: {e.strerror or e}")
    store.LAST_RESET.clear()
    return {"ok": True, "settings": store.public(saved)}


@router.post("/reset")
async def reset_settings():
    """Reset everything except API keys, so a reset never loses credentials."""
    current = store.load()
    keep = {k: current.get(k, "") for k in store.SECRET_KEYS + ("igdb_client_id", "google_client_id")}
    saved = store.save({**store.DEFAULTS, **keep})
    return {"ok": True, "settings": store.public(saved)}
