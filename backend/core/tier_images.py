"""
core/tier_images.py — Images imported for tier lists (the warehouse).

Each image is kept as the file the user gave us, plus a JSON record, in data/tier_images/.
Its ID is "image:local:<32 hex>". It is not a collection item: collections only take IDs from
a source (anilist, tmdb, igdb). Only PNG, JPEG, WebP and GIF are accepted, judged by the first
bytes of the file rather than its name.
"""
from __future__ import annotations

import io
import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from core.paths import DATA_DIR

IMAGE_DIR = DATA_DIR / "tier_images"   # read at call time, so tests can point it elsewhere
IMAGE_ID = re.compile(r"^image:local:([0-9a-f]{32})$")
HEX_ID = re.compile(r"^[0-9a-f]{32}$")
MAX_BYTES = 10 * 1024 * 1024
MAX_LABEL = 80
MAX_BULK = 500
DELETE_ALL_PHRASE = "DELETE ALL"

_SIGNATURES = [(b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpg"), (b"GIF87a", "gif"), (b"GIF89a", "gif")]


class ImageError(ValueError):
    """The upload is not usable (not an image, too large, empty). Reported per file."""


def image_id(hex_id: str) -> str:
    return f"image:local:{hex_id}"


def is_image_id(value) -> bool:
    return isinstance(value, str) and IMAGE_ID.match(value) is not None


def hex_of(value: str) -> str:
    match = IMAGE_ID.match(value)
    if not match:
        raise ValueError(f"Not an image id: {value!r}")
    return match.group(1)


def _kind(data: bytes) -> Optional[str]:
    for signature, kind in _SIGNATURES:
        if data.startswith(signature):
            return kind
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _dimensions(data: bytes) -> tuple[Optional[int], Optional[int]]:
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as im:   # reads the header only
            return im.width, im.height
    except (OSError, ValueError):
        return None, None


def _read_record(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _public(record: dict[str, Any]) -> dict[str, Any]:
    hex_id = record["id"].split(":")[-1]
    return {
        "id": record["id"],
        "label": record.get("label") or "",
        "width": record.get("width"),
        "height": record.get("height"),
        "created": record.get("created"),
        "url": f"/api/tiers/images/{hex_id}/file",
    }


def save_image(data: bytes, original_name: str) -> dict[str, Any]:
    if not data:
        raise ImageError("The file is empty.")
    if len(data) > MAX_BYTES:
        raise ImageError("The image is larger than 10 MB.")
    kind = _kind(data)
    if kind is None:
        raise ImageError("Only PNG, JPEG, WebP and GIF images can be imported.")
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    hex_id = uuid.uuid4().hex
    file_name = f"{hex_id}.{kind}"
    tmp = IMAGE_DIR / (file_name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, IMAGE_DIR / file_name)
    width, height = _dimensions(data)
    record = {
        "id": image_id(hex_id), "label": "", "file": file_name, "width": width, "height": height,
        "original_name": original_name[:120], "created": datetime.now().isoformat(timespec="seconds"),
    }
    _write_json(IMAGE_DIR / f"{hex_id}.json", record)
    return _public(record)


def _record_for(hex_id: str) -> Optional[dict[str, Any]]:
    """The record for an image whose file is still there, else None."""
    if not HEX_ID.match(hex_id):
        return None
    record = _read_record(IMAGE_DIR / f"{hex_id}.json")
    file_name = record.get("file")
    if not isinstance(file_name, str) or not file_name.startswith(hex_id + "."):
        return None
    path = IMAGE_DIR / file_name
    if not path.is_file() or path.is_symlink():
        return None
    return {**record, "id": image_id(hex_id)}


def list_images() -> list[dict[str, Any]]:
    if not IMAGE_DIR.is_dir():
        return []
    found = [_record_for(p.stem) for p in IMAGE_DIR.glob("*.json")]
    records = [_public(r) for r in found if r is not None]
    return sorted(records, key=lambda r: r["created"] or "", reverse=True)


def get_image(hex_id: str) -> Optional[dict[str, Any]]:
    record = _record_for(hex_id)
    return _public(record) if record else None


def image_path(hex_id: str) -> Optional[Path]:
    record = _record_for(hex_id)
    return IMAGE_DIR / record["file"] if record else None


def set_label(hex_id: str, label: str) -> Optional[dict[str, Any]]:
    record = _record_for(hex_id)
    if record is None:
        return None
    record["label"] = label.strip()[:MAX_LABEL]
    stored = {k: v for k, v in record.items() if k != "id"}
    _write_json(IMAGE_DIR / f"{hex_id}.json", stored)
    return _public({**stored, "id": image_id(hex_id)})


def _remove(hex_id: str) -> Optional[str]:
    """Delete one image's file and record. Returns an error message, or None when it is gone."""
    record = _record_for(hex_id)
    if record is None:
        return None
    try:
        (IMAGE_DIR / record["file"]).unlink()
    except OSError as exc:
        return f"Could not delete the image: {exc}"
    (IMAGE_DIR / f"{hex_id}.json").unlink(missing_ok=True)
    return None


def delete_images(ids: Optional[list[str]] = None, *, delete_all: bool = False,
                  confirm: Optional[str] = None) -> dict[str, Any]:
    """Delete images by id, or every image with the typed phrase. Returns deleted ids, missing ids, failures."""
    if delete_all:
        if ids:
            raise ValueError("Choose either the selected images or everything, not both.")
        if confirm != DELETE_ALL_PHRASE:
            raise ValueError(f"Type {DELETE_ALL_PHRASE} to confirm deleting every imported image.")
        targets = [r["id"] for r in list_images()]
    else:
        if not ids:
            raise ValueError("Choose at least one image.")
        if len(ids) > MAX_BULK:
            raise ValueError(f"Delete at most {MAX_BULK} images at a time.")
        bad = next((i for i in ids if not is_image_id(i)), None)
        if bad is not None:
            raise ValueError(f"'{bad}' is not an image id.")
        targets = list(dict.fromkeys(ids))

    deleted, missing, failed = [], [], []
    for image in targets:
        hex_id = hex_of(image)
        if _record_for(hex_id) is None:
            missing.append(image)
            continue
        error = _remove(hex_id)
        if error:
            failed.append({"id": image, "reason": error})
        else:
            deleted.append(image)
    return {"deleted": deleted, "missing": missing, "failed": failed}
