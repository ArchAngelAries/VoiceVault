"""
engines/audio_store.py — Listing and deleting stored audio: generated speech and reference clips.

VoiceVault only removes files it wrote:
  * a generated speech file is deleted together with its JSON record. The name must pass
    is_valid_audio_name, and the record must name that same WAV. A WAV without a record is
    never listed or removed;
  * a reference clip is a 32-character hex id in the references folder. Its JSON record is
    optional, because clips uploaded before records existed have none.
A clip that a generation is reading right now cannot be deleted. A bulk request is checked
in full before anything is removed, so a refused request removes nothing.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import uuid
import wave
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

import numpy as np

from engines.audio_io import is_valid_audio_name, write_json, write_wav

log = logging.getLogger(__name__)

REFERENCE_ID = re.compile(r"^[0-9a-f]{32}$")
DELETE_ALL_PHRASE = "DELETE ALL"
MAX_BULK = 500

_in_use: dict[str, int] = {}   # reference id -> generations reading it now
_lock = threading.Lock()       # guards _in_use and serialises reference deletion against it


class StoreError(ValueError):
    """The request cannot be carried out as asked: a bad name or a missing confirmation. HTTP 400."""


class InUseError(RuntimeError):
    """A clip is being read by a generation right now. HTTP 409."""


@contextmanager
def reference_in_use(reference_id: Optional[str]) -> Iterator[None]:
    """Hold a reference clip while a generation reads it, so it cannot be deleted mid-run."""
    if reference_id is None:
        yield
        return
    with _lock:
        _in_use[reference_id] = _in_use.get(reference_id, 0) + 1
    try:
        yield
    finally:
        with _lock:
            _in_use[reference_id] -= 1
            if _in_use[reference_id] == 0:
                del _in_use[reference_id]


def _read_record(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _checked_names(names: Optional[list[str]], is_valid: Callable[[str], bool], what: str) -> list[str]:
    if not names:
        raise StoreError("Choose at least one item.")
    if len(names) > MAX_BULK:
        raise StoreError(f"Delete at most {MAX_BULK} items at a time.")
    bad = next((n for n in names if not is_valid(n)), None)
    if bad is not None:
        raise StoreError(f"'{bad}' is not a {what}.")
    return list(dict.fromkeys(names))


def _remove_record(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.warning("Could not remove the record %s", path, exc_info=True)


def _failure_reason(name: str, exc: OSError) -> str:
    if isinstance(exc, PermissionError):
        return f"{name} is open in another program. Close it and try again."
    return f"Could not delete {name}: {exc}"


# ── Generated speech ─────────────────────────────────────────────

def _generation_files(outputs_dir: Path, name: str) -> Optional[tuple[Path, Path]]:
    """The WAV and its JSON record, or None unless VoiceVault wrote that pair."""
    wav = outputs_dir / name
    record = outputs_dir / f"{Path(name).stem}.json"
    if not wav.is_file() or wav.is_symlink() or record.is_symlink():
        return None
    if _read_record(record).get("file") != name:
        return None
    return wav, record


def owned_generation_names(outputs_dir: Path) -> list[str]:
    """Every generation in the folder that has a WAV and a record naming it."""
    if not outputs_dir.is_dir():
        return []
    names = []
    for record in outputs_dir.glob("*.json"):
        wav_name = f"{record.stem}.wav"
        if is_valid_audio_name(wav_name) and _generation_files(outputs_dir, wav_name) is not None:
            names.append(wav_name)
    return names


def delete_generations(outputs_dir: Path, names: Optional[list[str]] = None, *,
                       delete_all: bool = False, confirm: Optional[str] = None) -> dict[str, Any]:
    """Delete generations by file name, or all of them with the typed confirmation phrase."""
    if delete_all:
        if names:
            raise StoreError("Choose either the selected files or everything, not both.")
        if confirm != DELETE_ALL_PHRASE:
            raise StoreError(f"Type {DELETE_ALL_PHRASE} to confirm deleting every generated file.")
        names = owned_generation_names(outputs_dir)
    else:
        names = _checked_names(names, is_valid_audio_name, "generated file name")

    deleted, missing, failed = [], [], []
    for name in names:
        files = _generation_files(outputs_dir, name)
        if files is None:
            missing.append(name)
            continue
        wav, record = files
        try:
            wav.unlink()
        except OSError as exc:
            failed.append({"name": name, "reason": _failure_reason(name, exc)})
            continue
        deleted.append(name)
        _remove_record(record)  # without its WAV the record is hidden from history anyway
    return {"deleted": deleted, "missing": missing, "failed": failed}


# ── Reference clips ──────────────────────────────────────────────

def _wav_info(path: Path) -> Optional[tuple[float, int]]:
    try:
        with wave.open(str(path), "rb") as w:
            rate, frames = w.getframerate(), w.getnframes()
    except (wave.Error, EOFError, OSError):
        return None
    return (round(frames / rate, 2), rate) if rate > 0 else None


HQ_SUFFIX = ".hq44.wav"  # full-band copy for 44.1 kHz engines (Drama 3); the 24 kHz WAV stays the clip of record


def hq_reference_path(reference_wav: Path) -> Path:
    """The full-band sibling of a stored clip (it may not exist for older clips)."""
    return reference_wav.with_name(reference_wav.stem + HQ_SUFFIX)


def save_reference(refs_dir: Path, samples: np.ndarray, sample_rate: int, original_name: str,
                   hq_samples: Optional[np.ndarray] = None, hq_rate: Optional[int] = None) -> dict[str, Any]:
    """Store a cloning clip as a WAV with its JSON record, plus an optional full-band copy. Returns the record."""
    reference_id = uuid.uuid4().hex
    write_wav(refs_dir / f"{reference_id}.wav", samples, sample_rate)
    if hq_samples is not None and hq_rate:
        write_wav(refs_dir / f"{reference_id}{HQ_SUFFIX}", hq_samples, int(hq_rate))
    record = {
        "reference_id": reference_id,
        "name": original_name[:120],
        "duration_s": round(len(samples) / sample_rate, 2),
        "sample_rate": sample_rate,
        "hq_sample_rate": int(hq_rate) if hq_samples is not None and hq_rate else None,
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    write_json(refs_dir / f"{reference_id}.json", record)
    return record


def list_references(refs_dir: Path) -> list[dict[str, Any]]:
    """Stored clips, newest first. Clips without a readable WAV are skipped."""
    if not refs_dir.is_dir():
        return []
    items = []
    for wav in refs_dir.glob("*.wav"):
        info = _wav_info(wav) if REFERENCE_ID.match(wav.stem) and wav.is_file() else None
        if info is None:
            continue
        meta = _read_record(refs_dir / f"{wav.stem}.json")
        items.append({
            "reference_id": wav.stem,
            "name": meta.get("name") if isinstance(meta.get("name"), str) else None,
            "transcript": meta.get("transcript") if isinstance(meta.get("transcript"), str) else None,
            "duration_s": info[0],
            "sample_rate": info[1],
            "created": meta.get("created") if isinstance(meta.get("created"), str)
            else datetime.fromtimestamp(wav.stat().st_mtime).isoformat(timespec="seconds"),
        })
    return sorted(items, key=lambda r: r["created"], reverse=True)


def reference_path(refs_dir: Path, reference_id: str) -> Optional[Path]:
    """The WAV for a clip id, or None when it is not stored. Malformed ids raise StoreError."""
    if not REFERENCE_ID.match(reference_id):
        raise StoreError("Invalid reference id.")
    wav = refs_dir / f"{reference_id}.wav"
    return wav if wav.is_file() and not wav.is_symlink() else None


def delete_references(refs_dir: Path, ids: Optional[list[str]] = None, *,
                      delete_all: bool = False, confirm: Optional[str] = None) -> dict[str, Any]:
    """Delete clips by id, or all of them with the typed confirmation phrase.

    Nothing is removed if any requested clip is in use, so a refused request is all-or-nothing.
    """
    if delete_all:
        if ids:
            raise StoreError("Choose either the selected clips or everything, not both.")
        if confirm != DELETE_ALL_PHRASE:
            raise StoreError(f"Type {DELETE_ALL_PHRASE} to confirm deleting every reference clip.")
        ids = [p.stem for p in refs_dir.glob("*.wav") if REFERENCE_ID.match(p.stem)] if refs_dir.is_dir() else []
    else:
        ids = _checked_names(ids, lambda i: bool(REFERENCE_ID.match(i)), "reference id")

    deleted, missing, failed = [], [], []
    with _lock:
        targets = []
        for rid in ids:
            wav = reference_path(refs_dir, rid)
            if wav is None:
                missing.append(rid)
            else:
                targets.append((rid, wav))
        if any(_in_use.get(rid, 0) > 0 for rid, _ in targets):
            raise InUseError("A generation is reading one of these clips right now. Nothing was deleted; "
                             "try again when it finishes.")
        for rid, wav in targets:
            try:
                wav.unlink()
            except OSError as exc:
                failed.append({"name": rid, "reason": _failure_reason(rid, exc)})
                continue
            deleted.append(rid)
            _remove_record(refs_dir / f"{rid}.json")
            try:
                hq_reference_path(wav).unlink(missing_ok=True)
            except OSError:
                pass  # the 24 kHz clip is gone; a leftover full-band copy is never listed or used
    return {"deleted": deleted, "missing": missing, "failed": failed}


def remember_transcript(audio_path, text: str) -> bool:
    """Store a transcript made for a saved voice in its JSON, if it has none yet. Returns True when written."""
    import json as _json
    from pathlib import Path as _Path

    text = (text or "").strip()
    if not text or audio_path is None:
        return False
    path = _Path(audio_path)
    meta_path = path.with_name(path.name.split(".", 1)[0] + ".json")
    if not REFERENCE_ID.match(meta_path.stem) or not meta_path.is_file():
        return False
    try:
        meta = _json.loads(meta_path.read_text(encoding="utf-8"))
        if not isinstance(meta, dict) or (isinstance(meta.get("transcript"), str) and meta["transcript"].strip()):
            return False
        meta["transcript"] = text[:2000]
        tmp = meta_path.with_name(meta_path.name + ".tmp")
        tmp.write_text(_json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(meta_path)
        return True
    except (OSError, ValueError):
        return False
