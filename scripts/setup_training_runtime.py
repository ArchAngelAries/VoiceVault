"""Check or explicitly provision VoiceVault's optional OmniVoice training extras.

Default invocation is read-only. See --dry-run for the exact scoped pip commands;
only --install downloads and publishes files under DATA_DIR/training/runtime.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.dont_write_bytecode = True
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from training import runtime


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Read-only prerequisite and optional-runtime check (default)")
    mode.add_argument("--dry-run", action="store_true", help="Show the exact staging-only pip commands without running them")
    mode.add_argument("--install", action="store_true", help="Download, hash-check, and publish only the four audited CPU-side wheels")
    args = parser.parse_args(argv)
    try:
        status = runtime.runtime_readiness()
        if args.install:
            status = runtime.provision()
            print(json.dumps(status, indent=2))
            return 0 if status["ready_for_training_dependencies"] else 1
        if args.dry_run:
            stage = runtime.layout()["training"] / ".runtime-staging-<unique>"
            lock = ROOT / "scripts" / "training_requirements.lock"
            runtime.validate_lock(lock)
            download, install = runtime.pip_commands(sys.executable, lock, stage)
            print(json.dumps({"read_only": True, "core_python": sys.executable,
                              "runtime_root": str(runtime.layout()["runtime"]),
                              "download_command": download, "install_command": install,
                              "checks": status}, indent=2))
            return 0
        print(json.dumps(status, indent=2))
        return 0 if status["ready_for_training_dependencies"] else 1
    except (OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        command = " ".join(str(part) for part in error.cmd)
        print(f"ERROR: pip command failed ({error.returncode}): {command}", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired as error:
        command = " ".join(str(part) for part in error.cmd)
        print(f"ERROR: pip command exceeded the 180-second limit: {command}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
