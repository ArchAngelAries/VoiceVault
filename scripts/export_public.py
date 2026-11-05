"""Copy the publishable part of a VoiceVault working folder into a clean folder for git.

    python scripts/export_public.py <destination folder>

Only files that .gitignore allows are copied, so keys, libraries, chats, models, builds and
internal notes stay behind. Files in the destination that are no longer publishable are removed
(its .git folder is left alone). Afterwards every copied file is searched for personal strings:
your Windows user name, e-mail addresses, C:\\Users\\<name> paths, API-key shapes, and the lines in
scripts/audit-strings.local.txt (your own folder names; that file is never published).
Needs git on PATH. Nothing is committed or pushed.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def publishable(root: Path = ROOT) -> list[str]:
    """Paths (relative, forward slashes) that .gitignore allows, using a throwaway git folder."""
    with tempfile.TemporaryDirectory() as tmp:
        gitdir = Path(tmp) / "list.git"
        subprocess.run(["git", "init", "-q", "--bare", str(gitdir)], check=True)
        out = subprocess.run(["git", f"--git-dir={gitdir}", f"--work-tree={root}", "-c", "core.quotepath=off",
                              "ls-files", "--others", "--exclude-standard", "-z"],
                             check=True, capture_output=True).stdout
    return sorted(p for p in out.decode("utf-8").split("\0") if p)


def personal_patterns(root: Path = ROOT) -> list[tuple[str, re.Pattern]]:
    pats = [("Windows user folder", re.compile(r"[A-Z]:[\\/]+Users[\\/]+(?!runner|Public|Default|you\b|x\b|Someone\b)[A-Za-z0-9._-]+", re.I)),
            ("e-mail address", re.compile(r"[A-Za-z0-9._%+-]+@(gmail|outlook|hotmail|yahoo|icloud|proton)\.[a-z]+", re.I)),
            ("Hugging Face token", re.compile(r"hf_[A-Za-z0-9]{30,}")),
            ("API key", re.compile(r"sk-[A-Za-z0-9_-]{30,}"))]
    user = os.environ.get("USERNAME", "").strip()
    if len(user) >= 3:
        pats.append(("Windows user name", re.compile(rf"(?<![A-Za-z0-9]){re.escape(user)}(?![A-Za-z0-9])", re.I)))
    local = root / "scripts" / "audit-strings.local.txt"
    if local.is_file():
        for i, line in enumerate(l.strip() for l in local.read_text(encoding="utf-8").splitlines()):
            if len(line) >= 4:
                pats.append((f"private string #{i + 1}", re.compile(re.escape(line), re.I)))
    return pats


def scan(dest: Path, files: list[str], pats) -> list[tuple[str, str]]:
    hits = []
    for rel in files:
        text = (dest / rel).read_bytes().decode("latin-1")
        hits += [(rel, label) for label, rx in pats if rx.search(text)]
    return hits


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    dest = Path(argv[0]).resolve()
    if dest == ROOT or ROOT in dest.parents:
        print("The destination must be outside the VoiceVault folder.")
        return 2
    files = publishable()
    dest.mkdir(parents=True, exist_ok=True)
    keep = set(files)
    removed = 0
    for path in sorted(dest.rglob("*"), reverse=True):
        rel = path.relative_to(dest).as_posix()
        if rel == ".git" or rel.startswith(".git/"):
            continue
        if path.is_file() and rel not in keep:
            path.unlink(); removed += 1
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    for rel in files:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, target)
    size = sum((dest / f).stat().st_size for f in files)
    print(f"Copied {len(files)} files ({size / 1e6:.1f} MB) to {dest}; removed {removed} stale files.")
    hits = scan(dest, files, personal_patterns())
    for rel, label in hits:
        print(f"  PERSONAL DATA? {rel}: {label}")
    print("Personal-data scan: " + ("no hits." if not hits else f"{len(hits)} hit(s). Fix them before committing."))
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
