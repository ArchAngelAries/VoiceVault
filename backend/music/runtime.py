"""
music/runtime.py — Finds the Music Studio's model packages and runs them in audio.cpp.

One audio.cpp server (separate from the TTS one) is configured with every package found
in the model roots and loads at most one model at a time. GPU work is serialised with the
speech lock: before a music job runs, resident TTS models and Whisper are released, and
the TTS manager stops this server before it loads a speech model.

Packages (any location under a model root; names as published by audio-cpp on Hugging Face):
  yue2      Yue2-3B-GGUF/ folder with yue2-3b-*.gguf + yue2-vae-*.gguf + sidecars/  (CC BY-NC 4.0)
  ace_step  ace-step-1.5-*.gguf, self-contained (Apache-2.0)
  stems     htdemucs-6stems-*.gguf (drums, bass, vocals, other, guitar, piano)
  vocals    mel-band-roformer-*.gguf (vocals / instrumental)
  asr       qwen3-asr-*.gguf (lyrics transcription with word times)
  align     qwen3-forced-aligner-*.gguf (exact lyric word timing)
  score     sheetsage2-*.gguf (audio → ABC melody score; CC BY-NC 4.0)
  (Harvest cleaning, speaker analysis and Voice Lab packages are listed in _PATTERNS.)

UniverSR (role bwe) lacks a Vulkan op, so on a GPU backend it runs on a second, CPU-only audio.cpp
server; stop() stops both.
"""
from __future__ import annotations

import base64
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx

from engines.audiocpp_server import AudioCppServer, find_server_executable

log = logging.getLogger(__name__)

SKIP = {".cache", ".git", "__pycache__", "blobs", "refs"}


@dataclass
class Package:
    role: str
    family: str
    task: str
    path: Path            # file, or folder for yue2
    session_options: dict
    licence: str

    @property
    def model_id(self) -> str:
        return f"vv-{self.role}"


_PATTERNS = [
    # role, family, task, filename test, licence
    ("ace_step", "ace_step", "gen", lambda n: n.startswith("ace-step-1.5") and n.endswith(".gguf"), "Apache-2.0"),
    ("stems", "htdemucs_6stems", "sep", lambda n: n.startswith("htdemucs-6stems") and n.endswith(".gguf"), "MIT"),
    ("vocals", "mel_band_roformer", "sep", lambda n: n.startswith("mel-band-roformer") and n.endswith(".gguf"), "MIT"),
    ("asr", "qwen3_asr", "asr", lambda n: n.startswith("qwen3-asr") and n.endswith(".gguf"), "Apache-2.0"),
    ("align", "qwen3_forced_aligner", "align", lambda n: n.startswith("qwen3-forced-aligner") and n.endswith(".gguf"), "Apache-2.0"),
    ("score", "sheetsage2", "midi", lambda n: n.startswith("sheetsage2") and n.endswith(".gguf"), "CC-BY-NC-4.0"),
    ("sfx", "stable_audio", "gen", lambda n: n.startswith("stable-audio-3") and n.endswith(".gguf"),
     "Stability AI Community License (+ Gemma terms)"),
    # Voice-clip cleaning (Harvest)
    ("separate", "sam_audio", "s2s", lambda n: n.startswith("sam-audio") and n.endswith(".gguf"), "SAM License (Meta)"),
    ("restore", "sidon", "s2s", lambda n: n.startswith("sidon") and n.endswith(".gguf"), "MIT"),
    ("bwe", "universr", "s2s", lambda n: n.startswith("universr-speech") and n.endswith(".gguf"), "CC-BY-4.0"),
    # Who speaks when (Harvest speaker isolation)
    ("who_said", "moss_transcribe_diarize", "asr", lambda n: n.startswith("moss-transcribe-diarize") and n.endswith(".gguf"), "Apache-2.0"),
    # Voice to voice (Voice Lab)
    ("vc", "seed_vc", "vc", lambda n: n.startswith("seed-vc") and n.endswith(".gguf"), "GPL-3.0 (loaded at run time, not bundled)"),
]
# Roles whose models lack an op on the GPU backend and run on a second, CPU-only server.
# UniverSR's conditioning needs MUL_MAT variants Vulkan lacks (verified 2026-11-04); it is small.
CPU_ROLES = frozenset({"bwe"})
_PREFER = ("q8_0", "turbo", "1.7b", "orig", "f16")


def _rank(path: Path) -> tuple:
    name = path.name.lower()
    if name.startswith("sam-audio"):
        # Largest separator first; BF16 is near-lossless and half the size of F32.
        return ("large" not in name, "base" not in name, "bf16" not in name, "f32" not in name, name)
    if name.startswith("stable-audio-3"):
        # Best quality first: Medium (2B) over the 0.6B Small models, F16 over Q8.
        return ("medium" not in name, "sfx" not in name, "f16" not in name, name)
    return tuple(0 if key in name else 1 for key in _PREFER) + (name,)


def find_packages(roots: list[Path], max_depth: int = 6) -> dict[str, Package]:
    found: dict[str, list[Path]] = {}
    yue_dirs: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        base = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            here = Path(dirpath)
            dirnames[:] = [d for d in dirnames if d not in SKIP and len(here.parts) - base < max_depth]
            lower = [f.lower() for f in filenames]
            if any(f.startswith("yue2-3b") and f.endswith(".gguf") for f in lower) and any(
                    f.startswith("yue2-vae") for f in lower) and (here / "sidecars").is_dir():
                yue_dirs.append(here)
            for name, low in zip(filenames, lower):
                for role, *_rest in _PATTERNS:
                    if _rest[2](low):
                        found.setdefault(role, []).append(here / name)
    out: dict[str, Package] = {}
    for role, family, task, _test, licence in _PATTERNS:
        if found.get(role):
            out[role] = Package(role, family, task, sorted(found[role], key=_rank)[0], {}, licence)
    if yue_dirs:
        d = yue_dirs[0]
        mains = sorted((p for p in d.glob("yue2-3b-*.gguf") if "ios" not in p.name), key=_rank)
        vaes = sorted(d.glob("yue2-vae-*.gguf"), key=lambda p: (0 if "f16" in p.name else 1, p.name))
        if mains and vaes:
            out["yue2"] = Package("yue2", "yue2", "gen", d, {
                "yue2.model_gguf": mains[0].name, "yue2.vae_gguf": vaes[0].name, "yue2.mem_saver": True,
            }, "CC-BY-NC-4.0")
    return out


class MusicRuntimeError(RuntimeError):
    pass


class MusicRuntime:
    """Thread-safe wrapper around one audio.cpp server hosting the music packages."""

    def __init__(self, roots_provider, settings_provider, server: Optional[AudioCppServer] = None,
                 cpu_server: Optional[AudioCppServer] = None):
        self._roots_provider = roots_provider
        self._settings = settings_provider
        from core.paths import DATA_DIR

        self._server = server or AudioCppServer(work_dir=DATA_DIR / "tmp" / "audiocpp-music")
        self._cpu_server = cpu_server or AudioCppServer(work_dir=DATA_DIR / "tmp" / "audiocpp-music-cpu")
        self._packages: dict[str, Package] = {}
        self._lock = threading.Lock()

    # Discovery --------------------------------------------------------------
    def refresh(self) -> dict[str, Package]:
        self._packages = find_packages(self._roots_provider())
        return self._packages

    @property
    def packages(self) -> dict[str, Package]:
        if not self._packages:
            self.refresh()
        return self._packages

    def status(self) -> dict:
        exe = find_server_executable(str(self._settings().get("audiocpp_dir") or ""))
        return {
            "runtime": str(exe) if exe else None,
            "running": self._server.running or self._cpu_server.running,
            "packages": {r: {"path": str(p.path), "family": p.family, "licence": p.licence}
                         for r, p in self.packages.items()},
        }

    def has(self, role: str) -> bool:
        return role in self.packages

    # Server -----------------------------------------------------------------
    def _backend_name(self) -> str:
        cfg = self._settings()
        wanted = str(cfg.get("music_backend") or cfg.get("drama3_backend") or "auto").lower()
        exe = find_server_executable(str(cfg.get("audiocpp_dir") or ""))
        return ("vulkan" if exe is not None and "vulkan" in exe.parent.name.lower() else "cpu") \
            if wanted == "auto" else wanted

    def _split(self) -> bool:
        """True when CPU-only roles get their own CPU server (the main one is on a GPU backend)."""
        return self._backend_name() != "cpu"

    def _config(self, cpu_only: bool = False) -> dict:
        cfg = self._settings()
        backend = self._backend_name()
        split = backend != "cpu"
        if cpu_only:
            backend = "cpu"
        models = []
        for pkg in self.packages.values():
            if split and (pkg.role in CPU_ROLES) != cpu_only:
                continue
            entry: dict[str, Any] = {"id": pkg.model_id, "family": pkg.family, "path": pkg.path.as_posix(),
                                     "task": pkg.task, "mode": "offline"}
            if pkg.session_options:
                entry["session_options"] = dict(pkg.session_options)
            if pkg.family == "ace_step":
                # Tiled VAE: one untiled pass over a 30 s+ song needs a >4 GB buffer, past the Vulkan
                # per-allocation limit on the RX 7900 XT (ErrorOutOfDeviceMemory, verified).
                entry["session_options"] = {"ace_step.mem_saver": True, "ace_step.vae_tile_frames": 128,
                                            "ace_step.vae_tile_overlap_frames": 32}
            if pkg.family == "sam_audio":
                # Tiled codec work keeps long clips under the Vulkan per-allocation limit.
                entry["session_options"] = {"sam_audio.memory_bounded": True}
            if pkg.family == "stable_audio":
                entry["session_options"] = {"stable_audio.mem_saver": True}
            if pkg.family == "sheetsage2":
                # Tiled encoder attention keeps each score tensor under the Vulkan per-allocation
                # limit (a 32 s song otherwise asks for one 4.1 GB buffer; verified).
                entry["session_options"] = {"sheetsage2.attention_tile_rows": 1024}
            models.append(entry)
        # The CPU-only server runs alone on the processor, so it may use every logical core (UniverSR:
        # 10.9 s per flow evaluation at 24 threads vs 15.6 s at 12, measured on 3 s of speech).
        threads = (os.cpu_count() or 8) if cpu_only else int(cfg.get("drama3_threads") or max(1, (os.cpu_count() or 8) // 2))
        return {"backend": backend, "threads": threads,
                "lazy_load": True, "max_loaded_models": 1, "ui": False, "models": models}

    def backend(self, role: str = "") -> str:
        """The backend a role runs on (vulkan, cpu, cuda...); the main server's when no role is given."""
        return "cpu" if role in CPU_ROLES else self._backend_name()

    def _server_for(self, role: str) -> AudioCppServer:
        return self._cpu_server if role in CPU_ROLES and self._split() else self._server

    def ensure(self, role: str = "") -> str:
        exe = find_server_executable(str(self._settings().get("audiocpp_dir") or ""))
        if exe is None:
            raise MusicRuntimeError("The audio.cpp runtime was not found (runtime/audiocpp/<version>/).")
        if not self.packages:
            raise MusicRuntimeError("No music model packages were found in the model folders.")
        cpu = role in CPU_ROLES and self._split()
        try:
            return (self._cpu_server if cpu else self._server).ensure(exe, self._config(cpu_only=cpu))
        except RuntimeError as exc:
            raise MusicRuntimeError(str(exc)) from exc

    def stop(self) -> None:
        self._server.stop()
        self._cpu_server.stop()

    # Calls ------------------------------------------------------------------
    def _need(self, role: str) -> Package:
        pkg = self.packages.get(role)
        if pkg is None:
            raise MusicRuntimeError(f"The {role} model package is not installed.")
        return pkg

    def run_task(self, role: str, request: dict, include_artifacts: bool = False, timeout: float = 3600) -> dict:
        pkg = self._need(role)
        base = self.ensure(role)
        body = {"model": pkg.model_id, "request": request}
        if include_artifacts:
            body["include_artifacts"] = True
        try:
            r = self._server_for(role).http.post(f"{base}/v1/tasks/run", json=body, timeout=httpx.Timeout(timeout, connect=5))
        except httpx.HTTPError as exc:
            raise MusicRuntimeError(f"audio.cpp did not answer ({exc.__class__.__name__}).") from exc
        if r.status_code != 200:
            raise MusicRuntimeError(f"audio.cpp refused the {role} request: {_err(r)}")
        data = r.json()
        return data.get("result", data)

    def transcribe(self, audio_path: Path, language: Optional[str] = None) -> dict:
        pkg = self._need("asr")
        base = self.ensure()
        files = {"file": (audio_path.name, audio_path.read_bytes(), "audio/wav")}
        form = {"model": pkg.model_id}
        if language:
            form["language"] = language
        r = self._server.http.post(f"{base}/v1/audio/transcriptions/details", data=form, files=files,
                                   timeout=httpx.Timeout(1800, connect=5))
        if r.status_code != 200:
            raise MusicRuntimeError(f"Transcription failed: {_err(r)}")
        return r.json()

    def details(self, role: str, audio_path: Path, language: Optional[str] = None) -> dict:
        """Transcript/speaker details (segments, words, speaker_turns) from an ASR or diarization model."""
        pkg = self._need(role)
        base = self.ensure()
        files = {"file": (audio_path.name, audio_path.read_bytes(), "audio/wav")}
        form = {"model": pkg.model_id}
        if language:
            form["language"] = language
        r = self._server.http.post(f"{base}/v1/audio/transcriptions/details", data=form, files=files,
                                   timeout=httpx.Timeout(1800, connect=5))
        if r.status_code != 200:
            raise MusicRuntimeError(f"Speaker analysis failed: {_err(r)}")
        return r.json()

    def align(self, audio_path: Path, text: str, language: str = "English") -> dict:
        pkg = self._need("align")
        base = self.ensure()
        files = {"file": (audio_path.name, audio_path.read_bytes(), "audio/wav")}
        r = self._server.http.post(f"{base}/v1/audio/alignments",
                                   data={"model": pkg.model_id, "text": text, "language": language},
                                   files=files, timeout=httpx.Timeout(1800, connect=5))
        if r.status_code != 200:
            raise MusicRuntimeError(f"Lyric alignment failed: {_err(r)}")
        return r.json()


def _err(r: httpx.Response) -> str:
    try:
        body = r.json()
        err = body.get("error", body) if isinstance(body, dict) else body
        return str(err.get("message") if isinstance(err, dict) else err)[:400]
    except ValueError:
        return (r.text or f"HTTP {r.status_code}")[:400]


def result_audio(result: dict) -> bytes:
    data = result.get("audio")
    if not data:
        raise MusicRuntimeError("The model returned no audio.")
    if isinstance(data, str) and data.startswith("data:"):
        data = data.split(",", 1)[1]
    return base64.b64decode(data)


def named_outputs(result: dict) -> dict[str, bytes]:
    """Stem outputs: accepts {name: b64}, [{name, audio}], or [{id, audio|payload}]."""
    raw = result.get("named_audio_outputs") or result.get("outputs") or {}
    out: dict[str, bytes] = {}
    items = raw.items() if isinstance(raw, dict) else (
        ((i.get("name") or i.get("id") or f"stem{n}"), i) for n, i in enumerate(raw))
    for name, value in items:
        payload = (value.get("audio") or value.get("payload") or value.get("data")) if isinstance(value, dict) else value
        if isinstance(payload, str):
            if payload.startswith("data:"):
                payload = payload.split(",", 1)[1]
            out[str(name)] = base64.b64decode(payload)
    return out


def artifact_text(result: dict, kind_hint: str = "score") -> Optional[str]:
    for art in result.get("artifacts") or []:
        if kind_hint in str(art.get("id", "")) or kind_hint in str(art.get("kind", "")) or \
                "abc" in str((art.get("meta") or {}).get("format", "")) or "abc" in str(art.get("id", "")):
            payload = art.get("payload")
            if payload:
                try:
                    return base64.b64decode(payload).decode("utf-8", "replace")
                except (ValueError, TypeError):
                    return None
    return None
