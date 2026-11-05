from __future__ import annotations

import hashlib
from pathlib import Path
import zipfile

import pytest

from training import runtime


def _fake_wheel(path: Path, spec: runtime.WheelSpec, *, pth: bool = False, traversal: bool = False) -> None:
    metadata_dir = f"{spec.name}-{spec.version}.dist-info"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{metadata_dir}/METADATA", f"Name: {spec.name}\nVersion: {spec.version}\n")
        if spec.name == "sampleaddon":
            archive.writestr("sampleaddon/__init__.py", "VALUE = 1\n")
        if pth:
            archive.writestr("sampleaddon.pth", "import os; os.system('bad')\n")
        if traversal:
            archive.writestr("../escaped.py", "bad = True\n")


def test_readiness_is_read_only_and_provision_commands_are_targeted(tmp_path):
    paths = runtime.layout(tmp_path)
    status = runtime.runtime_readiness(tmp_path)
    assert status["missing_extras"] == [wheel.name for wheel in runtime.WHEELS]
    assert not paths["training"].exists()
    download, install = runtime.pip_commands("python", Path("training_requirements.lock"), paths["training"] / "stage")
    assert "--no-deps" in download and "--only-binary=:all:" in download and "--require-hashes" in download
    assert "--retries" in download and download[download.index("--retries") + 1] == "1"
    assert "--timeout" in download and download[download.index("--timeout") + 1] == "20"
    assert "--no-deps" in install and "--no-index" in install and "--target" in install
    assert "--no-compile" in install
    assert str(paths["training"] / "stage" / "site-packages") == install[install.index("--target") + 1]
    assert "--upgrade" not in download + install


def test_provision_stages_only_audited_wheels_and_backs_up_managed_runtime(tmp_path, monkeypatch):
    spec = runtime.WheelSpec("sampleaddon", "1.2", "sampleaddon-1.2-py3-none-any.whl", "", ("sampleaddon/",))
    wheel_path = tmp_path / spec.filename
    _fake_wheel(wheel_path, spec)
    spec = runtime.WheelSpec(spec.name, spec.version, spec.filename, hashlib.sha256(wheel_path.read_bytes()).hexdigest(), spec.roots)
    monkeypatch.setattr(runtime, "WHEELS", (spec,))
    monkeypatch.setitem(runtime._ALLOWED_WHEEL_TOPS, spec.name, {"sampleaddon", "sampleaddon-1.2.dist-info"})
    monkeypatch.setattr(runtime, "core_readiness", lambda: {"versions": {}, "missing": [], "incompatible": []})
    lock = tmp_path / "lock.txt"
    lock.write_text(f"{spec.name}=={spec.version} --hash=sha256:{spec.sha256}\n", encoding="utf-8")
    calls = []

    def fake_runner(command, cwd, check, timeout):
        calls.append((command, Path(cwd), check, timeout))
        if "download" in command:
            destination = Path(command[command.index("--dest") + 1])
            destination.mkdir(parents=True, exist_ok=True)
            (destination / spec.filename).write_bytes(wheel_path.read_bytes())
        else:
            target = Path(command[command.index("--target") + 1])
            target.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(Path(command[command.index("--find-links") + 1]) / spec.filename) as archive:
                archive.extractall(target)

    result = runtime.provision(tmp_path / "data", python="core-python", lock_path=lock, runner=fake_runner)
    published = tmp_path / "data" / "training" / "runtime"
    assert result["extras"] == {"sampleaddon": "1.2"}
    assert (published / ".voicevault-training-runtime.json").is_file()
    assert len(calls) == 2
    assert all("--no-deps" in call[0] and call[2] for call in calls)
    assert all(call[3] == 180 for call in calls)
    assert all(str(published) not in call[0] for call in calls)  # pip only touched staging

    runtime.provision(tmp_path / "data", python="core-python", lock_path=lock, runner=fake_runner)
    backups = list((tmp_path / "data" / "training").glob("runtime-backup-*"))
    assert len(backups) == 1 and (backups[0] / ".voicevault-training-runtime.json").is_file()


def test_provision_lock_refuses_concurrent_owner_and_preserves_their_lock(tmp_path):
    training = tmp_path / "training"
    training.mkdir()
    lock = training / ".runtime-provision.lock"
    lock.write_text('{"pid": 1234, "token": "another-owner"}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="PID 1234"):
        with runtime._provision_lock(training):
            pytest.fail("concurrent provision should not acquire the lock")
    assert lock.exists()


def test_wheel_validation_rejects_startup_payload_before_install(tmp_path, monkeypatch):
    spec = runtime.WheelSpec("sampleaddon", "1.2", "sampleaddon-1.2-py3-none-any.whl", "", ("sampleaddon/",))
    wheel_dir = tmp_path / "wheel-cache"
    wheel_dir.mkdir()
    wheel = wheel_dir / spec.filename
    _fake_wheel(wheel, spec, pth=True)
    spec = runtime.WheelSpec(spec.name, spec.version, spec.filename, hashlib.sha256(wheel.read_bytes()).hexdigest(), spec.roots)
    monkeypatch.setattr(runtime, "WHEELS", (spec,))
    monkeypatch.setitem(runtime._ALLOWED_WHEEL_TOPS, spec.name, {"sampleaddon", "sampleaddon-1.2.dist-info", "sampleaddon.pth"})
    with pytest.raises(RuntimeError, match="startup payload"):
        runtime.validate_wheelhouse(wheel_dir)


def test_wheel_validation_rejects_path_traversal(tmp_path, monkeypatch):
    spec = runtime.WheelSpec("sampleaddon", "1.2", "sampleaddon-1.2-py3-none-any.whl", "", ("sampleaddon/",))
    wheel_dir = tmp_path / "wheel-cache"
    wheel_dir.mkdir()
    wheel = wheel_dir / spec.filename
    _fake_wheel(wheel, spec, traversal=True)
    spec = runtime.WheelSpec(spec.name, spec.version, spec.filename, hashlib.sha256(wheel.read_bytes()).hexdigest(), spec.roots)
    monkeypatch.setattr(runtime, "WHEELS", (spec,))
    monkeypatch.setitem(runtime._ALLOWED_WHEEL_TOPS, spec.name, {"sampleaddon", "sampleaddon-1.2.dist-info"})
    with pytest.raises(RuntimeError, match="Unsafe archive path"):
        runtime.validate_wheelhouse(wheel_dir)
