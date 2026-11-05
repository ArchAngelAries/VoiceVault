"""HTTP-level tests for /api/tts, using a fake engine so no GPU or weights are needed."""
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

import api.tts as tts_api
from core import settings as app_settings
from engines.base import EngineCapabilities, EngineError, ModelInfo, ModelState, SynthesisResult, TTSEngine
from engines.manager import EngineManager
from engines.registry import ModelRegistry


class FakeEngine(TTSEngine):
    engine_id = "fake"
    display_name = "Fake engine"
    capabilities = EngineCapabilities(languages=["en"], voice_description=True, emotion_tags=["laugh"],
        delivery_emotions=["angry"], delivery_styles=["whisper"], delivery_expressions=["laugh"],
        delivery_tag_map={"emotion:angry": "angry", "style:whisper": "whisper"})

    def identify(self, model_dir):
        if not (model_dir / "config.json").is_file():
            return None
        state = ModelState.BLOCKED if model_dir.name == "blocked-model" else ModelState.IDENTIFIED
        reason = "blocked for the test" if state == ModelState.BLOCKED else None
        return ModelInfo(id="", engine_id=self.engine_id, family="Fake", architecture="fake",
                         path=str(model_dir), size_bytes=0, state=state, reason=reason)

    def runtime_problem(self):
        return None

    def load(self, model, device="cuda", dtype="bfloat16"):
        self._model = model

    def unload(self):
        self._model = None

    def synthesize(self, request):
        self.last_request = request
        if request.text == "boom":
            raise EngineError("Fake engine refused this text.")
        n = 2400
        return SynthesisResult(samples=np.sin(np.arange(n) / 10).astype(np.float32) * 0.3,
                               sample_rate=24000, meta={"engine": "fake", "device": "cpu"})


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = FakeEngine()
    models = tmp_path / "models"
    (models / "fake-model").mkdir(parents=True)
    (models / "fake-model" / "config.json").write_text("{}", encoding="utf-8")
    (models / "blocked-model").mkdir(parents=True)
    (models / "blocked-model" / "config.json").write_text("{}", encoding="utf-8")

    registry = ModelRegistry([engine], [models])
    registry.discover()
    manager = EngineManager({"fake": engine}, registry)
    outputs = tmp_path / "outputs"

    monkeypatch.setattr(tts_api, "_ENGINES", [engine])
    monkeypatch.setattr(tts_api, "_services", {"registry": registry, "manager": manager})
    monkeypatch.setattr(app_settings, "load", lambda: {"outputs_dir": str(outputs)})

    from main import app
    return TestClient(app), outputs


def test_engines_endpoint_reports_capabilities(client):
    c, _ = client

    body = c.get("/api/tts/engines").json()

    assert body["engines"][0]["id"] == "fake"
    assert body["engines"][0]["capabilities"]["voice_description"] is True
    assert body["engines"][0]["model_count"] == 2
    assert body["engines"][0]["usable_model_count"] == 1  # blocked-model is not usable


def test_models_endpoint_lists_discovered_models_with_state(client):
    c, _ = client

    models = {m["id"]: m for m in c.get("/api/tts/models").json()["models"]}

    assert models["fake:fake-model"]["state"] == "integrated"
    assert models["fake:blocked-model"]["state"] == "blocked"
    assert models["fake:blocked-model"]["reason"] == "blocked for the test"


def test_synthesize_writes_a_wav_and_serves_it(client):
    c, outputs = client

    r = c.post("/api/tts/synthesize", json={"model_id": "fake:fake-model", "text": "Hello there."})

    assert r.status_code == 200
    rec = r.json()
    assert rec["engine"] == "fake"
    assert rec["duration_s"] == 0.1
    assert (outputs / rec["file"]).is_file()
    sidecar = json.loads((outputs / rec["file"].replace(".wav", ".json")).read_text(encoding="utf-8"))
    assert sidecar["text"] == "Hello there."

    audio = c.get(rec["url"])
    assert audio.status_code == 200
    assert audio.headers["content-type"].startswith("audio/wav")


def test_synthesize_resolves_delivery_before_engine_and_records_warnings(client):
    c, _ = client
    engine = tts_api._ENGINES[0]
    response = c.post("/api/tts/synthesize", json={
        "model_id": "fake:fake-model", "text": "Hello.",
        "delivery": {"emotion": "angry", "style": "dramatic", "expression": "laugh"},
    })
    assert response.status_code == 200
    assert engine.last_request.text == "<angry> <laugh> Hello."
    record = response.json()
    assert record["meta"]["delivery"] == {"emotion": "angry", "style": None, "expression": "laugh"}
    assert record["meta"]["delivery_warnings"] == [
        "Style 'dramatic' is not supported by Fake engine in this mode."
    ]


def test_synthesize_unknown_model_is_404(client):
    c, _ = client

    r = c.post("/api/tts/synthesize", json={"model_id": "fake:missing", "text": "hi"})

    assert r.status_code == 404


def test_synthesize_blocked_model_is_refused_with_reason(client):
    c, _ = client

    r = c.post("/api/tts/synthesize", json={"model_id": "fake:blocked-model", "text": "hi"})

    assert r.status_code == 422
    assert "blocked for the test" in r.json()["detail"]


def test_engine_errors_reach_the_user_as_422(client):
    c, _ = client

    r = c.post("/api/tts/synthesize", json={"model_id": "fake:fake-model", "text": "boom"})

    assert r.status_code == 422
    assert r.json()["detail"] == "Fake engine refused this text."


def test_request_validation_rejects_out_of_range_settings(client):
    c, _ = client

    r = c.post("/api/tts/synthesize", json={"model_id": "fake:fake-model", "text": "hi", "temperature": 9})

    assert r.status_code == 422


def test_audio_endpoint_rejects_unsafe_names(client):
    c, _ = client

    assert c.get("/api/tts/audio/notes.txt").status_code == 400
    assert c.get("/api/tts/audio/missing.wav").status_code == 404


def test_history_lists_saved_generations(client):
    c, _ = client
    c.post("/api/tts/synthesize", json={"model_id": "fake:fake-model", "text": "first"})

    items = c.get("/api/tts/history").json()["items"]

    assert [i["text"] for i in items] == ["first"]


def test_unload_endpoint_releases_the_model(client):
    c, _ = client
    c.post("/api/tts/synthesize", json={"model_id": "fake:fake-model", "text": "hi"})

    r = c.post("/api/tts/unload")

    assert r.json() == {"ok": True, "loaded_model_id": None}
