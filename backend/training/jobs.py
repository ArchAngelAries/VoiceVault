"""Persistent, single-process OmniVoice fine-tuning jobs.

The service is deliberately lazy: importing it starts no worker and creates no files.
All inputs, logs, configs, and checkpoints are owned by DATA_DIR/training/jobs.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import zipfile
import stat
from typing import Any, Callable

from core.paths import DATA_DIR, VOICEVAULT_ROOT
from engines.base import ModelInfo, ModelState
from training.datasets import DatasetError, DatasetService


MAX_PENDING = 8
MAX_HISTORY = 100
MAX_LOG_BYTES = 256 * 1024
MAX_LOG_TAIL_CHARS = 32 * 1024
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBER_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 510
MAX_DATASET_CLIPS = 500
ONE_STEP_TIMEOUT_SECONDS = 180
NORMAL_TIMEOUT_SECONDS = 6 * 60 * 60
JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
CLIP_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
ACTIVE_STATUSES = {"queued", "waiting", "preprocessing", "training"}
TERMINAL_STATUSES = {"completed", "failed", "canceled"}
BACKEND_DIR = VOICEVAULT_ROOT / "backend"


class JobError(ValueError):
    """A request cannot be turned into a safe training job."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _inside(path: Path, root: Path) -> Path:
    root = root.resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise JobError("Training job path escaped its owned data directory.") from exc
    return resolved


def _safe_model(info: ModelInfo) -> tuple[Path, str]:
    if not isinstance(info, ModelInfo) or info.engine_id != "omnivoice":
        raise JobError("Choose an OmniVoice model from VoiceVault's model list.")
    if info.state not in {ModelState.INTEGRATED, ModelState.TESTED}:
        raise JobError("The selected OmniVoice model is not available in this runtime.")
    model_dir = Path(info.path).expanduser()
    if not model_dir.is_absolute() or not model_dir.is_dir():
        raise JobError("The selected model must be an existing local folder.")
    model_dir = model_dir.resolve()
    config_path = model_dir / "config.json"
    weights = model_dir / "model.safetensors"
    tokenizer = model_dir / "audio_tokenizer"
    if not config_path.is_file() or not weights.is_file() or not tokenizer.is_dir():
        raise JobError("The selected local OmniVoice checkpoint is missing config, weights, or audio tokenizer files.")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise JobError("The selected OmniVoice model config is unreadable.") from exc
    if not isinstance(config, dict) or config.get("model_type") != "omnivoice":
        raise JobError("The selected local folder is not an OmniVoice checkpoint.")
    if not _valid_safetensors(weights):
        raise JobError("The selected local OmniVoice checkpoint has an invalid or empty safetensors file.")
    return model_dir, str(info.id)


def _valid_safetensors(path: Path) -> bool:
    """Validate the bounded safetensors header without loading tensor storage."""
    try:
        size = path.stat().st_size
        if size < 16:
            return False
        with path.open("rb") as stream:
            header_size = int.from_bytes(stream.read(8), "little", signed=False)
            if header_size < 2 or header_size > 64 * 1024 * 1024 or header_size > size - 8:
                return False
            header = json.loads(stream.read(header_size))
        if not isinstance(header, dict):
            return False
        tensors = [(name, value) for name, value in header.items() if name != "__metadata__"]
        if not tensors:
            return False
        payload_size = size - 8 - header_size
        for name, value in tensors:
            if not isinstance(name, str) or not name or not isinstance(value, dict):
                return False
            shape, offsets = value.get("shape"), value.get("data_offsets")
            if (not isinstance(shape, list) or not all(isinstance(n, int) and n >= 0 for n in shape)
                    or not isinstance(offsets, list) or len(offsets) != 2
                    or not all(isinstance(n, int) for n in offsets)):
                return False
            start, end = offsets
            if start < 0 or end <= start or end > payload_size:
                return False
        return True
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


def _record_copy(record: dict[str, Any]) -> dict[str, Any]:
    # Paths, config blobs, and archive internals never leave the service boundary.
    keys = ("id", "dataset_id", "dataset_name", "dataset_version", "model_id", "model_name",
            "status", "phase", "purpose", "steps", "keep_last_n_checkpoints",
            "created", "updated", "error", "checkpoint_path")
    return {key: record.get(key) for key in keys}


class TrainJobService:
    """Sequential local training lane with persistent status and bounded queue/logs."""

    def __init__(
        self,
        data_dir: Path | None = None,
        *,
        dataset_service: DatasetService | None = None,
        python: str | None = None,
        popen_factory: Callable[..., Any] | None = None,
        speech_lock: Any = None,
        prepare_gpu: Callable[[], None] | None = None,
        terminate_tree: Callable[[Any], None] | None = None,
        timeout_override: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.data_dir = Path(data_dir) if data_dir is not None else DATA_DIR
        self.jobs_root = self.data_dir / "training" / "jobs"
        self.index_path = self.jobs_root / "index.json"
        self.dataset_service = dataset_service or DatasetService(self.data_dir)
        self.python = python or sys.executable
        self._injected_process_factory = popen_factory is not None
        self.popen_factory = popen_factory or subprocess.Popen
        if speech_lock is None:
            from core.gpu_scheduler import speech_resource_lock
            speech_lock = speech_resource_lock
        self.speech_lock = speech_lock
        self.prepare_gpu = prepare_gpu or self._release_speech_models
        self.terminate_tree = terminate_tree or self._terminate_tree
        self.timeout_override = timeout_override
        self.clock = clock

        self._mutex = threading.RLock()
        self._condition = threading.Condition(self._mutex)
        self._jobs: dict[str, dict[str, Any]] = {}
        self._history_error: str | None = None
        self._pending: deque[tuple[str, str, str]] = deque()
        self._reservations = 0
        self._cancel: dict[str, threading.Event] = {}
        self._worker: threading.Thread | None = None
        self._active_process: tuple[str, Any] | None = None
        self._shutdown = False
        self._terminate_mutex = threading.Lock()
        self._load_history()

    def _load_history(self) -> None:
        if not self.index_path.exists():
            return
        if self.index_path.is_symlink() or not self.index_path.is_file():
            self._history_error = "Training job history is not a regular file; preserve or repair the index before continuing."
            return
        changed = False
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("version") != 1 or not isinstance(raw.get("jobs"), list):
                raise ValueError("unsupported training history format")
            records = raw["jobs"]
            for record in records:
                if not isinstance(record, dict) or not JOB_ID_RE.fullmatch(str(record.get("id", ""))):
                    raise ValueError("invalid training job record")
                normalized = _record_copy(record)
                for private_key in ("purpose", "validation_eligible", "source_model_path", "source_model_signature",
                                    "source_runtime_signature",
                                    "batch_tokens", "save_steps"):
                    if private_key in record:
                        normalized[private_key] = record[private_key]
                if normalized["status"] in ACTIVE_STATUSES:
                    normalized["status"] = "failed"
                    normalized["phase"] = "recovered"
                    normalized["error"] = "Backend restarted before this job finished."
                    normalized["checkpoint_path"] = None
                    normalized["updated"] = _now()
                    changed = True
                self._jobs[normalized["id"]] = normalized
        except (OSError, ValueError, TypeError):
            self._history_error = "Training job history is unreadable or unsupported; preserve or repair index.json before continuing."
            self._jobs.clear()
            return
        if self._jobs:
            ordered = sorted(self._jobs.items(), key=lambda item: item[1].get("created", ""))[-MAX_HISTORY:]
            if len(ordered) != len(self._jobs):
                changed = True
            self._jobs = dict(ordered)
            if changed:
                self._write_index()

    def _check_history(self) -> None:
        if self._history_error:
            raise JobError(self._history_error)

    def shutdown(self) -> bool:
        """Cancel owned work and wait at most 15 seconds for the worker to exit."""
        process = None
        with self._condition:
            self._shutdown = True
            for job_id, _model_path, _archive_hash in tuple(self._pending):
                self._cancel.setdefault(job_id, threading.Event()).set()
                record = self._jobs.get(job_id)
                if record and record["status"] not in TERMINAL_STATUSES:
                    record.update(status="canceled", phase="canceled", error=None,
                                  checkpoint_path=None, updated=_now())
                    self._persist(job_id)
            self._pending.clear()
            for job_id, record in self._jobs.items():
                if record.get("status") in ACTIVE_STATUSES:
                    self._cancel.setdefault(job_id, threading.Event()).set()
                    record.update(status="canceled", phase="canceled", error=None,
                                  checkpoint_path=None, updated=_now())
                    self._persist(job_id)
            if self._active_process is not None:
                process = self._active_process[1]
            worker = self._worker
            self._condition.notify_all()
        if process is not None:
            self._stop_process(process)
        if worker is None or worker is threading.current_thread():
            return worker is None or not worker.is_alive()
        worker.join(timeout=15)
        return not worker.is_alive()

    def _ensure_root(self) -> None:
        training_root = self.data_dir.resolve() / "training"
        jobs_root = training_root / "jobs"
        if training_root.is_symlink() or jobs_root.is_symlink():
            raise JobError("Training job data folder cannot be a symlink.")
        jobs_root.mkdir(parents=True, exist_ok=True)
        _inside(jobs_root, self.data_dir)

    def _write_index(self) -> None:
        if self.index_path.is_symlink():
            raise JobError("Training job index cannot be a symlink.")
        rows = sorted(self._jobs.values(), key=lambda item: item.get("created", ""))[-MAX_HISTORY:]
        _atomic_json(self.index_path, {"version": 1, "jobs": rows})

    def _persist(self, job_id: str) -> None:
        record = self._jobs[job_id]
        job_dir = self.jobs_root / job_id
        _inside(job_dir, self.jobs_root)
        if job_dir.is_symlink():
            raise JobError("Job folder cannot be a symlink.")
        job_dir.mkdir(parents=True, exist_ok=True)
        _atomic_json(job_dir / "job.json", record)
        self._write_index()

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._condition:
            record = self._jobs.get(job_id)
            if record is None:
                return
            record.update(changes)
            record["updated"] = _now()
            self._persist(job_id)
            self._condition.notify_all()

    def _complete_job(self, job_id: str, checkpoint: Path, event: threading.Event) -> None:
        with self._condition:
            record = self._jobs.get(job_id)
            if record is None:
                return
            if self._shutdown or event.is_set():
                record.update(status="canceled", phase="canceled", error=None, checkpoint_path=None)
            else:
                record.update(status="completed", phase="completed", error=None,
                              checkpoint_path=str(checkpoint.resolve()))
            record["updated"] = _now()
            self._persist(job_id)
            self._condition.notify_all()

    def submit(self, dataset_id: str, model_info: ModelInfo, *, steps: int = 1,
               batch_tokens: int = 256, save_steps: int = 1) -> dict[str, Any]:
        self._check_history()
        with self._mutex:
            if self._shutdown:
                raise JobError("Training service is shutting down and cannot accept new jobs.")
        for value, label, low, high in ((steps, "steps", 1, 10_000),
                                        (batch_tokens, "batch_tokens", 256, 2_048),
                                        (save_steps, "save_steps", 1, 1_000)):
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise JobError(f"{label} must be an integer from {low} to {high}.")
        model_dir, model_id = _safe_model(model_info)
        source_signature = self._model_signature(model_dir)
        from training.runtime import core_readiness
        runtime_signature = core_readiness().get("versions", {})
        if not self._injected_process_factory:
            from training.runtime import runtime_readiness
            readiness = runtime_readiness(self.data_dir)
            if not readiness.get("ready_for_training_dependencies"):
                raise JobError("Training runtime is not ready; provision the audited optional dependencies first.")
            runtime_signature = readiness["core"].get("versions", {})
        if steps > 1 and not self.has_validated_model(model_id, model_info):
            raise JobError("Run a successful one-step check with this local model before full training.")

        with self._condition:
            pending_count = len(self._pending) + self._reservations
            if pending_count >= MAX_PENDING:
                raise JobError("The training queue is full; wait for a pending job to start.")
            self._reservations += 1

        job_id = uuid.uuid4().hex
        job_dir = self.jobs_root / job_id
        reservation = True
        try:
            self._ensure_root()
            _inside(job_dir, self.jobs_root)
            job_dir.mkdir()
            exported = self.dataset_service.export(dataset_id)
            archive_path = self.dataset_service.artifact_path(exported["id"])
            archive_hash = self._snapshot_archive(archive_path, job_dir / "input.zip")
            try:
                with zipfile.ZipFile(job_dir / "input.zip") as archive:
                    dataset_manifest = json.loads(archive.read("dataset.json"))
            except (OSError, zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
                raise JobError("Dataset export metadata is invalid.") from exc
            if dataset_manifest.get("dataset_id") != dataset_id or dataset_manifest.get("version") != exported.get("version"):
                raise JobError("Dataset export version changed; export it again before training.")
            record = {
                "id": job_id,
                "dataset_id": dataset_id,
                "dataset_name": str(dataset_manifest.get("name", ""))[:100],
                "dataset_version": int(exported.get("version", 0)),
                "model_id": model_id,
                "model_name": f"{model_info.family} ({model_dir.name})"[:160],
                "status": "queued",
                "phase": "queued",
                "steps": steps,
                "batch_tokens": batch_tokens,
                "save_steps": save_steps,
                "purpose": "one-step-check" if steps == 1 else "full-training",
                "keep_last_n_checkpoints": 2,
                "validation_eligible": not self._injected_process_factory,
                "source_model_path": str(model_dir),
                "source_model_signature": source_signature,
                "source_runtime_signature": runtime_signature,
                "created": _now(),
                "updated": _now(),
                "error": None,
                "checkpoint_path": None,
            }
            item = (job_id, str(model_dir), archive_hash)
            with self._condition:
                if self._shutdown:
                    raise JobError("Training service is shutting down and cannot accept new jobs.")
                self._reservations -= 1
                reservation = False
                self._jobs[job_id] = record
                self._trim_history()
                self._persist(job_id)
                self._pending.append(item)
                self._cancel[job_id] = threading.Event()
                self._start_worker_locked()
                self._condition.notify_all()
            return _record_copy(record)
        except (DatasetError, OSError, zipfile.BadZipFile) as exc:
            raise JobError(str(exc)) from exc
        finally:
            if reservation:
                with self._condition:
                    self._reservations -= 1
                    self._condition.notify_all()

    @staticmethod
    def _model_signature(model_dir: Path) -> dict[str, Any]:
        config = model_dir / "config.json"
        weights = model_dir / "model.safetensors"
        weight_stat = weights.stat()
        return {"path": str(model_dir.resolve()),
                "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
                "weights_size": weight_stat.st_size,
                "weights_mtime_ns": weight_stat.st_mtime_ns}

    def has_validated_model(self, model_id: str, model_info: ModelInfo | None = None) -> bool:
        """Check for a successful real one-step run with this still-unchanged model."""
        self._check_history()
        if not isinstance(model_id, str) or not model_id:
            return False
        expected_signature = None
        if model_info is not None:
            try:
                model_dir, current_id = _safe_model(model_info)
                if current_id != model_id:
                    return False
                expected_signature = self._model_signature(model_dir)
            except (JobError, OSError):
                return False
        from training.runtime import core_readiness
        runtime_signature = core_readiness().get("versions", {})
        with self._mutex:
            records = [dict(record) for record in self._jobs.values()]
        for record in reversed(sorted(records, key=lambda item: item.get("created", ""))):
            if (record.get("model_id") != model_id or record.get("status") != "completed"
                    or record.get("steps") != 1 or record.get("purpose") != "one-step-check"
                    or record.get("validation_eligible") is not True):
                continue
            checkpoint_value = record.get("checkpoint_path")
            signature = record.get("source_model_signature")
            source_path = record.get("source_model_path")
            if not checkpoint_value or not isinstance(signature, dict) or not source_path:
                continue
            if record.get("source_runtime_signature") != runtime_signature:
                continue
            try:
                source = Path(source_path).resolve()
                if self._model_signature(source) != signature:
                    continue
                if expected_signature is not None and expected_signature != signature:
                    continue
                checkpoint = Path(checkpoint_value).resolve()
                _inside(checkpoint, self.jobs_root)
                if self._checkpoint_valid(checkpoint):
                    return True
            except (OSError, JobError):
                continue
        return False

    def _snapshot_archive(self, source: Path, destination: Path) -> str:
        root = (self.data_dir / "training" / "artifacts").resolve()
        if source.is_symlink() or not source.is_file():
            raise JobError("The dataset export archive is missing or linked.")
        source_path = _inside(source, root)
        before = source_path.stat()
        if before.st_size > MAX_ARCHIVE_BYTES:
            raise JobError("Dataset export exceeds the training archive size limit.")
        temp = destination.with_suffix(".zip.tmp")
        digest = hashlib.sha256()
        try:
            with source_path.open("rb") as inp, temp.open("xb") as out:
                while chunk := inp.read(1024 * 1024):
                    digest.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            after = source_path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise JobError("Dataset export changed while it was being copied; retry the job.")
            if temp.stat().st_size != before.st_size:
                raise JobError("Dataset export snapshot is incomplete.")
            os.replace(temp, destination)
            return digest.hexdigest()
        finally:
            temp.unlink(missing_ok=True)

    def _trim_history(self) -> None:
        if len(self._jobs) > MAX_HISTORY:
            ordered = sorted(self._jobs.values(), key=lambda item: item.get("created", ""))
            keep = {item["id"] for item in ordered[-MAX_HISTORY:]}
            self._jobs = {job_id: record for job_id, record in self._jobs.items() if job_id in keep}

    def _start_worker_locked(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._worker_loop, name="voicevault-training", daemon=True)
            self._worker.start()

    def list_jobs(self) -> list[dict[str, Any]]:
        self._check_history()
        with self._mutex:
            return [_record_copy(item) for item in sorted(self._jobs.values(),
                    key=lambda value: value.get("created", ""), reverse=True)]

    def get_job(self, job_id: str) -> dict[str, Any]:
        self._check_history()
        self._validate_job_id(job_id)
        with self._mutex:
            record = self._jobs.get(job_id)
            if record is None:
                raise JobError("Training job not found.")
            return _record_copy(record)

    def log_tail(self, job_id: str) -> str:
        self._check_history()
        self._validate_job_id(job_id)
        path = self._job_dir(job_id) / "worker.log"
        if path.is_symlink() or not path.is_file():
            return ""
        try:
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - MAX_LOG_TAIL_CHARS * 4))
                return stream.read().decode("utf-8", errors="replace")[-MAX_LOG_TAIL_CHARS:]
        except OSError:
            return ""

    def cancel(self, job_id: str) -> dict[str, Any]:
        self._check_history()
        self._validate_job_id(job_id)
        process = None
        with self._condition:
            record = self._jobs.get(job_id)
            if record is None:
                raise JobError("Training job not found.")
            if record["status"] in TERMINAL_STATUSES:
                if record["status"] == "canceled":
                    return _record_copy(record)
                raise JobError("A finished training job cannot be canceled.")
            event = self._cancel.setdefault(job_id, threading.Event())
            event.set()
            for item in tuple(self._pending):
                if item[0] == job_id:
                    self._pending.remove(item)
                    record.update(status="canceled", phase="canceled", error=None,
                                  checkpoint_path=None, updated=_now())
                    self._persist(job_id)
                    self._condition.notify_all()
                    return _record_copy(record)
            if self._active_process and self._active_process[0] == job_id:
                process = self._active_process[1]
            elif record["status"] in {"queued", "waiting"}:
                record.update(status="canceled", phase="canceled", error=None,
                              checkpoint_path=None, updated=_now())
                self._persist(job_id)
                self._condition.notify_all()
                return _record_copy(record)
            self._condition.notify_all()
        if process is not None:
            self._stop_process(process)
        return self.get_job(job_id)

    @staticmethod
    def _validate_job_id(job_id: str) -> None:
        if not isinstance(job_id, str) or not JOB_ID_RE.fullmatch(job_id):
            raise JobError("Invalid training job id.")

    def _job_dir(self, job_id: str) -> Path:
        path = self.jobs_root / job_id
        return _inside(path, self.jobs_root)

    def _worker_loop(self) -> None:
        while True:
            with self._condition:
                while not self._pending:
                    if self._shutdown:
                        return
                    self._condition.wait()
                job_id, model_path, archive_hash = self._pending.popleft()
                self._condition.notify_all()
            try:
                self._run_job(job_id, Path(model_path), archive_hash)
            except Exception as exc:  # Job errors must not kill the single worker.
                canceled = self._cancel.get(job_id, threading.Event()).is_set()
                self._update(job_id, status="canceled" if canceled else "failed",
                             phase="canceled" if canceled else "failed",
                             error=None if canceled else str(exc)[:1000], checkpoint_path=None)
            finally:
                with self._condition:
                    self._cancel.pop(job_id, None)

    def _run_job(self, job_id: str, model_dir: Path, archive_hash: str) -> None:
        event = self._cancel.setdefault(job_id, threading.Event())
        record = self.get_job(job_id)
        if event.is_set() or record["status"] in TERMINAL_STATUSES:
            return
        timeout = self.timeout_override or (ONE_STEP_TIMEOUT_SECONDS if record["steps"] == 1
                                             else NORMAL_TIMEOUT_SECONDS)
        deadline = self.clock() + timeout
        self._update(job_id, status="waiting", phase="preparing_inputs", error=None)
        acquired = False
        try:
            job_dir = self._job_dir(job_id)
            _metadata, jsonl_path, clip_count = self._extract_snapshot(job_id, archive_hash)
            if event.is_set():
                self._update(job_id, status="canceled", phase="canceled", checkpoint_path=None)
                return

            gpu_deadline = self.clock() + (ONE_STEP_TIMEOUT_SECONDS if record["steps"] == 1 else timeout)
            self._update(job_id, status="waiting", phase="waiting_gpu", error=None)
            while not event.is_set():
                if self.speech_lock.acquire(timeout=0.2):
                    acquired = True
                    break
                if self.clock() >= gpu_deadline:
                    raise JobError("Training job exceeded its time limit while waiting for the GPU.")
            if event.is_set():
                self._update(job_id, status="canceled", phase="canceled", checkpoint_path=None)
                return
            self.prepare_gpu()
            prepared = job_dir / "prepared"
            prepared.mkdir()
            tar_pattern = Path("audios") / "shard-%06d.tar"
            labels_pattern = Path("txts") / "shard-%06d.jsonl"
            tokenizer_path = model_dir / "audio_tokenizer"
            preprocess = self._child_command("omnivoice.scripts.extract_audio_tokens", [
                "--input_jsonl", str(jsonl_path),
                "--tar_output_pattern", str(tar_pattern),
                "--jsonl_output_pattern", str(labels_pattern),
                "--tokenizer_path", str(tokenizer_path),
                "--loader_workers", "0", "--nj_per_gpu", "1",
                "--min_num_shards", "1", "--shuffle", "false",
            ])
            phase_deadline = self.clock() + ONE_STEP_TIMEOUT_SECONDS if record["steps"] == 1 else deadline
            self._run_child(job_id, "preprocessing", preprocess, phase_deadline, event, cwd=prepared)
            manifest = prepared / "data.lst"
            self._normalize_preprocessor_manifest(manifest, prepared)
            self._validate_preprocessed(manifest, clip_count)

            train_config_path = job_dir / "train_config.json"
            data_config_path = job_dir / "data_config.json"
            output_dir = job_dir / "run"
            data_config = {"train": [{"manifest_path": [str(manifest.resolve())]}]}
            _atomic_json(data_config_path, data_config)
            steps = record["steps"]
            train_config = {
                "output_dir": str(output_dir.resolve()),
                "data_config": str(data_config_path.resolve()),
                "init_from_checkpoint": str(model_dir.resolve()),
                "steps": steps,
                "save_steps": min(record.get("save_steps", 1), steps),
                "logging_steps": 1,
                "eval_steps": max(1, steps),
                "batch_tokens": record.get("batch_tokens", 256),
                "gradient_accumulation_steps": 1,
                "num_workers": 1,
                "min_sample_tokens": 50,
                "max_sample_tokens": 2000,
                "max_batch_size": 1,
                "mixed_precision": "bf16",
                "attn_implementation": "sdpa",
                "use_deepspeed": False,
                "deepspeed_config": None,
                "warmup_type": "ratio",
                "warmup_ratio": 0.0,
                "warmup_steps": 0,
                "keep_last_n_checkpoints": record.get("keep_last_n_checkpoints", 2),
            }
            _atomic_json(train_config_path, train_config)
            train = self._child_command("omnivoice.cli.train", [
                "--train_config", str(train_config_path),
                "--data_config", str(data_config_path),
                "--output_dir", str(output_dir),
            ])
            phase_deadline = self.clock() + ONE_STEP_TIMEOUT_SECONDS if record["steps"] == 1 else deadline
            self._run_child(job_id, "training", train, phase_deadline, event, cwd=prepared)
            self.speech_lock.release()
            acquired = False
            if event.is_set():
                self._update(job_id, status="canceled", phase="canceled", checkpoint_path=None)
                return
            finalization_deadline = (self.clock() + ONE_STEP_TIMEOUT_SECONDS
                                     if record["steps"] == 1 else deadline)
            self._update(job_id, status="training", phase="finalizing_checkpoint")
            checkpoint = output_dir / f"checkpoint-{steps}"
            self._verify_checkpoint(checkpoint, model_dir, event, finalization_deadline)
            if event.is_set():
                self._update(job_id, status="canceled", phase="canceled", checkpoint_path=None)
                return
            self._complete_job(job_id, checkpoint, event)
        except JobError as exc:
            if event.is_set():
                self._update(job_id, status="canceled", phase="canceled", error=None,
                             checkpoint_path=None)
            else:
                self._update(job_id, status="failed", phase="failed", error=str(exc)[:1000],
                             checkpoint_path=None)
        finally:
            if acquired:
                self.speech_lock.release()

    def _extract_snapshot(self, job_id: str, expected_hash: str) -> tuple[dict[str, Any], Path, int]:
        job_dir = self._job_dir(job_id)
        archive_path = job_dir / "input.zip"
        if archive_path.is_symlink() or not archive_path.is_file():
            raise JobError("The immutable dataset snapshot is missing.")
        digest = hashlib.sha256()
        with archive_path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != expected_hash:
            raise JobError("The immutable dataset snapshot failed its integrity check.")

        input_root = job_dir / "input"
        input_root.mkdir(exist_ok=True)
        _inside(input_root, job_dir)
        metadata_bytes = None
        dataset_bytes = None
        audio_members: set[str] = set()
        total_size = 0
        with zipfile.ZipFile(archive_path, "r") as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_MEMBERS:
                raise JobError("Dataset export contains too many files.")
            seen: set[str] = set()
            for info in infos:
                name = info.filename
                posix = PurePosixPath(name)
                if "\\" in name or posix.is_absolute() or any(part in {"", ".", ".."} for part in posix.parts):
                    raise JobError("Dataset export contains an unsafe archive path.")
                if name in seen:
                    raise JobError("Dataset export contains duplicate archive paths.")
                seen.add(name)
                mode = (info.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode):
                    raise JobError("Dataset export contains a symlink.")
                if info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
                    raise JobError("Dataset export contains an oversized file.")
                total_size += info.file_size
                if total_size > MAX_ARCHIVE_BYTES:
                    raise JobError("Dataset export exceeds the expanded size limit.")
                if info.compress_size == 0 and info.file_size > 0 or (
                    info.compress_size and info.file_size / info.compress_size > 200
                ):
                    raise JobError("Dataset export has an unsafe compression ratio.")
                if info.is_dir():
                    if name != "audio/":
                        raise JobError("Dataset export contains an unexpected directory.")
                    (input_root / "audio").mkdir(exist_ok=True)
                    continue
                if name == "metadata.jsonl":
                    if info.file_size > 4 * 1024 * 1024:
                        raise JobError("Dataset metadata exceeds the supported size limit.")
                    metadata_bytes = archive.read(info)
                    continue
                if name == "dataset.json":
                    if info.file_size > 2 * 1024 * 1024:
                        raise JobError("Dataset manifest exceeds the supported size limit.")
                    dataset_bytes = archive.read(info)
                    continue
                if name == "README.txt":
                    if info.file_size > 1024 * 1024:
                        raise JobError("Dataset readme exceeds the supported size limit.")
                    continue
                if posix.parts[0] == "audio" and len(posix.parts) == 2 and posix.suffix.lower() == ".wav":
                    rel = PurePosixPath(*posix.parts)
                    target = input_root.joinpath(*rel.parts)
                    _inside(target, input_root)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info, "r") as inp, target.open("xb") as out:
                        shutil.copyfileobj(inp, out, length=1024 * 1024)
                    audio_members.add(name)
                    continue
                raise JobError("Dataset export contains an unsupported file.")
        if metadata_bytes is None or dataset_bytes is None:
            raise JobError("Dataset export is missing its metadata or manifest.")
        try:
            dataset_manifest = json.loads(dataset_bytes)
            if dataset_manifest.get("dataset_id") != self._jobs[job_id]["dataset_id"]:
                raise JobError("Dataset export belongs to a different dataset.")
            if int(dataset_manifest.get("version", -1)) != self._jobs[job_id]["dataset_version"]:
                raise JobError("Dataset export version changed after job submission.")
        except (ValueError, TypeError) as exc:
            raise JobError("Dataset export manifest is invalid.") from exc

        rows = []
        ids: set[str] = set()
        try:
            text = metadata_bytes.decode("utf-8")
            for line in text.splitlines():
                if not line.strip():
                    continue
                if len(line) > 1_000_000:
                    raise JobError("Dataset metadata contains an oversized record.")
                row = json.loads(line)
                clip_id = row.get("id") if isinstance(row, dict) else None
                audio_path = row.get("audio_path") if isinstance(row, dict) else None
                transcript = row.get("text") if isinstance(row, dict) else None
                if not isinstance(clip_id, str) or not CLIP_ID_RE.fullmatch(clip_id) or clip_id in ids:
                    raise JobError("Dataset metadata contains an invalid or duplicate clip id.")
                if audio_path != f"audio/{clip_id}.wav" or audio_path not in audio_members:
                    raise JobError("Dataset metadata references an unexpected or missing audio file.")
                if not isinstance(transcript, str) or not transcript.strip() or len(transcript) > 2_000:
                    raise JobError("Every training clip needs a reviewed transcript of at most 2,000 characters.")
                audio_file = _inside(input_root / "audio" / f"{clip_id}.wav", input_root)
                if audio_file.is_symlink() or not audio_file.is_file():
                    raise JobError("A training audio file is missing or linked.")
                rows.append({**row, "audio_path": str(audio_file.resolve())})
                ids.add(clip_id)
                if len(rows) > MAX_DATASET_CLIPS:
                    raise JobError("Dataset exceeds the supported clip limit.")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JobError("Dataset metadata JSONL is invalid.") from exc
        if not rows or set(f"audio/{clip_id}.wav" for clip_id in ids) != audio_members:
            raise JobError("Dataset export must contain matching metadata and audio for every clip.")
        normalized = job_dir / "input.jsonl"
        with normalized.open("x", encoding="utf-8") as out:
            for row in rows:
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
        return dataset_manifest, normalized, len(rows)

    def _child_command(self, module: str, args: list[str]) -> list[str]:
        return [self.python, "-m", module, *args]

    def _child_env(self) -> dict[str, str]:
        env = os.environ.copy()
        extras = str((self.data_dir / "training" / "runtime" / "site-packages").resolve())
        backend = str(BACKEND_DIR.resolve())
        prior = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = os.pathsep.join(part for part in (extras, backend, prior) if part)
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
        env["HF_DATASETS_OFFLINE"] = "1"
        env["VOICEVAULT_DATA_DIR"] = str(self.data_dir.resolve())
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def _run_child(self, job_id: str, phase: str, command: list[str], deadline: float,
                   cancel_event: threading.Event, *, cwd: Path = BACKEND_DIR) -> None:
        if cancel_event.is_set():
            raise JobError("Job was canceled.")
        if self.clock() >= deadline:
            raise JobError("Training job exceeded its time limit.")
        status = "preprocessing" if phase == "preprocessing" else "training"
        self._update(job_id, status=status, phase=phase, error=None)
        kwargs: dict[str, Any] = {"cwd": str(cwd), "env": self._child_env(),
                                  "stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE,
                                  "stderr": subprocess.STDOUT, "bufsize": 0}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = self.popen_factory(command, **kwargs)
        except OSError as exc:
            raise JobError(f"Could not start OmniVoice {phase}: {exc}") from exc
        with self._mutex:
            self._active_process = (job_id, process)
        reader = threading.Thread(target=self._drain_output, args=(job_id, process),
                                  name=f"voicevault-log-{job_id[:8]}", daemon=True)
        try:
            reader.start()
            while True:
                if cancel_event.is_set():
                    self._stop_process(process)
                    raise JobError("Job was canceled.")
                remaining = deadline - self.clock()
                if remaining <= 0:
                    self._stop_process(process)
                    raise JobError("Training job exceeded the 180-second one-step limit." if
                                   self._jobs[job_id]["steps"] == 1 else
                                   "Training job exceeded the six-hour limit.")
                try:
                    return_code = process.wait(timeout=min(0.25, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
            reader.join(timeout=2)
            if cancel_event.is_set():
                raise JobError("Job was canceled.")
            if return_code != 0:
                raise JobError(f"OmniVoice {phase} exited with code {return_code}; see the job log.")
        except BaseException:
            self._stop_process(process)
            raise
        finally:
            if getattr(process, "stdout", None) is not None:
                try:
                    process.stdout.close()
                except OSError:
                    pass
            reader.join(timeout=2)
            with self._mutex:
                if self._active_process and self._active_process[1] is process:
                    self._active_process = None

    def _drain_output(self, job_id: str, process: Any) -> None:
        stream = getattr(process, "stdout", None)
        if stream is None:
            return
        try:
            while True:
                chunk = stream.read(8192)
                if not chunk:
                    return
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8", errors="replace")
                self._append_log(job_id, chunk)
        except (OSError, ValueError):
            return

    def _append_log(self, job_id: str, chunk: bytes) -> None:
        path = self._job_dir(job_id) / "worker.log"
        with self._mutex:
            if path.is_symlink():
                return
            existing = path.read_bytes() if path.is_file() else b""
            combined = existing + chunk
            if len(combined) > MAX_LOG_BYTES:
                combined = combined[-MAX_LOG_BYTES:]
            temp = path.with_name(".worker.log.tmp")
            with temp.open("wb") as out:
                out.write(combined)
            os.replace(temp, path)

    def _validate_preprocessed(self, manifest: Path, expected_count: int) -> None:
        if manifest.is_symlink() or not manifest.is_file():
            raise JobError("OmniVoice preprocessing produced no data.lst manifest.")
        prepared_root = manifest.parent.resolve()
        total = 0
        label_files: list[Path] = []
        try:
            for line in manifest.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) != 4:
                    raise JobError("OmniVoice preprocessing wrote an invalid manifest row.")
                tar_path, labels_path, count, _seconds = parts
                tar_file = Path(tar_path)
                label_file = Path(labels_path)
                if not tar_file.is_absolute():
                    tar_file = prepared_root / tar_file
                if not label_file.is_absolute():
                    label_file = prepared_root / label_file
                _inside(tar_file, prepared_root)
                _inside(label_file, prepared_root)
                if not tar_file.is_file() or tar_file.is_symlink() or not label_file.is_file() or label_file.is_symlink():
                    raise JobError("OmniVoice preprocessing produced missing shard files.")
                if tar_file.parent != prepared_root / "audios" or label_file.parent != prepared_root / "txts":
                    raise JobError("OmniVoice preprocessing wrote shard files outside its job workspace.")
                if tar_file.suffix.lower() != ".tar" or label_file.suffix.lower() != ".jsonl":
                    raise JobError("OmniVoice preprocessing wrote an unexpected shard format.")
                if int(count) <= 0:
                    raise JobError("OmniVoice preprocessing produced an empty shard.")
                total += int(count)
                label_files.append(label_file)
        except (OSError, ValueError) as exc:
            raise JobError("OmniVoice preprocessing manifest could not be read.") from exc
        if total != expected_count:
            raise JobError(f"OmniVoice preprocessing retained {total} of {expected_count} clips; fix failed audio before training.")
        observed = 0
        out_of_range = 0
        for path in label_files:
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    length = row.get("num_tokens")
                    if not isinstance(length, int):
                        raise JobError("OmniVoice tokenized metadata is missing token lengths.")
                    observed += 1
                    if length < 50 or length > 2_000:
                        out_of_range += 1
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                raise JobError("OmniVoice tokenized shard metadata is invalid.") from exc
        if observed != expected_count:
            raise JobError(f"OmniVoice token shards contain {observed} of {expected_count} clips.")
        if out_of_range:
            raise JobError(f"OmniVoice SDPA batching would skip {out_of_range} reviewed clips outside 50–2,000 audio tokens.")

    def _normalize_preprocessor_manifest(self, manifest: Path, prepared_root: Path) -> None:
        """Use relative paths because OmniVoice's manifest parser splits on whitespace."""
        if manifest.is_symlink() or not manifest.is_file():
            raise JobError("OmniVoice preprocessing produced no data.lst manifest.")
        raw_rows = manifest.read_text(encoding="utf-8").splitlines()
        tar_files = sorted((prepared_root / "audios").glob("shard-*.tar"))
        label_files = sorted((prepared_root / "txts").glob("shard-*.jsonl"))
        if not raw_rows or len(raw_rows) != len(tar_files) or len(tar_files) != len(label_files):
            raise JobError("OmniVoice preprocessing produced an inconsistent shard manifest.")
        normalized: list[str] = []
        for raw, tar_file, label_file in zip(raw_rows, tar_files, label_files):
            match = re.search(r"\s+(\d+)\s+([0-9]+(?:\.[0-9]+)?)\s*$", raw)
            if not match:
                raise JobError("OmniVoice preprocessing wrote an invalid shard manifest row.")
            try:
                with label_file.open(encoding="utf-8") as stream:
                    label_count = sum(1 for line in stream if line.strip())
            except OSError as exc:
                raise JobError("OmniVoice preprocessing produced an unreadable label shard.") from exc
            if int(match.group(1)) != label_count:
                raise JobError("OmniVoice shard counts do not match its label files.")
            normalized.append(f"audios/{tar_file.name} txts/{label_file.name} {label_count} {match.group(2)}")
        manifest.write_text("\n".join(normalized) + "\n", encoding="utf-8")

    def _verify_checkpoint(self, checkpoint: Path, model_dir: Path,
                           cancel_event: threading.Event | None = None,
                           deadline: float | None = None) -> None:
        self._check_finalization(cancel_event, deadline)
        if checkpoint.is_symlink() or not checkpoint.is_dir():
            raise JobError("OmniVoice exited without a completed checkpoint directory.")
        if not (checkpoint / "config.json").is_file() or not _valid_safetensors(checkpoint / "model.safetensors"):
            raise JobError("OmniVoice checkpoint is missing a valid model config or safetensors weights.")
        try:
            config = json.loads((checkpoint / "config.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise JobError("OmniVoice checkpoint config is invalid.") from exc
        if not isinstance(config, dict) or config.get("model_type") != "omnivoice":
            raise JobError("Training produced a checkpoint with the wrong model type.")
        tokenizer_markers = ("tokenizer.json", "tokenizer_config.json", "vocab.json")
        if not any((checkpoint / name).is_file() for name in tokenizer_markers):
            raise JobError("OmniVoice checkpoint is missing its text tokenizer files.")
        audio_tokenizer = checkpoint / "audio_tokenizer"
        if audio_tokenizer.exists():
            raise JobError("Unexpected audio tokenizer already exists in the training checkpoint.")
        self._copy_audio_tokenizer(model_dir / "audio_tokenizer", audio_tokenizer, cancel_event, deadline)
        self._check_finalization(cancel_event, deadline)
        if not self._checkpoint_valid(checkpoint):
            raise JobError("Could not package the local audio tokenizer into the checkpoint.")

    def _check_finalization(self, cancel_event: threading.Event | None, deadline: float | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise JobError("Job was canceled.")
        if deadline is not None and self.clock() >= deadline:
            raise JobError("Training checkpoint finalization exceeded its time limit.")

    def _copy_audio_tokenizer(self, source: Path, destination: Path,
                              cancel_event: threading.Event | None = None,
                              deadline: float | None = None) -> None:
        destination.mkdir()
        for current, directories, files in os.walk(source, followlinks=False):
            self._check_finalization(cancel_event, deadline)
            current_path = Path(current)
            relative = current_path.relative_to(source)
            target_dir = destination / relative
            for directory in list(directories):
                source_dir = current_path / directory
                if source_dir.is_symlink():
                    directories.remove(directory)
                    raise JobError("Audio tokenizer contains a linked directory.")
                (target_dir / directory).mkdir(exist_ok=True)
            for filename in files:
                self._check_finalization(cancel_event, deadline)
                src = current_path / filename
                if not src.resolve().is_file():
                    raise JobError("Audio tokenizer contains an invalid file.")
                before = src.stat()
                destination_file = target_dir / filename
                with src.open("rb") as source_stream, destination_file.open("xb") as target_stream:
                    while chunk := source_stream.read(1024 * 1024):
                        self._check_finalization(cancel_event, deadline)
                        target_stream.write(chunk)
                    target_stream.flush()
                    os.fsync(target_stream.fileno())
                after = src.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise JobError("Audio tokenizer changed while the checkpoint was being finalized.")
                os.utime(destination_file, ns=(before.st_atime_ns, before.st_mtime_ns))

    def _stop_process(self, process: Any) -> None:
        with self._terminate_mutex:
            if process.poll() is None:
                self.terminate_tree(process)

    @staticmethod
    def _checkpoint_valid(checkpoint: Path) -> bool:
        if checkpoint.is_symlink() or not checkpoint.is_dir():
            return False
        try:
            config = json.loads((checkpoint / "config.json").read_text(encoding="utf-8"))
            if not isinstance(config, dict) or config.get("model_type") != "omnivoice":
                return False
            if not _valid_safetensors(checkpoint / "model.safetensors"):
                return False
            if not any((checkpoint / name).is_file() for name in ("tokenizer.json", "tokenizer_config.json", "vocab.json")):
                return False
            audio_tokenizer = checkpoint / "audio_tokenizer"
            return audio_tokenizer.is_dir() and not audio_tokenizer.is_symlink() and any(audio_tokenizer.iterdir())
        except (OSError, ValueError):
            return False

    def _release_speech_models(self) -> None:
        from core.gpu_scheduler import release_tts_for_asr
        from asr.service import unload_transcriber
        release_tts_for_asr()
        unload_transcriber()

    def _terminate_tree(self, process: Any) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=10,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                               check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

