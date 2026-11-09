"""One-click Drama 3: orchestration with fakes (no 41 GB download), recipe parity, resume, cleanup."""
import subprocess
import sys
import types
from pathlib import Path

import pytest

from models import drama_convert as dc


def test_q6_recipe_matches_the_verified_build(tmp_path):
    ck = tmp_path / "ck"
    ck.mkdir()
    for n in ("config.json", "tokenizer.json", "LICENSE"):
        (ck / n).write_text("x")
    cmd = dc.q6_command(Path("audiocpp_gguf.exe"), Path("f16.gguf"), Path("out.gguf"), ck, Path("spec.json"))
    s = " ".join(cmd)
    assert "--type q8_0" in s and "--family fish_drama3" in s and "--fold-weight-norm codec_weights*" in s
    assert "model_weights/model.language_model.embed_tokens.weight=f16" in s
    assert sum(1 for x in cmd if x.endswith(".mlp.experts.*=q6_k")) == 40
    assert sum(1 for x in cmd if ".mlp.gate.weight*=orig" in x) == 40
    assert sum(1 for x in cmd if x.startswith(str(ck)) and x.endswith(("=config.json", "=tokenizer.json", "=LICENSE"))) == 3


def test_disk_need():
    assert dc.disk_needed(False) > 140e9 and dc.disk_needed(True) - dc.disk_needed(False) == dc.Q6_BYTES


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    runtime = tmp_path / "rt"
    (runtime / "model_specs").mkdir(parents=True)
    exe = runtime / "audiocpp_server.exe"
    exe.write_text("")
    (runtime / "audiocpp_gguf.exe").write_text("")
    (runtime / "model_specs" / "fish_drama3.json").write_text("{}")
    monkeypatch.setattr("engines.audiocpp_server.find_server_executable", lambda d="": exe)
    monkeypatch.setattr(dc.os, "name", "nt")
    monkeypatch.setattr(dc.shutil, "disk_usage", lambda p: types.SimpleNamespace(free=10 ** 13))

    def fake_tools(work, say):
        (work / "tools" / "pylib" / "gguf").mkdir(parents=True, exist_ok=True)
        conv = work / "tools" / "convert_fish_drama3.py"
        conv.write_text("")
        return conv, work / "tools" / "pylib"
    monkeypatch.setattr(dc, "prepare_tools", fake_tools)
    downloads = []

    def snapshot_download(repo, revision=None, local_dir=None, token=None):
        downloads.append((repo, revision))
        root = Path(local_dir)
        root.mkdir(parents=True, exist_ok=True)
        for i in range(1, 16):
            (root / f"model-{i:05d}-of-00015.safetensors").write_bytes(b"w")
        (root / "codec.pth").write_bytes(b"c")
        (root / "LICENSE").write_text("Fish Audio Community License")
        return str(root)
    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(snapshot_download=snapshot_download))
    runs = []

    def fake_run(args, env=None, creationflags=0, **kw):
        runs.append(args)
        out = None
        if "--quantized-output" in args:
            out = Path(args[args.index("--quantized-output") + 1])
            if "--output" in args:
                Path(args[args.index("--output") + 1]).write_bytes(b"f16")
        elif "--output" in args:
            out = Path(args[args.index("--output") + 1])
        if out:
            out.write_bytes(b"gguf")
        return types.SimpleNamespace(returncode=0)
    monkeypatch.setattr(dc.subprocess, "run", fake_run)
    return types.SimpleNamespace(models=tmp_path / "Models", downloads=downloads, runs=runs)


def test_full_run_builds_q4_and_q6_writes_notices_and_cleans_up(fake_env):
    msgs = []
    files = dc.run(fake_env.models, "Drama-3-GGUF-final", None, also_max=True, keep_checkpoint=False, say=msgs.append)
    out = fake_env.models / "Drama-3-GGUF-final"
    assert (out / dc.Q4_NAME).is_file() and (out / dc.Q6_NAME).is_file()
    assert "Fish Audio Community License" in (out / "LICENSE").read_text()
    notice = (out / "NOTICE").read_text()
    assert "Built with Fish Audio" in notice and dc.CHECKPOINT_REVISION in notice and "Q6_K" in notice
    assert fake_env.downloads == [(dc.CHECKPOINT_REPO, dc.CHECKPOINT_REVISION)]
    assert not (fake_env.models / ".drama3-work").exists()          # 110 GB of temporary files removed
    assert set(files) == {str(out / n) for n in (dc.Q4_NAME, dc.Q6_NAME, "LICENSE", "NOTICE")}
    conv_args = fake_env.runs[0]
    assert "--expert-type" in conv_args and conv_args[conv_args.index("--expert-type") + 1] == "q4_k"
    assert any("Step 3/5" in m for m in msgs)


def test_resume_skips_finished_package(fake_env):
    out = fake_env.models / "Drama-3-GGUF-final"
    out.mkdir(parents=True)
    (out / dc.Q4_NAME).write_bytes(b"done")
    dc.run(fake_env.models, "Drama-3-GGUF-final", None, also_max=False, keep_checkpoint=True, say=lambda m: None)
    assert fake_env.runs == []                                         # nothing re-converted
    assert fake_env.downloads == []                                    # and nothing downloaded just to be kept


def test_not_enough_disk(fake_env, monkeypatch):
    monkeypatch.setattr(dc.shutil, "disk_usage", lambda p: types.SimpleNamespace(free=10 ** 9))
    with pytest.raises(RuntimeError, match="Not enough disk space"):
        dc.run(fake_env.models, "Drama-3-GGUF-final", None, False, False, lambda m: None)


def test_catalog_offers_one_click_drama():
    from models.catalog import BY_ID
    src = BY_ID["drama3_final"].sources[0]
    assert src.kind == "drama_convert" and src.revision == dc.CHECKPOINT_REVISION and src.repo == dc.CHECKPOINT_REPO


# ── Chosen sizes (quantization levels) ──────────────────────────────────────

def test_levels_recipe_and_disclosure_data():
    assert dc.clean_levels(None) == ["q4_k"]                          # Q4 is always built
    assert dc.clean_levels(["q8_0", "q3_k", "bogus"], also_max=True) == ["q3_k", "q4_k", "q6_k", "q8_0"]
    cmd = dc.level_command(Path("t.exe"), Path("f16.gguf"), Path("o.gguf"), Path("ck"), Path("s.json"), "q3_k")
    assert sum(1 for x in cmd if x.endswith(".mlp.experts.*=q3_k")) == 40
    assert sum(1 for x in cmd if ".mlp.gate.weight*=orig" in x) == 40          # routers stay full precision
    assert "model_weights/lm_head.weight*=orig" in cmd                        # speech head too
    assert dc.level_name("q4_k") == dc.Q4_NAME and dc.level_name("q6_k") == dc.Q6_NAME
    assert "Q3_K" in dc.level_name("q3_k") and "routers-head-F16" in dc.level_name("q3_k")
    # every level states its quality; the tested ones are Q4 and Q6; smaller ones carry a warning
    assert {lv for lv, i in dc.LEVELS.items() if i["tested"]} == {"q4_k", "q6_k"}
    assert dc.LEVELS["q2_k"]["warning"] and dc.LEVELS["q3_k"]["warning"] and not dc.LEVELS["q4_k"]["warning"]
    assert all(i["note"] for i in dc.LEVELS.values())
    assert dc.disk_needed(levels=["q3_k"]) - dc.disk_needed(levels=["q3_k"], have_checkpoint=True) == dc.CHECKPOINT_BYTES


def test_building_extra_sizes_later_from_the_kept_original(fake_env):
    out = fake_env.models / "Drama-3-GGUF-final"
    dc.run(fake_env.models, "Drama-3-GGUF-final", None, also_max=False, keep_checkpoint=True, say=lambda m: None)
    assert (out / dc.Q4_NAME).is_file()
    first_downloads = len(fake_env.downloads)
    fake_env.runs.clear()
    files = dc.run(fake_env.models, "Drama-3-GGUF-final", None, also_max=False, keep_checkpoint=False,
                   say=lambda m: None, levels=["q3_k", "q8_0"])
    assert (out / dc.level_name("q3_k")).is_file() and (out / dc.level_name("q8_0")).is_file()
    # Q4 existed, so the intermediate was rebuilt into a scratch file, and the kept download was reused
    assert any("q4-scratch.gguf" in " ".join(r) for r in fake_env.runs)
    assert len(fake_env.downloads) == first_downloads + 1 and fake_env.downloads[-1] == fake_env.downloads[0]
    assert str(out / dc.level_name("q8_0")) in files
    notice = (out / "NOTICE").read_text()
    assert "Q3_K" in notice and "Q8_0" in notice


def test_package_labels_disclose_lower_quality():
    from engines.fish_drama import _describe_package
    assert "lower quality" in _describe_package(dc.level_name("q3_k"))
    assert "lower quality" in _describe_package(dc.level_name("q2_k"))
    assert "lower quality" not in _describe_package(dc.Q4_NAME)
    assert "max quality" in _describe_package(dc.Q6_NAME)


def test_manager_rejects_unknown_sizes(tmp_path, monkeypatch):
    from models import manager
    monkeypatch.setattr(manager, "models_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "_read", lambda name: ["drama3_final"] if name == "accepted.json" else {})
    with pytest.raises(manager.ModelError, match="Unknown Drama 3 size"):
        manager.start("drama3_final", True, {"levels": ["q9_x"]})


def test_nothing_new_to_build_downloads_nothing(fake_env):
    out = fake_env.models / "Drama-3-GGUF-final"
    out.mkdir(parents=True)
    (out / dc.Q4_NAME).write_bytes(b"done")
    dc.run(fake_env.models, "Drama-3-GGUF-final", None, also_max=False, keep_checkpoint=False, say=lambda m: None)
    assert fake_env.downloads == [] and fake_env.runs == []


def test_smaller_sizes_need_the_quality_acknowledgement_on_the_server_too(tmp_path, monkeypatch):
    from models import manager
    monkeypatch.setattr(manager, "models_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "_jobs", {})
    monkeypatch.setattr(manager, "_read", lambda name: ["drama3_final"] if name == "accepted.json" else {})
    with pytest.raises(manager.ModelError, match="lose quality"):
        manager.start("drama3_final", True, {"levels": ["q3_k"]})
    started = []
    monkeypatch.setattr(manager.threading, "Thread", lambda **kw: type("T", (), {"start": lambda self: started.append(kw)})())
    monkeypatch.setattr(manager.shutil, "disk_usage", lambda p: type("U", (), {"free": 10 ** 13})())
    job = manager.start("drama3_final", True, {"levels": ["q3_k"], "acknowledge_lower_quality": True})
    assert job["options"]["levels"] == ["q3_k"] and started
