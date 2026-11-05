"""Complete project setup without replacing an existing PyTorch/GPU build.

Called by setup.bat using the project environment. --check and --dry-run never
install packages, generate clients, or modify the database.
"""
from __future__ import annotations

import argparse
import importlib.metadata as metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Step:
    title: str
    command: list[str]
    cwd: Path


def gpu_constraints(distributions=None) -> list[str]:
    """Constrain installed GPU packages so pip fails instead of replacing them."""
    protected = {"torch", "torchaudio", "torchvision", "triton", "triton-rocm"}
    result = []
    for distribution in (metadata.distributions() if distributions is None else distributions):
        name = distribution.metadata.get("Name", "").lower().replace("_", "-")
        if name in protected or name.startswith("rocm"):
            result.append(f"{name}=={distribution.version}")
    return sorted(set(result))


def node_tools() -> tuple[str, str]:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js was not found on PATH. Install Node.js and reopen setup.")
    output = subprocess.run([node, "--version"], check=True, capture_output=True, text=True).stdout.strip()
    major = int(output.lstrip("v").split(".")[0])
    if major != 18 and major < 20:
        raise RuntimeError(f"Node.js {output} does not meet the frontend requirement (18.x or 20+).")
    # Run npm's JavaScript with Node directly: no shell/path quoting problems on Windows.
    npm = shutil.which("npm")
    candidates = [Path(node).resolve().parent / "node_modules/npm/bin/npm-cli.js"]
    if npm:
        location = Path(npm)
        candidates += [location.parent / "node_modules/npm/bin/npm-cli.js", location.resolve()]
    for candidate in candidates:
        if candidate.is_file() and candidate.suffix == ".js":
            return node, str(candidate)
    raise RuntimeError("npm's CLI was not found with Node.js. Repair the Node.js/npm installation.")


def steps(root: Path, python: str, node: str, npm: str, constraints: Path, has_torch: bool) -> list[Step]:
    plan = [Step("Prepare pip", [python, "-m", "pip", "install", "--upgrade", "pip"], root)]
    if not has_torch:
        plan.append(Step("Install a suitable PyTorch build", [python, str(root / "scripts/install_torch.py")], root))
    plan += [
        Step("Install backend dependencies", [python, "-m", "pip", "install", "-c", str(constraints),
             "-c", str(root / "backend/requirements-lock.txt"),
             "-r", str(root / "backend/requirements.txt"), "-r", str(root / "backend/requirements-tts.txt")], root),
        Step("Install OmniVoice inference runtime", [python, "-m", "pip", "install", "--no-deps", "omnivoice==0.2.1"], root),
        Step("Generate the database client", [python, "-m", "prisma", "generate", "--schema", str(root / "backend/db/schema.prisma")], root),
        Step("Install frontend dependencies", [node, npm, "ci", "--no-audit", "--no-fund"], root / "frontend"),
        Step("Build the frontend", [node, npm, "run", "build"], root / "frontend"),
    ]
    return plan


def execute(plan: list[Step], runner=subprocess.run, after_step=None) -> None:
    for index, step in enumerate(plan, 1):
        print(f"[{index}/{len(plan)}] {step.title}...", flush=True)
        runner(step.command, cwd=step.cwd, check=True)
        if after_step:
            after_step(step)


def check_installation(root: Path) -> list[str]:
    problems = []
    for package in ("fastapi", "uvicorn", "prisma", "transformers", "torch", "omnivoice"):
        try:
            metadata.version(package)
        except metadata.PackageNotFoundError:
            problems.append(f"Missing Python package: {package}")
    # Client generation is independent of package installation; inspect rather than import it.
    try:
        package = metadata.distribution("prisma")
        if not Path(package.locate_file("prisma/models.py")).is_file():
            problems.append("Prisma database client has not been generated.")
    except metadata.PackageNotFoundError:
        pass
    for name in ("node_modules/electron/dist/electron.exe", "node_modules/vite/bin/vite.js", "dist/index.html"):
        if not (root / "frontend" / name).is_file():
            problems.append(f"Missing frontend file: {name}")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check prerequisites and installed files without changing them")
    parser.add_argument("--dry-run", action="store_true", help="Show the install sequence without executing it")
    args = parser.parse_args(argv)
    try:
        if sys.version_info[:2] != (3, 12):
            raise RuntimeError("Python 3.12 is required; the existing environment was not changed.")
        node, npm = node_tools()
        if not (ROOT / "frontend/package-lock.json").is_file():
            raise RuntimeError("The frontend lockfile is missing; restore it before setup.")
        if not (ROOT / "backend/requirements-lock.txt").is_file():
            raise RuntimeError("The verified backend constraints file is missing; restore it before setup.")
        protected = gpu_constraints()
        has_torch = any(line.startswith("torch==") for line in protected)
        if args.check:
            problems = check_installation(ROOT)
            for problem in problems:
                print(f"ERROR: {problem}")
            if not shutil.which("ffmpeg"):
                problems.append("ffmpeg is missing from PATH.")
                print("ERROR: ffmpeg is missing from PATH; audio workflows need it.")
            if not problems:
                print("Setup files and prerequisites are present. No packages or data were changed.")
            return int(bool(problems))
        if args.dry_run:
            plan = steps(ROOT, sys.executable, node, npm, ROOT / "data/tmp/setup-gpu-constraints.txt", has_torch)
            print(json.dumps({"preserved_gpu_packages": protected, "steps": [
                {"title": step.title, "command": step.command, "cwd": str(step.cwd)} for step in plan]}, indent=2))
            return 0
        tmp = ROOT / "data/tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="setup-", dir=tmp) as directory:
            constraints = Path(directory) / "gpu-constraints.txt"
            constraints.write_text("\n".join(protected) + "\n", encoding="utf-8")
            plan = steps(ROOT, sys.executable, node, npm, constraints, has_torch)
            # A first install must also preserve the new GPU build in subsequent pip steps.
            def preserve_new_build(step):
                if step.title == "Install a suitable PyTorch build":
                    constraints.write_text("\n".join(gpu_constraints()) + "\n", encoding="utf-8")
            execute(plan, after_step=preserve_new_build)
        if not shutil.which("ffmpeg"):
            print("WARNING: ffmpeg is missing from PATH. Install it before using audio workflows.")
        print("Setup complete. Run dev_start.bat to launch VoiceVault.")
        return 0
    except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
