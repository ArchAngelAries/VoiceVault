"""CPU-only publication checks using tiny local checkpoint fixtures."""
import hashlib
import json
from pathlib import Path

import pytest

from core import model_paths
from training.publication import PublicationError, PublicationService


def _safetensors(path: Path):
    header = json.dumps({"tensor": {"dtype": "U8", "shape": [1], "data_offsets": [0, 1]}}).encode()
    path.write_bytes(len(header).to_bytes(8, "little") + header + b"\0")


def _job(tmp_path, *, job_id="a" * 32):
    checkpoint = tmp_path / "training" / "jobs" / job_id / "run" / "checkpoint-2"
    checkpoint.mkdir(parents=True)
    (checkpoint / "config.json").write_text(json.dumps({
        "model_type": "omnivoice", "_name_or_path": "C:\\private\\source-model"}), encoding="utf-8")
    _safetensors(checkpoint / "model.safetensors")
    (checkpoint / "tokenizer.json").write_text(json.dumps({"model": {"type": "WordLevel"}}), encoding="utf-8")
    (checkpoint / "tokenizer_config.json").write_text(json.dumps({
        "tokenizer_class": "AutoTokenizer", "tokenizer_file": "C:\\private\\tokenizer.json"}), encoding="utf-8")
    (checkpoint / "train_config.json").write_text(json.dumps({
        "output_dir": "C:\\private\\run", "init_from_checkpoint": "C:\\private\\base"}), encoding="utf-8")
    (checkpoint / "optimizer.bin").write_bytes(b"training-only")
    audio = checkpoint / "audio_tokenizer"
    audio.mkdir()
    (audio / "config.json").write_text(json.dumps({"model_type": "higgs_audio", "path": "C:\\private"}), encoding="utf-8")
    _safetensors(audio / "model.safetensors")
    job = {"id": job_id, "status": "completed", "checkpoint_path": str(checkpoint),
           "dataset_name": "Voice Set"}
    (tmp_path / "training" / "jobs" / job_id / "job.json").write_text(json.dumps(job), encoding="utf-8")
    return checkpoint, job


def test_publish_is_explicit_atomic_inference_only_and_idempotent(tmp_path):
    checkpoint, job = _job(tmp_path)
    original = (checkpoint / "model.safetensors").read_bytes()
    service = PublicationService(tmp_path)

    first = service.publish(job)
    published = Path(first["path"])
    assert published == tmp_path / "training" / "published_models" / job["id"]
    assert first["voice_quality_verified"] is False and first["reused"] is False
    assert first["dataset_name"] == "Voice Set"
    assert (published / "model.safetensors").read_bytes() == original
    assert not (published / "train_config.json").exists()
    assert not (published / "optimizer.bin").exists()
    config = json.loads((published / "config.json").read_text(encoding="utf-8"))
    tokenizer_config = json.loads((published / "tokenizer_config.json").read_text(encoding="utf-8"))
    assert "_name_or_path" not in config and "tokenizer_file" not in tokenizer_config
    assert json.loads((published / "audio_tokenizer" / "config.json").read_text())["model_type"] == "higgs_audio"

    second = service.publish({**job, "dataset_name": "Renamed display"})
    assert second["reused"] is True
    assert second["dataset_name"] == "Voice Set"
    assert second["manifest_sha256"] == first["manifest_sha256"]
    assert service.get(job["id"])["path"] == str(published)
    assert service.list()[0]["job_id"] == job["id"]
    assert (checkpoint / "model.safetensors").read_bytes() == original


def test_publish_requires_completed_owned_checkpoint_and_valid_single_file_model(tmp_path):
    checkpoint, job = _job(tmp_path)
    service = PublicationService(tmp_path)
    with pytest.raises(PublicationError, match="completed"):
        service.publish({**job, "status": "failed"})
    with pytest.raises(PublicationError, match="Checkpoint path"):
        service.publish({**job, "checkpoint_path": str(tmp_path / "elsewhere")})

    (checkpoint / "model.safetensors").write_bytes(b"broken")
    with pytest.raises(PublicationError, match="invalid or incomplete"):
        service.publish(job)
    assert service.get(job["id"]) is None


def test_existing_publication_reuses_without_free_space_and_rejects_same_size_corruption(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import training.publication as publication
    checkpoint, job = _job(tmp_path)
    service = PublicationService(tmp_path)
    published = Path(service.publish(job)["path"])
    monkeypatch.setattr(publication.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    assert service.publish(job)["reused"] is True
    weights = published / "model.safetensors"
    payload = weights.read_bytes()
    weights.write_bytes(payload[:-1] + bytes([payload[-1] ^ 1]))
    with pytest.raises(PublicationError, match="content-hash"):
        service.publish(job)
    assert (checkpoint / "model.safetensors").read_bytes() == payload


def test_publish_rejects_symlinks_and_reuse_detects_source_change(tmp_path):
    checkpoint, job = _job(tmp_path)
    linked = checkpoint / "linked.txt"
    try:
        linked.symlink_to(checkpoint / "config.json")
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks are unavailable in this test environment.")
    service = PublicationService(tmp_path)
    with pytest.raises(PublicationError, match="symlink"):
        service.publish(job)
    linked.unlink()
    first = service.publish(job)
    (checkpoint / "tokenizer.json").write_text('{"changed": true}', encoding="utf-8")
    with pytest.raises(PublicationError, match="different publication"):
        service.publish(job)
    assert service.get(job["id"])["manifest_sha256"] == first["manifest_sha256"]


def test_model_roots_adds_only_existing_owned_publications_and_keeps_user_roots(tmp_path, monkeypatch):
    monkeypatch.setattr(model_paths.app_paths, "DATA_DIR", tmp_path / "data")
    user_root = tmp_path / "shared-models"
    roots = model_paths.model_roots({"model_search_roots": [str(user_root)]},
                                    project_root=tmp_path / "project", home=tmp_path / "home")
    published = tmp_path / "data" / "training" / "published_models"
    assert user_root.resolve() in roots and published.resolve() not in roots

    published.mkdir(parents=True)
    roots = model_paths.model_roots({"model_search_roots": [str(user_root)]},
                                    project_root=tmp_path / "project", home=tmp_path / "home")
    assert user_root.resolve() in roots and published.resolve() in roots
    assert not any(path.name == "jobs" for path in roots)
