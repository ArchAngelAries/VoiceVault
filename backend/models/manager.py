"""
models/manager.py — Status, licence acceptance, downloads and removal for the model catalog.

- Status: an entry is installed when its files are found in any model root VoiceVault scans (including
  Stability Matrix / ComfyUI folders and the Hugging Face cache), so existing installs are recognised.
- Downloads run in a separate Python process (models/fetch.py) so they can be cancelled at once and
  resumed later; Hugging Face's own resume and checksum handling is used, plus a size check.
- Removal only touches files VoiceVault downloaded itself (recorded in <data>/models/installed.json),
  never other apps' folders.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Optional

from core import paths as app_paths
from models.catalog import BY_ID, CATALOG, Entry

SCAN_EXT = (".gguf", ".safetensors", ".bin", ".exe", ".tiktoken")
SKIP = {".git", "__pycache__", "node_modules", "venv", ".cache", "blobs"}
SCAN_DEPTH = 5


def models_dir() -> Path:
    return Path(os.environ.get("VOICEVAULT_MODELS_DIR") or (app_paths.VOICEVAULT_ROOT / "Models"))


def _state_dir() -> Path:
    d = app_paths.DATA_DIR / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read(name: str) -> dict:
    try:
        return json.loads((_state_dir() / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(name: str, data: dict) -> None:
    p = _state_dir() / name
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(p)


# ── Detection ───────────────────────────────────────────────────────────────

_index: dict[str, Any] = {"at": 0.0, "files": {}}
_index_lock = threading.Lock()


def _roots() -> list[Path]:
    from core import settings as app_settings
    from core.model_paths import model_roots

    roots = [models_dir()]
    try:
        roots += model_roots(app_settings.load())
    except Exception:
        pass
    roots.append(app_paths.VOICEVAULT_ROOT / "runtime")
    roots.append(app_paths.runtime_dir())
    out, seen = [], set()
    for r in roots:
        key = str(r).lower()
        if key not in seen and r.is_dir():
            seen.add(key)
            out.append(r)
    return out


def file_index(max_age: float = 300.0) -> dict[str, str]:
    """name (lower case) -> full path for model-like files, and "dir:<name>" for folders, under every root."""
    with _index_lock:
        if time.time() - _index["at"] < max_age:
            return _index["files"]
        files: dict[str, str] = {}
        for root in _roots():
            base = len(root.parts)
            for dirpath, dirnames, filenames in os.walk(root):
                here = Path(dirpath)
                dirnames[:] = [d for d in dirnames if d not in SKIP and len(here.parts) - base < SCAN_DEPTH]
                for d in dirnames:
                    files.setdefault("dir:" + d.lower(), str(here / d))
                for name in filenames:
                    if name.lower().endswith(SCAN_EXT):
                        files.setdefault(name.lower(), str(here / name))
        _index.update(at=time.time(), files=files)
        return files


def invalidate() -> None:
    with _index_lock:
        _index["at"] = 0.0


def _in_hf_cache(repo: str, filename: str) -> bool:
    try:
        from huggingface_hub import try_to_load_from_cache
        return isinstance(try_to_load_from_cache(repo, filename), str)
    except Exception:
        return False


def _espeak_found() -> bool:
    try:
        from engines.kokoro import find_espeak
        return find_espeak() is not None
    except Exception:
        return False


def installed(entry: Entry) -> tuple[bool, Optional[str]]:
    if entry.id == "espeak_ng":
        return _espeak_found(), None
    idx = file_index()
    for name in entry.detect:
        if name.lower() in idx:
            return True, idx[name.lower()]
    for repo, fname in entry.detect_cache:
        if _in_hf_cache(repo, fname):
            return True, f"Hugging Face cache: {repo}"
    return False, None


# ── Sizes (from the Hub, cached) ────────────────────────────────────────────

def _token() -> Optional[str]:
    from core import settings as app_settings
    tok = (app_settings.load().get("huggingface_token") or "").strip()
    return tok or None


def source_files(src) -> list[dict]:
    """[{path, size}] a source will download (network)."""
    from huggingface_hub import HfApi

    api = HfApi(token=_token())
    if src.kind == "hf_files":
        info = api.get_paths_info(src.repo, src.files, revision=src.revision)
        return [{"path": i.path, "size": int(getattr(i, "size", 0) or 0)} for i in info if hasattr(i, "size")]
    if src.kind in ("hf_cache", "hf_local"):
        out = []
        for item in api.list_repo_tree(src.repo, recursive=True, revision=src.revision):
            if not hasattr(item, "size") or getattr(item, "size", None) is None:
                continue
            if src.allow and not any(fnmatch(item.path, pat) or fnmatch(item.path.split("/")[-1], pat) for pat in src.allow):
                continue
            out.append({"path": item.path, "size": int(item.size)})
        return out
    if src.kind == "espeak_msi":
        return [{"path": "espeak-ng.msi", "size": 12_765_862}]
    if src.kind == "drama_convert":
        from models.drama_convert import CHECKPOINT_BYTES
        return [{"path": "checkpoint", "size": CHECKPOINT_BYTES}]
    return []


def sizes(entry_id: str, refresh: bool = False) -> Optional[int]:
    cache = _read("sizes.json")
    if entry_id in cache and not refresh:
        return cache[entry_id]
    entry = BY_ID[entry_id]
    total = 0
    for src in entry.sources:
        if src.kind == "manual":
            return None
        total += sum(f["size"] for f in source_files(src))
    cache[entry_id] = total
    _write("sizes.json", cache)
    return total


# ── Catalog view ────────────────────────────────────────────────────────────

def vram_total_gb() -> Optional[float]:
    try:
        import torch
        if torch.cuda.is_available():
            return torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
    except Exception:
        pass
    return None


def fit(entry: Entry, vram: Optional[float]) -> str:
    if not entry.vram_gb or entry.task == "Runtime":
        return "ok"
    if vram is None:
        return "cpu"
    if entry.vram_gb <= vram * 0.8:
        return "ok"
    if entry.vram_gb <= vram:
        return "tight"
    return "offload"


def catalog() -> dict:
    accepted = _read("accepted.json")
    owned = _read("installed.json")
    cached_sizes = _read("sizes.json")
    vram = vram_total_gb()
    items = []
    for e in CATALOG:
        ok, where = installed(e)
        items.append({**e.to_dict(), "installed": ok, "location": where, "accepted": e.id in accepted,
                      "size_bytes": cached_sizes.get(e.id), "fit": fit(e, vram),
                      "downloadable": all(s.kind != "manual" for s in e.sources) and (e.id != "espeak_ng" or os.name == "nt"),
                      "owned": e.id in owned, "job": _active_job(e.id)})
    return {"items": items, "models_dir": str(models_dir()), "vram_gb": round(vram, 1) if vram else None}


# ── Jobs ────────────────────────────────────────────────────────────────────

_jobs: dict[str, dict] = {}
_procs: dict[str, subprocess.Popen] = {}
_jobs_lock = threading.Lock()


def _active_job(entry_id: str) -> Optional[dict]:
    for j in _jobs.values():
        if j["entry"] == entry_id and j["state"] in ("queued", "running"):
            return {k: j[k] for k in ("id", "state", "done_bytes", "total_bytes", "message")}
    return None


def jobs() -> list[dict]:
    return sorted(_jobs.values(), key=lambda j: j["created"], reverse=True)[:50]


def accept(entry_id: str) -> None:
    acc = _read("accepted.json")
    acc[entry_id] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _write("accepted.json", acc)


class ModelError(ValueError):
    pass


def start(entry_id: str, accept_license: bool, options: Optional[dict] = None) -> dict:
    entry = BY_ID.get(entry_id)
    if entry is None:
        raise ModelError("Unknown model.")
    if any(s.kind == "manual" for s in entry.sources):
        raise ModelError("This model can't be downloaded automatically; follow the steps shown.")
    if entry_id not in _read("accepted.json"):
        if not accept_license:
            raise ModelError(f"Please accept the {entry.license} licence first.")
        accept(entry_id)
    if _active_job(entry_id):
        raise ModelError("This model is already downloading.")
    options = {k: bool(v) for k, v in (options or {}).items() if k in ("also_max", "keep_checkpoint")}
    total = sizes(entry_id)
    need = total * 1.1 if total else 0
    if any(s.kind == "drama_convert" for s in entry.sources):
        from models.drama_convert import disk_needed
        need = disk_needed(options.get("also_max", False))
    models_dir().mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(models_dir()).free
    if need and free < need:
        raise ModelError(f"Not enough disk space: needs about {need / 1e9:.0f} GB free, {free / 1e9:.0f} GB available.")
    job = {"id": uuid.uuid4().hex[:12], "entry": entry_id, "state": "queued", "created": time.time(),
           "done_bytes": 0, "total_bytes": total or 0, "message": "Starting…", "error": None, "options": options}
    with _jobs_lock:
        _jobs[job["id"]] = job
    threading.Thread(target=_run, args=(job, entry), daemon=True, name=f"model-{entry_id}").start()
    return job


def cancel(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if job is None:
        raise ModelError("Download not found.")
    job["state"] = "canceled"
    proc = _procs.get(job_id)
    if proc and proc.poll() is None:
        if os.name == "nt":   # also stop child processes (the Drama 3 converter, audiocpp_gguf)
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            proc.terminate()
    return job


def _targets(entry: Entry) -> list[Path]:
    """Folders whose growing size shows download progress."""
    from huggingface_hub.constants import HF_HUB_CACHE

    out = []
    for s in entry.sources:
        if s.kind == "hf_cache":
            out.append(Path(HF_HUB_CACHE) / ("models--" + s.repo.replace("/", "--")))
        elif s.kind == "espeak_msi":
            out.append(app_paths.runtime_dir() / "espeak-ng")
        elif s.kind == "drama_convert":
            out.append(models_dir() / ".drama3-work" / "drama-3-final")
        else:
            out.append(models_dir() / (s.dest or ""))
    return out


def _size(paths: list[Path]) -> int:
    total = 0
    for p in paths:
        if p.is_dir():
            for dirpath, _, filenames in os.walk(p):
                for n in filenames:
                    try:
                        total += os.path.getsize(os.path.join(dirpath, n))
                    except OSError:
                        pass
    return total


def _run(job: dict, entry: Entry) -> None:
    targets = _targets(entry)
    before = _size(targets)
    spec = {"entry": entry.id, "models_dir": str(models_dir()), "token": _token(),
            "runtime_dir": str(app_paths.runtime_dir()),
            "sources": [s.__dict__ for s in entry.sources], "options": job.get("options") or {}}
    job["state"] = "running"
    job["message"] = "Downloading from the official source…"
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "HF_HUB_DISABLE_PROGRESS_BARS": "1"}
    try:
        proc = subprocess.Popen([sys.executable, "-u", "-m", "models.fetch"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
                                cwd=str(Path(__file__).resolve().parents[1]),
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as exc:
        job.update(state="failed", error=str(exc), message="Could not start the downloader.")
        return
    _procs[job["id"]] = proc
    proc.stdin.write(json.dumps(spec))
    proc.stdin.close()
    lines: list[str] = []

    def reader():
        for line in proc.stdout:
            lines.append(line.rstrip())
            if line.startswith("STAGE "):
                job["message"] = line[6:].strip()
    t = threading.Thread(target=reader, daemon=True)
    t.start()
    while proc.poll() is None:
        job["done_bytes"] = min(job["total_bytes"] or 10 ** 15, max(0, _size(targets) - before))
        time.sleep(1.0)
    t.join(timeout=5)
    _procs.pop(job["id"], None)
    invalidate()
    if job["state"] == "canceled":
        job["message"] = "Canceled. Starting again resumes where it stopped."
        return
    result = next((json.loads(l[7:]) for l in reversed(lines) if l.startswith("RESULT ")), None)
    if proc.returncode != 0 or not result or not result.get("ok"):
        err = (result or {}).get("error") or next((l for l in reversed(lines) if l.strip()), "Download failed.")
        job.update(state="failed", error=err[:500], message="Download failed. Starting again resumes it.")
        return
    owned = _read("installed.json")
    owned[entry.id] = {"files": result.get("files", []), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    _write("installed.json", owned)
    job.update(state="done", done_bytes=job["total_bytes"] or job["done_bytes"], message="Installed. Rescan to use it.")
    _rescan()


def _rescan() -> None:
    """Make new models visible to speech engines and the Music Studio."""
    try:
        from api import tts as tts_api
        if "registry" in tts_api._services:
            tts_api._services["registry"].discover()
    except Exception:
        pass
    try:
        from api import music as music_api
        if "s" in music_api._studio:
            music_api._studio["s"].runtime.refresh()
    except Exception:
        pass


def remove(entry_id: str) -> dict:
    """Delete only the files VoiceVault itself downloaded for this entry."""
    owned = _read("installed.json")
    rec = owned.get(entry_id)
    if not rec:
        raise ModelError("VoiceVault didn't download this model, so it won't delete it. Remove it where you installed it.")
    base_models = models_dir().resolve()
    removed = 0
    for f in rec.get("files", []):
        p = Path(f).resolve()
        if p.is_file() and (base_models in p.parents or (app_paths.runtime_dir() / "espeak-ng").resolve() in p.parents):
            p.unlink()
            removed += 1
    owned.pop(entry_id, None)
    _write("installed.json", owned)
    invalidate()
    _rescan()
    return {"removed_files": removed}
