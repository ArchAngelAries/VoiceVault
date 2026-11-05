from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api import tts
from engines import voice_presets as store


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "PRESETS_DIR", tmp_path / "voice-presets")
    return tmp_path / "voice-presets"


@pytest.fixture
def client(library):
    app = FastAPI()
    app.include_router(tts.router, prefix="/api/tts")
    return TestClient(app)


def payload(**updates):
    value = {
        "name": "  Studio voice  ",
        "engine_id": "  breeze  ",
        "model_id": "missing/model@revision",
        "speech_mode": "description",
        "voice_description": "warm, low voice",
        "reference_id": None,
        "reference_text": "",
        "language": "en",
        "delivery": {"emotion": "calm", "style": "dramatic"},
        "generation": {"temperature": 0.7, "seed": 12},
    }
    value.update(updates)
    return value


def test_api_round_trip_preserves_identity_and_replaces_atomically(client, library):
    created = client.post("/api/tts/presets", json=payload())
    assert created.status_code == 200
    first = created.json()
    assert first["name"] == "Studio voice"
    assert first["engine_id"] == "breeze"
    assert first["model_id"] == "missing/model@revision"
    assert first["delivery"] == {"emotion": "calm", "style": "dramatic", "expression": None}
    assert first["generation"] == {
        "temperature": 0.7, "top_p": 0.9, "repetition_penalty": 1.1,
        "max_new_tokens": 4000, "seed": 12,
    }
    assert list(library.glob("*.json")) == [library / f"{first['id']}.json"]
    assert client.get("/api/tts/presets").json() == {"items": [first]}

    changed = client.put(f"/api/tts/presets/{first['id']}", json=payload(
        name="Renamed", model_id="also-missing", speech_mode="reference", reference_id="ref:old-id"))
    assert changed.status_code == 200
    second = changed.json()
    assert second["id"] == first["id"]
    assert second["created_at"] == first["created_at"]
    assert second["updated_at"] >= first["updated_at"]
    assert second["model_id"] == "also-missing"
    assert second["reference_id"] == "ref:old-id"

    assert client.delete(f"/api/tts/presets/{first['id']}").json() == {"ok": True}
    assert client.get("/api/tts/presets").json() == {"items": []}
    assert client.delete(f"/api/tts/presets/{first['id']}").status_code == 404


@pytest.mark.parametrize("invalid", [
    {"name": "   "}, {"name": "x" * 121}, {"engine_id": " "}, {"engine_id": "x" * 101},
    {"model_id": ""}, {"model_id": "x" * 501}, {"speech_mode": "auto"},
    {"voice_description": "x" * 1001}, {"reference_id": "x" * 501},
    {"reference_text": "x" * 2001}, {"language": "x" * 41},
    {"generation": {"temperature": 2.01}}, {"generation": {"top_p": 0.09}},
    {"generation": {"repetition_penalty": 2.01}}, {"generation": {"max_new_tokens": 99}},
    {"generation": {"seed": -1}}, {"delivery": {"credential": "never"}},
    {"unexpected": "field"},
])
def test_invalid_fields_are_rejected(client, invalid):
    assert client.post("/api/tts/presets", json=payload(**invalid)).status_code == 422


def test_create_is_capped_at_one_hundred(client):
    for index in range(store.MAX_PRESETS):
        result = client.post("/api/tts/presets", json=payload(name=f"Voice {index}"))
        assert result.status_code == 200
    assert client.post("/api/tts/presets", json=payload(name="overflow")).status_code == 409
    assert len(client.get("/api/tts/presets").json()["items"]) == 100


def test_corrupt_record_fails_closed_for_reads_and_mutations(client, library):
    library.mkdir()
    record_id = str(uuid.uuid4())
    (library / f"{record_id}.json").write_text("{broken", encoding="utf-8")
    assert client.get("/api/tts/presets").status_code == 500
    assert client.post("/api/tts/presets", json=payload()).status_code == 500
    assert client.put(f"/api/tts/presets/{record_id}", json=payload()).status_code == 500
    assert client.delete(f"/api/tts/presets/{record_id}").status_code == 500
    assert (library / f"{record_id}.json").read_text(encoding="utf-8") == "{broken"


def test_store_rejects_unknown_fields_without_persisting(library):
    with pytest.raises(ValidationError):
        store.VoicePresetInput.model_validate(payload(api_key="secret"))
    assert not library.exists()

