"""Audited optional runtime for OmniVoice fine-tuning.

The runtime is provisioned only by scripts/setup_training_runtime.py --install.
Importing this module never installs packages or imports torch/model code.
"""
from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable

from core.paths import DATA_DIR, VOICEVAULT_ROOT


@dataclass(frozen=True)
class WheelSpec:
    name: str
    version: str
    filename: str
    sha256: str
    roots: tuple[str, ...]


WHEELS = (
    WheelSpec("braceexpand", "0.1.7", "braceexpand-0.1.7-py2.py3-none-any.whl",
              "91332d53de7828103dcae5773fb43bc34950b0c8160e35e0f44c4427a3b85014",
              ("braceexpand/",)),
    WheelSpec("protobuf", "7.34.1", "protobuf-7.34.1-py3-none-any.whl",
              "bb3812cd53aefea2b028ef42bd780f5b96407247f20c6ef7c679807e9d188f11",
              ("google/protobuf/",)),
    WheelSpec("tensorboardx", "2.6.4", "tensorboardx-2.6.4-py3-none-any.whl",
              "5970cf3a1f0a6a6e8b180ccf46f3fe832b8a25a70b86e5a237048a7c0beb18e2",
              ("tensorboardX/",)),
    WheelSpec("webdataset", "1.0.2", "webdataset-1.0.2-py3-none-any.whl",
              "3dbfced32b25c0d199c6b9787937b6f85742bc3c84f652c846893075c1c082d9",
              ("webdataset/",)),
)

_CORE_REQUIREMENTS = {
    "omnivoice": ("0.2.1", "=="),
    "torch": ("2.4", ">="),
    "torchaudio": ("2.4", ">="),
    "transformers": ("5.3.0", ">="),
    "accelerate": (None, None),
    "numpy": (None, None),
    "librosa": (None, None),
    "soundfile": (None, None),
    "packaging": (None, None),
    "pyyaml": (None, None),
    "tqdm": (None, None),
}
_FORBIDDEN_SUFFIXES = {".pth", ".pyc", ".pyd", ".dll", ".exe", ".com", ".bat", ".cmd", ".ps1", ".sh", ".so", ".dylib"}
_ALLOWED_WHEEL_TOPS = {
    "braceexpand": {"braceexpand", "braceexpand-0.1.7.dist-info"},
    "protobuf": {"google", "protobuf-7.34.1.dist-info"},
    "tensorboardx": {"tensorboardX", "tensorboardx-2.6.4.dist-info"},
    "webdataset": {"webdataset", "webdataset-1.0.2.dist-info"},
}


def layout(data_dir: Path = DATA_DIR) -> dict[str, Path]:
    """Return normalized project data/runtime paths without creating them."""
    data_root = Path(data_dir).expanduser().resolve()
    training_root = data_root / "training"
    runtime_root = training_root / "runtime"
    return {
        "data": data_root,
        "training": training_root,
        "runtime": runtime_root,
        "site": runtime_root / "site-packages",
        "wheel_cache": runtime_root / "wheel-cache",
    }


def _contained(path: Path, parent: Path) -> Path:
    resolved_parent = parent.resolve()
    resolved_path = path.resolve()
    if resolved_path != resolved_parent and resolved_parent not in resolved_path.parents:
        raise RuntimeError(f"Refusing path outside owned directory: {path}")
    return resolved_path


def _distribution_versions(path: Path | None = None) -> dict[str, str]:
    found: dict[str, str] = {}
    if path is None:
        for name in _CORE_REQUIREMENTS:
            try:
                found[name] = metadata.version(name)
            except metadata.PackageNotFoundError:
                pass
        return found
    for dist in metadata.distributions(path=[str(path)]):
        name = dist.metadata.get("Name", "").lower().replace("_", "-")
        if name:
            found[name] = dist.version
    return found


def core_readiness() -> dict:
    versions = _distribution_versions()
    missing = []
    incompatible = []
    try:
        from packaging.version import Version
    except ImportError:
        Version = None
    for name, (required, operator) in _CORE_REQUIREMENTS.items():
        actual = versions.get(name)
        if actual is None:
            missing.append(name)
        elif required:
            if operator == "==" and actual != required:
                incompatible.append(f"{name} {actual} (requires {operator}{required})")
            elif operator == ">=" and Version is None:
                incompatible.append(f"{name} version could not be checked because packaging is unavailable")
            elif operator == ">=" and Version(actual) < Version(required):
                incompatible.append(f"{name} {actual} (requires {operator}{required})")
    return {"versions": {name: versions[name] for name in _CORE_REQUIREMENTS if name in versions},
            "missing": missing, "incompatible": incompatible}


def runtime_readiness(data_dir: Path = DATA_DIR) -> dict:
    paths = layout(data_dir)
    path_problem = None
    try:
        _contained(paths["training"], paths["data"])
        if paths["training"].is_symlink():
            raise RuntimeError("Training path contains a symlink.")
        _contained(paths["runtime"], paths["data"])
        if paths["runtime"].is_symlink() or paths["site"].is_symlink():
            raise RuntimeError("Runtime path contains a symlink.")
    except RuntimeError:
        path_problem = "Runtime path resolves outside its owned data directory or contains a symlink."
    versions = (_distribution_versions(paths["site"])
                if path_problem is None and paths["site"].is_dir() else {})
    required = {wheel.name: wheel.version for wheel in WHEELS}
    missing = [name for name, version in required.items() if versions.get(name) != version]
    unexpected = sorted(set(versions) - set(required))
    marker_problem = path_problem
    marker_path = paths["runtime"] / ".voicevault-training-runtime.json"
    if marker_problem is None and paths["runtime"].exists():
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            if marker.get("managed_by") != "VoiceVault training runtime" or marker.get("requirements") != required:
                marker_problem = "Runtime marker does not match the audited package set."
        except (OSError, ValueError):
            marker_problem = "Managed runtime marker is missing or invalid."
    elif marker_problem is None and not versions:
        marker_problem = "Optional training runtime has not been provisioned."
    core = core_readiness()
    return {
        "runtime": str(paths["runtime"]),
        "extras": {name: versions[name] for name in required if name in versions},
        "missing_extras": missing,
        "unexpected_extras": unexpected,
        "marker_problem": marker_problem,
        "core": core,
        "ready_for_training_dependencies": not missing and not unexpected and not marker_problem and not core["missing"] and not core["incompatible"],
        "training_validated": False,
        "note": "Only optional CPU-side packages are checked; model, ROCm/SDPA behavior, VRAM fit, and full training remain unverified.",
    }


def _normal_distribution(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _expected_lock_lines() -> set[str]:
    return {f"{w.name}=={w.version} --hash=sha256:{w.sha256}" for w in WHEELS}


def validate_lock(lock_path: Path) -> None:
    try:
        lines = {line.strip() for line in lock_path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")}
    except OSError as exc:
        raise RuntimeError(f"Training requirements lock is unavailable: {exc}") from exc
    if lines != _expected_lock_lines():
        raise RuntimeError("Training runtime lock does not match the four audited version/hash pins.")


def validate_wheelhouse(wheel_dir: Path) -> list[Path]:
    """Verify exact wheel set, hashes, metadata, and non-executable safe contents."""
    entries = list(wheel_dir.iterdir())
    if any(not entry.is_file() or entry.is_symlink() for entry in entries):
        raise RuntimeError("Wheel cache contains a directory or symlink.")
    files = sorted(entries)
    expected_names = {spec.filename for spec in WHEELS}
    if {p.name for p in files} != expected_names or len(files) != len(expected_names):
        raise RuntimeError("Wheel cache contains missing or unexpected files.")
    for spec in WHEELS:
        wheel_path = wheel_dir / spec.filename
        digest = hashlib.sha256(wheel_path.read_bytes()).hexdigest()
        if digest != spec.sha256:
            raise RuntimeError(f"SHA256 mismatch for {spec.filename}.")
        top_names: set[str] = set()
        try:
            with zipfile.ZipFile(wheel_path) as archive:
                members = archive.infolist()
                if len(members) > 10000 or sum(item.file_size for item in members) > 100_000_000:
                    raise RuntimeError(f"Wheel exceeds safe archive limits: {spec.filename}")
                if len({item.filename for item in members}) != len(members):
                    raise RuntimeError(f"Wheel contains duplicate archive entries: {spec.filename}")
                metadata_text = None
                for item in members:
                    name = item.filename
                    posix = PurePosixPath(name)
                    if "\\" in name or posix.is_absolute() or any(part in {"", ".", ".."} for part in posix.parts):
                        raise RuntimeError(f"Unsafe archive path in {spec.filename}: {name}")
                    if item.file_size > 25_000_000:
                        raise RuntimeError(f"Oversized archive entry in {spec.filename}: {name}")
                    if stat.S_ISLNK((item.external_attr >> 16) & 0xFFFF):
                        raise RuntimeError(f"Symlink entry is not allowed in {spec.filename}: {name}")
                    if Path(name).suffix.lower() in _FORBIDDEN_SUFFIXES or ".pth" in Path(name).name.lower():
                        raise RuntimeError(f"Executable or startup payload is not allowed: {name}")
                    top_names.add(name.split("/", 1)[0])
                    if name.endswith(".dist-info/METADATA"):
                        metadata_text = archive.read(item).decode("utf-8", errors="strict")
                allowed = _ALLOWED_WHEEL_TOPS[spec.name]
                if not top_names <= allowed:
                    raise RuntimeError(f"Unexpected wheel payload roots in {spec.filename}: {sorted(top_names - allowed)}")
                if metadata_text is None:
                    raise RuntimeError(f"Wheel metadata is missing in {spec.filename}.")
                fields = {key.strip(): value.strip() for line in metadata_text.splitlines()
                          if ":" in line and not line[:1].isspace()
                          for key, value in [line.split(":", 1)]}
                if _normal_distribution(fields.get("Name", "")) != spec.name or fields.get("Version", "").strip() != spec.version:
                    raise RuntimeError(f"Wheel distribution metadata mismatch: {spec.filename}.")
        except zipfile.BadZipFile as exc:
            raise RuntimeError(f"Invalid wheel archive: {spec.filename}") from exc
    return files


def pip_commands(python: str, lock_path: Path, stage: Path) -> tuple[list[str], list[str]]:
    wheels = stage / "wheel-cache"
    target = stage / "site-packages"
    download = [python, "-m", "pip", "download", "--disable-pip-version-check", "--no-cache-dir",
                "--retries", "1", "--timeout", "20", "--only-binary=:all:", "--no-deps",
                "--require-hashes", "--dest", str(wheels), "-r", str(lock_path)]
    install = [python, "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir",
               "--no-index", "--find-links", str(wheels), "--only-binary=:all:", "--no-deps", "--no-compile",
               "--require-hashes", "--target", str(target), "-r", str(lock_path)]
    return download, install


@contextmanager
def _provision_lock(training: Path):
    """Serialize provisioning across processes; stale locks require explicit cleanup."""
    lock_path = training / ".runtime-provision.lock"
    _contained(lock_path, training)
    if lock_path.is_symlink():
        raise RuntimeError("Provision lock path is a symlink; refusing to continue.")
    token = uuid.uuid4().hex
    payload = json.dumps({"pid": os.getpid(), "token": token})
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        try:
            existing = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {"pid": "unknown"}
        raise RuntimeError(
            f"Another runtime provisioner may be active (lock PID {existing.get('pid')}). "
            f"If it is no longer running, remove only {lock_path} and retry."
        ) from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as lock_file:
            lock_file.write(payload)
            lock_file.flush()
        yield
    finally:
        try:
            if json.loads(lock_path.read_text(encoding="utf-8")).get("token") == token:
                lock_path.unlink()
        except (OSError, ValueError):
            pass


def _verify_staged_distributions(site: Path) -> None:
    if site.is_symlink():
        raise RuntimeError("Staged site-packages path became a symlink.")
    found = _distribution_versions(site)
    expected = {wheel.name: wheel.version for wheel in WHEELS}
    if any(found.get(name) != version for name, version in expected.items()):
        raise RuntimeError("Staged site-packages does not contain the exact audited distributions.")
    extra = set(found) - set(expected)
    if extra:
        raise RuntimeError(f"Unexpected distributions appeared in staged runtime: {sorted(extra)}")
    allowed_roots = set().union(*_ALLOWED_WHEEL_TOPS.values())
    entries = list(site.iterdir())
    unexpected = {entry.name for entry in entries} - allowed_roots
    if unexpected or any(entry.is_symlink() for entry in entries):
        raise RuntimeError(f"Unexpected or linked site-packages entries: {sorted(unexpected)}")
    for entry in site.rglob("*"):
        _contained(entry, site)
        if entry.is_symlink() or Path(entry.name).suffix.lower() in _FORBIDDEN_SUFFIXES or ".pth" in entry.name.lower():
            raise RuntimeError(f"Executable, startup, or linked payload in staged runtime: {entry.name}")


def provision(data_dir: Path = DATA_DIR, python: str = sys.executable,
              lock_path: Path | None = None,
              runner: Callable = subprocess.run) -> dict:
    """Download verified wheels, install into staging, then publish with a backup."""
    lock_path = lock_path or VOICEVAULT_ROOT / "scripts" / "training_requirements.lock"
    validate_lock(lock_path)
    readiness = core_readiness()
    if readiness["missing"] or readiness["incompatible"]:
        raise RuntimeError("Core training prerequisites are missing/incompatible: " + ", ".join(readiness["missing"] + readiness["incompatible"]))
    paths = layout(data_dir)
    training = paths["training"]
    runtime = paths["runtime"]
    _contained(training, paths["data"])
    _contained(runtime, paths["data"])
    if training.is_symlink():
        raise RuntimeError("Refusing to use a symlink at the training data path.")
    if runtime.is_symlink():
        raise RuntimeError("Refusing to replace a symlink at the optional runtime path.")
    if runtime.exists():
        marker = runtime / ".voicevault-training-runtime.json"
        if not marker.is_file():
            raise RuntimeError("Existing runtime path is not marked as VoiceVault-managed; refusing to replace it.")
        try:
            managed = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("Existing runtime marker is invalid; refusing to replace it.") from exc
        if managed.get("managed_by") != "VoiceVault training runtime" or managed.get("requirements") != {w.name: w.version for w in WHEELS}:
            raise RuntimeError("Existing runtime marker does not match this audited runtime; refusing replacement.")
    training.mkdir(parents=True, exist_ok=True)
    _contained(training, paths["data"])
    with _provision_lock(training):
        stage = Path(tempfile.mkdtemp(prefix=".runtime-staging-", dir=training))
        _contained(stage, training)
        wheel_dir = stage / "wheel-cache"
        site = stage / "site-packages"
        wheel_dir.mkdir()
        site.mkdir()
        download, install = pip_commands(python, Path(lock_path).resolve(), stage)
        try:
            runner(download, cwd=str(VOICEVAULT_ROOT), check=True, timeout=180)
            _contained(wheel_dir, stage)
            if wheel_dir.is_symlink():
                raise RuntimeError("Staged wheel cache became a symlink.")
            validate_wheelhouse(wheel_dir)
            runner(install, cwd=str(VOICEVAULT_ROOT), check=True, timeout=180)
            _contained(site, stage)
            _verify_staged_distributions(site)
            (stage / ".voicevault-training-runtime.json").write_text(
                json.dumps({"managed_by": "VoiceVault training runtime",
                            "requirements": {w.name: w.version for w in WHEELS}}, indent=2) + "\n",
                encoding="utf-8")
            backup = training / f"runtime-backup-{uuid.uuid4().hex}"
            _contained(backup, training)
            moved_old = False
            if runtime.exists():
                os.replace(runtime, backup)
                moved_old = True
            try:
                os.replace(stage, runtime)
            except Exception:
                if moved_old and backup.exists():
                    os.replace(backup, runtime)
                raise
            return runtime_readiness(data_dir)
        except Exception:
            if stage.exists():
                # Stage is newly created by this invocation and containment was checked above.
                shutil.rmtree(_contained(stage, training), ignore_errors=True)
            raise


def activate_runtime_extras(data_dir: Path = DATA_DIR) -> Path:
    """Prepend a provisioned optional package target for a future child worker."""
    status = runtime_readiness(data_dir)
    if (status["missing_extras"] or status["unexpected_extras"] or status["marker_problem"]
            or status["core"]["missing"] or status["core"]["incompatible"]):
        problems = status["missing_extras"] + status["unexpected_extras"]
        if status["marker_problem"]:
            problems.append(status["marker_problem"])
        problems.extend(status["core"]["missing"] + status["core"]["incompatible"])
        raise RuntimeError("Optional training runtime does not match its audited package set: " + ", ".join(problems))
    site = layout(data_dir)["site"].resolve()
    if str(site) not in sys.path:
        sys.path.insert(0, str(site))
    return site
