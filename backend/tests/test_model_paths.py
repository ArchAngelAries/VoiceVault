"""Local path resolution fixtures only; no network, model loads, or GPU."""
import os
from pathlib import Path

import pytest

from core import settings
from core.model_paths import huggingface_cache, model_roots
from engines.base import ModelInfo, ModelState
from engines.registry import ModelRegistry
from engines.vibevoice_engine import _tokenizer_dir


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    for key in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "HF_HOME", "XDG_CACHE_HOME",
                "VOICEVAULT_MODEL_ROOTS", "VOICEVAULT_COMFYUI_DIRS", "VOICEVAULT_FORGE_DIRS",
                "VOICEVAULT_VIBEVOICE_TOKENIZER_DIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    monkeypatch.setattr(settings, "load", lambda: {})
    return tmp_path


def roots(cfg, base):
    return model_roots(cfg, project_root=base / "apps" / "VoiceVault", home=base / "home")


def test_hf_cache_environment_precedence(isolated, monkeypatch):
    base = isolated
    assert huggingface_cache() == base / "hf" / "hub"
    monkeypatch.setenv("HUGGINGFACE_HUB_CACHE", str(base / "old-cache"))
    monkeypatch.setenv("HF_HUB_CACHE", str(base / "preferred-cache"))
    assert huggingface_cache() == base / "preferred-cache"
    monkeypatch.delenv("HF_HUB_CACHE")
    assert huggingface_cache() == base / "old-cache"
    monkeypatch.delenv("HUGGINGFACE_HUB_CACHE")
    monkeypatch.delenv("HF_HOME")
    monkeypatch.setenv("XDG_CACHE_HOME", str(base / "xdg"))
    assert huggingface_cache() == base / "xdg" / "huggingface" / "hub"


def test_custom_legacy_and_extra_roots_are_retained_and_deduplicated(isolated, monkeypatch):
    legacy = isolated / "shared"
    extra = isolated / "additional"
    monkeypatch.setenv("VOICEVAULT_MODEL_ROOTS", os.pathsep.join([str(legacy), str(extra)]))
    cfg = {"shared_models_dir": str(legacy), "model_search_roots": [str(extra), ""]}
    found = roots(cfg, isolated)
    assert found[:2] == [legacy, extra]
    assert len(found) == 3  # plus HF cache
    assert cfg["shared_models_dir"] == str(legacy)


def test_blank_cache_override_falls_back_and_trims_surrounding_spaces(isolated, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", "   ")
    monkeypatch.setenv("HUGGINGFACE_HUB_CACHE", " \t ")
    assert huggingface_cache() == isolated / "hf" / "hub"
    monkeypatch.setenv("HF_HUB_CACHE", f"  {isolated / 'cache'}  ")
    assert huggingface_cache() == isolated / "cache"
    monkeypatch.setenv("HF_HUB_CACHE", " ")
    monkeypatch.delenv("HF_HOME")
    monkeypatch.setattr("huggingface_hub.constants.HF_HUB_CACHE", " ")
    assert huggingface_cache() == Path.home() / ".cache" / "huggingface" / "hub"


def test_drive_root_is_rejected_but_nested_roots_are_preserved(isolated, caplog):
    filesystem_root = Path(isolated.anchor)
    configured = isolated / "shared"
    nested = configured / "deep" / "model"
    found = roots({"model_search_roots": [str(filesystem_root), str(configured), str(nested)]}, isolated)
    assert filesystem_root not in found
    assert configured in found and nested in found
    assert "Ignoring filesystem root" in caplog.text


def test_comfy_shared_config_and_forge_model_folder(isolated):
    comfy = isolated / "apps" / "ComfyUI"
    comfy.mkdir(parents=True)
    shared = isolated / "shared"
    (comfy / "extra_model_paths.yaml").write_text(
        f"external:\n  base_path: {shared.as_posix()}\n  checkpoints: checkpoints\n  tts: speech\n",
        encoding="utf-8")
    forge = isolated / "elsewhere" / "forge"
    found = roots({"forge_dirs": [str(forge)]}, isolated)
    assert comfy / "models" in found  # named shallow sibling discovered
    assert shared in found
    assert shared / "speech" in found
    assert forge / "models" in found


def test_comfy_absolute_category_paths_allow_shared_infrastructure(isolated):
    comfy = isolated / "comfy"
    comfy.mkdir()
    shared = isolated / "shared-library"
    (comfy / "extra_model_paths.yaml").write_text(
        f"any-name:\n  checkpoints: |-\n    {(shared / 'Stable-Diffusion').as_posix()}\n",
        encoding="utf-8")
    assert shared in roots({"comfyui_dirs": [str(comfy)]}, isolated)


def test_malformed_or_unsafe_yaml_does_not_execute(isolated, caplog):
    comfy = isolated / "comfy"
    comfy.mkdir()
    (comfy / "extra_model_paths.yaml").write_text(
        "!!python/object/apply:os.system ['this-must-not-run']", encoding="utf-8")
    assert comfy / "models" in roots({"comfyui_dirs": [str(comfy)]}, isolated)
    assert "Invalid ComfyUI" in caplog.text


def test_no_recursive_app_search(isolated):
    nested = isolated / "apps" / "unrelated" / "ComfyUI"
    nested.mkdir(parents=True)
    assert nested / "models" not in roots({}, isolated)


class FakeEngine:
    engine_id = "fake"

    def identify(self, folder):
        return ModelInfo("", "fake", "Fake", "Fake", str(folder), 0, ModelState.INTEGRATED)

    def runtime_problem(self):
        return None


def test_registry_deduplicates_same_model_but_retains_distinct_colliding_models(isolated):
    first, second = isolated / "first", isolated / "second"
    for root in (first, second):
        folder = root / "model"
        folder.mkdir(parents=True)
        (folder / "config.json").write_text("{}", encoding="utf-8")
    registry = ModelRegistry([FakeEngine()], [first, first / "model", second])
    models = registry.discover()
    assert len(models) == 2
    assert models[0].id == "fake:model"
    assert models[1].id.startswith("fake:model:")
    registry.set_roots([second])
    assert [m.path for m in registry.discover()] == [str(second / "model")]


def tokenizer_at(folder):
    folder.mkdir(parents=True)
    for name in ("tokenizer.json", "tokenizer_config.json"):
        (folder / name).write_text("{}", encoding="utf-8")
    return folder


def test_vibevoice_finds_tokenizer_in_hf_cache(isolated):
    repo = isolated / "hf" / "hub" / "models--Qwen--Qwen2.5-1.5B"
    cached = tokenizer_at(repo / "snapshots" / "version1")
    (repo / "refs").mkdir()
    (repo / "refs" / "main").write_text("version1", encoding="utf-8")
    assert _tokenizer_dir(isolated / "model") == cached


def test_vibevoice_respects_explicit_tokenizer_over_adjacent_folder(isolated, monkeypatch):
    adjacent = tokenizer_at(isolated / "vibevoice" / "tokenizer")
    custom = tokenizer_at(isolated / "custom-tokenizer")
    monkeypatch.setenv("VOICEVAULT_VIBEVOICE_TOKENIZER_DIR", str(custom))
    assert _tokenizer_dir(adjacent.parent / "model") == custom
    monkeypatch.setenv("VOICEVAULT_VIBEVOICE_TOKENIZER_DIR", str(isolated / "missing"))
    assert _tokenizer_dir(adjacent.parent / "model") is None  # never silently fall back from a bad explicit choice
