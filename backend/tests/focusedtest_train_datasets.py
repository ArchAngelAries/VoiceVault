"""Small filesystem-only checks for dataset review and portable export."""
import hashlib
import json
import zipfile

import numpy as np
import pytest
import soundfile as sf

from engines.audio_io import write_wav
from engines import audio_store
from training.datasets import DatasetError, DatasetService


def _reference(tmp_path, reference_id="a" * 32, transcript="Hello from the speaker."):
    refs = tmp_path / "references"
    refs.mkdir(parents=True, exist_ok=True)
    wav = refs / f"{reference_id}.wav"
    write_wav(wav, np.full(24000, 0.05, dtype=np.float32), 24000)
    (refs / f"{reference_id}.json").write_text(
        json.dumps({"reference_id": reference_id, "name": "sample.wav", "transcript": transcript}),
        encoding="utf-8")
    return wav


def _ready(service, dataset):
    clip = dataset["clips"][0]
    return service.patch_clip(dataset["id"], clip["clip_id"], reviewed=True)


def test_review_export_reload_and_immutable_original(tmp_path):
    original = _reference(tmp_path)
    before = hashlib.sha256(original.read_bytes()).hexdigest()
    service = DatasetService(tmp_path)
    data = service.create_dataset("Sample set", speaker="Narrator", language_id="en", reference_ids=["a" * 32])
    clip = data["clips"][0]
    assert clip["text"] == "Hello from the speaker."
    assert clip["reviewed"] is False and clip["enabled"] is True
    assert clip["quality"]["sample_rate"] == 24000 and "warnings" in clip["quality"]

    ready = _ready(service, data)
    artifact = service.export(data["id"])
    with zipfile.ZipFile(service.artifact_path(artifact["id"])) as archive:
        rows = [json.loads(line) for line in archive.read("metadata.jsonl").decode().splitlines()]
        assert rows == [{"id": clip["clip_id"], "audio_path": f"audio/{clip['clip_id']}.wav",
                         "text": "Hello from the speaker.", "language_id": "en"}]
        assert "C:\\" not in archive.read("dataset.json").decode()

    changed = service.patch_clip(data["id"], clip["clip_id"], text="Edited transcript")
    assert changed["version"] == ready["version"] + 1
    second = _ready(service, changed)
    assert second["version"] == changed["version"] + 1
    artifact2 = service.export(data["id"])
    assert artifact2["version"] == second["version"] and artifact2["id"] != artifact["id"]
    # A retry at the same version reuses the immutable artifact.
    assert service.export(data["id"]) == artifact2
    assert hashlib.sha256(original.read_bytes()).hexdigest() == before
    reloaded = DatasetService(tmp_path).get_dataset(data["id"])
    assert reloaded["clips"][0]["text"] == "Edited transcript"
    assert len(reloaded["artifacts"]) == 2


def test_preflight_rejects_duplicate_missing_symlink_and_unreviewed(tmp_path):
    _reference(tmp_path)
    service = DatasetService(tmp_path)
    with pytest.raises(DatasetError, match="unique"):
        service.create_dataset("Duplicates", reference_ids=["a" * 32, "a" * 32])
    with pytest.raises(DatasetError):
        service.create_dataset("Missing", reference_ids=["b" * 32])
    with pytest.raises(DatasetError):
        service.create_dataset("Missing second", reference_ids=["a" * 32, "b" * 32])
    assert "a" * 32 not in audio_store._in_use
    with pytest.raises(DatasetError):
        service.create_dataset("Traversal", reference_ids=["../" + "b" * 28])

    dataset = service.create_dataset("Review first", reference_ids=["a" * 32])
    with pytest.raises(DatasetError, match="reviewed"):
        service.export(dataset["id"])
    ref_wav = tmp_path / "references" / ("c" * 32 + ".wav")
    ref_wav.symlink_to(tmp_path / "references" / ("a" * 32 + ".wav"))
    with pytest.raises(DatasetError):
        service.create_dataset("Symlink", reference_ids=["c" * 32])
    bad_audio = tmp_path / "references" / ("d" * 32 + ".wav")
    sf.write(bad_audio, np.array([np.nan, 0.1], dtype=np.float32), 24000, subtype="FLOAT")
    with pytest.raises(DatasetError, match="non-finite"):
        service.create_dataset("Nonfinite", reference_ids=["d" * 32])


def test_snapshot_survives_reference_deletion_and_review_tracks_transcript(tmp_path):
    _reference(tmp_path)
    service = DatasetService(tmp_path)
    dataset = service.create_dataset("Snapshot", reference_ids=["a" * 32])
    clip = dataset["clips"][0]
    reviewed = service.patch_clip(dataset["id"], clip["clip_id"], reviewed=True)
    unchanged = service.patch_clip(dataset["id"], clip["clip_id"], reviewed=True)
    assert unchanged["version"] == reviewed["version"]
    edited = service.patch_clip(dataset["id"], clip["clip_id"], text="A corrected transcript")
    assert edited["version"] == reviewed["version"] + 1
    assert edited["clips"][0]["reviewed"] is False

    audio_store.delete_references(service.references, ["a" * 32])
    snapshot = service.clip_audio_path(dataset["id"], clip["clip_id"])
    assert snapshot.is_file()


def test_export_rejects_changed_snapshot_and_keeps_safe_harvest_provenance(tmp_path):
    _reference(tmp_path)
    ref_json = tmp_path / "references" / ("a" * 32 + ".json")
    ref_json.write_text(json.dumps({"name": "sample", "transcript": "Hello",
        "source_id": "source-1", "variant_id": "variant-2",
        "provenance": {"source_id": "source-1", "start_s": 2.0, "end_s": 8.0,
                       "cleanup": "denoise", "local_path": "C:\\private\\source.wav"}}), encoding="utf-8")
    service = DatasetService(tmp_path)
    dataset = service.create_dataset("Provenance", reference_ids=["a" * 32])
    provenance = dataset["clips"][0]["provenance"]
    assert provenance["source_id"] == "source-1"
    assert provenance["variant_id"] == "variant-2"
    harvest = provenance["harvest"]
    assert harvest.get("start_s") == 2.0
    assert "local_path" not in harvest
    clip = dataset["clips"][0]
    service.patch_clip(dataset["id"], clip["clip_id"], reviewed=True)
    audio = service.clip_audio_path(dataset["id"], clip["clip_id"])
    audio.write_bytes(audio.read_bytes() + b"changed")
    with pytest.raises(DatasetError, match="changed since review"):
        service.export(dataset["id"])

