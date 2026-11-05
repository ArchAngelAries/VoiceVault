"""
api/models_manager.py — /api/models: the model catalog, licence acceptance, downloads and removal.

Models come from their official sources onto the user's PC; VoiceVault never redistributes weights.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from models import manager

router = APIRouter()


class DownloadIn(BaseModel):
    id: str = Field(max_length=60)
    accept_license: bool = False
    options: dict[str, bool] = Field(default_factory=dict)   # Drama 3: also_max, keep_checkpoint


def _call(fn, *args):
    try:
        return fn(*args)
    except manager.ModelError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/catalog")
async def catalog():
    return await asyncio.to_thread(manager.catalog)


@router.post("/catalog/sizes")
async def fill_sizes():
    """Look up download sizes on the Hub for entries that don't have one yet (network)."""
    from models.catalog import CATALOG

    done, failed = {}, []
    for e in CATALOG:
        if any(s.kind == "manual" for s in e.sources):
            continue
        try:
            done[e.id] = await asyncio.to_thread(manager.sizes, e.id)
        except Exception:
            failed.append(e.id)
    return {"sizes": done, "failed": failed}


@router.post("/download")
async def download(body: DownloadIn):
    return await asyncio.to_thread(_call, manager.start, body.id, body.accept_license, body.options)


@router.get("/jobs")
async def jobs():
    return {"jobs": manager.jobs()}


@router.post("/jobs/{job_id}/cancel")
async def cancel(job_id: str):
    return _call(manager.cancel, job_id)


@router.delete("/installed/{entry_id}")
async def remove(entry_id: str):
    return await asyncio.to_thread(_call, manager.remove, entry_id)


@router.post("/rescan")
async def rescan():
    manager.invalidate()
    await asyncio.to_thread(manager._rescan)
    return await asyncio.to_thread(manager.catalog)
