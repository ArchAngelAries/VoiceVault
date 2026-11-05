"""First-run setup of the desktop app's private Python environment, using uv.

The installed VoiceVault desktop app runs this with the environment's own Python
after `uv python install` and `uv venv`. It mirrors scripts/setup_project.py:
a GPU-matched PyTorch first (scripts/install_torch.py: ROCm 7.2.1 on AMD, CUDA on
NVIDIA, CPU otherwise; an existing GPU build is never replaced), then the backend
requirements constrained to the verified lock and the installed GPU packages, then
OmniVoice without its extras, then the Prisma client. A marker with the versions
is written last, so an interrupted setup is resumed on the next launch.

Nothing here uses Node.js tooling for the frontend: the installer ships the built UI.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from setup_project import gpu_constraints  # noqa: E402  (shared with setup.bat)

MARKER_SCHEMA = 1
VERIFY_CODE = (
    "import json, torch, fastapi, uvicorn, transformers, prisma\n"
    "from importlib.metadata import version\n"
    "import prisma.models\n"
    "cuda = torch.cuda.is_available()\n"
    "hip = getattr(torch.version, 'hip', None)\n"
    "print('VERIFY ' + json.dumps({'torch': torch.__version__, 'gpu_available': cuda,\n"
    "  'accelerator': ('rocm' if hip else 'cuda') if cuda else 'cpu',\n"
    "  'device': torch.cuda.get_device_name(0) if cuda else 'cpu',\n"
    "  'fastapi': version('fastapi'), 'transformers': version('transformers')}))\n"
)


@dataclass(frozen=True)
class Step:
    title: str
    command: list[str]


def uv_install(uv: str, python: str, *args: str) -> list[str]:
    return [uv, "pip", "install", "--python", python, *args]


def steps(root: Path, python: str, uv: str, constraints: Path) -> list[Step]:
    backend = root / "backend"
    return [
        Step("Install PyTorch for your graphics card", [python, str(root / "scripts" / "install_torch.py")]),
        Step("Install VoiceVault's Python packages", uv_install(
            uv, python, "-c", str(constraints), "-c", str(backend / "requirements-lock.txt"),
            "-r", str(backend / "requirements.txt"), "-r", str(backend / "requirements-tts.txt"))),
        Step("Install the OmniVoice runtime", uv_install(uv, python, "--no-deps", "omnivoice==0.2.1")),
        Step("Prepare the library database (downloads the Prisma engine)",
             [python, "-m", "prisma", "generate", "--schema", str(backend / "db" / "schema.prisma")]),
        Step("Check the installation", [python, "-c", VERIFY_CODE]),
    ]


def parse_verification(output: str) -> dict:
    for line in reversed(output.splitlines()):
        if line.startswith("VERIFY "):
            return json.loads(line[len("VERIFY "):])
    raise RuntimeError("The installation check printed no result.")


def write_marker(path: Path, fingerprint: str, verification: dict) -> dict:
    marker = {
        "schema": MARKER_SCHEMA,
        "fingerprint": fingerprint,
        "python": sys.version.split()[0],
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **verification,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return marker


def execute(plan: list[Step], constraints: Path, runner=subprocess.run, env=None) -> str:
    """Run each step, streaming output. Returns the output of the final (check) step."""
    final_output = ""
    for index, step in enumerate(plan, 1):
        print(f"STEP {index}/{len(plan)} {step.title}", flush=True)
        if index == len(plan):
            result = runner(step.command, check=True, env=env, capture_output=True, text=True)
            final_output = result.stdout or ""
            print(final_output, end="", flush=True)
        else:
            runner(step.command, check=True, env=env)
        if step.title.startswith("Install PyTorch"):
            # Pin whatever GPU build is now installed so later steps fail instead of replacing it.
            constraints.write_text("\n".join(gpu_constraints()) + "\n", encoding="utf-8")
    return final_output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resources", required=True, help="Folder holding backend/ and scripts/")
    parser.add_argument("--marker", required=True, help="Marker file written after a successful setup")
    parser.add_argument("--fingerprint", required=True, help="Requirement-set fingerprint to record")
    args = parser.parse_args(argv)
    try:
        if sys.version_info[:2] != (3, 12):
            raise RuntimeError(f"Python 3.12 is required, found {sys.version.split()[0]}.")
        uv = os.environ.get("VOICEVAULT_UV", "").strip()
        if not uv or not Path(uv).is_file():
            raise RuntimeError("VOICEVAULT_UV must point to uv.exe.")
        root = Path(args.resources).resolve()
        marker = Path(args.marker).resolve()
        constraints = marker.parent / "gpu-constraints.txt"
        constraints.parent.mkdir(parents=True, exist_ok=True)
        constraints.write_text("\n".join(gpu_constraints()) + "\n", encoding="utf-8")
        plan = steps(root, sys.executable, uv, constraints)
        verification = parse_verification(execute(plan, constraints, env=os.environ.copy()))
        write_marker(marker, args.fingerprint, verification)
        print(f"DONE torch {verification['torch']} ({verification['accelerator']}: {verification['device']})", flush=True)
        return 0
    except subprocess.CalledProcessError as error:
        print(f"ERROR: setup step failed with exit code {error.returncode}", file=sys.stderr, flush=True)
        if error.stderr:
            print(error.stderr, file=sys.stderr, flush=True)
        return 1
    except (RuntimeError, ValueError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
