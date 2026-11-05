from __future__ import annotations

import io
import json
from pathlib import Path
import threading
import time
import zipfile

import pytest

from engines.base import ModelInfo, ModelState
from training.jobs import JobError, TrainJobService
from training.runtime import core_readiness


def _inputs(tmp_path: Path, *, traversal: bool = False):
    data_dir = tmp_path / "data"
    artifacts = data_dir / "training" / "artifacts"
    artifacts.mkdir(parents=True)
    archive = artifacts / "artifact.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("dataset.json", json.dumps({"dataset_id": "dataset1", "version": 3}))
        zf.writestr("metadata.jsonl", json.dumps({"id": "clip1", "audio_path": "audio/clip1.wav",
                                                   "text": "hello there"}) + "\n")
        zf.writestr("audio/clip1.wav", b"fake wav data")
        if traversal:
            zf.writestr("../escaped.txt", "no")
    model = tmp_path / "local-model"
    (model / "audio_tokenizer").mkdir(parents=True)
    (model / "audio_tokenizer" / "tokenizer.json").write_text("{}", encoding="utf-8")
    (model / "config.json").write_text(json.dumps({"model_type": "omnivoice"}), encoding="utf-8")
    _save_safetensors(model / "model.safetensors")
    info = ModelInfo("omnivoice:local", "omnivoice", "OmniVoice", "OmniVoice", str(model),
                     13, ModelState.INTEGRATED)

    class FakeDatasets:
        def export(self, dataset_id):
            assert dataset_id == "dataset1"
            return {"id": "artifact", "version": 3}

        def get_dataset(self, dataset_id):
            return {"id": dataset_id, "name": "Test voice", "version": 3}

        def artifact_path(self, artifact_id):
            assert artifact_id == "artifact"
            return archive

    return data_dir, archive, model, info, FakeDatasets()


def _save_safetensors(path: Path, size: int = 1):
    import torch
    from safetensors.torch import save_file
    save_file({"tiny.weight": torch.zeros((size,), dtype=torch.float32)}, str(path))


class FakeProcess:
    def __init__(self, return_code=0, *, hang=False):
        self.return_code = return_code
        self.hang = hang
        self.stdout = io.BytesIO(b"fake child progress\n")
        self.pid = 98765
        self.terminated = False

    def wait(self, timeout=None):
        if self.hang and not self.terminated:
            time.sleep(min(timeout or 0.01, 0.01))
            raise __import__("subprocess").TimeoutExpired("fake", timeout)
        return self.return_code

    def poll(self):
        return self.return_code if not self.hang or self.terminated else None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True


def _wait_terminal(service: TrainJobService, job_id: str):
    until = time.monotonic() + 5
    while time.monotonic() < until:
        record = service.get_job(job_id)
        if record["status"] in {"completed", "failed", "canceled"}:
            return record
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_job_runs_audited_single_process_pipeline_and_verifies_checkpoint(tmp_path):
    data_dir, archive, model, info, datasets = _inputs(tmp_path)
    archive_before = archive.read_bytes()
    source_before = (model / "model.safetensors").read_bytes()
    commands = []
    environments = []
    lock = threading.RLock()

    def fake_popen(command, **kwargs):
        commands.append(command)
        environments.append(kwargs["env"])
        assert Path(kwargs["cwd"]).name == "prepared"
        assert kwargs["stdin"] == __import__("subprocess").DEVNULL
        assert lock._is_owned()  # GPU-consuming children run under the shared speech lock.
        assert kwargs["env"]["PYTHONUTF8"] == "1"
        assert kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
        if "omnivoice.scripts.extract_audio_tokens" in command:
            tar_pattern = Path(command[command.index("--tar_output_pattern") + 1])
            label_pattern = Path(command[command.index("--jsonl_output_pattern") + 1])
            tar_path = Path(kwargs["cwd"]) / str(tar_pattern).replace("%06d", "000000")
            label_path = Path(kwargs["cwd"]) / str(label_pattern).replace("%06d", "000000")
            tar_path.parent.mkdir(parents=True, exist_ok=True)
            label_path.parent.mkdir(parents=True, exist_ok=True)
            tar_path.write_bytes(b"fake tar")
            label_path.write_text(json.dumps({"id": "clip1", "num_tokens": 150}) + "\n", encoding="utf-8")
            (tar_path.parent.parent / "data.lst").write_text(
                f"{tar_path} {label_path} 1 1.0\n", encoding="utf-8")
        else:
            output = Path(command[command.index("--output_dir") + 1])
            checkpoint = output / "checkpoint-1"
            checkpoint.mkdir(parents=True)
            (checkpoint / "config.json").write_text(json.dumps({"model_type": "omnivoice"}), encoding="utf-8")
            _save_safetensors(checkpoint / "model.safetensors")
            (checkpoint / "tokenizer.json").write_text("{}", encoding="utf-8")
        return FakeProcess()

    prepared = []
    service = TrainJobService(data_dir, dataset_service=datasets, python="core-python",
                              popen_factory=fake_popen, speech_lock=lock,
                              prepare_gpu=lambda: prepared.append("released"), timeout_override=10)
    original_extract = service._extract_snapshot
    original_verify = service._verify_checkpoint

    def check_cpu_preparation(*args, **kwargs):
        assert not lock._is_owned()
        assert service.get_job(args[0])["phase"] == "preparing_inputs"
        return original_extract(*args, **kwargs)

    def check_cpu_finalization(*args, **kwargs):
        assert not lock._is_owned()
        return original_verify(*args, **kwargs)

    service._extract_snapshot = check_cpu_preparation
    service._verify_checkpoint = check_cpu_finalization
    queued = service.submit("dataset1", info, steps=1, batch_tokens=256, save_steps=1)
    result = _wait_terminal(service, queued["id"])
    assert result["status"] == "completed"
    assert result["purpose"] == "one-step-check"
    assert not service.has_validated_model(info.id)  # injected fake processes never count as a real run
    checkpoint = Path(result["checkpoint_path"])
    assert checkpoint.name == "checkpoint-1"
    assert (checkpoint / "audio_tokenizer" / "tokenizer.json").is_file()
    assert (model / "model.safetensors").read_bytes() == source_before
    assert archive.read_bytes() == archive_before
    assert len(commands) == 2
    assert commands[0][commands[0].index("--loader_workers") + 1] == "0"
    assert commands[0][commands[0].index("--nj_per_gpu") + 1] == "1"
    assert commands[0][commands[0].index("--min_num_shards") + 1] == "1"
    assert "--train_config" in commands[1]
    assert all(env["HF_HUB_OFFLINE"] == "1" and env["TRANSFORMERS_OFFLINE"] == "1" for env in environments)
    assert prepared == ["released"]
    assert len(service.log_tail(queued["id"])) > 0
    assert len(service.list_jobs()) == 1


def test_bad_export_paths_fail_before_child_and_queue_cancel_is_terminal(tmp_path):
    data_dir, _archive, _model, info, datasets = _inputs(tmp_path, traversal=True)
    lock = threading.Lock()
    lock.acquire()
    called = []
    service = TrainJobService(data_dir, dataset_service=datasets, popen_factory=lambda *a, **k: called.append(a),
                              speech_lock=lock, prepare_gpu=lambda: None)
    job = service.submit("dataset1", info)
    # The worker waits for the GPU lock; queued cancellation removes it without launching a child.
    service.cancel(job["id"])
    canceled = _wait_terminal(service, job["id"])
    assert canceled["status"] == "canceled"
    assert called == []
    assert service.log_tail(job["id"]) == ""
    lock.release()


def test_unsafe_archive_fails_and_restart_marks_unfinished_jobs_failed(tmp_path):
    data_dir, _archive, _model, info, datasets = _inputs(tmp_path, traversal=True)
    lock = threading.Lock()
    lock.acquire()
    service = TrainJobService(data_dir, dataset_service=datasets, popen_factory=lambda *a, **k: pytest.fail("unsafe archive launched a child"),
                              speech_lock=lock, prepare_gpu=lambda: None)
    job = service.submit("dataset1", info)
    # Let worker acquire after we release the lock and reject the zip member traversal.
    lock.release()
    failed = _wait_terminal(service, job["id"])
    assert failed["status"] == "failed"
    assert "unsafe archive path" in failed["error"]

    # Simulate a backend restart with a persisted in-flight record.
    index = data_dir / "training" / "jobs" / "index.json"
    stored = json.loads(index.read_text(encoding="utf-8"))
    record = stored["jobs"][0]
    record.update(status="training", phase="training", checkpoint_path=None)
    index.write_text(json.dumps(stored), encoding="utf-8")
    recovered = TrainJobService(data_dir, dataset_service=datasets)
    restarted = recovered.get_job(job["id"])
    assert restarted["status"] == "failed"
    assert "restarted" in restarted["error"]


def test_validates_public_bounds_and_only_accepts_registry_models(tmp_path):
    data_dir, _archive, _model, info, datasets = _inputs(tmp_path)
    service = TrainJobService(data_dir, dataset_service=datasets)
    with pytest.raises(JobError, match="steps"):
        service.submit("dataset1", info, steps=0)
    bad_model = ModelInfo("planned:x", "planned", "x", "x", info.path, 1, ModelState.TESTED)
    with pytest.raises(JobError, match="OmniVoice"):
        service.submit("dataset1", bad_model)
    with pytest.raises(JobError, match="one-step check"):
        service = TrainJobService(data_dir, dataset_service=datasets, popen_factory=lambda *a, **k: None)
        service.submit("dataset1", info, steps=2)


def test_persisted_real_one_step_check_is_bound_to_source_and_checkpoint_files(tmp_path):
    data_dir, _archive, model, info, datasets = _inputs(tmp_path)
    jobs_root = data_dir / "training" / "jobs"
    job_id = "a" * 32
    checkpoint = jobs_root / job_id / "run" / "checkpoint-1"
    checkpoint.mkdir(parents=True)
    (checkpoint / "config.json").write_text(json.dumps({"model_type": "omnivoice"}), encoding="utf-8")
    _save_safetensors(checkpoint / "model.safetensors")
    (checkpoint / "tokenizer.json").write_text("{}", encoding="utf-8")
    (checkpoint / "audio_tokenizer").mkdir()
    (checkpoint / "audio_tokenizer" / "tokenizer.json").write_text("{}", encoding="utf-8")
    service = TrainJobService(data_dir, dataset_service=datasets)
    record = {
        "id": job_id, "dataset_id": "dataset1", "dataset_name": "Test voice", "dataset_version": 3,
        "model_id": info.id, "model_name": info.id, "status": "completed", "phase": "completed",
        "purpose": "one-step-check", "steps": 1, "created": "2026-10-10T00:00:00+00:00",
        "updated": "2026-10-10T00:00:00+00:00", "error": None,
        "checkpoint_path": str(checkpoint), "validation_eligible": True,
        "source_model_path": str(model.resolve()),
        "source_model_signature": service._model_signature(model.resolve()),
        "source_runtime_signature": core_readiness()["versions"],
    }
    service._jobs[job_id] = record
    service._ensure_root()
    service._write_index()
    reloaded = TrainJobService(data_dir, dataset_service=datasets)
    assert reloaded.has_validated_model(info.id, info)

    _save_safetensors(model / "model.safetensors", size=2)
    assert not reloaded.has_validated_model(info.id, info)


def test_corrupt_history_fails_closed_without_overwriting_original_or_starting_child(tmp_path):
    data_dir, _archive, _model, info, datasets = _inputs(tmp_path)
    jobs_root = data_dir / "training" / "jobs"
    jobs_root.mkdir(parents=True)
    index = jobs_root / "index.json"
    original = b"{this is not valid job history\n"
    index.write_bytes(original)
    launched = []
    service = TrainJobService(data_dir, dataset_service=datasets,
                              popen_factory=lambda *a, **k: launched.append(a))
    with pytest.raises(JobError, match="history is unreadable"):
        service.submit("dataset1", info)
    assert index.read_bytes() == original
    assert launched == []


def test_shutdown_cancels_owned_child_and_pending_jobs_releases_lock_and_rejects_submit(tmp_path):
    data_dir, _archive, _model, info, datasets = _inputs(tmp_path)
    lock = threading.RLock()
    child_started = threading.Event()
    calls = []

    def fake_popen(command, **kwargs):
        calls.append(command)
        child_started.set()
        return FakeProcess(hang=True)

    service = TrainJobService(data_dir, dataset_service=datasets, popen_factory=fake_popen,
                              speech_lock=lock, prepare_gpu=lambda: None,
                              terminate_tree=lambda process: process.terminate())
    first = service.submit("dataset1", info)
    assert child_started.wait(5), "owned child did not start"
    second = service.submit("dataset1", info)

    assert service.shutdown() is True
    assert service.get_job(first["id"])["status"] == "canceled"
    assert service.get_job(second["id"])["status"] == "canceled"
    assert len(calls) == 1
    assert lock.acquire(timeout=0.1)
    lock.release()
    with pytest.raises(JobError, match="shutting down"):
        service.submit("dataset1", info)
    assert len(calls) == 1
