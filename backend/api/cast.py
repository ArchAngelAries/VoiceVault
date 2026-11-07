"""The Cast (reusable characters) and user personas (who the user plays in a chat).

A cast member is a full character profile (the same shape a chat uses) plus where its facts came
from. Each save keeps the previous version, so an AI rewrite can always be undone. Personas are
small profiles of the user's role-play self. Both are JSON files in the profile's state folder.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from core.paths import DATA_DIR
from .converse import Profile, UserPersona

router = APIRouter()
personas_router = APIRouter()
CAST_DIR = DATA_DIR / "cast"
PERSONA_DIR = DATA_DIR / "personas"
MAX_VERSIONS = 20


# ── Models ───────────────────────────────────────────────────────

class Source(BaseModel):
    """One piece of evidence a persona was built from."""
    title: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=1, max_length=2048)
    licence: str = Field("", max_length=120)
    excerpt: str = Field("", max_length=4000)
    fetched_at: str = Field("", max_length=40)

    @field_validator("url")
    @classmethod
    def web_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("A source must be an http(s) link.")
        return value


class CastPayload(BaseModel):
    model_config = {"extra": "forbid"}
    kind: Literal["established", "original"] = "original"
    variant: str = Field("", max_length=200)          # e.g. "Twelfth Doctor" or "Rick C-137"
    profile: Profile
    sources: list[Source] = Field(default_factory=list, max_length=30)
    open_questions: list[str] = Field(default_factory=list, max_length=20)
    review_state: Literal["draft", "reviewed"] = "draft"
    note: str = Field("", max_length=200)              # why this version was saved, shown in history

    @field_validator("open_questions")
    @classmethod
    def short_questions(cls, values: list[str]) -> list[str]:
        values = [value.strip() for value in values if value.strip()]
        if any(len(value) > 300 for value in values):
            raise ValueError("Open questions must be at most 300 characters each.")
        return values


class RestoreRequest(BaseModel):
    version: str = Field(min_length=1, max_length=80, pattern=r"^[0-9TZ._-]+$")


class DefaultPersona(BaseModel):
    id: str | None = Field(None, max_length=80)


# ── Storage helpers ──────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(item_id: str, what: str) -> str:
    try:
        return str(uuid.UUID(item_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(404, f"{what} not found.") from None


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=1)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _read(path: Path, what: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError
        return value
    except FileNotFoundError:
        raise HTTPException(404, f"{what} not found.") from None
    except (OSError, ValueError):
        raise HTTPException(500, f"Saved {what.lower()} could not be read.") from None


def _all(folder: Path) -> list[dict]:
    items = []
    if not folder.is_dir():
        return items
    for path in folder.glob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and value.get("id") == path.stem:
                items.append(value)
        except (OSError, ValueError):
            continue
    return items


def _cast_path(cast_id: str) -> Path:
    return CAST_DIR / f"{_canonical(cast_id, 'Cast member')}.json"


def _history_dir(cast_id: str) -> Path:
    return CAST_DIR / "history" / _canonical(cast_id, "Cast member")


MAX_USER_VERSIONS = 50


def is_ai_note(note: str | None) -> bool:
    """Versions written by AI crafting or an agent (their notes say so)."""
    low = (note or "").lower()
    return "crafted" in low or "agent" in low or "with ai" in low


def _keep_version(current: dict, saving_note: str = "") -> None:
    """Copy the current state into history before it changes.

    History is bounded: the newest MAX_VERSIONS AI or agent versions are kept. Versions the user made
    are only ever trimmed by the user's own saves (MAX_USER_VERSIONS), so an agent saving in a loop
    can never push the user's work out of history.
    """
    folder = _history_dir(current["id"])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    _write(folder / f"{stamp}.json", current)
    ai_versions, user_versions = [], []
    for path in sorted(folder.glob("*.json")):
        try:
            note = json.loads(path.read_text(encoding="utf-8")).get("note", "")
        except (OSError, ValueError, AttributeError):
            note = ""
        (ai_versions if is_ai_note(note) else user_versions).append(path)
    for old in ai_versions[:-MAX_VERSIONS]:
        old.unlink(missing_ok=True)
    if not is_ai_note(saving_note):
        for old in user_versions[:-MAX_USER_VERSIONS]:
            old.unlink(missing_ok=True)


def _member(payload: CastPayload, cast_id: str, created_at: str) -> dict:
    profile = payload.profile.model_dump()
    profile["cast_id"] = cast_id
    return {"id": cast_id, "created_at": created_at, "updated_at": _now(),
            "kind": payload.kind, "variant": payload.variant.strip(), "profile": profile,
            "sources": [source.model_dump() for source in payload.sources],
            "open_questions": payload.open_questions, "review_state": payload.review_state,
            "note": payload.note.strip()}


def find_by_catalog(identity: str) -> dict | None:
    for member in _all(CAST_DIR):
        if (member.get("profile") or {}).get("catalog_identity") == identity:
            return member
    return None


def list_cast(q: str | None = None) -> list[dict]:
    members = _all(CAST_DIR)
    if q and q.strip():
        needle = q.strip().lower()
        members = [m for m in members if needle in " ".join(
            str(v) for v in ((m.get("profile") or {}).get("name"), m.get("variant"),
                             (m.get("profile") or {}).get("description"))).lower()]
    return sorted(members, key=lambda m: ((m.get("profile") or {}).get("name") or "").lower())


def create_member(payload: CastPayload) -> tuple[dict, bool]:
    """Create a cast member. A catalog character already in the Cast returns the existing one."""
    identity = payload.profile.catalog_identity
    if identity and (existing := find_by_catalog(identity)):
        return existing, False
    cast_id = str(uuid.uuid4())
    member = _member(payload, cast_id, _now())
    _write(_cast_path(cast_id), member)
    return member, True


def update_member(cast_id: str, payload: CastPayload) -> dict:
    path = _cast_path(cast_id)
    current = _read(path, "Cast member")
    _keep_version(current, payload.note)
    member = _member(payload, current["id"], current.get("created_at") or _now())
    _write(path, member)
    return member


# ── Cast routes ──────────────────────────────────────────────────

@router.get("")
async def get_cast(q: str | None = None):
    return {"cast": list_cast(q)}


@router.post("")
async def post_cast(payload: CastPayload):
    member, created = create_member(payload)
    return {**member, "existing": not created}


@router.get("/candidates")
async def cast_candidates():
    """Favourite characters that aren't in the Cast yet: the "craft personas for my favourites" list."""
    from db.client import get_db
    db = await get_db()
    favourites = await db.collection.find_unique(where={"slug": "favorites"})
    if not favourites:
        return {"candidates": []}
    items = await db.collectionitem.find_many(where={"collection_id": favourites.id, "item_type": "character"})
    in_cast = {(m.get("profile") or {}).get("catalog_identity") for m in _all(CAST_DIR)}
    return {"candidates": [{"item_id": i.item_id, "name": i.title, "source": i.source,
                            "image_url": i.cover_url} for i in items if i.item_id not in in_cast]}


@router.get("/{cast_id}")
async def get_member(cast_id: str):
    return _read(_cast_path(cast_id), "Cast member")


@router.put("/{cast_id}")
async def put_member(cast_id: str, payload: CastPayload):
    return update_member(cast_id, payload)


@router.get("/{cast_id}/versions")
async def get_versions(cast_id: str):
    folder = _history_dir(cast_id)
    _read(_cast_path(cast_id), "Cast member")
    versions = []
    for path in sorted(folder.glob("*.json"), reverse=True) if folder.is_dir() else []:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        versions.append({"version": path.stem, "updated_at": value.get("updated_at"),
                         "review_state": value.get("review_state"), "note": value.get("note", ""),
                         "name": (value.get("profile") or {}).get("name", "")})
    return {"versions": versions}


@router.post("/{cast_id}/restore")
async def restore_version(cast_id: str, body: RestoreRequest):
    path = _cast_path(cast_id)
    current = _read(path, "Cast member")
    old = _read(_history_dir(cast_id) / f"{body.version}.json", "Version")
    _keep_version(current)
    restored = {**old, "id": current["id"], "updated_at": _now(), "note": f"Restored {body.version}"}
    _write(path, restored)
    return restored


@router.delete("/{cast_id}")
async def delete_member(cast_id: str):
    path = _cast_path(cast_id)
    _read(path, "Cast member")
    path.unlink(missing_ok=True)
    folder = _history_dir(cast_id)
    if folder.is_dir():
        for old in folder.glob("*.json"):
            old.unlink(missing_ok=True)
        try:
            folder.rmdir()
        except OSError:
            pass
    return {"deleted": True}


# ── Persona routes ───────────────────────────────────────────────

def _persona_path(persona_id: str) -> Path:
    return PERSONA_DIR / f"{_canonical(persona_id, 'Persona')}.json"


def _default_id() -> str | None:
    try:
        value = json.loads((PERSONA_DIR / "settings.json").read_text(encoding="utf-8"))
        default = value.get("default_id") if isinstance(value, dict) else None
        return default if default and _persona_path(default).exists() else None
    except (OSError, ValueError, HTTPException):
        return None


def list_personas() -> list[dict]:
    return sorted((p for p in _all(PERSONA_DIR)), key=lambda p: (p.get("name") or "").lower())


def save_persona(payload: UserPersona, persona_id: str | None = None) -> dict:
    if not payload.name.strip():
        raise HTTPException(422, "A persona needs a name.")
    if persona_id:
        path = _persona_path(persona_id)
        current = _read(path, "Persona")
        created_at = current.get("created_at") or _now()
    else:
        persona_id = str(uuid.uuid4())
        path = _persona_path(persona_id)
        created_at = _now()
    value = {**payload.model_dump(), "id": path.stem, "created_at": created_at, "updated_at": _now()}
    _write(path, value)
    return value


@personas_router.get("")
async def get_personas():
    return {"personas": list_personas(), "default_id": _default_id()}


@personas_router.post("")
async def post_persona(payload: UserPersona):
    return save_persona(payload)


@personas_router.put("/default")
async def put_default(body: DefaultPersona):
    if body.id:
        _read(_persona_path(body.id), "Persona")
    _write(PERSONA_DIR / "settings.json", {"default_id": body.id})
    return {"default_id": body.id}


@personas_router.get("/{persona_id}")
async def get_persona(persona_id: str):
    return _read(_persona_path(persona_id), "Persona")


@personas_router.put("/{persona_id}")
async def put_persona(persona_id: str, payload: UserPersona):
    return save_persona(payload, persona_id)


@personas_router.delete("/{persona_id}")
async def delete_persona(persona_id: str):
    path = _persona_path(persona_id)
    _read(path, "Persona")
    path.unlink(missing_ok=True)
    if _default_id() is None:
        _write(PERSONA_DIR / "settings.json", {"default_id": None})
    return {"deleted": True}


# AI crafting lives in its own module; mounted here so it shares the /api/cast prefix.
from .cast_craft import router as _craft_router  # noqa: E402
router.include_router(_craft_router)
