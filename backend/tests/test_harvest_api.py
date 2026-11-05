from __future__ import annotations

import threading
import time
import wave

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers
from starlette.datastructures import UploadFile as StarletteUploadFile
from tempfile import SpooledTemporaryFile

import api.harvest as harvest_api
from harvest.service import HarvestService


@pytest.fixture(autouse=True)
def stop_api_worker(monkeypatch):
    yield
    if harvest_api.service is not None:
        assert harvest_api.service.shutdown()


def test_lifespan_restart_allows_fresh_lazy_service(tmp_path, monkeypatch):
    monkeypatch.setattr(harvest_api, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(harvest_api, '_service_stopping', False)
    backend = HarvestService(tmp_path)
    monkeypatch.setattr(harvest_api, 'service', backend)
    async def direct_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)
    monkeypatch.setattr(harvest_api.asyncio, 'to_thread', direct_to_thread)
    _run(harvest_api.shutdown_harvest_jobs())
    assert not backend._worker.is_alive()
    harvest_api.startup_harvest_jobs()
    assert harvest_api.service is None
    assert not harvest_api._service_stopping
    monkeypatch.setattr(harvest_api, 'HarvestService', lambda: HarvestService(tmp_path))
    assert harvest_api._service() is not backend


def _run(coro):
    """Drive a route coroutine whose thread offloads are replaced with immediate test calls."""
    try:
        coro.send(None)
    except StopIteration as done:
        return done.value
    raise AssertionError("The route unexpectedly suspended on an external async operation.")


def test_harvest_routes_validate_cancel_queue_and_promote_reference(tmp_path, monkeypatch):
    import core.paths

    assert harvest_api.service is None  # importing the router must not initialize user data
    library = tmp_path / "library"
    monkeypatch.setattr(core.paths, "DATA_DIR", library)
    monkeypatch.setattr(harvest_api, "DATA_DIR", library)
    monkeypatch.setattr(harvest_api, "_service_stopping", False)
    async def direct_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)
    monkeypatch.setattr(harvest_api.asyncio, "to_thread", direct_to_thread)
    backend = HarvestService(tmp_path / "workspace")
    monkeypatch.setattr(harvest_api, "service", backend)

    with pytest.raises(HTTPException) as missing:
        _run(harvest_api.get_job("not-an-id"))
    assert missing.value.status_code == 404
    with pytest.raises(HTTPException) as bad_url:
        _run(harvest_api.download_audio(harvest_api.DownloadRequest(url="http://localhost/watch?v=abc", end_s=5)))
    assert bad_url.value.status_code == 400
    with pytest.raises(HTTPException) as missing_audio:
        _run(harvest_api.audio("f" * 32, "../secret.wav"))
    assert missing_audio.value.status_code == 404

    started, release = threading.Event(), threading.Event()

    def hold(job_id, job):
        started.set()
        release.wait(3)
        return {"source_id": "f" * 32}

    real_execute = backend._execute
    monkeypatch.setattr(backend, "_execute", hold)
    active = backend.add_source_job(operation="test", payload={})
    assert started.wait(2)
    for _ in range(7):
        backend.add_source_job(operation="test", payload={})
    with pytest.raises(HTTPException) as full:
        _run(harvest_api.download_audio(harvest_api.DownloadRequest(url="https://youtu.be/abc", end_s=5)))
    assert full.value.status_code == 429
    cancelled = _run(harvest_api.cancel_job(active["id"]))
    assert cancelled["stage"] == "Cancelling"
    release.set()
    for _ in range(40):
        status = backend.get_job(active["id"])
        if status["state"] == "cancelled":
            break
        time.sleep(.025)
    assert status["state"] == "cancelled"
    for _ in range(100):
        if all(item["state"] in {"completed", "failed", "cancelled"} for item in backend.jobs()):
            break
        time.sleep(.02)
    monkeypatch.setattr(backend, "_execute", real_execute)

    # Upload staging is request-owned and returns a queued job without touching the TTS library.
    stream = SpooledTemporaryFile()
    stream.write(b"fake media bytes")
    stream.seek(0)
    upload = StarletteUploadFile(file=stream, filename="voice.wav", headers=Headers())
    uploaded_job = _run(harvest_api.import_audio(upload))
    assert uploaded_job["state"] == "queued"
    for _ in range(100):
        if backend.get_job(uploaded_job["id"])["state"] in {"completed", "failed"}:
            break
        time.sleep(.02)
    from core.library import voices_dir
    if voices_dir().exists():
        assert not list(voices_dir().glob("*.wav"))

    sid, vid = "f" * 32, "1" * 32
    folder = backend.root / sid
    folder.mkdir()
    clip_name = f"{sid}_{vid[:12]}_none.wav"
    with wave.open(str(folder / clip_name), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x00\x00" * 24_000 * 4)
    with backend._lock:
        backend._state["sources"][sid] = {"id": sid, "name": "Local", "duration_s": 4,
            "created_at": "2026-10-10", "provenance": {"type": "upload"},
            "audio_url": f"/api/harvest/audio/{sid}/{sid}_preview.wav",
            "original_file": f"{sid}_original.wav", "preview_file": f"{sid}_preview.wav",
            "variants": [{"id": vid, "name": "Clip", "duration_s": 4, "file": clip_name,
                "original_file": clip_name, "audio_url": f"/api/harvest/audio/{sid}/{clip_name}",
                "method": "none", "start_s": 0, "end_s": 4,
                "provenance": {"source_id": sid, "start_s": 0, "end_s": 4}}]}
        backend._save()

    queued = _run(harvest_api.promote_reference(sid, vid, harvest_api.ReferenceRequest(transcript="four seconds of voice")))
    assert queued["state"] == "queued"
    for _ in range(100):
        result = backend.get_job(queued["id"])
        if result["state"] in {"completed", "failed"}:
            break
        time.sleep(.025)
    assert result["state"] == "completed", result.get("error")
    saved = result["result"]
    assert saved["reference_id"] and saved["source_id"] == sid and saved["variant_id"] == vid
    assert saved["transcript"] == "four seconds of voice"
    again = _run(harvest_api.promote_reference(sid, vid, harvest_api.ReferenceRequest(transcript="four seconds of voice")))
    for _ in range(100):
        second = backend.get_job(again["id"])
        if second["state"] in {"completed", "failed"}:
            break
        time.sleep(.025)
    assert second["state"] == "completed"
    assert second["result"]["reference_id"] == saved["reference_id"]

    monkeypatch.setattr(backend, "capabilities", lambda: {"asr": {"available": False, "reason": "cache missing"}})
    with pytest.raises(HTTPException) as asr_unavailable:
        _run(harvest_api.transcribe_variant(sid, vid))
    assert asr_unavailable.value.status_code == 503
