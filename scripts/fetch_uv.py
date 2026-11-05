"""Fetch the pinned uv release for the Windows installer and verify its checksum.

uv (Astral, MIT OR Apache-2.0) creates VoiceVault's private Python environment on
the user's first launch. The installer bundles uv.exe and its licence texts only.

Usage: python scripts/fetch_uv.py [--dest frontend/build-resources/uv]
Re-running is cheap: an existing uv.exe whose recorded archive hash matches is kept.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
UV_VERSION = "0.13.5"
ARCHIVE = "uv-x86_64-pc-windows-msvc.zip"
# From the release's .sha256 asset, cross-checked against the GitHub API asset digest (2026-11-05).
ARCHIVE_SHA256 = "a38b8d1902b7cfbc76b423ba1bb456f623f42064cdc72628b6075b89afd1019a"
BASE = f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}"
LICENSES = {
    "LICENSE-MIT.txt": f"https://raw.githubusercontent.com/astral-sh/uv/{UV_VERSION}/LICENSE-MIT",
    "LICENSE-APACHE.txt": f"https://raw.githubusercontent.com/astral-sh/uv/{UV_VERSION}/LICENSE-APACHE",
}


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "VoiceVault-build"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def verify_archive(data: bytes, expected: str = ARCHIVE_SHA256) -> None:
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise RuntimeError(f"uv archive checksum mismatch: expected {expected}, got {actual}")


def extract_uv(data: bytes, dest: Path) -> Path:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = [n for n in archive.namelist() if Path(n).name.lower() == "uv.exe"]
        if len(names) != 1:
            raise RuntimeError("uv.exe was not found exactly once in the release archive")
        target = dest / "uv.exe"
        target.write_bytes(archive.read(names[0]))
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", default=str(ROOT / "frontend" / "build-resources" / "uv"))
    args = parser.parse_args(argv)
    dest = Path(args.dest)
    record = dest / "uv-source.json"
    try:
        current = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = {}
    if (current.get("archive_sha256") == ARCHIVE_SHA256 and (dest / "uv.exe").is_file()
            and all((dest / name).is_file() for name in LICENSES)):
        print(f"uv {UV_VERSION} already present in {dest}")
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    data = _get(f"{BASE}/{ARCHIVE}")
    verify_archive(data)
    exe = extract_uv(data, dest)
    for name, url in LICENSES.items():
        (dest / name).write_bytes(_get(url))
    record.write_text(json.dumps({
        "name": "uv", "version": UV_VERSION, "archive": f"{BASE}/{ARCHIVE}",
        "archive_sha256": ARCHIVE_SHA256, "uv_exe_sha256": hashlib.sha256(exe.read_bytes()).hexdigest(),
        "license": "MIT OR Apache-2.0", "homepage": "https://github.com/astral-sh/uv",
    }, indent=2) + "\n", encoding="utf-8")
    print(f"uv {UV_VERSION} verified and extracted to {dest}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
