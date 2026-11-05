"""HTTP tests for /api/asr with a fake transcriber, so no GPU or model download is needed."""
import numpy as np
import pytest
from fastapi.testclient import TestClient

import api.asr as asr_api
from asr import service as asr_service
from engines.audio_io import write_wav


def test_api_and_tts_service_use_the_same_whisper_singleton():
    assert asr_api._transcriber is asr_service.get_transcriber()


class FakeTranscriber:
    model_id = "fake/whisper"
    device = "cuda"
    dtype = "float16"
    loaded = True

    def runtime_problem(self):
        return None

    def is_cached(self):
        return True

    def load(self, *args, **kwargs):
        self.loaded = True

    def unload(self):
        self.loaded = False

    def transcribe(self, samples, language=None, timestamps=True):
        return {"text": "fake text", "language": language, "duration_s": len(samples) / 16000,
                "segments": [{"start": 0.0, "end": 1.0, "text": "fake text"}], "model": self.model_id,
                "device": self.device, "dtype": self.dtype, "transcribe_seconds": 0.01, "real_time_factor": 0.01}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(asr_api, "_transcriber", FakeTranscriber())
    monkeypatch.setattr(asr_api, "TMP_DIR", tmp_path / "tmp")
    from main import app
    return TestClient(app), tmp_path


def test_status_reports_model_and_cache(client):
    c, _ = client

    body = c.get("/api/asr/status").json()

    assert body["model"] == "fake/whisper"
    assert body["cached"] is True
    assert body["runtime_problem"] is None


def test_upload_returns_text_and_segments(client):
    c, tmp = client
    wav = tmp / "clip.wav"
    write_wav(wav, np.zeros(24000, np.float32), 24000)

    r = c.post("/api/asr/transcribe", files={"file": ("clip.wav", wav.read_bytes(), "audio/wav")})

    assert r.status_code == 200
    assert r.json()["text"] == "fake text"
    assert r.json()["segments"][0]["end"] == 1.0


def test_non_audio_upload_is_rejected_with_422(client):
    c, _ = client

    r = c.post("/api/asr/transcribe", files={"file": ("notes.txt", b"hello there", "text/plain")})

    assert r.status_code == 422


def test_empty_upload_is_rejected(client):
    c, _ = client

    r = c.post("/api/asr/transcribe", files={"file": ("empty.wav", b"", "audio/wav")})

    assert r.status_code == 422


def test_temporary_upload_is_removed_after_the_request(client):
    c, tmp = client
    wav = tmp / "clip.wav"
    write_wav(wav, np.zeros(2400, np.float32), 24000)

    c.post("/api/asr/transcribe", files={"file": ("clip.wav", wav.read_bytes(), "audio/wav")})

    assert list((tmp / "tmp").glob("*")) == []
