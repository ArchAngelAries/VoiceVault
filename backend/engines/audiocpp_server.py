"""
engines/audiocpp_server.py — Runs one audio.cpp HTTP server owned by VoiceVault.

audio.cpp (https://github.com/0xShug0/audio.cpp, Apache-2.0) is a native C++ runtime
for GGUF speech models. VoiceVault starts its prebuilt `audiocpp_server.exe` hidden,
on a free localhost port, with a config written under DATA_DIR/tmp/audiocpp, and stops
the whole process tree on unload. That releases all GPU memory the server held.

The executable is found in the `audiocpp_dir` setting, else under the project's
runtime/audiocpp/<version>/ folder. Nothing is downloaded or installed here.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from core.paths import DATA_DIR, VOICEVAULT_ROOT

log = logging.getLogger(__name__)

EXE_NAME = "audiocpp_server.exe" if os.name == "nt" else "audiocpp_server"
DEFAULT_RUNTIME_ROOT = VOICEVAULT_ROOT / "runtime" / "audiocpp"
STARTUP_TIMEOUT_S = 120.0


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def find_server_executable(configured_dir: str = "", runtime_root: Path = DEFAULT_RUNTIME_ROOT) -> Optional[Path]:
    """Return the server executable from the configured folder, else the newest bundled runtime."""
    if configured_dir:
        candidate = Path(configured_dir).expanduser()
        exe = candidate if candidate.is_file() else candidate / EXE_NAME
        return exe if exe.is_file() else None
    if not runtime_root.is_dir():
        return None
    found = sorted((p for p in runtime_root.glob(f"*/{EXE_NAME}") if p.is_file()),
                   key=lambda p: p.parent.name, reverse=True)
    # Prefer a Vulkan build (AMD/NVIDIA/Intel GPUs) over a CPU-only one, and a "portable" build
    # (CPU features chosen at run time) over a native one: the native v0.9.3 build crashes with
    # an illegal instruction in ACE-Step on CPUs without AVX-512 (verified on a Ryzen 9 3900X).
    def rank(p: Path) -> tuple:
        name = p.parent.name.lower()
        return ("vulkan" not in name, "portable" not in name)
    return min(found, key=rank) if found else None


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class AudioCppServer:
    """Owns at most one running server process. Thread-safe start/stop."""

    def __init__(self, popen: Callable[..., Any] = subprocess.Popen, http: Optional[httpx.Client] = None,
                 work_dir: Optional[Path] = None):
        self._popen = popen
        self._http = http or httpx.Client(timeout=httpx.Timeout(600.0, connect=5.0), trust_env=False)
        self._work_dir = work_dir or (DATA_DIR / "tmp" / "audiocpp")
        self._lock = threading.Lock()
        self._proc: Any = None
        self._config_key: Optional[str] = None
        self._log_file: Any = None
        self.base_url: Optional[str] = None

    @property
    def running(self) -> bool:
        return bool(self._proc is not None and self._proc.poll() is None)

    @property
    def http(self) -> httpx.Client:
        return self._http

    def ensure(self, exe: Path, config: dict[str, Any]) -> str:
        """Start the server for this config (restarting if it changed) and return its base URL."""
        key = json.dumps(config, sort_keys=True)
        with self._lock:
            if self.running and self._config_key == key and self.base_url:
                return self.base_url
            self._stop_locked()
            port = free_port()
            full = {**config, "host": "127.0.0.1", "port": port}
            self._work_dir.mkdir(parents=True, exist_ok=True)
            cfg_path = self._work_dir / "server.json"
            cfg_path.write_text(json.dumps(full, indent=2), encoding="utf-8")
            log_path = self._work_dir / "server.log"
            self._log_file = open(log_path, "wb")
            log.info("Starting audio.cpp server %s on port %d", exe, port)
            self._proc = self._popen(
                [str(exe), "--config", str(cfg_path), "--log"], cwd=str(exe.parent),
                stdout=self._log_file, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                creationflags=_creation_flags(),
            )
            self.base_url = f"http://127.0.0.1:{port}"
            self._config_key = key
            try:
                self._wait_ready(log_path)
            except Exception:
                self._stop_locked()
                raise
            return self.base_url

    def _wait_ready(self, log_path: Path) -> None:
        deadline = time.monotonic() + STARTUP_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                raise RuntimeError("The audio.cpp server exited during startup: " + _tail(log_path))
            try:
                r = self._http.get(f"{self.base_url}/health", timeout=2.0)
                if r.status_code == 200 and r.json().get("status") == "ok":
                    return
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(0.25)
        raise RuntimeError("The audio.cpp server did not become ready within "
                           f"{int(STARTUP_TIMEOUT_S)} s: " + _tail(log_path))

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        proc, self._proc = self._proc, None
        self._config_key = None
        self.base_url = None
        if proc is not None and proc.poll() is None:
            log.info("Stopping audio.cpp server (pid %s)", getattr(proc, "pid", "?"))
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=10, creationflags=_creation_flags())
                else:
                    proc.terminate()
                proc.wait(timeout=10)
            except Exception:
                log.exception("audio.cpp server did not stop cleanly")
                try:
                    proc.kill()
                except Exception:
                    pass
        if self._log_file is not None:
            try:
                self._log_file.close()
            except OSError:
                pass
            self._log_file = None


def _tail(path: Path, limit: int = 600) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return "(no server log)"
    return text[-limit:] if text else "(empty server log)"
