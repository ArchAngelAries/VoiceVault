from __future__ import annotations

import json
import shutil
import threading
import wave

import pytest

from harvest.service import HarvestError, HarvestService


@pytest.fixture(autouse=True)
def stop_test_workers(monkeypatch):
    services = []
    original = HarvestService.__init__

    def tracked_init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        services.append(self)

    monkeypatch.setattr(HarvestService, "__init__", tracked_init)
    yield
    for service in services:
        assert service.shutdown()


def test_link_validation_accepts_public_sites_and_refuses_local_or_unsafe_links(tmp_path, monkeypatch):
    import socket
    def fake_dns(host, *args, **kwargs):
        table = {"intranet.example": "192.168.1.20", "loop.example": "127.0.0.1", "meta.example": "169.254.169.254"}
        if host == "nowhere.invalid":
            raise socket.gaierror("no such host")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (table.get(host, "93.184.216.34"), 0))]
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns)
    service = HarvestService(tmp_path)
    for url in ("https://youtu.be/abc123", "https://www.youtube.com/watch?v=abc123", "https://www.instagram.com/reel/xyz/",
                "https://www.reddit.com/r/startrek/comments/abc/scene/", "https://vimeo.com/123", "http://example.com/clip.mp3"):
        assert service.validate_video_url(url) == url
    for url in ("ftp://example.com/a.mp3", "https://user:pass@youtube.com/watch?v=a", "https://youtube.com:444/watch?v=a",
                "https://youtube.com/playlist?list=abc", "https://youtube.com/watch?v=a&list=abc", "http://localhost/x",
                "https://printer.local/x", "https://intranet.example/x", "https://loop.example/x", "https://meta.example/x",
                "https://127.0.0.1/x", "https://[::1]/x", "file:///C:/secret.wav"):
        with pytest.raises(HarvestError):
            service.validate_video_url(url)
    with pytest.raises(HarvestError, match="could not be found"):
        service.validate_video_url("https://nowhere.invalid/x")


def test_audio_paths_accept_only_canonical_owned_names(tmp_path):
    service = HarvestService(tmp_path)
    source_id = "a" * 32
    folder = service.root / source_id
    folder.mkdir()
    (folder / f"{source_id}_preview.wav").write_bytes(b"wav")
    service._state["sources"][source_id] = {
        "id": source_id, "original_file": f"{source_id}_original.wav", "variants": []
    }
    assert service.source_file(source_id, f"{source_id}_preview.wav").read_bytes() == b"wav"
    for bad in ("../secret.wav", f"{source_id}_original/../../secret", "anything.wav"):
        with pytest.raises(HarvestError):
            service.source_file(source_id, bad)
    with pytest.raises(HarvestError):
        service.get_source("../source")


def test_queued_job_cancellation_and_queue_bound(tmp_path):
    service = HarvestService(tmp_path)
    # Hold the worker lock so these jobs remain pending while their queue capacity is checked.
    with service._condition:
        jobs = [service.add_source_job(operation="test", payload={}) for _ in range(8)]
        with pytest.raises(HarvestError, match="queue is full"):
            service.add_source_job(operation="test", payload={})
        cancelled = service.cancel(jobs[0]["id"])
        assert cancelled["state"] == "cancelled"
        assert jobs[0]["id"] not in service._queue


def test_cancel_queued_import_removes_only_its_owned_staging_file(tmp_path):
    service = HarvestService(tmp_path)
    owned = service.root / "upload_owned.bin"
    unrelated = service.root / "notes.txt"
    outside = tmp_path / "outside_upload.bin"
    for path in (owned, unrelated, outside):
        path.write_bytes(b"keep or remove")
    with service._condition:
        job = service.add_source_job(operation="import", payload={"path": str(owned)})
        cancelled = service.cancel(job["id"])
        assert cancelled["state"] == "cancelled"
        outside_job = service.add_source_job(operation="import", payload={"path": str(outside)})
        service.cancel(outside_job["id"])
    assert not owned.exists()
    assert unrelated.read_bytes() == b"keep or remove"
    assert outside.read_bytes() == b"keep or remove"


def test_cancel_before_commit_discards_result_but_late_cancel_keeps_commit(tmp_path):
    service = HarvestService(tmp_path)
    with service._condition:
        before = service.add_source_job(operation="stub", payload={})
        service._state["jobs"][before["id"]]["state"] = "running"
        pending = service.cancel(before["id"])
        event = service._events[before["id"]]
        with pytest.raises(HarvestError, match="Cancelled"):
            service._commit_locked(before["id"], event, {"source_id": "1" * 32})
        assert service._state["jobs"][before["id"]].get("result") is None

        after = service.add_source_job(operation="stub", payload={})
        service._state["jobs"][after["id"]]["state"] = "running"
        service._commit_locked(after["id"], service._events[after["id"]], {"source_id": "2" * 32})
    assert pending["stage"] == "Cancelling"
    assert service.cancel(after["id"])["state"] == "completed"


def test_restart_marks_inflight_jobs_as_interrupted(tmp_path):
    root = tmp_path / "harvest"
    root.mkdir()
    active_id = "b" * 32
    (root / "harvest.json").write_text(json.dumps({
        "sources": {}, "jobs": {active_id: {"id": active_id, "state": "running", "stage": "Working"}}
    }), encoding="utf-8")
    service = HarvestService(tmp_path)
    job = service.get_job(active_id)
    assert job["state"] == "failed"
    assert job["stage"] == "interrupted"
    assert "restarted" in job["error"].lower()


def test_corrupt_store_is_preserved_before_recovery(tmp_path):
    root = tmp_path / "harvest"
    root.mkdir()
    broken = root / "harvest.json"
    broken.write_text("{not valid json", encoding="utf-8")
    service = HarvestService(tmp_path)
    assert json.loads(broken.read_text(encoding="utf-8")) == {"sources": {}, "jobs": {}}
    backups = list(root.glob("harvest.corrupt-*.json"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{not valid json"


def test_reference_promotion_preserves_transcript_and_reuses_same_version(tmp_path, monkeypatch):
    import core.paths

    monkeypatch.setattr(core.paths, "DATA_DIR", tmp_path / "library")
    service = HarvestService(tmp_path / "workspace")
    sid, vid = "d" * 32, "e" * 32
    folder = service.root / sid
    folder.mkdir()
    filename = f"{sid}_{vid[:12]}_none.wav"
    clip = folder / filename
    with wave.open(str(clip), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x00\x00" * 24_000 * 4)
    service._state["sources"][sid] = {"id": sid, "name": "Voice", "variants": [{
        "id": vid, "name": "Clip", "duration_s": 4, "file": filename,
        "provenance": {"source_id": sid, "start_s": 1, "end_s": 5}
    }]}
    service._save()
    cancel = threading.Event()
    first = service._reference("2" * 32, {"source_id": sid, "variant_id": vid, "transcript": "hello there"}, cancel)
    second = service._reference("3" * 32, {"source_id": sid, "variant_id": vid, "transcript": "hello there"}, cancel)
    assert first["reference_id"] == second["reference_id"]
    assert first["source_id"] == sid and first["variant_id"] == vid
    assert first["transcript"] == "hello there"
    assert first["provenance"]["start_s"] == 1
    third = service._reference("4" * 32, {"source_id": sid, "variant_id": vid, "transcript": "edited transcript"}, cancel)
    assert third["reference_id"] != first["reference_id"]
    from core.library import voices_dir
    assert len([p for p in voices_dir().glob("*.wav") if not p.name.endswith(".hq44.wav")]) == 2   # Library/Voices


def test_run_cancellation_stops_only_its_child(tmp_path):
    import sys
    from harvest.service import _run

    cancel = threading.Event()
    seen = []

    def request_cancel(proc):
        seen.append(proc)
        cancel.set()

    with pytest.raises(HarvestError, match="Cancelled"):
        _run([sys.executable, "-c", "import time; time.sleep(10)"], timeout=5,
             cancel=cancel, process_callback=request_cancel)
    assert seen and seen[0].poll() is not None


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg tools are optional")
def test_real_ffmpeg_import_and_matched_clip(tmp_path):
    from pathlib import Path

    fixture = Path(__file__).parents[2] / "data" / "tmp" / "harvest-smoke" / "source-fixture.wav"
    if not fixture.is_file():
        pytest.skip("Local synthetic audio fixture is unavailable")
    service = HarvestService(tmp_path)
    staged = service.root / "upload_fixture.bin"
    shutil.copyfile(fixture, staged)
    job_id = "c" * 32
    cancel = threading.Event()
    imported = service._import(job_id, {"path": str(staged), "name": "fixture.wav"}, cancel)
    source = service.get_source(imported["source_id"])
    clipped = service._clip(job_id, {"source_id": source["id"], "start_s": 1, "end_s": 7,
                                     "cleanup": "denoise", "name": "Fixture voice"}, cancel)
    variant = next(v for v in service.get_source(source["id"])["variants"]
                   if v["id"] == clipped["variant_id"])
    original = service.source_file(source["id"], variant["original_file"])
    processed = service.source_file(source["id"], variant["file"])
    assert original.is_file() and processed.is_file()
    assert variant["duration_s"] == pytest.approx(6, abs=.15)
    assert variant["original_audio_url"] != variant["audio_url"]
    assert source["provenance"]["original_sha256"]


def test_shutdown_cancels_active_and_queued_work(tmp_path, monkeypatch):
    import sys
    from harvest.service import _run

    service = HarvestService(tmp_path)
    started = threading.Event()
    seen = []

    def execute(job_id, job):
        def track(proc):
            service._set_process(job_id, proc)
            if proc is not None:
                seen.append(proc)
                started.set()
        _run([sys.executable, '-c', 'import time; time.sleep(30)'], timeout=35,
             cancel=service._events[job_id], process_callback=track)
        return {}

    monkeypatch.setattr(service, '_execute', execute)
    active = service.add_source_job(operation='test', payload={})
    assert started.wait(5)
    staged = service.root / 'upload_pending.bin'
    staged.write_bytes(b'pending')
    queued = service.add_source_job(operation='import', payload={'path': str(staged)})
    assert service.shutdown()
    assert seen[0].poll() is not None
    assert not staged.exists()
    assert not service._queue and not service._events and not service._processes
    assert service.get_job(active['id'])['state'] == 'cancelled'
    assert service.get_job(queued['id'])['state'] == 'cancelled'
    with pytest.raises(HarvestError, match='shutting down'):
        service.add_source_job(operation='test', payload={})


def test_process_registered_after_shutdown_is_terminated(tmp_path, monkeypatch):
    service = HarvestService(tmp_path)
    assert service.shutdown()
    seen = []
    monkeypatch.setattr(service, '_terminate_owned', seen.append)
    proc = object()
    service._set_process('late', proc)
    assert seen == [proc]
    service._set_process('late', None)


def test_run_callback_failure_still_reaps_child():
    import sys
    from harvest.service import _run

    seen = []
    def broken_callback(proc):
        if proc is not None:
            seen.append(proc)
            raise RuntimeError('tracking failure')
    with pytest.raises(RuntimeError, match='tracking failure'):
        _run([sys.executable, '-c', 'import time; time.sleep(30)'], timeout=35,
             process_callback=broken_callback)
    assert seen[0].poll() is not None

def test_api_shutdown_does_not_create_service_and_blocks_late_requests(monkeypatch):
    import api.harvest as api
    from fastapi import HTTPException

    monkeypatch.setattr(api, 'service', None)
    monkeypatch.setattr(api, '_service_stopping', False)
    shutdown = api.shutdown_harvest_jobs()
    with pytest.raises(StopIteration):
        shutdown.send(None)
    assert api.service is None
    with pytest.raises(HTTPException) as exc:
        api._service()
    assert exc.value.status_code == 503


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg tools are optional")
def test_clip_cleaning_steps_keep_full_band_copy_and_promote_it(tmp_path, monkeypatch):
    import base64
    import io
    import numpy as np
    import soundfile as sf
    import core.paths
    import harvest.service as hs

    def wav_b64(seconds, rate, freq, channels=1):
        t = np.arange(int(seconds * rate)) / rate
        tone = (0.2 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
        buf = io.BytesIO()
        sf.write(buf, np.stack([tone] * channels, axis=1), rate, format="WAV")
        return base64.b64encode(buf.getvalue()).decode()

    calls = []

    class FakeRuntime:
        def has(self, role):
            return role in {"vocals", "separate", "restore", "bwe"}

    class FakeStudio:
        runtime = FakeRuntime()

        def run_tool(self, role, request, timeout=0):
            calls.append((role, request))
            seconds = sf.info(request["audio"]).duration
            if role == "vocals":
                return {"named_audio_outputs": {"vocals": wav_b64(seconds, 44100, 300, 2), "instrumental": wav_b64(seconds, 44100, 90, 2)}}
            if role == "separate":
                return {"named_audio_outputs": {"target": wav_b64(seconds, 48000, 310), "residual": wav_b64(seconds, 48000, 50)}}
            return {"audio": wav_b64(seconds, 48000, 320)}

    monkeypatch.setattr(hs, "_studio", lambda: FakeStudio())
    monkeypatch.setattr(core.paths, "DATA_DIR", tmp_path / "library")
    service = HarvestService(tmp_path / "work")
    staged = service.root / "upload_song.bin"
    staged.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(staged), (0.2 * np.sin(np.arange(48000 * 40) / 30)).astype(np.float32), 48000, format="WAV")
    cancel = threading.Event()
    sid = service._import("a" * 32, {"path": str(staged), "name": "song.wav"}, cancel)["source_id"]

    caps = {c["id"]: c["available"] for c in service.capabilities()["cleanup"]}
    assert caps["remove_music"] and caps["isolate_voice"] and caps["restore"] and caps["denoise"]
    assert caps["bandwidth"]  # UniverSR is offered on every backend (it runs on a CPU server)

    with pytest.raises(HarvestError, match="Unknown cleanup"):
        service._clip("b" * 32, {"source_id": sid, "start_s": 0, "end_s": 5, "cleanup": ["explode"]}, cancel)
    with pytest.raises(HarvestError, match="3 to 60"):
        service._clip("b" * 32, {"source_id": sid, "start_s": 0, "end_s": 39.5 + 30, "cleanup": "none"}, cancel)

    made = service._clip("c" * 32, {"source_id": sid, "start_s": 2, "end_s": 32,   # 30 s: longer than the old cap
                                    "cleanup": ["restore", "remove_music", "isolate_voice"],
                                    "voice_prompt": "a woman speaking"}, cancel)
    variant = next(v for v in service.get_source(sid)["variants"] if v["id"] == made["variant_id"])
    assert variant["cleanup_steps"] == ["remove_music", "isolate_voice", "restore"]   # fixed order
    assert [c[0] for c in calls] == ["vocals", "separate", "restore"]
    assert calls[1][1]["text"] == "a woman speaking"
    assert sf.info(str(service.source_file(sid, variant["hq_file"]))).samplerate == 48000
    assert sf.info(str(service.source_file(sid, variant["file"]))).samplerate == 24000
    assert variant["duration_s"] == pytest.approx(30, abs=0.2)

    ref = service._reference("d" * 32, {"source_id": sid, "variant_id": variant["id"], "transcript": "hi"}, cancel)
    from core.library import voices_dir
    hq = voices_dir() / f"{ref['reference_id']}.hq44.wav"
    assert hq.is_file() and sf.info(str(hq)).samplerate == 44100


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg tools are optional")
def test_speaker_lines_and_keep_only_selected_lines(tmp_path, monkeypatch):
    import numpy as np
    import soundfile as sf
    import harvest.service as hs

    class FakeRuntime:
        def has(self, role):
            return role == "who_said"

    class FakeStudio:
        runtime = FakeRuntime()

        def run_details(self, role, path, language=None):
            assert role == "who_said" and sf.info(str(path)).samplerate == 16000
            sr = 16000
            return {"sample_rate": sr, "speaker_turns": [
                {"start_sample": 0, "end_sample": 2 * sr, "speaker_id": "S01", "text": "Hello there."},
                {"start_sample": 2 * sr, "end_sample": 4 * sr, "speaker_id": "S02", "text": "Interruption."},
                {"start_sample": 4 * sr, "end_sample": 9 * sr, "speaker_id": "S01", "text": "As I was saying."},
                {"start_sample": 9 * sr, "end_sample": 9 * sr + 800, "speaker_id": "S03", "text": "uh"}]}

    monkeypatch.setattr(hs, "_studio", lambda: FakeStudio())
    service = HarvestService(tmp_path / "work")
    staged = service.root / "upload.bin"
    staged.parent.mkdir(parents=True, exist_ok=True)
    rate = 48000
    tone = np.concatenate([np.full(2 * rate, 0.1), np.full(2 * rate, 0.5), np.full(6 * rate, 0.1)]).astype(np.float32)
    sf.write(str(staged), tone, rate, format="WAV")
    cancel = threading.Event()
    sid = service._import("a" * 32, {"path": str(staged), "name": "scene.wav"}, cancel)["source_id"]

    found = service._speakers("b" * 32, {"source_id": sid}, cancel)
    assert [l["speaker"] for l in found["lines"]] == ["S01", "S02", "S01"]     # the 0.05 s blip is dropped
    assert found["speakers"] == ["S01", "S02"] and service._public_source(service.get_source(sid))["speakers"]["lines"]

    keep = [(l["start"], l["end"]) for l in found["lines"] if l["speaker"] == "S01"]
    made = service._clip("c" * 32, {"source_id": sid, "segments": keep, "cleanup": "none", "name": "S01 only"}, cancel)
    variant = next(v for v in service.get_source(sid)["variants"] if v["id"] == made["variant_id"])
    clip, clip_rate = sf.read(str(service.source_file(sid, variant["hq_file"])))
    # 2 s + 5 s of S01 (with small pads) plus one 0.3 s gap; the loud S02 line (0.5) is gone.
    assert 7.2 <= len(clip) / clip_rate <= 7.6
    assert float(np.max(np.abs(clip))) < 0.2
    assert variant["segments"] == [[0.0, 2.0], [4.0, 9.0]]

    with pytest.raises(HarvestError, match="add up to"):
        service._clip("d" * 32, {"source_id": sid, "segments": [(0.0, 1.0)], "cleanup": "none"}, cancel)


def test_download_failures_are_explained_without_workarounds():
    explain = HarvestService.explain_download_failure
    assert "requires a login" in explain("ERROR: [Reddit] 1xml3rs: Account authentication is required. Use --cookies-from-browser")
    assert "automated" in explain("ERROR: [SomeSite] 673687ff9e792: Unable to download webpage: HTTP Error 410: Gone")
    assert "HTTP 403" in explain("ERROR: unable to download video data: HTTP Error 403: Forbidden")
    assert "not found" in explain("ERROR: [youtube] abc: Video unavailable")
    assert "not supported" in explain("ERROR: Unsupported URL: https://example.com/")
    assert "Check the link" in explain("something unexpected")
