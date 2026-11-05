"""Installer safety checks with fake commands; no installation is executed."""
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from scripts.setup_project import execute, gpu_constraints, steps


def test_preserves_exact_gpu_build_versions_without_pinning_unrelated_packages():
    installed = [SimpleNamespace(metadata={"Name": name}, version=version) for name, version in [
        ("torch", "2.9.1+rocm7.2.1"), ("torchaudio", "2.9.1+rocm7.2.1"),
        ("rocm_sdk_core", "7.2.1"), ("httpx", "0.28.1"),
    ]]
    assert gpu_constraints(installed) == [
        "rocm-sdk-core==7.2.1", "torch==2.9.1+rocm7.2.1", "torchaudio==2.9.1+rocm7.2.1",
    ]


def test_existing_gpu_setup_skips_torch_install_but_completes_client_and_locked_frontend(tmp_path):
    root = tmp_path / "Project with spaces"
    constraint = tmp_path / "constraints.txt"
    plan = steps(root, "python.exe", "node.exe", "npm-cli.js", constraint, has_torch=True)
    assert not any("install_torch.py" in " ".join(step.command) for step in plan)
    backend = next(step for step in plan if step.title == "Install backend dependencies")
    assert backend.command[backend.command.index("-c") + 1] == str(constraint)
    assert str(root / "backend/requirements-lock.txt") in backend.command
    client = next(step for step in plan if step.title == "Generate the database client")
    assert client.command[-1] == str(root / "backend/db/schema.prisma")
    frontend = next(step for step in plan if step.title == "Install frontend dependencies")
    assert frontend.cwd == root / "frontend" and "ci" in frontend.command
    assert not any("db" in step.command and "push" in step.command for step in plan)
    assert plan[-1].command[-2:] == ["run", "build"]


def test_failed_step_stops_before_later_installs_and_callbacks(tmp_path):
    plan = steps(tmp_path, "python", "node", "npm-cli.js", Path("constraints.txt"), has_torch=True)
    calls, successful = [], []

    def runner(command, cwd, check):
        assert check
        calls.append(command)
        if len(calls) == 2:
            raise subprocess.CalledProcessError(1, command)

    with pytest.raises(subprocess.CalledProcessError):
        execute(plan, runner=runner, after_step=lambda step: successful.append(step.title))
    assert len(calls) == 2 and successful == ["Prepare pip"]


def test_fresh_install_places_gpu_preparation_before_backend_resolution(tmp_path):
    plan = steps(tmp_path, "python", "node", "npm-cli.js", Path("constraints.txt"), has_torch=False)
    assert plan[1].command[-1] == str(tmp_path / "scripts/install_torch.py")
    assert plan[2].title == "Install backend dependencies"
