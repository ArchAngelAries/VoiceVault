"""Deleting generated speech and reference clips: path safety, record rule, bulk rules, in-use holds. Fake engine, no GPU."""
import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

import api.tts as tts_api
from core import settings as app_settings
from engines import audio_store
from engines.audio_io import save_generation, write_wav
from engines.base import EngineCapabilities, ModelInfo, ModelState, SynthesisResult, TTSEngine
from engines.manager import EngineManager
from engines.registry import ModelRegistry

HEX_A = "a" * 32
HEX_B = "b" * 32
HEX_C = "c" * 32


class DeleteEngine(TTSEngine):
    engine_id = "clone"
    display_name = "Delete test engine"
    capabilities = EngineCapabilities(languages=["en"], reference_audio=True)
    refused_during_run = []

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
        if request.reference_audio is not None:
            # A delete issued while the engine reads the clip must be refused.
            try:
                audio_store.delete_references(request.reference_audio.parent, [request.reference_audio.stem])
            except audio_store.InUseError as exc:
                DeleteEngine.refused_during_run.append(str(exc))
        return SynthesisResult(samples=np.zeros(2400, np.float32), sample_rate=24000, meta={"mode": "clone"})


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Returns (client, root, outputs, references). Everything lives under tmp_path."""
    DeleteEngine.refused_during_run = []
    models = tmp_path / "models" / "clone-model"
    models.mkdir(parents=True)
    (models / "config.json").write_text("{}", encoding="utf-8")
    engine = DeleteEngine()
    registry = ModelRegistry([engine], [tmp_path / "models"])
    registry.discover()
    manager = EngineManager({"clone": engine}, registry)
    outputs, references = tmp_path / "outputs", tmp_path / "references"
    monkeypatch.setattr(tts_api, "_ENGINES", [engine])
    monkeypatch.setattr(tts_api, "_services", {"registry": registry, "manager": manager})
    monkeypatch.setattr(tts_api, "REFERENCE_DIR", references)
    monkeypatch.setattr(app_settings, "load", lambda: {"outputs_dir": str(outputs)})
    from main import app
    return TestClient(app), tmp_path, outputs, references


def _generation(outputs: Path, text: str = "hello") -> str:
    record = save_generation(outputs, np.zeros(2400, np.float32), 24000,
                             {"engine": "clone", "model_id": "clone:clone-model", "text": text, "meta": {}})
    return record["file"]


def _reference_wav(references: Path, reference_id: str, seconds: float = 5.0) -> None:
    write_wav(references / f"{reference_id}.wav", np.zeros(int(seconds * 24000), np.float32) + 0.01, 24000)


def _upload(client: TestClient, root: Path, name: str = "ref.wav", seconds: float = 5.0) -> str:
    clip = root / f"upload_{seconds}.wav"
    write_wav(clip, np.zeros(int(seconds * 24000), np.float32) + 0.01, 24000)
    r = client.post("/api/tts/references", files={"file": (name, clip.read_bytes(), "audio/wav")})
    assert r.status_code == 200, r.text
    return r.json()["reference_id"]


def _history_files(client: TestClient) -> list[str]:
    return [item["file"] for item in client.get("/api/tts/history").json()["items"]]


# ── Generated speech ──────────────────────────────────────────────

def test_deleting_one_generation_removes_its_wav_record_and_history_entry(env):
    client, _, outputs, _ = env
    keep = _generation(outputs, "keep")
    gone = _generation(outputs, "gone")

    r = client.delete(f"/api/tts/audio/{gone}")

    assert r.status_code == 200
    assert r.json() == {"deleted": [gone], "missing": [], "failed": []}
    assert not (outputs / gone).exists()
    assert not (outputs / f"{Path(gone).stem}.json").exists()
    assert _history_files(client) == [keep]


@pytest.mark.parametrize("name", ["notes.txt", "a b.wav", "..%2Fsecret.wav", "..wav", ".wav"])
def test_names_that_are_not_generated_files_are_refused_and_nothing_outside_is_touched(env, name):
    client, root, outputs, _ = env
    outputs.mkdir(parents=True)
    (outputs / "notes.txt").write_text("keep", encoding="utf-8")
    secret = root / "secret.wav"  # the file a ../ traversal would reach
    secret.write_bytes(b"keep me")

    r = client.delete(f"/api/tts/audio/{name}")

    assert r.status_code in (400, 404)
    assert secret.read_bytes() == b"keep me"
    assert (outputs / "notes.txt").read_text(encoding="utf-8") == "keep"


def test_store_rejects_path_traversal_before_touching_the_disk(env):
    _, root, outputs, _ = env
    outputs.mkdir(parents=True)
    secret = root / "secret.wav"
    secret.write_bytes(b"keep me")

    with pytest.raises(audio_store.StoreError):
        audio_store.delete_generations(outputs, ["../secret.wav"])
    assert secret.read_bytes() == b"keep me"


def test_a_wav_without_a_record_is_neither_listed_nor_deleted(env):
    client, _, outputs, _ = env
    outputs.mkdir(parents=True)
    write_wav(outputs / "orphan.wav", np.zeros(2400, np.float32), 24000)

    assert client.delete("/api/tts/audio/orphan.wav").status_code == 404
    r = client.post("/api/tts/audio/bulk-delete", json={"all": True, "confirm": "DELETE ALL"})

    assert r.status_code == 200 and r.json()["deleted"] == []
    assert (outputs / "orphan.wav").is_file()


def test_a_record_that_names_another_file_does_not_authorise_deleting_this_one(env):
    client, _, outputs, _ = env
    name = _generation(outputs)
    (outputs / f"{Path(name).stem}.json").write_text(json.dumps({"file": "other.wav"}), encoding="utf-8")

    assert client.delete(f"/api/tts/audio/{name}").status_code == 404
    assert (outputs / name).is_file()


def test_bulk_delete_of_selected_files_reports_what_was_missing(env):
    client, _, outputs, _ = env
    a, b, keep = _generation(outputs, "a"), _generation(outputs, "b"), _generation(outputs, "keep")

    r = client.post("/api/tts/audio/bulk-delete", json={"names": [a, b, "never-made.wav"]})

    assert r.status_code == 200
    assert r.json() == {"deleted": [a, b], "missing": ["never-made.wav"], "failed": []}
    assert _history_files(client) == [keep]


def test_a_bulk_request_with_one_bad_name_removes_nothing(env):
    client, _, outputs, _ = env
    a = _generation(outputs)

    r = client.post("/api/tts/audio/bulk-delete", json={"names": [a, "../x.wav"]})

    assert r.status_code == 400
    assert (outputs / a).is_file()


def test_delete_all_generations_needs_the_typed_phrase_and_keeps_other_files(env):
    client, _, outputs, _ = env
    a, b = _generation(outputs, "a"), _generation(outputs, "b")
    write_wav(outputs / "my_own_recording.wav", np.zeros(2400, np.float32), 24000)  # no record: not ours
    (outputs / "notes.txt").write_text("keep", encoding="utf-8")

    lowercase = client.post("/api/tts/audio/bulk-delete", json={"all": True, "confirm": "delete all"})
    assert lowercase.status_code == 400
    assert (outputs / a).is_file() and (outputs / b).is_file()

    r = client.post("/api/tts/audio/bulk-delete", json={"all": True, "confirm": "DELETE ALL"})

    assert r.status_code == 200
    assert sorted(r.json()["deleted"]) == sorted([a, b])
    assert _history_files(client) == []
    assert (outputs / "my_own_recording.wav").is_file() and (outputs / "notes.txt").is_file()


def test_selected_and_all_cannot_be_combined_and_an_empty_selection_is_refused(env):
    client, _, outputs, _ = env
    a = _generation(outputs)

    both = client.post("/api/tts/audio/bulk-delete", json={"names": [a], "all": True, "confirm": "DELETE ALL"})
    empty = client.post("/api/tts/audio/bulk-delete", json={"names": []})

    assert both.status_code == 400 and empty.status_code == 400
    assert (outputs / a).is_file()


def test_a_bulk_request_over_the_limit_is_refused(env):
    client, _, _, _ = env

    r = client.post("/api/tts/audio/bulk-delete", json={"names": [f"x{i}.wav" for i in range(501)]})

    assert r.status_code == 422


# ── Reference clips ───────────────────────────────────────────────

def test_an_uploaded_clip_gets_a_record_and_appears_in_the_list(env):
    client, root, _, references = env
    ref = _upload(client, root, name="my voice.wav", seconds=5)

    listing = client.get("/api/tts/references").json()["items"]

    assert [item["reference_id"] for item in listing] == [ref]
    assert listing[0]["name"] == "my voice.wav"
    assert listing[0]["duration_s"] == pytest.approx(5.0, abs=0.05)
    assert listing[0]["url"] == f"/api/tts/references/{ref}/audio"
    assert (references / f"{ref}.json").is_file()


def test_a_stored_clip_plays_back_and_bad_ids_are_refused(env):
    client, root, _, _ = env
    ref = _upload(client, root)

    ok = client.get(f"/api/tts/references/{ref}/audio")

    assert ok.status_code == 200 and ok.headers["content-type"].startswith("audio/wav")
    assert client.get(f"/api/tts/references/{HEX_C}/audio").status_code == 404
    assert client.get("/api/tts/references/not-an-id/audio").status_code == 400


def test_deleting_a_clip_removes_its_wav_and_record(env):
    client, root, _, references = env
    ref = _upload(client, root)

    r = client.delete(f"/api/tts/references/{ref}")

    assert r.status_code == 200 and r.json()["deleted"] == [ref]
    assert not (references / f"{ref}.wav").exists() and not (references / f"{ref}.json").exists()
    assert client.get("/api/tts/references").json()["items"] == []


def test_a_clip_from_before_records_existed_can_be_listed_and_deleted(env):
    client, _, _, references = env
    _reference_wav(references, HEX_B, seconds=4)

    listing = client.get("/api/tts/references").json()["items"]
    assert listing[0]["reference_id"] == HEX_B and listing[0]["name"] is None
    assert client.delete(f"/api/tts/references/{HEX_B}").status_code == 200
    assert not (references / f"{HEX_B}.wav").exists()


def test_a_clip_that_a_generation_is_reading_cannot_be_deleted(env):
    client, root, _, references = env
    ref = _upload(client, root)

    with audio_store.reference_in_use(ref):
        r = client.delete(f"/api/tts/references/{ref}")
        assert r.status_code == 409
        assert (references / f"{ref}.wav").is_file()

    assert client.delete(f"/api/tts/references/{ref}").status_code == 200


def test_synthesis_holds_its_clip_while_the_engine_reads_it(env):
    client, root, _, references = env
    ref = _upload(client, root)

    r = client.post("/api/tts/synthesize", json={"model_id": "clone:clone-model", "text": "Say this.",
                                                 "reference_id": ref})

    assert r.status_code == 200
    assert len(DeleteEngine.refused_during_run) == 1 and "reading" in DeleteEngine.refused_during_run[0]
    assert (references / f"{ref}.wav").is_file()
    assert client.delete(f"/api/tts/references/{ref}").status_code == 200  # released once the run ends


def test_a_bulk_reference_delete_removes_nothing_when_one_clip_is_in_use(env):
    client, root, _, references = env
    first, second = _upload(client, root), _upload(client, root, seconds=6)

    with audio_store.reference_in_use(first):
        r = client.post("/api/tts/references/bulk-delete", json={"names": [first, second]})

    assert r.status_code == 409
    assert (references / f"{first}.wav").is_file() and (references / f"{second}.wav").is_file()


def test_delete_all_references_needs_the_phrase_and_leaves_other_files_alone(env):
    client, root, outputs, references = env
    _upload(client, root)
    _reference_wav(references, HEX_B)  # legacy clip, no record
    (references / "notes.wav").write_bytes(b"not a clip id")
    (references / "notes.txt").write_text("keep", encoding="utf-8")
    kept_generation = _generation(outputs)

    refused = client.post("/api/tts/references/bulk-delete", json={"all": True, "confirm": "yes"})
    assert refused.status_code == 400
    assert len([p for p in references.glob("*.wav") if not p.name.endswith(".hq44.wav")]) == 3  # full-band copies aside

    r = client.post("/api/tts/references/bulk-delete", json={"all": True, "confirm": "DELETE ALL"})

    assert r.status_code == 200
    assert len(r.json()["deleted"]) == 2 and HEX_B in r.json()["deleted"]
    assert (references / "notes.wav").is_file() and (references / "notes.txt").is_file()
    assert not list(references.glob("*.hq44.wav"))  # full-band copies go with their clips
    assert (outputs / kept_generation).is_file()


def test_malformed_reference_ids_are_refused(env):
    client, _, _, _ = env

    single = client.delete("/api/tts/references/not-an-id")
    bulk = client.post("/api/tts/references/bulk-delete", json={"names": ["../../etc"]})

    assert single.status_code == 400 and bulk.status_code == 400
