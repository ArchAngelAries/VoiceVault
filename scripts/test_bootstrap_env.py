"""Desktop first-run setup checks with fake commands; nothing is installed."""
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from scripts import bootstrap_env
from scripts.bootstrap_env import Step, execute, parse_verification, steps, write_marker
from scripts.install_torch import install_command


def test_plan_installs_gpu_torch_first_then_constrained_requirements(tmp_path):
    root, constraints = tmp_path / "res", tmp_path / "c.txt"
    plan = steps(root, "py.exe", "uv.exe", constraints)
    assert [s.title for s in plan][0].startswith("Install PyTorch")
    assert plan[0].command == ["py.exe", str(root / "scripts" / "install_torch.py")]
    deps = plan[1].command
    assert deps[:5] == ["uv.exe", "pip", "install", "--python", "py.exe"]
    assert deps[deps.index("-c") + 1] == str(constraints)
    assert str(root / "backend" / "requirements-lock.txt") in deps
    assert str(root / "backend" / "requirements-tts.txt") in deps
    assert plan[2].command[-2:] == ["--no-deps", "omnivoice==0.2.1"]
    assert plan[3].command[1:4] == ["-m", "prisma", "generate"]
    assert plan[-1].title == "Check the installation"


def test_torch_installer_uses_uv_only_when_requested():
    assert install_command("torch", env={})[1:4] == ["-m", "pip", "install"]
    command = install_command("torch", env={"VOICEVAULT_UV": "C:/x/uv.exe"})
    assert command[:4] == ["C:/x/uv.exe", "pip", "install", "--python"]
    assert command[-1] == "torch"


def test_execute_pins_new_gpu_build_and_returns_check_output(tmp_path, monkeypatch):
    constraints = tmp_path / "c.txt"
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout='VERIFY {"torch": "2.9.1+rocm7.2.1"}\n')

    monkeypatch.setattr(bootstrap_env, "gpu_constraints", lambda: ["torch==2.9.1+rocm7.2.1"])
    plan = [Step("Install PyTorch for your graphics card", ["a"]), Step("Check the installation", ["b"])]
    output = execute(plan, constraints, runner=runner)
    assert calls == [["a"], ["b"]]
    assert constraints.read_text(encoding="utf-8").strip() == "torch==2.9.1+rocm7.2.1"
    assert parse_verification(output) == {"torch": "2.9.1+rocm7.2.1"}


def test_failed_step_stops_the_plan(tmp_path):
    def runner(command, **kwargs):
        raise subprocess.CalledProcessError(2, command)

    with pytest.raises(subprocess.CalledProcessError):
        execute([Step("x", ["a"]), Step("y", ["b"])], tmp_path / "c.txt", runner=runner)


def test_marker_records_fingerprint_and_versions(tmp_path):
    marker = tmp_path / "env" / "marker.json"
    write_marker(marker, "abc", {"torch": "2.9.1+rocm7.2.1", "accelerator": "rocm"})
    data = json.loads(marker.read_text(encoding="utf-8"))
    assert data["fingerprint"] == "abc" and data["accelerator"] == "rocm" and data["schema"] == 1
    assert not list(marker.parent.glob("*.tmp"))


def test_missing_verification_is_an_error():
    with pytest.raises(RuntimeError):
        parse_verification("nothing here")


def test_main_refuses_without_uv(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("VOICEVAULT_UV", raising=False)
    monkeypatch.setattr(bootstrap_env.sys, "version_info", (3, 12, 0))
    code = bootstrap_env.main(["--resources", str(tmp_path), "--marker", str(tmp_path / "m.json"),
                               "--fingerprint", "x"])
    assert code == 1 and "VOICEVAULT_UV" in capsys.readouterr().err
