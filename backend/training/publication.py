"""Explicit, atomic publication of completed OmniVoice inference checkpoints."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping

from core import paths as app_paths

JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
MANIFEST_NAME = "publication.json"
MAX_FILES = 12_000
MAX_BYTES = 32 * 1024**3
MAX_CONFIG_JSON_BYTES = 2 * 1024**2
MAX_TOKENIZER_JSON_BYTES = 64 * 1024**2
DISK_RESERVE_BYTES = 64 * 1024**2
_TEXT_TOKENIZER_FILES = {
    "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "added_tokens.json",
    "vocab.json", "merges.txt", "vocab.txt", "tokenizer.model", "spiece.model",
    "sentencepiece.bpe.model", "tekken.json",
}
_TOKENIZER_REQUIRED = ({"tokenizer.json"}, {"vocab.json", "merges.txt"}, {"vocab.txt"},
                       {"tokenizer.model"}, {"spiece.model"}, {"sentencepiece.bpe.model"})
_PATH_KEYS = {"path", "file", "name_or_path", "_name_or_path", "model_name_or_path",
              "tokenizer_file", "vocab_file", "merges_file"}
_RAW_TOKENIZER_JSON = {"tokenizer.json", "vocab.json", "added_tokens.json"}
_AUDIO_CONFIG_JSON = {"config.json", "tokenizer_config.json", "special_tokens_map.json",
                      "preprocessor_config.json", "feature_extractor_config.json", "generation_config.json"}
_locks_guard = threading.Lock()
_locks: dict[str, threading.RLock] = {}


class PublicationError(ValueError):
    """A checkpoint cannot be safely published as a local inference model."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _lock_for(root: Path) -> threading.RLock:
    key = os.path.normcase(str(root.resolve()))
    with _locks_guard:
        return _locks.setdefault(key, threading.RLock())


def _within(path: Path, root: Path) -> Path:
    root_abs = root.resolve()
    path_abs = path.resolve()
    try:
        path_abs.relative_to(root_abs)
    except ValueError as exc:
        raise PublicationError("Checkpoint path is outside its owned training job folder.") from exc
    return path_abs


def _reject_link_components(path: Path, root: Path) -> None:
    root_abs = root.absolute()
    candidate = path.absolute()
    try:
        relative = candidate.relative_to(root_abs)
    except ValueError as exc:
        raise PublicationError("Managed path is outside its owned data folder.") from exc
    current = root_abs
    if current.is_symlink():
        raise PublicationError("Symlinks are not allowed in owned training paths.")
    for part in relative.parts:
        if part in (".", ".."):
            raise PublicationError("Managed path contains an unsafe component.")
        current = current / part
        if current.is_symlink():
            raise PublicationError("Symlinks are not allowed in owned training paths.")


def _scan_checkpoint(checkpoint: Path, jobs_root: Path, job_id: str) -> list[Path]:
    job_root = jobs_root / job_id
    _reject_link_components(job_root, jobs_root)
    _reject_link_components(checkpoint, job_root)
    source = _within(checkpoint, job_root)
    if source.parent.name != "run" or source.parent.parent != _within(job_root, jobs_root):
        raise PublicationError("Completed checkpoint must be directly inside this job's run folder.")
    if not source.is_dir():
        raise PublicationError("Completed checkpoint folder is missing.")

    # Refuse links anywhere in this owned checkpoint, including files not selected for inference.
    observed = 0
    for current, directories, filenames in os.walk(source, followlinks=False):
        folder = Path(current)
        for name in directories + filenames:
            item = folder / name
            observed += 1
            if observed > MAX_FILES:
                raise PublicationError("Checkpoint contains too many files to inspect safely.")
            if item.is_symlink():
                raise PublicationError("Checkpoint contains a symlink; publication is refused.")
            if name in directories and not item.is_dir():
                raise PublicationError("Checkpoint contains an unexpected non-directory entry.")
            if name in filenames and not item.is_file():
                raise PublicationError("Checkpoint contains an unexpected non-file entry.")
    return [source / "config.json", source / "model.safetensors"]


def _tokenizer_files(checkpoint: Path) -> list[Path]:
    candidates = [checkpoint / name for name in _TEXT_TOKENIZER_FILES
                  if (checkpoint / name).is_file()]
    names = {path.name for path in candidates}
    if not any(required <= names for required in _TOKENIZER_REQUIRED):
        raise PublicationError("Checkpoint is missing usable local text-tokenizer files.")
    return sorted(candidates, key=lambda p: p.name.casefold())


def _audio_weight_files(audio_dir: Path) -> set[Path]:
    """Validate local audio-tokenizer weight/index files without opening tensors."""
    files = {path.resolve() for path in audio_dir.rglob("*") if path.is_file()}
    by_name: dict[str, Path] = {path.relative_to(audio_dir.resolve()).as_posix(): path for path in files}
    direct = {name for name in by_name if "/" not in name and name in {"model.safetensors", "pytorch_model.bin"}}
    if direct:
        return {by_name[name] for name in direct if by_name[name].stat().st_size > 0}

    indices = [name for name in by_name if name in {"model.safetensors.index.json", "pytorch_model.bin.index.json"}]
    if len(indices) != 1:
        raise PublicationError("Bundled audio tokenizer needs a supported local model weight file or shard index.")
    index_path = by_name[indices[0]]
    try:
        if index_path.stat().st_size > MAX_CONFIG_JSON_BYTES:
            raise PublicationError("Audio tokenizer shard index is too large to validate.")
        index = json.loads(index_path.read_text(encoding="utf-8"))
        weight_map = index.get("weight_map") if isinstance(index, dict) else None
        shards = set(weight_map.values()) if isinstance(weight_map, dict) else set()
        if not shards or any(not isinstance(name, str) for name in shards):
            raise PublicationError("Audio tokenizer shard index has no valid weight map.")
        selected = set()
        for shard in shards:
            posix = PurePosixPath(shard)
            if posix.is_absolute() or "\\" in shard or any(part in (".", "..") for part in posix.parts):
                raise PublicationError("Audio tokenizer shard index contains an unsafe path.")
            resolved = posix.as_posix()
            path = by_name.get(resolved)
            if path is None or path.stat().st_size <= 0:
                raise PublicationError("Audio tokenizer shard index names a missing weight shard.")
            selected.add(path)
        return selected
    except PublicationError:
        raise
    except (OSError, UnicodeDecodeError, ValueError, TypeError, AttributeError) as exc:
        raise PublicationError("Audio tokenizer shard index is invalid.") from exc


def _safe_json_bytes(source: Path, *, max_bytes: int = MAX_CONFIG_JSON_BYTES) -> bytes:
    before = source.stat()
    try:
        if before.st_size > max_bytes:
            raise PublicationError(f"Checkpoint JSON file is too large: {source.name}.")
        value = json.loads(source.read_text(encoding="utf-8"))
    except PublicationError:
        raise
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise PublicationError(f"Checkpoint JSON is invalid: {source.name}.") from exc
    after = source.stat()
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise PublicationError(f"Checkpoint JSON changed while being read: {source.name}.")

    def clean(item: Any) -> Any:
        if isinstance(item, dict):
            out = {}
            for key, child in item.items():
                if not isinstance(key, str):
                    continue
                if key.lower() in _PATH_KEYS and isinstance(child, str):
                    if Path(child).is_absolute() or PureWindowsPath(child).is_absolute():
                        continue
                out[key] = clean(child)
            return out
        if isinstance(item, list):
            return [clean(child) for child in item]
        return item

    return (json.dumps(clean(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _selected_files(checkpoint: Path) -> list[tuple[Path, str, bytes | None]]:
    config_path = checkpoint / "config.json"
    weights_path = checkpoint / "model.safetensors"
    if not config_path.is_file() or config_path.is_symlink():
        raise PublicationError("Checkpoint is missing config.json.")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise PublicationError("Checkpoint model config is unreadable.") from exc
    if not isinstance(config, dict) or config.get("model_type") != "omnivoice":
        raise PublicationError("Checkpoint config is not an OmniVoice model.")
    if not weights_path.is_file() or weights_path.is_symlink():
        raise PublicationError("Checkpoint is missing the single-file model.safetensors weights.")

    # Reuse the bounded header verifier already used to accept training input models.
    from training.jobs import _valid_safetensors
    if not _valid_safetensors(weights_path):
        raise PublicationError("Checkpoint model.safetensors is invalid or incomplete.")
    selected: list[tuple[Path, str, bytes | None]] = [
        (config_path, "config.json", _safe_json_bytes(config_path)),
        (weights_path, "model.safetensors", None),
    ]
    for path in _tokenizer_files(checkpoint):
        if path.name in _RAW_TOKENIZER_JSON and path.stat().st_size > MAX_TOKENIZER_JSON_BYTES:
            raise PublicationError(f"Tokenizer JSON is larger than the {MAX_TOKENIZER_JSON_BYTES // 1024**2} MiB limit.")
        transformed = (_safe_json_bytes(path) if path.name in {"tokenizer_config.json", "special_tokens_map.json"}
                       else None)
        selected.append((path, path.name, transformed))

    audio_dir = checkpoint / "audio_tokenizer"
    if not audio_dir.is_dir() or audio_dir.is_symlink():
        raise PublicationError("Checkpoint is missing its bundled audio_tokenizer folder.")
    audio_config = audio_dir / "config.json"
    if not audio_config.is_file() or audio_config.is_symlink():
        raise PublicationError("Bundled audio tokenizer is missing config.json.")
    weight_files = _audio_weight_files(audio_dir)

    for current, directories, filenames in os.walk(audio_dir, followlinks=False):
        folder = Path(current)
        for name in directories:
            path = folder / name
            if path.is_symlink():
                raise PublicationError("Audio tokenizer contains a symlink.")
        for name in filenames:
            path = folder / name
            if path.is_symlink() or not path.is_file():
                raise PublicationError("Audio tokenizer contains a non-regular file.")
            rel = path.relative_to(checkpoint).as_posix()
            is_weight = path.resolve() in weight_files
            is_metadata = path.name in _AUDIO_CONFIG_JSON or path.name in _RAW_TOKENIZER_JSON or path.name in {
                "model.safetensors.index.json", "pytorch_model.bin.index.json"}
            is_tokenizer_text = path.name in _TEXT_TOKENIZER_FILES
            if not (is_weight or is_metadata or is_tokenizer_text):
                continue
            if path.name in _RAW_TOKENIZER_JSON and path.stat().st_size > MAX_TOKENIZER_JSON_BYTES:
                raise PublicationError(f"Audio tokenizer JSON is larger than the {MAX_TOKENIZER_JSON_BYTES // 1024**2} MiB limit.")
            if is_weight and path.stat().st_size <= 0:
                raise PublicationError("Audio tokenizer contains an empty weight file.")
            transformed = (_safe_json_bytes(path) if path.suffix.lower() == ".json"
                           and path.name not in _RAW_TOKENIZER_JSON
                           and path.name not in {"model.safetensors.index.json", "pytorch_model.bin.index.json"}
                           else None)
            selected.append((path, rel, transformed))
    if not any(path.resolve() in weight_files for path, _rel, _transformed in selected):
        raise PublicationError("Bundled audio tokenizer has no supported local weight files.")
    return selected


def _canonical_manifest_hash(manifest: dict[str, Any]) -> str:
    value = {key: item for key, item in manifest.items() if key != "manifest_sha256"}
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _read_manifest(folder: Path, job_id: str, *, stat_check: bool = True) -> dict[str, Any] | None:
    manifest_path = folder / MANIFEST_NAME
    if folder.is_symlink() or manifest_path.is_symlink() or not manifest_path.is_file():
        return None
    try:
        if manifest_path.stat().st_size > 1024 * 1024:
            return None
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
            or manifest.get("job_id") != job_id or manifest.get("engine") != "omnivoice"
            or not isinstance(manifest.get("dataset_name"), str)
            or len(manifest.get("dataset_name", "")) > 100
            or not isinstance(manifest.get("published_at"), str)
            or len(manifest.get("published_at", "")) > 64
            or not isinstance(manifest.get("files"), list)
            or not manifest["files"] or len(manifest["files"]) > MAX_FILES
            or not isinstance(manifest.get("manifest_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", manifest["manifest_sha256"])
            or manifest.get("voice_quality_verified") is not False
            or manifest.get("manifest_sha256") != _canonical_manifest_hash(manifest)):
        return None
    try:
        datetime.fromisoformat(manifest["published_at"])
    except ValueError:
        return None
    if stat_check:
        total = 0
        seen_paths = set()
        for record in manifest["files"]:
            if (not isinstance(record, dict) or not isinstance(record.get("path"), str)
                    or not isinstance(record.get("size"), int) or isinstance(record.get("size"), bool)
                    or record["size"] < 0 or not isinstance(record.get("sha256"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])):
                return None
            posix = PurePosixPath(record["path"])
            if posix.is_absolute() or "\\" in record["path"] or any(p in (".", "..") for p in posix.parts):
                return None
            if record["path"] in seen_paths:
                return None
            seen_paths.add(record["path"])
            file_path = folder.joinpath(*posix.parts)
            try:
                _reject_link_components(file_path, folder)
                st = file_path.stat()
            except (OSError, PublicationError):
                return None
            if not stat.S_ISREG(st.st_mode) or st.st_size != record["size"]:
                return None
            total += st.st_size
        if total > MAX_BYTES:
            return None
    return manifest


def _public_record(folder: Path, manifest: dict[str, Any], *, reused: bool | None = None) -> dict[str, Any]:
    result = {"job_id": manifest["job_id"], "dataset_name": manifest["dataset_name"],
              "path": str(folder),
              "published_at": manifest["published_at"], "voice_quality_verified": False,
              "manifest_sha256": manifest["manifest_sha256"]}
    if reused is not None:
        result["reused"] = reused
    return result


class PublicationService:
    def __init__(self, data_dir: Path | None = None):
        self.data_dir = Path(data_dir) if data_dir is not None else app_paths.DATA_DIR
        self.training_root = self.data_dir / "training"
        self.jobs_root = self.training_root / "jobs"
        self.root = self.training_root / "published_models"
        self.staging_root = self.training_root / ".publication-staging"
        self._lock = _lock_for(self.root)

    def _require_owned_root(self) -> None:
        _reject_link_components(self.training_root, self.data_dir)
        _reject_link_components(self.jobs_root, self.training_root)
        if not self.jobs_root.is_dir():
            raise PublicationError("Training job folder is missing.")

    def publish(self, completed_job: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(completed_job, Mapping):
            raise PublicationError("Completed job metadata is invalid.")
        job_id = completed_job.get("id")
        if not isinstance(job_id, str) or not JOB_ID_RE.fullmatch(job_id):
            raise PublicationError("Invalid training job id.")
        if completed_job.get("status") != "completed":
            raise PublicationError("Only a completed training job can be published.")
        checkpoint_value = completed_job.get("checkpoint_path")
        if not isinstance(checkpoint_value, str) or not checkpoint_value.strip():
            raise PublicationError("Completed job has no checkpoint path.")
        checkpoint = Path(checkpoint_value).expanduser()
        with self._lock:
            self._require_owned_root()
            job_root = self.jobs_root / job_id
            job_record_path = job_root / "job.json"
            _reject_link_components(job_record_path, self.jobs_root)
            try:
                if job_record_path.stat().st_size > 1024 * 1024:
                    raise PublicationError("Training job record is too large to validate.")
                persisted = json.loads(job_record_path.read_text(encoding="utf-8"))
            except PublicationError:
                raise
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                raise PublicationError("Persisted training job record is missing or unreadable.") from exc
            if (not isinstance(persisted, dict) or persisted.get("id") != job_id
                    or persisted.get("status") != "completed"
                    or not isinstance(persisted.get("checkpoint_path"), str)):
                raise PublicationError("Persisted training job is not completed with a checkpoint.")
            try:
                persisted_checkpoint = Path(persisted["checkpoint_path"]).expanduser().resolve()
                supplied_checkpoint = checkpoint.resolve()
            except (OSError, RuntimeError, ValueError) as exc:
                raise PublicationError("Checkpoint path could not be resolved safely.") from exc
            if persisted_checkpoint != supplied_checkpoint:
                raise PublicationError("Checkpoint path does not match the completed job record.")
            try:
                checkpoint.absolute().relative_to(job_root.absolute())
            except ValueError as exc:
                raise PublicationError("Checkpoint path is outside its owned training job folder.") from exc
            _reject_link_components(checkpoint, job_root)
            checkpoint = _within(checkpoint, job_root)
            if str(checkpoint) != str(Path(checkpoint_value).expanduser().resolve()):
                raise PublicationError("Checkpoint path did not resolve consistently.")
            _scan_checkpoint(checkpoint, self.jobs_root, job_id)
            selected = _selected_files(checkpoint)
            total_size = sum((len(transformed) if transformed is not None else source.stat().st_size)
                             for source, _rel, transformed in selected)
            if len(selected) > MAX_FILES or total_size > MAX_BYTES:
                raise PublicationError("Inference checkpoint exceeds the publication size limits.")

            self.root.mkdir(parents=True, exist_ok=True)
            _reject_link_components(self.root, self.training_root)
            destination = self.root / job_id
            existing = _read_manifest(destination, job_id)
            if existing is not None:
                source_signatures = []
                for source, rel, transformed in selected:
                    digest = hashlib.sha256(transformed).hexdigest() if transformed is not None else _sha256(source)
                    size = len(transformed) if transformed is not None else source.stat().st_size
                    source_signatures.append({"path": rel, "size": size, "sha256": digest})
                if (sorted(source_signatures, key=lambda item: item["path"])
                        == sorted(existing["files"], key=lambda item: item.get("path", ""))):
                    try:
                        for record in existing["files"]:
                            posix = PurePosixPath(record["path"])
                            published_file = destination.joinpath(*posix.parts)
                            if _sha256(published_file) != record["sha256"]:
                                raise PublicationError("Existing published model failed its content-hash check.")
                    except OSError as exc:
                        raise PublicationError(f"Could not verify existing publication files: {exc}") from exc
                    return _public_record(destination, existing, reused=True)
                raise PublicationError("A different publication already exists for this job id.")
            if destination.exists() or destination.is_symlink():
                raise PublicationError("Published model folder exists without a matching valid manifest.")

            try:
                available = shutil.disk_usage(self.training_root).free
            except OSError as exc:
                raise PublicationError(f"Could not check free disk space before publishing: {exc}") from exc
            if available < total_size + DISK_RESERVE_BYTES:
                raise PublicationError("Not enough free disk space to publish this model safely.")

            self.staging_root.mkdir(parents=True, exist_ok=True)
            _reject_link_components(self.staging_root, self.training_root)
            stage = Path(tempfile.mkdtemp(prefix=f"{job_id}.", dir=self.staging_root))
            manifest_files = []
            try:
                for source, rel, transformed in selected:
                    posix = PurePosixPath(rel)
                    target = stage.joinpath(*posix.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    digest, size = _copy_hash(source, target, transformed)
                    manifest_files.append({"path": rel, "size": size, "sha256": digest})
                dataset_name = completed_job.get("dataset_name")
                if not isinstance(dataset_name, str) or not dataset_name.strip():
                    dataset_name = "OmniVoice model"
                dataset_name = dataset_name.strip()[:100]
                manifest = {"schema_version": 1, "job_id": job_id, "engine": "omnivoice",
                            "dataset_name": dataset_name,
                            "published_at": _now(), "voice_quality_verified": False,
                            "files": manifest_files}
                manifest["manifest_sha256"] = _canonical_manifest_hash(manifest)
                (stage / MANIFEST_NAME).write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                if not _read_manifest(stage, job_id):
                    raise PublicationError("Staged publication failed its integrity check.")
                if destination.exists() or destination.is_symlink():
                    raise PublicationError("Published model folder appeared during publication.")
                os.replace(stage, destination)
                return _public_record(destination, manifest, reused=False)
            except OSError as exc:
                raise PublicationError(f"Could not copy or publish the checkpoint: {exc}") from exc
            finally:
                if stage.exists():
                    shutil.rmtree(stage, ignore_errors=True)

    def get(self, job_id: str) -> dict[str, Any] | None:
        if not isinstance(job_id, str) or not JOB_ID_RE.fullmatch(job_id):
            return None
        folder = self.root / job_id
        manifest = _read_manifest(folder, job_id, stat_check=True)
        return _public_record(folder, manifest) if manifest is not None else None

    def list(self) -> list[dict[str, Any]]:
        if not self.root.is_dir() or self.root.is_symlink():
            return []
        records = []
        for folder in self.root.iterdir():
            if folder.is_symlink() or not folder.is_dir() or not JOB_ID_RE.fullmatch(folder.name):
                continue
            manifest = _read_manifest(folder, folder.name, stat_check=True)
            if manifest is not None:
                records.append(_public_record(folder, manifest))
        return sorted(records, key=lambda item: item["published_at"], reverse=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_hash(source: Path, destination: Path, transformed: bytes | None) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        if transformed is not None:
            if _safe_json_bytes(source) != transformed:
                raise PublicationError(f"Checkpoint JSON changed before copy: {source.name}.")
            with destination.open("xb") as target:
                target.write(transformed)
                target.flush()
                os.fsync(target.fileno())
            digest.update(transformed)
            return digest.hexdigest(), len(transformed)
        before = source.stat()
        with source.open("rb") as origin, destination.open("xb") as target:
            while chunk := origin.read(1024 * 1024):
                target.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            target.flush()
            os.fsync(target.fileno())
        after = source.stat()
        if (size != before.st_size or size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns):
            raise PublicationError(f"Checkpoint file changed while copying: {source.name}.")
        return digest.hexdigest(), size
    except OSError as exc:
        raise PublicationError(f"Could not copy checkpoint file {source.name}: {exc}") from exc


def publish_checkpoint(job_id: str, checkpoint_path: str | Path,
                       completed_job: Mapping[str, Any], data_dir: Path | None = None) -> dict[str, Any]:
    """Functional wrapper for callers that already have the completed job record."""
    if completed_job.get("id") != job_id:
        raise PublicationError("Training job id does not match completed job metadata.")
    record = {**completed_job, "checkpoint_path": str(checkpoint_path)}
    return PublicationService(data_dir).publish(record)

