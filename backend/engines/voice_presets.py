"""Durable, bounded storage for reusable authored TTS settings."""
from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from typing_extensions import Annotated

from core.paths import DATA_DIR
from engines.delivery import Delivery

PRESETS_DIR = DATA_DIR / "voice-presets"
MAX_PRESETS = 100
_LOCK = threading.RLock()


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GenerationSettings(_StrictModel):
    temperature: float = Field(default=0.4, ge=0.1, le=2.0)
    top_p: float = Field(default=0.9, ge=0.1, le=1.0)
    repetition_penalty: float = Field(default=1.1, ge=1.0, le=2.0)
    max_new_tokens: int = Field(default=4000, ge=100, le=16000)
    seed: Optional[int] = Field(default=0, ge=0, le=2**32 - 1)


TrimmedName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
TrimmedEngine = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class VoicePresetInput(_StrictModel):
    name: TrimmedName
    engine_id: TrimmedEngine
    model_id: str = Field(min_length=1, max_length=500)
    speech_mode: str = Field(pattern=r"^(description|reference)$")
    voice_description: str = Field(default="", max_length=1000)
    reference_id: Optional[str] = Field(default=None, max_length=500)
    reference_text: str = Field(default="", max_length=2000)
    language: str = Field(default="", max_length=40)
    delivery: Delivery = Field(default_factory=Delivery)
    generation: GenerationSettings = Field(default_factory=GenerationSettings)

    @field_validator("model_id")
    @classmethod
    def model_identity_nonempty(cls, value: str) -> str:
        if not value:
            raise ValueError("model_id must not be empty")
        return value


class VoicePreset(VoicePresetInput):
    id: str
    created_at: str
    updated_at: str


class PresetStoreError(Exception):
    """The on-disk preset library cannot safely complete the requested action."""


class PresetNotFound(PresetStoreError):
    pass


class PresetLimitReached(PresetStoreError):
    pass


class CorruptPresetStore(PresetStoreError):
    pass


def _read_all() -> list[VoicePreset]:
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    items: list[VoicePreset] = []
    for path in sorted(PRESETS_DIR.glob("*.json")):
        try:
            record = VoicePreset.model_validate_json(path.read_text(encoding="utf-8"))
            if path.stem != record.id or str(uuid.UUID(record.id)) != record.id:
                raise ValueError("filename and preset id do not match")
            items.append(record)
        except Exception as exc:
            raise CorruptPresetStore(f"Saved voice preset record '{path.name}' is corrupt: {exc}") from exc
    if len(items) > MAX_PRESETS:
        raise CorruptPresetStore(f"Saved voice preset library exceeds the {MAX_PRESETS} record limit.")
    items.sort(key=lambda item: (item.name.casefold(), item.created_at, item.id))
    return items


def list_presets() -> list[dict]:
    with _LOCK:
        return [item.model_dump(mode="json") for item in _read_all()]


def create_preset(body: VoicePresetInput) -> dict:
    with _LOCK:
        existing = _read_all()
        if len(existing) >= MAX_PRESETS:
            raise PresetLimitReached(f"You can save up to {MAX_PRESETS} voice presets.")
        now = _now()
        record = VoicePreset.model_validate({**body.model_dump(mode="json"), "id": str(uuid.uuid4()),
                                             "created_at": now, "updated_at": now})
        _write(record)
        return record.model_dump(mode="json")


def replace_preset(preset_id: str, body: VoicePresetInput) -> dict:
    safe_id = _valid_id(preset_id)
    with _LOCK:
        existing = _read_all()
        current = next((item for item in existing if item.id == safe_id), None)
        if current is None:
            raise PresetNotFound("Saved voice preset not found.")
        record = VoicePreset.model_validate({**body.model_dump(mode="json"), "id": safe_id,
                                             "created_at": current.created_at, "updated_at": _now()})
        _write(record)
        return record.model_dump(mode="json")


def delete_preset(preset_id: str) -> None:
    safe_id = _valid_id(preset_id)
    with _LOCK:
        existing = _read_all()
        if not any(item.id == safe_id for item in existing):
            raise PresetNotFound("Saved voice preset not found.")
        (PRESETS_DIR / f"{safe_id}.json").unlink()


def _valid_id(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise PresetNotFound("Saved voice preset not found.") from exc
    if str(parsed) != value:
        raise PresetNotFound("Saved voice preset not found.")
    return value


def _write(record: VoicePreset) -> None:
    path = PRESETS_DIR / f"{record.id}.json"
    temp = PRESETS_DIR / f".{record.id}.{uuid.uuid4().hex}.tmp"
    try:
        with temp.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(record.model_dump_json(indent=2))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
