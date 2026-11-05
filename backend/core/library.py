"""
core/library.py — One clear home for every piece of audio VoiceVault keeps.

    <VoiceVault>/Library/
        Voices/          cloning voices (24 kHz clip, full-band .hq44 copy, JSON with name/transcript)
        Generations/     speech from Generate, Converse/Scenes and saved Voice Lab results
        Harvest/         fetched and dropped sources with their clips (one folder per source)
        Voice Lab/
            Uploads/     audio dropped into the Voice Lab
        Music/
            Projects/    songs and sound effects (takes, stems)
        Exports/         anything exported (music mixes etc.)
        library.json     titles, characters/actors, labels, categories and notes (the in-app Library)

Working files (previews, conversions in progress, caches) stay in data/tmp, not in the Library.

The location follows the data directory: the normal install uses <VoiceVault>/Library; isolated runs
(VOICEVAULT_DATA_DIR / VOICEVAULT_STATE_DIR, tests) get <data dir>/Library so they never touch the
real Library. VOICEVAULT_LIBRARY_DIR overrides both. Paths are computed when asked, so tests that
patch core.paths.DATA_DIR are isolated too.
"""
from __future__ import annotations

import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from core import paths

FOLDERS = {
    "voices": ("Voices",),
    "generations": ("Generations",),
    "harvest": ("Harvest",),
    "voice_lab_uploads": ("Voice Lab", "Uploads"),
    "music_projects": ("Music", "Projects"),
    "exports": ("Exports",),
}


def library_dir() -> Path:
    override = os.environ.get("VOICEVAULT_LIBRARY_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    data = Path(paths.DATA_DIR)
    if data.resolve() == (paths.VOICEVAULT_ROOT / "data").resolve():
        return paths.VOICEVAULT_ROOT / "Library"
    return data / "Library"


def folder(key: str) -> Path:
    return library_dir().joinpath(*FOLDERS[key])


def voices_dir() -> Path:
    return folder("voices")


def generations_dir() -> Path:
    return folder("generations")


def harvest_dir() -> Path:
    return folder("harvest")


def voice_lab_uploads_dir() -> Path:
    return folder("voice_lab_uploads")


def music_projects_dir() -> Path:
    return folder("music_projects")


def exports_dir() -> Path:
    return folder("exports")


def index_file() -> Path:
    return library_dir() / "library.json"


def ensure_folders() -> None:
    for key in FOLDERS:
        folder(key).mkdir(parents=True, exist_ok=True)


# ── One-time move of the older scattered layout ──────────────────────────────

LEGACY = {  # library folder key -> old location under the data directory
    "voices": ("references",),
    "generations": ("outputs",),
    "harvest": ("harvest",),
    "voice_lab_uploads": ("voice", "sources"),
    "music_projects": ("music", "projects"),
    "exports": ("music", "exports"),
}


def legacy_files() -> dict[str, list[Path]]:
    data = Path(paths.DATA_DIR)
    found = {}
    for key, parts in LEGACY.items():
        old = data.joinpath(*parts)
        if old.is_dir():
            files = [p for p in old.rglob("*") if p.is_file()]
            if files:
                found[key] = files
    return found


def migrate_legacy(dry_run: bool = False) -> dict:
    """Back up the old folders into one ZIP, then move their contents into the Library.

    Nothing is overwritten: a file that already exists in the Library is left where it was and reported.
    """
    data = Path(paths.DATA_DIR)
    plan = legacy_files()
    report = {"moved": 0, "skipped": [], "backup": None, "folders": {k: len(v) for k, v in plan.items()}}
    if dry_run or not plan:
        return report
    backups = data / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    backup = backups / f"library-migration-{datetime.now():%Y%m%d-%H%M%S}.zip"
    with zipfile.ZipFile(backup, "w", zipfile.ZIP_DEFLATED) as zf:
        for files in plan.values():
            for f in files:
                zf.write(f, f.relative_to(data).as_posix())
    report["backup"] = str(backup)
    for key, files in plan.items():
        old_root = data.joinpath(*LEGACY[key])
        new_root = folder(key)
        for f in files:
            dest = new_root / f.relative_to(old_root)
            if dest.exists():
                report["skipped"].append(str(f))
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(dest))
            report["moved"] += 1
        # Remove now-empty old folders (only empty ones).
        for d in sorted((p for p in old_root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            try:
                d.rmdir()
            except OSError:
                pass
        try:
            old_root.rmdir()
        except OSError:
            pass
    ensure_folders()
    return report
