"""Model manager: catalog facts, licence gate, safe removal and download layout (no network)."""
import json
import sys
import types
from pathlib import Path

import pytest

from models import catalog as cat
from models import fetch, manager


@pytest.fixture
def mm(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEVAULT_MODELS_DIR", str(tmp_path / "Models"))
    from core import paths
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(manager, "_roots", lambda: [tmp_path / "Models"] if (tmp_path / "Models").is_dir() else [])
    monkeypatch.setattr(manager, "sizes", lambda eid, refresh=False: 1000)
    started = []
    monkeypatch.setattr(manager, "_run", lambda job, entry: started.append(entry.id))   # never download in tests
    manager.invalidate()
    return tmp_path


def test_every_entry_states_licence_and_commercial_use():
    ids = [e.id for e in cat.CATALOG]
    assert len(ids) == len(set(ids))
    for e in cat.CATALOG:
        assert e.license and e.license_url.startswith("https://")
        assert e.commercial in ("yes", "no", "limited"), e.id
        assert e.sources and all(s.kind in ("hf_files", "hf_cache", "hf_local", "espeak_msi", "manual", "drama_convert") for s in e.sources)
        for s in e.sources:
            if s.kind == "hf_files":
                assert s.repo.startswith("audio-cpp/") and s.files
    # Non-commercial weights are marked as such.
    assert {e.id for e in cat.CATALOG if e.commercial == "no"} >= {"omnivoice", "breeze", "yue2", "sheetsage2"}


def test_licence_must_be_accepted_and_manual_entries_refuse(mm, monkeypatch):
    with pytest.raises(manager.ModelError, match="accept"):
        manager.start("htdemucs", accept_license=False)
    with pytest.raises(manager.ModelError, match="follow the steps"):
        manager.start("audiocpp", accept_license=True)
    monkeypatch.setattr(manager.shutil, "disk_usage", lambda p: type("U", (), {"free": 10 ** 9})())
    with pytest.raises(manager.ModelError, match="Not enough disk space"):   # Drama 3 needs ~140 GB free
        manager.start("drama3_final", accept_license=True, options={"also_max": True})


def test_disk_space_is_checked(mm, monkeypatch):
    monkeypatch.setattr(manager, "sizes", lambda eid, refresh=False: 10 ** 15)
    with pytest.raises(manager.ModelError, match="Not enough disk space"):
        manager.start("htdemucs", accept_license=True)


def test_removal_only_touches_voicevaults_own_files(mm):
    own = mm / "Models" / "HTDemucs-6stems-GGUF" / "htdemucs-6stems-q8_0.gguf"
    own.parent.mkdir(parents=True)
    own.write_bytes(b"x")
    outside = mm / "elsewhere" / "important.gguf"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"y")
    manager._write("installed.json", {"htdemucs": {"files": [str(own), str(outside)]}})
    assert manager.remove("htdemucs") == {"removed_files": 1}
    assert not own.exists() and outside.exists()
    with pytest.raises(manager.ModelError, match="didn't download"):
        manager.remove("yue2")


def test_installed_detection_by_file_and_folder(mm):
    (mm / "Models" / "x").mkdir(parents=True)
    (mm / "Models" / "x" / "sidon-v0.1-f32.gguf").write_bytes(b"x")
    (mm / "Models" / "models--aoi-ot--VibeVoice-Large").mkdir()
    manager.invalidate()
    assert manager.installed(cat.BY_ID["sidon"])[0]
    assert manager.installed(cat.BY_ID["vibevoice_large"])[0]
    assert not manager.installed(cat.BY_ID["ace_step"])[0]


def test_fetch_keeps_repo_folders_and_checks_sizes(tmp_path, monkeypatch):
    calls = []
    fake = types.SimpleNamespace()

    def hf_hub_download(repo, f, revision=None, local_dir=None, token=None):
        calls.append((repo, f, local_dir))
        p = Path(local_dir) / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"12345")
        return str(p)

    class Api:
        def __init__(self, token=None):
            pass

        def get_paths_info(self, repo, files, revision=None):
            return [types.SimpleNamespace(path=f, size=5) for f in files]
    fake.hf_hub_download, fake.HfApi = hf_hub_download, Api
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake)
    models = tmp_path / "Models"
    out = fetch._hf_files({"repo": "audio-cpp/audio.cpp-gguf", "files": ["Kokoro-82M-GGUF/k.gguf"], "dest": ""}, models, None)
    assert Path(out[0]) == models / "Kokoro-82M-GGUF" / "k.gguf"
    out = fetch._hf_files({"repo": "audio-cpp/Yue2-3B-GGUF", "files": ["sidecars/a.json", "y.gguf"], "dest": "Yue2-3B-GGUF"},
                          models, None)
    assert [Path(p) for p in out] == [models / "Yue2-3B-GGUF" / "sidecars" / "a.json", models / "Yue2-3B-GGUF" / "y.gguf"]

    class BadApi(Api):
        def get_paths_info(self, repo, files, revision=None):
            return [types.SimpleNamespace(path=f, size=999) for f in files]
    fake.HfApi = BadApi
    with pytest.raises(RuntimeError, match="does not match"):
        fetch._hf_files({"repo": "r/x", "files": ["a.gguf"], "dest": "X"}, models, None)


def test_api_catalog_shape(mm, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api import models_manager

    app = FastAPI()
    app.include_router(models_manager.router, prefix="/api/models")
    c = TestClient(app)
    data = c.get("/api/models/catalog").json()
    item = next(i for i in data["items"] if i["id"] == "kokoro")
    assert item["commercial"] == "yes" and item["downloadable"] and not item["installed"]
    r = c.post("/api/models/download", json={"id": "kokoro"})
    assert r.status_code == 400 and "accept" in r.json()["detail"]
