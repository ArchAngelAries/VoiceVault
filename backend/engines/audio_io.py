"""
engines/audio_io.py — Writing generated audio and its metadata.

Each generation is saved as a WAV plus a JSON sidecar with the text, settings and timings.
Both files are written to a temporary name and then renamed, so a crash cannot leave
a half-written file behind.
"""
from __future__ import annotations

import json
import os
import re
import uuid
import wave
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.wav$")


def safe_name(text: str, max_len: int = 40) -> str:
    return _SAFE.sub("-", text).strip("-")[:max_len] or "item"


def is_valid_audio_name(name: str) -> bool:
    return bool(_NAME_RE.match(name)) and ".." not in name


def write_wav(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    """16-bit mono PCM. Writes to a .tmp file and renames it into place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0) * 32767).astype("<i2").tobytes()
    tmp = path.with_name(path.name + ".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    os.replace(tmp, path)


def write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def save_generation(
    outputs_dir: Path,
    samples: np.ndarray,
    sample_rate: int,
    record: dict[str, Any],
) -> dict[str, Any]:
    """Write <stamp>_<engine>_<id>.wav and its .json sidecar. Returns the stored record."""
    outputs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    gen_id = uuid.uuid4().hex[:8]
    stem = f"{stamp}_{safe_name(record.get('engine', 'tts'), 16)}_{gen_id}"
    wav_path = outputs_dir / f"{stem}.wav"
    write_wav(wav_path, samples, sample_rate)
    record = {
        **record,
        "id": gen_id,
        "file": wav_path.name,
        "sample_rate": sample_rate,
        "duration_s": round(len(samples) / sample_rate, 3),
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    write_json(outputs_dir / f"{stem}.json", record)
    return record


def list_generations(outputs_dir: Path, limit: int = 50) -> list[dict[str, Any]]:
    if not outputs_dir.is_dir():
        return []
    records = []
    # File names start with a timestamp, so reverse name order is newest first.
    for sidecar in sorted(outputs_dir.glob("*.json"), reverse=True):
        try:
            rec = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (outputs_dir / rec.get("file", "")).is_file():
            records.append(rec)
        if len(records) >= limit:
            break
    return records
