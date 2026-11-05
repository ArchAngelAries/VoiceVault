"""Reference clips for cloning: upload checks, storage, and passing them to synthesis. Fake engine, no GPU."""
import numpy as np
import pytest
from fastapi.testclient import TestClient

import api.tts as tts_api
from core import settings as app_settings
from engines.audio_io import write_wav
from engines.base import EngineCapabilities, ModelInfo, ModelState, SynthesisResult, TTSEngine
from engines.manager import EngineManager
from engines.registry import ModelRegistry


class CloneEngine(TTSEngine):
    engine_id = "clone"
    display_name = "Clone test engine"
    capabilities = EngineCapabilities(languages=["en"], reference_audio=True)
    seen = []

    def identify(self, model_dir):
        if not (model_dir / "config.json").is_file():
            return None
        return ModelInfo(id="", engine_id=self.engine_id, family="Clone", architecture="x", path=str(model_dir),
                         size_bytes=0, state=ModelState.IDENTIFIED)

    def runtime_problem(self):
        return None

    def load(self, model, device="cuda", dtype="bfloat16"):
        pass

    def unload(self):
        pass

    def synthesize(self, request):
        CloneEngine.seen.append(request)
        return SynthesisResult(samples=np.zeros(2400, np.float32), sample_rate=24000, meta={"mode": "clone"})


@pytest.fixture
def client(monkeypatch, tmp_path):
    CloneEngine.seen = []
    models = tmp_path / "models" / "clone-model"
    models.mkdir(parents=True)
    (models / "config.json").write_text("{}", encoding="utf-8")
    engine = CloneEngine()
    registry = ModelRegistry([engine], [tmp_path / "models"])
    registry.discover()
    manager = EngineManager({"clone": engine}, registry)
    monkeypatch.setattr(tts_api, "_ENGINES", [engine])
    monkeypatch.setattr(tts_api, "_services", {"registry": registry, "manager": manager})
    monkeypatch.setattr(tts_api, "REFERENCE_DIR", tmp_path / "references")
    monkeypatch.setattr(app_settings, "load", lambda: {"outputs_dir": str(tmp_path / "outputs")})
    from main import app
    return TestClient(app), tmp_path


def _wav_bytes(tmp, seconds):
    path = tmp / f"clip_{seconds}.wav"
    write_wav(path, np.zeros(int(seconds * 24000), np.float32) + 0.01, 24000)
    return path.read_bytes()


def test_a_valid_reference_is_stored_and_returns_an_id(client):
    c, tmp = client

    r = c.post("/api/tts/references", files={"file": ("ref.wav", _wav_bytes(tmp, 5), "audio/wav")})

    assert r.status_code == 200
    body = r.json()
    assert len(body["reference_id"]) == 32 and body["sample_rate"] == 24000
    assert body["duration_s"] == pytest.approx(5.0, abs=0.05)
    assert (tmp / "references" / f"{body['reference_id']}.wav").is_file()
    assert (tmp / "references" / f"{body['reference_id']}.hq44.wav").is_file()  # full-band copy for 44.1 kHz engines


@pytest.mark.parametrize("seconds", [1, 70])  # storage allows 3-60 s; engines enforce their own maximum
def test_out_of_range_references_are_refused(client, seconds):
    c, tmp = client

    r = c.post("/api/tts/references", files={"file": ("ref.wav", _wav_bytes(tmp, seconds), "audio/wav")})

    assert r.status_code == 422
    assert "seconds" in r.json()["detail"]


def test_a_non_audio_reference_is_refused(client):
    c, _ = client

    r = c.post("/api/tts/references", files={"file": ("notes.txt", b"not audio at all", "text/plain")})

    assert r.status_code == 422


def test_synthesis_passes_the_stored_reference_and_its_transcript(client):
    c, tmp = client
    ref_id = c.post("/api/tts/references", files={"file": ("ref.wav", _wav_bytes(tmp, 5), "audio/wav")}).json()["reference_id"]

    r = c.post("/api/tts/synthesize", json={"model_id": "clone:clone-model", "text": "Say this.",
                                            "reference_id": ref_id, "reference_text": "Transcript here.",
                                            "language": "English"})

    assert r.status_code == 200
    request = CloneEngine.seen[0]
    assert request.reference_audio == tmp / "references" / f"{ref_id}.wav"
    assert request.reference_text == "Transcript here."
    assert request.language == "English"


def test_unknown_or_malformed_reference_ids_are_refused(client):
    c, _ = client

    missing = c.post("/api/tts/synthesize", json={"model_id": "clone:clone-model", "text": "x",
                                                  "reference_id": "0" * 32})
    malformed = c.post("/api/tts/synthesize", json={"model_id": "clone:clone-model", "text": "x",
                                                    "reference_id": "../../etc/passwd"})

    assert missing.status_code == 404
    assert malformed.status_code == 400
