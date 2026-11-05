"""Safe, reviewable dataset snapshots and portable OmniVoice exports."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import uuid
import zipfile
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from pathlib import PureWindowsPath
from typing import Any, Iterable

import numpy as np
import soundfile as sf

from core import paths
from engines import audio_store

ID_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_CLIPS = 500
MAX_TOTAL_SECONDS = 2 * 60 * 60
MAX_CLIP_SECONDS = 60 * 60
BLOCK_FRAMES = 65536
_PROVENANCE_FIELDS = {"source_id", "variant_id", "method", "cleanup", "start_s", "end_s",
                      "source_url", "url", "original_sha256", "section_start_s", "section_end_s"}

_locks_guard = threading.Lock()
_locks: dict[str, threading.RLock] = {}


class DatasetError(ValueError):
    """Expected dataset request or source validation error."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _lock_for(root: Path) -> threading.RLock:
    key = str(root.resolve())
    with _locks_guard:
        return _locks.setdefault(key, threading.RLock())


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _safe_child(root: Path, path: Path, *, allow_missing: bool = False) -> Path:
    """Ensure path and every existing component stay under root and are not symlinks."""
    root = root.resolve()
    candidate = path if path.is_absolute() else root / path
    try:
        candidate.absolute().relative_to(root)
    except ValueError as exc:
        raise DatasetError("Dataset path is outside its managed data folder.") from exc
    current = root
    try:
        rel = candidate.absolute().relative_to(root)
    except ValueError as exc:
        raise DatasetError("Dataset path is outside its managed data folder.") from exc
    if any(part in ("..", ".") for part in rel.parts):
        raise DatasetError("Dataset path is outside its managed data folder.")
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise DatasetError("Symlinks are not allowed in managed training data.")
        if not allow_missing and not current.exists():
            raise DatasetError("Managed training file is missing.")
    return candidate


def _read_dataset(root: Path, dataset_id: str) -> dict[str, Any]:
    if not ID_RE.fullmatch(dataset_id):
        raise DatasetError("Invalid dataset id.")
    file = _safe_child(root, root / dataset_id / "dataset.json")
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DatasetError("Dataset record is unreadable.") from exc
    if not isinstance(data, dict) or data.get("id") != dataset_id:
        raise DatasetError("Dataset record is invalid.")
    return data


def _summary(data: dict[str, Any]) -> dict[str, Any]:
    clips = data.get("clips", [])
    return {"id": data["id"], "name": data["name"], "speaker": data.get("speaker"),
            "language_id": data.get("language_id"), "version": data["version"],
            "clip_count": len(clips), "reviewed_count": sum(bool(c.get("reviewed")) for c in clips),
            "created": data["created"], "updated": data["updated"]}


def _detail(data: dict[str, Any]) -> dict[str, Any]:
    for clip in data.get("clips", []):
        clip_id = clip.get("clip_id")
        clip["id"] = clip_id
        clip["url"] = f"/api/train/datasets/{data['id']}/clips/{clip_id}/audio"
        clip["ready"] = bool(clip.get("reviewed") and str(clip.get("text", "")).strip())
        clip.setdefault("quality", {}).setdefault("duration_s", clip.get("duration_s"))
    return data


def _safe_provenance(meta: dict[str, Any]) -> dict[str, Any]:
    """Keep useful Harvest lineage fields while excluding filesystem paths and unbounded values."""
    result: dict[str, Any] = {}
    for key in ("source_id", "variant_id"):
        value = meta.get(key)
        if isinstance(value, str) and len(value) <= 128:
            result[key] = value
    provenance = meta.get("provenance")
    if isinstance(provenance, dict):
        clean: dict[str, Any] = {}
        for key, value in provenance.items():
            if key not in _PROVENANCE_FIELDS or "path" in key.lower() or "file" in key.lower():
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                if (not isinstance(value, str) or (len(value) <= 512
                    and not Path(value).is_absolute() and not PureWindowsPath(value).is_absolute())):
                    clean[key] = value
        if clean:
            result["harvest"] = clean
    return result


def _quality(path: Path) -> tuple[dict[str, Any], str, int]:
    """Stream through an audio file to inspect quality and hash its exact source bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as raw:
        while block := raw.read(1024 * 1024):
            digest.update(block)
    try:
        info = sf.info(str(path))
        if info.format != "WAV" or info.frames <= 0 or info.samplerate <= 0 or info.channels not in (1, 2):
            raise DatasetError("Reference must be a readable mono or stereo WAV with audio frames.")
        if info.duration > MAX_CLIP_SECONDS:
            raise DatasetError(f"Reference exceeds the {MAX_CLIP_SECONDS // 60}-minute clip limit.")
        peak = 0.0
        sum_sq = 0.0
        count = clipped = quiet = 0
        with sf.SoundFile(str(path)) as audio:
            while True:
                samples = audio.read(BLOCK_FRAMES, dtype="float32", always_2d=True)
                if not len(samples):
                    break
                if not np.isfinite(samples).all():
                    raise DatasetError("Audio contains non-finite samples and cannot be used safely.")
                finite = samples
                abs_samples = np.abs(finite)
                peak = max(peak, float(abs_samples.max(initial=0.0)))
                sum_sq += float(np.square(finite, dtype=np.float64).sum())
                count += finite.size
                clipped += int(np.count_nonzero(abs_samples >= 0.999))
                quiet += int(np.count_nonzero(abs_samples <= 0.001))
    except DatasetError:
        raise
    except (OSError, RuntimeError, sf.SoundFileRuntimeError) as exc:
        raise DatasetError("Reference WAV could not be decoded.") from exc
    if not count:
        raise DatasetError("Reference WAV contains no audio samples.")
    quality = {"sample_rate": int(info.samplerate), "channels": int(info.channels),
               "peak": round(peak, 6), "rms": round((sum_sq / count) ** 0.5, 6),
               "clipping_ratio": round(clipped / count, 6), "quiet_ratio": round(quiet / count, 6),
               "warnings": []}
    if quality["clipping_ratio"] > 0.01:
        quality["warnings"].append("Some samples are near full scale; check for clipping.")
    if quality["quiet_ratio"] > 0.5:
        quality["warnings"].append("More than half the samples are near silence.")
    if quality["rms"] < 0.003:
        quality["warnings"].append("Audio level is very quiet.")
    return quality, digest.hexdigest(), int(info.frames)


class DatasetService:
    def __init__(self, data_dir: Path | None = None):
        self.data_dir = Path(data_dir) if data_dir is not None else paths.DATA_DIR
        self.root = self.data_dir / "training" / "datasets"
        if data_dir is None:
            from core.library import voices_dir
            self.references = voices_dir()          # Library/Voices, same as Generate
        else:
            self.references = self.data_dir / "references"   # isolated tests
        self.artifacts = self.data_dir / "training" / "artifacts"
        self._lock = _lock_for(self.root)

    def references_list(self) -> list[dict[str, Any]]:
        out = []
        for item in audio_store.list_references(self.references):
            rid = item["reference_id"]
            wav = self.references / f"{rid}.wav"
            try:
                wav = _safe_child(self.references, wav)
                meta_path = self.references / f"{rid}.json"
                transcript = None
                if meta_path.is_file() and not meta_path.is_symlink():
                    try:
                        meta = json.loads(meta_path.read_text(encoding="utf-8"))
                        transcript = meta.get("transcript") if isinstance(meta, dict) else None
                    except (OSError, ValueError):
                        pass
                out.append({"reference_id": rid, "name": item.get("name"),
                            "duration_s": item["duration_s"],
                            "url": f"/api/tts/references/{rid}/audio", "transcript": transcript})
            except DatasetError:
                continue
        return out

    def list_datasets(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        results = []
        for folder in self.root.iterdir():
            if not ID_RE.fullmatch(folder.name) or folder.is_symlink():
                continue
            try:
                results.append(_summary(_read_dataset(self.root, folder.name)))
            except DatasetError:
                continue
        return sorted(results, key=lambda x: x["updated"], reverse=True)

    def get_dataset(self, dataset_id: str) -> dict[str, Any]:
        with self._lock:
            return _detail(_read_dataset(self.root, dataset_id))

    def clip_audio_path(self, dataset_id: str, clip_id: str) -> Path:
        if not ID_RE.fullmatch(clip_id):
            raise DatasetError("Invalid clip id.")
        with self._lock:
            data = _read_dataset(self.root, dataset_id)
            clip = next((c for c in data["clips"] if c.get("clip_id") == clip_id), None)
            if clip is None:
                raise DatasetError("Clip not found.")
            path = _safe_child(self.root / dataset_id,
                               self.root / dataset_id / clip.get("audio_path", ""))
            if not path.is_file():
                raise DatasetError("Dataset audio snapshot is missing.")
            return path

    def create_dataset(self, name: str, speaker: str | None = None, language_id: str | None = None,
                       reference_ids: Iterable[str] = ()) -> dict[str, Any]:
        ids = list(reference_ids)
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise DatasetError("Dataset name must contain 1-100 characters.")
        if speaker is not None and (not isinstance(speaker, str) or len(speaker) > 100):
            raise DatasetError("Speaker must be at most 100 characters.")
        if language_id is not None and (not isinstance(language_id, str) or len(language_id) > 32):
            raise DatasetError("Language id must be at most 32 characters.")
        if not ids or len(ids) > MAX_CLIPS:
            raise DatasetError(f"Choose between 1 and {MAX_CLIPS} references.")
        if any(not isinstance(rid, str) or not ID_RE.fullmatch(rid) for rid in ids):
            raise DatasetError("Every reference id must be a 32-character hex id.")
        if len(set(ids)) != len(ids):
            raise DatasetError("Reference ids must be unique.")

        with self._lock, ExitStack() as holds:
            sources = []
            total = 0.0
            # Hold every source against concurrent deletion through validation and publication.
            for rid in ids:
                holds.enter_context(audio_store.reference_in_use(rid))
                src = _safe_child(self.references, self.references / f"{rid}.wav")
                quality, digest, frames = _quality(src)
                duration = frames / quality["sample_rate"]
                total += duration
                if total > MAX_TOTAL_SECONDS:
                    raise DatasetError("Dataset exceeds the two-hour total audio limit.")
                meta: dict[str, Any] = {}
                meta_path = self.references / f"{rid}.json"
                if meta_path.is_file() and not meta_path.is_symlink():
                    try:
                        loaded = json.loads(meta_path.read_text(encoding="utf-8"))
                        if isinstance(loaded, dict):
                            meta = loaded
                    except (OSError, ValueError):
                        pass
                transcript = meta.get("transcript")
                if not isinstance(transcript, str):
                    transcript = ""
                sources.append((rid, src, quality, digest, round(duration, 3), meta, transcript))

            dataset_id = uuid.uuid4().hex
            created = _now()
            data: dict[str, Any] = {"id": dataset_id, "name": name.strip(), "speaker": speaker,
                                    "language_id": language_id, "version": 1, "created": created,
                                    "updated": created, "clips": [], "artifacts": []}
            self.root.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=f".{dataset_id}.", dir=self.root))
            try:
                (staging / "audio").mkdir()
                for index, (rid, src, quality, digest, duration, meta, transcript) in enumerate(sources):
                    clip_id = uuid.uuid4().hex
                    dst = staging / "audio" / f"{clip_id}.wav"
                    with src.open("rb") as inp, dst.open("xb") as out:
                        shutil.copyfileobj(inp, out, length=1024 * 1024)
                    copied_hash = hashlib.sha256()
                    with dst.open("rb") as f:
                        while block := f.read(1024 * 1024):
                            copied_hash.update(block)
                    if copied_hash.hexdigest() != digest:
                        raise DatasetError("A reference changed while it was being copied; retry dataset creation.")
                    data["clips"].append({"clip_id": clip_id, "reference_id": rid,
                        "name": str(meta.get("name") or f"Reference {index + 1}")[:120],
                        "duration_s": duration, "text": transcript[:2000], "reviewed": False,
                        "enabled": True, "audio_path": f"audio/{clip_id}.wav",
                        "source_sha256": digest, "quality": quality,
                        "provenance": {"reference_id": rid, "source_sha256": digest,
                                       **_safe_provenance(meta)}})
                _atomic_json(staging / "dataset.json", data)
                os.replace(staging, self.root / dataset_id)
                return _detail(data)
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                raise

    def patch_clip(self, dataset_id: str, clip_id: str, *, text: str | None = None,
                   reviewed: bool | None = None, enabled: bool | None = None) -> dict[str, Any]:
        if not ID_RE.fullmatch(clip_id):
            raise DatasetError("Invalid clip id.")
        if text is not None and (not isinstance(text, str) or len(text) > 2000):
            raise DatasetError("Clip text must be at most 2000 characters.")
        if reviewed is not None and not isinstance(reviewed, bool):
            raise DatasetError("Reviewed must be true or false.")
        if enabled is not None and not isinstance(enabled, bool):
            raise DatasetError("Enabled must be true or false.")
        with self._lock:
            data = _read_dataset(self.root, dataset_id)
            clip = next((c for c in data["clips"] if c.get("clip_id") == clip_id), None)
            if clip is None:
                raise DatasetError("Clip not found.")
            changed = False
            if text is not None and text != clip.get("text", ""):
                clip["text"] = text
                changed = True
                if reviewed is None:
                    clip["reviewed"] = False
            if reviewed is not None and reviewed != clip.get("reviewed"):
                clip["reviewed"] = reviewed
                changed = True
            if enabled is not None and enabled != clip.get("enabled"):
                clip["enabled"] = enabled
                changed = True
            if not changed:
                return _detail(data)
            data["version"] += 1
            data["updated"] = _now()
            _atomic_json(self.root / dataset_id / "dataset.json", data)
            return _detail(data)

    def export(self, dataset_id: str) -> dict[str, Any]:
        with self._lock:
            data = _read_dataset(self.root, dataset_id)
            ready = [c for c in data["clips"] if c.get("enabled")]
            if not ready:
                raise DatasetError("Enable at least one clip before exporting.")
            invalid = [c for c in ready if not c.get("reviewed") or not str(c.get("text", "")).strip()]
            if invalid:
                raise DatasetError("Every enabled clip needs a reviewed, nonempty transcript before export.")
            for clip in ready:
                audio = _safe_child(self.root / dataset_id,
                                    self.root / dataset_id / clip.get("audio_path", ""))
                if not audio.is_file():
                    raise DatasetError("A dataset audio snapshot is missing.")
                _metrics, digest, _frames = _quality(audio)
                if digest != clip.get("source_sha256"):
                    raise DatasetError("A dataset audio snapshot has changed since review; rebuild the dataset before exporting.")
            for item in data.get("artifacts", []):
                if item.get("version") == data["version"]:
                    zip_path = self.artifacts / f"{item['id']}.zip"
                    if zip_path.is_file() and not zip_path.is_symlink():
                        return {k: item[k] for k in ("id", "url", "clip_count", "version")}
            artifact_id = uuid.uuid4().hex
            self.artifacts.mkdir(parents=True, exist_ok=True)
            temp = self.artifacts / f".{artifact_id}.tmp"
            dest = self.artifacts / f"{artifact_id}.zip"
            rows = []
            try:
                with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                    for clip in ready:
                        rel_audio = f"audio/{clip['clip_id']}.wav"
                        source = self.root / dataset_id / clip["audio_path"]
                        zf.write(source, rel_audio)
                        row = {"id": clip["clip_id"], "audio_path": rel_audio, "text": clip["text"]}
                        if data.get("language_id"):
                            row["language_id"] = data["language_id"]
                        rows.append(row)
                    zf.writestr("metadata.jsonl", "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
                    manifest = {"dataset_id": data["id"], "name": data["name"], "speaker": data.get("speaker"),
                                "language_id": data.get("language_id"), "version": data["version"],
                                "clip_count": len(rows), "format": "OmniVoice JSONL + audio WAV",
                                "quality": [{"clip_id": c["clip_id"], "metrics": c["quality"],
                                             "source_sha256": c["source_sha256"], "provenance": c["provenance"]}
                                            for c in ready]}
                    zf.writestr("dataset.json", json.dumps(manifest, indent=2, ensure_ascii=False))
                    zf.writestr("README.txt", "OmniVoice dataset export\n\n"
                        "This archive contains metadata.jsonl with one JSON record per audio clip. "
                        "Each audio_path is relative to this extracted folder. Run the OmniVoice "
                        "preprocessor with the extracted folder as its working directory so those "
                        "relative paths resolve. Dataset review and export are supported by "
                        "VoiceVault; model training launch is not integrated yet.\n")
                os.replace(temp, dest)
            finally:
                temp.unlink(missing_ok=True)
            artifact = {"id": artifact_id, "url": f"/api/train/artifacts/{artifact_id}",
                        "clip_count": len(rows), "version": data["version"]}
            data.setdefault("artifacts", []).append(artifact)
            data["updated"] = _now()
            _atomic_json(self.root / dataset_id / "dataset.json", data)
            return {k: artifact[k] for k in ("id", "url", "clip_count", "version")}

    def artifact_path(self, artifact_id: str) -> Path:
        if not ID_RE.fullmatch(artifact_id):
            raise DatasetError("Invalid artifact id.")
        path = _safe_child(self.artifacts, self.artifacts / f"{artifact_id}.zip")
        if not path.is_file():
            raise DatasetError("Artifact not found.")
        # Artifact must be reachable from a stored dataset record.
        for summary in self.list_datasets():
            try:
                data = _read_dataset(self.root, summary["id"])
            except DatasetError:
                continue
            if any(a.get("id") == artifact_id for a in data.get("artifacts", [])):
                return path
        raise DatasetError("Artifact not found.")


def capabilities() -> dict[str, Any]:
    from training.runtime import runtime_readiness

    status = runtime_readiness(paths.DATA_DIR)
    missing = sorted(set(status["core"]["missing"] + status["missing_extras"]
                         + status["core"]["incompatible"]))
    return {"engine": "omnivoice", "dataset_available": True, "training_available": False,
            "reason": "Dataset review and export are available; training is unavailable until runtime and runner validation are complete.",
            "missing_dependencies": missing,
            "training_dependencies_ready": status["ready_for_training_dependencies"],
            "training_runtime": {"ready_for_training_dependencies": status["ready_for_training_dependencies"],
                "training_validated": False, "missing_extras": status["missing_extras"],
                "unexpected_extras": status["unexpected_extras"], "marker_problem": status["marker_problem"],
                "core": status["core"]}}

