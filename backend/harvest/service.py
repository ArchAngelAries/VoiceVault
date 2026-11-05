"""Persistent, sequential job service for the Harvest workspace."""
from __future__ import annotations

import json
import hashlib
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import wave
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from core.paths import DATA_DIR

log = logging.getLogger(__name__)
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_DURATION_S = 20 * 60
MAX_CLIP_S = 60   # Drama 3 and Breeze take up to 60 s; 25 s engines refuse longer clips themselves
MIN_CLIP_S = 3
MAX_JOBS = 8
FFMPEG_TIMEOUT_S = 180
DOWNLOAD_TIMEOUT_S = 300
SAFE_ID = re.compile(r"^[0-9a-f]{32}$")


class HarvestError(ValueError):
    """A safe, user-facing Harvest error."""


def _voices_dir() -> Path:
    """Where promoted voices go: Library/Voices (follows a patched DATA_DIR in tests)."""
    from core.library import voices_dir
    return voices_dir()


def _studio():
    """The Music Studio owns the audio.cpp server and GPU coordination; Harvest borrows it."""
    from api.music import get_studio
    return get_studio()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_child(root: Path, *parts: str) -> Path:
    """Join canonical internally generated path components and enforce containment."""
    path = root.joinpath(*parts).resolve()
    base = root.resolve()
    if path != base and base not in path.parents:
        raise HarvestError("The requested audio file is unavailable.")
    return path


def _display_name(name: str, fallback: str = "Audio") -> str:
    # Never use a client supplied value as a path. Keep a readable label only.
    value = Path(name.replace("\\", "/")).name
    value = re.sub(r"[\x00-\x1f\x7f]", "", value).strip()
    return (value[:100] or fallback)


def _which(name: str) -> str | None:
    return shutil.which(name)


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _run(command: list[str], *, timeout: float, cancel: threading.Event | None = None,
         process_callback: Callable[[subprocess.Popen | None], None] | None = None,
         stdout=subprocess.PIPE) -> bytes:
    """Run a process without a shell, tracking it for targeted cancellation."""
    proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout,
                            stderr=subprocess.PIPE, creationflags=_creation_flags())
    buffers: dict[str, list[bytes]] = {"stdout": [], "stderr": []}

    def drain(name: str, stream) -> None:
        kept = 0
        while True:
            try:
                chunk = os.read(stream.fileno(), 65536)
            except (OSError, ValueError):
                return
            if not chunk:
                return
            if kept < 1024 * 1024:
                take = chunk[:1024 * 1024 - kept]
                buffers[name].append(take)
                kept += len(take)

    readers = []
    started = time.monotonic()
    try:
        if process_callback:
            process_callback(proc)
        for name, stream in (("stdout", proc.stdout), ("stderr", proc.stderr)):
            if stream is not None:
                reader = threading.Thread(target=drain, args=(name, stream), daemon=True)
                reader.start()
                readers.append(reader)
        while True:
            if cancel is not None and cancel.is_set():
                HarvestService._terminate_owned(proc)
                raise HarvestError("Cancelled.")
            if time.monotonic() - started > timeout:
                HarvestService._terminate_owned(proc)
                raise HarvestError("The audio operation took too long. Try a shorter file.")
            if proc.poll() is not None:
                break
            time.sleep(.05)
        proc.wait()
        for reader in readers:
            reader.join(timeout=2)
        out = b"".join(buffers["stdout"])
        err = b"".join(buffers["stderr"])
        if proc.returncode:
            detail = err.decode("utf-8", "replace")[-1200:].strip()
            log.warning("Harvest process failed (%s): %s", proc.returncode, detail)
            error = HarvestError("Could not process this audio file. Check that it is a supported audio format.")
            error.detail = detail  # the tool's own message, for callers that can explain it (link downloads)
            raise error
        return out or b""
    finally:
        try:
            if proc.poll() is None:
                HarvestService._terminate_owned(proc)
        finally:
            for reader in readers:
                reader.join(timeout=2)
            for stream in (proc.stdout, proc.stderr):
                if stream:
                    stream.close()
            if process_callback:
                process_callback(None)


class HarvestService:
    def __init__(self, data_dir: Path | None = None):
        from core.library import harvest_dir
        self.root = data_dir / "harvest" if data_dir else harvest_dir()   # Library/Harvest
        self.root.mkdir(parents=True, exist_ok=True)
        self.store_path = self.root / "harvest.json"
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._queue: deque[str] = deque()
        self._events: dict[str, threading.Event] = {}
        self._processes: dict[str, subprocess.Popen] = {}
        self._stopping = False
        self._recovery_notice: str | None = None
        self._state = self._load()
        for job in self._state["jobs"].values():
            if job.get("state") in {"queued", "running"}:
                payload = job.get("payload") or {}
                staged = payload.get("path") if job.get("operation") == "import" else None
                if staged:
                    candidate = Path(staged).resolve()
                    if candidate.parent == self.root.resolve() and candidate.name.startswith("upload_"):
                        candidate.unlink(missing_ok=True)
                if job.get("operation") == "download" and SAFE_ID.fullmatch(job.get("id", "")):
                    shutil.rmtree(self.root / f"{job['id']}_download", ignore_errors=True)
                job.update(state="failed", stage="interrupted", error="The server restarted before this job finished.")
        self._save()
        self._worker = threading.Thread(target=self._work, name="harvest-worker", daemon=True)
        self._worker.start()

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("sources"), dict) and isinstance(data.get("jobs"), dict):
                return data
        except FileNotFoundError:
            return {"sources": {}, "jobs": {}}
        except (OSError, ValueError):
            pass
        if self.store_path.exists():
            backup = self.store_path.with_name(f"harvest.corrupt-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}.json")
            try:
                shutil.copy2(self.store_path, backup)
                log.error("Harvest metadata could not be read; preserved the original as %s", backup)
                self._recovery_notice = f"Some Harvest records could not be read. The original metadata was preserved as {backup.name}."
            except OSError as exc:
                raise RuntimeError("Harvest metadata is damaged and could not be preserved; refusing to overwrite it.") from exc
        return {"sources": {}, "jobs": {}}

    def _save(self) -> None:
        tmp = self.store_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.store_path)

    def capabilities(self) -> dict[str, Any]:
        ffmpeg, ytdlp = _which("ffmpeg"), False
        try:
            import yt_dlp  # noqa: F401
            ytdlp = True
        except ImportError:
            pass
        cleanup = [{"id": "none", "name": "Original audio", "available": bool(ffmpeg)}]
        try:
            from music.cleanup import available_steps
            runtime = _studio().runtime
            # UniverSR (bandwidth) runs on a CPU audio.cpp server; Vulkan lacks an op it needs.
            cleanup += available_steps(runtime.has, bool(ffmpeg))
        except Exception:
            cleanup.append({"id": "denoise", "name": "Gentle noise reduction", "available": bool(ffmpeg),
                            "reason": None if ffmpeg else "FFmpeg is not installed."})
        try:
            from asr.service import get_transcriber
            transcriber = get_transcriber()
            reason = transcriber.runtime_problem()
            cached = bool(transcriber.is_cached())
            available = not bool(reason) and cached
            asr = {"available": available, "cached": cached,
                   "reason": reason or (None if cached else "The Whisper model is not cached. Configure an existing model cache before transcribing.")}
        except Exception:
            asr = {"available": False, "cached": False, "reason": "Speech recognition is unavailable."}
        try:
            has_who = _studio().runtime.has("who_said")
        except Exception:
            has_who = False
        speakers = {"available": has_who and bool(ffmpeg),
                    "reason": None if has_who else "The speaker model (MOSS-Transcribe-Diarize) is not installed."}
        result = {"ffmpeg": bool(ffmpeg), "yt_dlp": ytdlp, "cleanup": cleanup, "asr": asr, "speakers": speakers}
        if self._recovery_notice:
            result["recovery_notice"] = self._recovery_notice
        return result

    def sources(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._public_source(x) for x in sorted(self._state["sources"].values(),
                    key=lambda s: s.get("created_at", ""), reverse=True)]

    def _public_source(self, source: dict[str, Any]) -> dict[str, Any]:
        variants = []
        for v in source.get("variants", []):
            variants.append({k: v[k] for k in ("id", "name", "duration_s", "audio_url", "original_audio_url",
                                                 "method", "start_s", "end_s", "transcript", "provenance",
                                                 "reference_id", "promoted_at", "cleanup_steps", "notes", "voice_prompt", "segments",
                                                 "hq_audio_url") if k in v})
        return {"id": source["id"], "name": source["name"], "duration_s": source["duration_s"],
                "created_at": source["created_at"], "provenance": source["provenance"],
                "audio_url": source["audio_url"], "variants": variants, "speakers": source.get("speakers")}

    def get_source(self, source_id: str) -> dict[str, Any]:
        if not SAFE_ID.fullmatch(source_id):
            raise HarvestError("This source is unavailable.")
        with self._lock:
            source = self._state["sources"].get(source_id)
            if not source:
                raise HarvestError("This source is unavailable.")
            return source

    def source_file(self, source_id: str, filename: str) -> Path:
        if not SAFE_ID.fullmatch(source_id):
            raise HarvestError("This audio file is unavailable.")
        with self._lock:
            source = self._state["sources"].get(source_id)
            owned = {source.get("original_file"), source.get("preview_file", f"{source_id}_preview.wav")} if source else set()
            if source:
                owned.update(v.get("file") for v in source.get("variants", []))
                owned.update(v.get("original_file") for v in source.get("variants", []))
                owned.update(v.get("hq_file") for v in source.get("variants", []) if v.get("hq_file"))
        if not source or filename not in owned:
            raise HarvestError("This audio file is unavailable.")
        path = _safe_child(self.root, source_id, filename)
        if path.is_symlink() or not path.is_file():
            raise HarvestError("This audio file is unavailable.")
        return path

    def add_source_job(self, *, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._condition:
            if self._stopping:
                raise HarvestError("Harvest is shutting down. Restart the server before adding a job.")
            active = sum(j.get("state") in {"queued", "running"} for j in self._state["jobs"].values())
            if active >= MAX_JOBS:
                raise HarvestError("The Harvest queue is full. Wait for a job to finish, then try again.")
            job_id = uuid.uuid4().hex
            job = {"id": job_id, "state": "queued", "stage": "Waiting in queue", "created_at": _now(),
                   "operation": operation, "payload": payload}
            self._state["jobs"][job_id] = job
            finished = sorted((j for j in self._state["jobs"].values()
                               if j.get("state") in {"completed", "failed", "cancelled"}),
                              key=lambda j: j.get("created_at", ""), reverse=True)
            for stale in finished[100:]:
                self._state["jobs"].pop(stale["id"], None)
            self._events[job_id] = threading.Event()
            self._queue.append(job_id)
            self._save()
            self._condition.notify()
            return self._public_job(job)

    def jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._public_job(x) for x in sorted(self._state["jobs"].values(), key=lambda j: j.get("created_at", ""), reverse=True)]

    def get_job(self, job_id: str) -> dict[str, Any]:
        if not SAFE_ID.fullmatch(job_id):
            raise HarvestError("This job is unavailable.")
        with self._lock:
            job = self._state["jobs"].get(job_id)
            if not job:
                raise HarvestError("This job is unavailable.")
            return self._public_job(job)

    def _public_job(self, job: dict[str, Any]) -> dict[str, Any]:
        result = {k: v for k, v in job.items() if k not in {"payload", "operation"}}
        return result

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._condition:
            job = self._state["jobs"].get(job_id) if SAFE_ID.fullmatch(job_id) else None
            if not job:
                raise HarvestError("This job is unavailable.")
            if job["state"] in {"completed", "failed", "cancelled"}:
                return self._public_job(job)
            ev = self._events.get(job_id)
            if ev:
                ev.set()
            proc = self._processes.get(job_id)
            if proc and proc.poll() is None:
                try:
                    self._terminate_owned(proc)
                except OSError:
                    pass
            if job["state"] == "queued":
                if job.get("operation") == "import":
                    staged = (job.get("payload") or {}).get("path")
                    if staged:
                        candidate = Path(staged).resolve()
                        if candidate.parent == self.root.resolve() and candidate.name.startswith("upload_"):
                            candidate.unlink(missing_ok=True)
                job.update(state="cancelled", stage="Cancelled")
                self._events.pop(job_id, None)
                try:
                    self._queue.remove(job_id)
                except ValueError:
                    pass
                self._save()
            else:
                job.update(stage="Cancelling")
                self._save()
            return self._public_job(job)

    def _set(self, job_id: str, **values: Any) -> None:
        with self._lock:
            job = self._state["jobs"].get(job_id)
            if job:
                job.update(values)
                self._save()

    def _commit_locked(self, job_id: str, cancel: threading.Event, result: dict[str, Any]) -> None:
        """Publish a finished job result while holding the lock used by cancel()."""
        if cancel.is_set():
            raise HarvestError("Cancelled.")
        job = self._state["jobs"].get(job_id)
        if job:
            job.update(state="completed", stage="Complete", result=result, error=None)
            self._save()

    def _set_process(self, job_id: str, proc: subprocess.Popen | None) -> None:
        with self._lock:
            if proc is None:
                self._processes.pop(job_id, None)
            else:
                self._processes[job_id] = proc
                if self._stopping or (self._events.get(job_id) and self._events[job_id].is_set()):
                    self._terminate_owned(proc)

    def shutdown(self, timeout: float = 10) -> bool:
        """Cancel owned work and wait a bounded time for the worker to exit."""
        deadline = time.monotonic() + max(0, timeout)
        with self._condition:
            self._stopping = True
            for job in list(self._state["jobs"].values()):
                if job.get("state") in {"queued", "running"}:
                    self.cancel(job["id"])
            self._condition.notify_all()
        if threading.current_thread() is not self._worker:
            self._worker.join(timeout=max(0, deadline - time.monotonic()))
        stopped = not self._worker.is_alive()
        if not stopped:
            log.warning("Harvest worker is still finishing cancelled work after shutdown timeout")
        return stopped

    @staticmethod
    def _terminate_owned(proc: subprocess.Popen) -> None:
        """Stop this job's process tree only (yt-dlp may own an ffmpeg child)."""
        if proc.poll() is not None:
            return
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=5, creationflags=_creation_flags())
                try:
                    proc.wait(timeout=1)
                    return
                except subprocess.TimeoutExpired:
                    pass
            except (OSError, subprocess.TimeoutExpired):
                pass
        try:
            proc.terminate()
        except OSError:
            if proc.poll() is None:
                raise
            return
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                if proc.poll() is None:
                    raise
            proc.wait(timeout=3)

    def _work(self) -> None:
        while True:
            with self._condition:
                while not self._queue and not self._stopping:
                    self._condition.wait()
                if self._stopping:
                    return
                job_id = self._queue.popleft()
                job = self._state["jobs"].get(job_id)
                if not job or job["state"] != "queued":
                    continue
                job.update(state="running", stage="Preparing")
                self._save()
            try:
                result = self._execute(job_id, job)
                ev = self._events[job_id]
                with self._lock:
                    current = self._state["jobs"].get(job_id)
                    if current and current["state"] == "completed":
                        pass  # committed with its source, variant, transcript, or reference
                    elif ev.is_set():
                        self._set(job_id, state="cancelled", stage="Cancelled", result=None, error=None)
                    else:
                        self._set(job_id, state="completed", stage="Complete", result=result, error=None)
            except HarvestError as exc:
                ev = self._events[job_id]
                self._set(job_id, state="cancelled" if ev.is_set() or str(exc) == "Cancelled." else "failed",
                          stage="Cancelled" if ev.is_set() or str(exc) == "Cancelled." else "Failed",
                          error=None if ev.is_set() or str(exc) == "Cancelled." else str(exc))
            except Exception:
                log.exception("Harvest job failed: %s", job_id)
                self._set(job_id, state="failed", stage="Failed", error="The job failed. See the backend log for details.")
            finally:
                with self._lock:
                    self._events.pop(job_id, None)
                    self._processes.pop(job_id, None)

    def _execute(self, job_id: str, job: dict[str, Any]) -> dict[str, Any]:
        op, data, cancel = job["operation"], job["payload"], self._events[job_id]
        if op == "import":
            self._set(job_id, stage="Checking audio")
            return self._import(job_id, data, cancel)
        if op == "download":
            self._set(job_id, stage="Downloading audio")
            return self._download(job_id, data, cancel)
        if op == "clip":
            self._set(job_id, stage="Creating clip")
            return self._clip(job_id, data, cancel)
        if op == "speakers":
            self._set(job_id, stage="Finding speakers")
            return self._speakers(job_id, data, cancel)
        if op == "transcribe":
            self._set(job_id, stage="Transcribing")
            return self._transcribe(job_id, data, cancel)
        if op == "reference":
            self._set(job_id, stage="Saving voice reference")
            return self._reference(job_id, data, cancel)
        raise HarvestError("Unknown Harvest operation.")

    def _probe(self, path: Path, cancel: threading.Event) -> float:
        ffprobe = _which("ffprobe")
        if not ffprobe:
            raise HarvestError("FFprobe is unavailable. Install FFmpeg and try again.")
        out = _run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                   timeout=30, cancel=cancel, process_callback=None)
        try:
            duration = float(json.loads(out)["format"]["duration"])
        except (ValueError, KeyError, TypeError):
            raise HarvestError("Could not read the audio duration.")
        if not math.isfinite(duration) or duration <= 0 or duration > MAX_DURATION_S:
            raise HarvestError("Audio must be longer than zero and no more than 20 minutes.")
        return round(duration, 3)

    def _ffmpeg(self, job_id: str, args: list[str], cancel: threading.Event) -> None:
        ffmpeg = _which("ffmpeg")
        if not ffmpeg:
            raise HarvestError("FFmpeg is unavailable. Install FFmpeg and try again.")
        _run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args], timeout=FFMPEG_TIMEOUT_S,
             cancel=cancel, process_callback=lambda p: self._set_process(job_id, p))

    def _new_source(self, job_id: str, cancel: threading.Event, name: str, duration: float,
                    provenance: dict[str, Any], original: Path, preview: Path) -> dict[str, Any]:
        sid = uuid.uuid4().hex
        dest = self.root / sid
        dest.mkdir(parents=True, exist_ok=False)
        try:
            extension = original.suffix.lower()
            if not re.fullmatch(r"\.[a-z0-9]{1,8}", extension):
                extension = ".bin"
            original_name = f"{sid}_original{extension}"
            preview_name = f"{sid}_preview.wav"
            shutil.copyfile(original, dest / original_name)
            shutil.copyfile(preview, dest / preview_name)
            digest = hashlib.sha256((dest / original_name).read_bytes()).hexdigest()
            provenance = {**provenance, "original_sha256": digest}
            record = {"id": sid, "name": _display_name(name, "Imported audio"), "duration_s": duration, "created_at": _now(),
                      "provenance": provenance, "audio_url": f"/api/harvest/audio/{sid}/{preview_name}",
                      "original_file": original_name, "preview_file": preview_name, "variants": []}
            with self._lock:
                result = {"source_id": sid}
                if cancel.is_set():
                    raise HarvestError("Cancelled.")
                self._state["sources"][sid] = record
                self._commit_locked(job_id, cancel, result)
            return result
        except Exception:
            shutil.rmtree(dest, ignore_errors=True)
            raise

    def _make_preview(self, job_id: str, src: Path, out: Path, cancel: threading.Event) -> None:
        self._ffmpeg(job_id, ["-i", str(src), "-vn", "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(out)], cancel)

    def _import(self, job_id: str, data: dict[str, Any], cancel: threading.Event) -> dict[str, Any]:
        path = Path(data["path"]).resolve()
        try:
            if path.stat().st_size <= 0:
                raise HarvestError("The uploaded file is empty.")
            if path.stat().st_size > MAX_UPLOAD_BYTES:
                raise HarvestError("The file is larger than 200 MB.")
            duration = self._probe(path, cancel)
            stage = self.root / f"{job_id}_preview.wav"
            self._make_preview(job_id, path, stage, cancel)
            self._set(job_id, stage="Saving original")
            result = self._new_source(job_id, cancel, data.get("name", "Imported audio"), duration,
                                      {"type": "upload", "original_name": _display_name(data.get("name", "Audio"))}, path, stage)
            result["variant_id"] = None
            return result
        finally:
            path.unlink(missing_ok=True)
            (self.root / f"{job_id}_preview.wav").unlink(missing_ok=True)

    @staticmethod
    def validate_video_url(url: str) -> str:
        """Accept one public http(s) media link; refuse credentials, odd ports, playlists and local hosts.

        Site support comes from yt-dlp (about 1,800 extractors). Hosts that resolve to loopback,
        private, link-local or reserved addresses are refused so a pasted link cannot reach this PC
        or the home network.
        """
        import ipaddress
        import socket
        not_found = False
        try:
            if any(ord(char) < 32 or ord(char) == 127 for char in url):
                raise ValueError
            parsed = urlsplit(url.strip())
            host = (parsed.hostname or "").lower().rstrip(".")
            if parsed.scheme not in ("https", "http") or not host or parsed.username or parsed.password:
                raise ValueError
            if parsed.port not in (None, 80, 443):
                raise ValueError
            if host in {"localhost"} or host.endswith((".local", ".localhost", ".internal", ".lan", ".home")):
                raise ValueError
            youtube = host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be"}
            if youtube and (parsed.path.startswith("/playlist") or "list" in parse_qs(parsed.query, keep_blank_values=True)):
                raise ValueError
            try:
                literal = ipaddress.ip_address(host.strip("[]"))
            except ValueError:
                literal = None
            if literal is not None and not literal.is_global:
                raise ValueError
            try:
                addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
            except socket.gaierror:
                addresses = None
            if addresses is None:
                not_found = True
                raise ValueError
            for address in addresses:
                ip = ipaddress.ip_address(address.split("%")[0])
                if not ip.is_global:
                    raise ValueError
            return url.strip()
        except (ValueError, UnicodeError):
            if not_found:
                raise HarvestError("That website could not be found. Check the link.")
            raise HarvestError("Use a single public video or audio link (https://…). Links to this computer, "
                               "the local network, playlists or with passwords are not accepted.")

    @staticmethod
    def explain_download_failure(detail: str) -> str:
        """Turn yt-dlp's error text into a plain reason the user can act on (no workaround is attempted)."""
        text = (detail or "").lower()
        if any(k in text for k in ("authentication is required", "login required", "log in", "sign in",
                                   "cookies-from-browser", "--cookies", "private video", "members-only")):
            return ("This site requires a login to get that video, which Harvest does not do. Download the video "
                    "yourself and drop the file into Harvest instead.")
        if any(k in text for k in ("http error 410", "gone", "not available in your country", "geo restrict",
                                   "geo-restrict", "blocked in your", "not available in your region")):
            return ("The site refused this download (HTTP 410/blocked). Many sites answer this way to automated "
                    "downloads even when the video plays fine in a browser; it can also mean removal or a regional "
                    "block. Harvest does not work around site blocks: download the video yourself and drop the "
                    "file into Harvest instead.")
        if "http error 403" in text or "forbidden" in text:
            return ("The site blocked the download (HTTP 403). Some sites block automated downloads; download the "
                    "video yourself and drop the file into Harvest instead.")
        if "http error 404" in text or "not found" in text or "video unavailable" in text or "removed" in text:
            return "That video was not found. It may have been deleted or made private."
        if "unsupported url" in text:
            return "That site or page is not supported by the downloader. Try the direct video page, or drop a file."
        if "max-filesize" in text or "file is larger" in text:
            return "The video is larger than the download limit. Choose a shorter section or drop a file."
        return ("Could not download from that link. The site may need a login, block downloads, or not be supported. "
                "Check the link and try again.")

    def _download(self, job_id: str, data: dict[str, Any], cancel: threading.Event) -> dict[str, Any]:
        url = self.validate_video_url(data["url"])
        keep_video = bool(data.get("keep_video"))
        start, end = float(data.get("start_s", 0)), float(data["end_s"])
        if not (0 <= start < end <= MAX_DURATION_S):
            raise HarvestError("Choose a start and end time within the first 20 minutes.")
        try:
            import yt_dlp
        except ImportError:
            raise HarvestError("Link downloads are unavailable because yt-dlp is not installed.")
        stage = self.root / f"{job_id}_download"
        stage.mkdir(parents=True, exist_ok=True)
        try:
            # Limit the extractor to the requested range and disable playlists, browser state,
            # plugins and external command hooks. yt-dlp handles only the fixed validated URL.
            outtmpl = str(stage / "source.%(ext)s")
            cmd = [sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-playlist", "--no-cookies",
                   "--no-plugin-dirs", "--no-exec", "--no-remote-components", "--no-warnings", "--quiet", "--no-color",
                   "--max-filesize", "1500M" if keep_video else "200M", "--socket-timeout", "15",
                   "--retries", "1", "--fragment-retries", "1",
                   "--format", "bestvideo*[height<=1080]+bestaudio/best" if keep_video else "bestaudio/best",
                   *(["--merge-output-format", "mp4"] if keep_video else []),
                   "--no-simulate", "--print", "after_move:%(title).200s\t%(extractor_key)s",
                   "--download-sections", f"*{start}-{end}", "--force-keyframes-at-cuts",
                   "--output", outtmpl, url]
            self._set_process(job_id, None)
            try:
                printed = _run(cmd, timeout=DOWNLOAD_TIMEOUT_S * (3 if keep_video else 1), cancel=cancel,
                               process_callback=lambda p: self._set_process(job_id, p))
            except HarvestError as exc:
                detail = getattr(exc, "detail", None)
                if detail is None:
                    raise  # cancellation or timeout already carry a clear message
                raise HarvestError(self.explain_download_failure(detail)) from exc
            title, _, site = printed.decode("utf-8", "replace").strip().splitlines()[-1].partition("\t") if printed.strip() else ("", "", "")
            info = {"title": title.strip() or "Downloaded audio", "site": site.strip() or None}
            if cancel.is_set():
                raise HarvestError("Cancelled.")
            sources = [p for p in stage.iterdir() if p.is_file() and p.suffix.lower() not in {".part", ".ytdl"}]
            if not sources:
                raise HarvestError("The video did not provide a usable audio segment.")
            src = sources[0]
            duration = self._probe(src, cancel)
            # Extractor range behavior can include keyframe padding; crop the requested exact range.
            cropped = stage / "segment.wav"
            self._ffmpeg(job_id, ["-ss", "0", "-i", str(src), "-t", str(min(end-start, duration)), "-vn",
                                  "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(cropped)], cancel)
            final_duration = self._probe(cropped, cancel)
            return self._new_source(job_id, cancel, info["title"],
                                    final_duration, {"type": "link", "site": info["site"], "url": url, "start_s": start,
                                                     "end_s": end, "section_start_s": start, "section_end_s": end,
                                                     "video_kept": keep_video}, src, cropped)
        except HarvestError:
            raise
        except Exception as exc:
            log.warning("yt-dlp failed: %s", type(exc).__name__)
            raise HarvestError("Could not download from that link. The site may need a login, block downloads, "
                               "or not be supported. Check the link and try again.")
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def drop_video(self, source_id: str) -> dict[str, Any]:
        """Replace a kept video with a lossless FLAC of its audio, freeing the space. Clips are unaffected."""
        source = self.get_source(source_id)
        if not source.get("provenance", {}).get("video_kept"):
            raise HarvestError("This source has no kept video.")
        video = _safe_child(self.root, source_id, source["original_file"])
        audio_name = f"{source_id}_original.flac"
        audio = _safe_child(self.root, source_id, audio_name)
        _run([_which("ffmpeg") or "ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vn", "-c:a", "flac", str(audio)],
             timeout=600)
        digest = hashlib.sha256(audio.read_bytes()).hexdigest()
        with self._lock:
            current = self._state["sources"][source_id]
            current["original_file"] = audio_name
            current["provenance"] = {**current["provenance"], "video_kept": False, "video_removed_at": _now(),
                                     "original_sha256": digest, "video_sha256": current["provenance"].get("original_sha256")}
            self._save()
        if video.resolve() != audio.resolve():
            video.unlink(missing_ok=True)
        return self._public_source(self.get_source(source_id))

    def add_variant(self, job_id: str, cancel: threading.Event, source: dict[str, Any], path: Path,
                    original_path: Path, name: str, duration: float, method: str, start: float, end: float,
                    hq_path: Path | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        vid = uuid.uuid4().hex
        filename = f"{source['id']}_{vid[:12]}_{method}.wav"
        dest = _safe_child(self.root, source["id"], filename)
        shutil.copyfile(path, dest)
        hq_name = None
        if hq_path is not None:
            hq_name = f"{source['id']}_{vid[:12]}_{method}_hq.wav"
            shutil.copyfile(hq_path, _safe_child(self.root, source["id"], hq_name))
        original_name = f"{source['id']}_{vid[:12]}_original.wav"
        original_dest = _safe_child(self.root, source["id"], original_name)
        shutil.copyfile(original_path, original_dest)
        original_url = f"/api/harvest/audio/{source['id']}/{original_name}"
        provenance = source.get("provenance", {})
        offset = float(provenance.get("section_start_s", 0))
        variant = {"id": vid, "name": _display_name(name, "Voice clip"), "duration_s": duration,
                   "audio_url": f"/api/harvest/audio/{source['id']}/{filename}", "file": filename,
                   "original_audio_url": original_url, "original_file": original_name,
                   "method": method, "start_s": start, "end_s": end, **(extra or {}),
                   **({"hq_file": hq_name, "hq_audio_url": f"/api/harvest/audio/{source['id']}/{hq_name}"} if hq_name else {}),
                   "provenance": {"source_id": source["id"], "source_url": provenance.get("url"),
                                  "start_s": offset + start, "end_s": offset + end}}
        try:
            with self._lock:
                current = self._state["sources"].get(source["id"])
                if current is None:
                    raise HarvestError("This source is unavailable.")
                if cancel.is_set():
                    raise HarvestError("Cancelled.")
                current["variants"].append(variant)
                result = {"source_id": source["id"], "variant_id": vid}
                self._commit_locked(job_id, cancel, result)
            return result
        except Exception:
            dest.unlink(missing_ok=True)
            original_dest.unlink(missing_ok=True)
            if hq_name:
                _safe_child(self.root, source["id"], hq_name).unlink(missing_ok=True)
            raise

    def _speakers(self, job_id: str, data: dict[str, Any], cancel: threading.Event) -> dict[str, Any]:
        """Split a source into transcribed lines with suggested speaker labels (MOSS-Transcribe-Diarize).

        Labels are suggestions: on similar voices the model can split one person into two labels or
        merge two people, so the UI lets the user hear each line and choose which lines to keep.
        """
        source = self.get_source(data["source_id"])
        studio = _studio()
        if not studio.runtime.has("who_said"):
            raise HarvestError("The speaker model (MOSS-Transcribe-Diarize) is not installed.")
        preview = _safe_child(self.root, source["id"], source.get("preview_file", source["original_file"]))
        wav16 = self.root / f"{job_id}_16k.wav"
        try:
            self._set(job_id, stage="Preparing audio")
            self._ffmpeg(job_id, ["-i", str(preview), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav16)], cancel)
            self._set(job_id, stage="Finding who speaks when")
            try:
                raw = studio.run_details("who_said", wav16, data.get("language") or None)
            except Exception as exc:
                if exc.__class__.__name__ == "MusicRuntimeError":
                    raise HarvestError(str(exc)) from exc
                raise
        finally:
            wav16.unlink(missing_ok=True)
        if cancel.is_set():
            raise HarvestError("Cancelled.")
        rate = float(raw.get("sample_rate") or 16000)
        turns = raw.get("speaker_turns") or raw.get("segments") or []
        lines = []
        for n, t in enumerate(turns):
            start, end = t["start_sample"] / rate, t["end_sample"] / rate
            if end - start < 0.15:
                continue
            lines.append({"id": n, "start": round(start, 2), "end": round(end, 2),
                          "speaker": str(t.get("speaker_id") or "S?"), "text": (t.get("text") or "").strip()})
        speakers = sorted({l["speaker"] for l in lines})
        result = {"source_id": source["id"], "lines": lines, "speakers": speakers, "model": "moss_transcribe_diarize",
                  "analysed_at": _now()}
        with self._lock:
            current = self._state["sources"].get(source["id"])
            if current is None:
                raise HarvestError("This source is unavailable.")
            current["speakers"] = result
            self._commit_locked(job_id, cancel, {"source_id": source["id"], "lines": len(lines), "speakers": speakers})
        return result

    @staticmethod
    def _join_segments(audio, rate: int, segments: list[tuple[float, float]], offset: float, pad: float = 0.06,
                       gap: float = 0.3, exclude: list[tuple[float, float]] | None = None):
        """Concatenate the chosen segments (times relative to the source) with short silent gaps.

        Each segment is padded a little so word edges are not clipped, but never into an excluded
        interval (another speaker's line), so no sliver of the unwanted voice leaks in.
        """
        import numpy as np
        parts = []
        fade = max(1, int(0.01 * rate))
        ramp = np.linspace(0, 1, fade, dtype=np.float32)[:, None]
        exclude = exclude or []
        for start, end in segments:
            lo, hi = start - pad, end + pad
            for xa, xb in exclude:
                if xb <= start + 1e-6 and xb > lo:
                    lo = xb
                if xa >= end - 1e-6 and xa < hi:
                    hi = xa
            a = max(0, int((lo - offset) * rate))
            b = min(len(audio), int((hi - offset) * rate))
            piece = audio[a:b].copy()
            if len(piece) > 2 * fade:
                piece[:fade] *= ramp
                piece[-fade:] *= ramp[::-1]
            if parts:
                parts.append(np.zeros((int(gap * rate), audio.shape[1]), np.float32))
            parts.append(piece)
        return np.concatenate(parts) if parts else audio[:0]

    def _clip(self, job_id: str, data: dict[str, Any], cancel: threading.Event) -> dict[str, Any]:
        """Cut a clip from the original file, optionally clean it, and keep a full-band copy.

        The clip of record stays 24 kHz mono (as before, for every engine and the UI); the full-band
        copy (48 kHz mono, cut from the original upload, not the 24 kHz preview) is what 44.1 kHz
        engines clone from after promotion. The matched original section is kept for A/B listening.
        """
        from music.cleanup import CleanupError, clean, normalise_steps
        source = self.get_source(data["source_id"])
        segments = sorted((float(a), float(b)) for a, b in (data.get("segments") or []) if float(b) > float(a))
        if segments:
            # Keep only the chosen lines (e.g. one speaker): the clip covers their span; others are cut out.
            if any(a < 0 or b > source["duration_s"] + 0.05 for a, b in segments) or len(segments) > 200:
                raise HarvestError("The selected lines do not fit within the source.")
            start, end = max(0.0, segments[0][0] - 0.1), min(source["duration_s"], segments[-1][1] + 0.1)
            kept = sum(b - a for a, b in segments) + 0.3 * (len(segments) - 1)
            if not (MIN_CLIP_S <= kept <= MAX_CLIP_S):
                raise HarvestError(f"The selected lines add up to {kept:.1f} s; choose {MIN_CLIP_S} to {MAX_CLIP_S} seconds.")
        else:
            start, end = float(data["start_s"]), float(data["end_s"])
            if not (0 <= start < end <= source["duration_s"] and MIN_CLIP_S <= end - start <= MAX_CLIP_S):
                raise HarvestError(f"Voice clips must be {MIN_CLIP_S} to {MAX_CLIP_S} seconds and fit within the source.")
        try:
            steps = normalise_steps(data.get("cleanup"))
        except CleanupError as exc:
            raise HarvestError(str(exc)) from exc
        preview = _safe_child(self.root, source["id"], source.get("preview_file", source["original_file"]))
        original = _safe_child(self.root, source["id"], source["original_file"])
        if not original.is_file():
            original = preview
        temp = self.root / f"{job_id}_clip.wav"        # 24 kHz original section (A/B reference)
        full = self.root / f"{job_id}_full.wav"        # 48 kHz original section (cleaning input)
        out24 = self.root / f"{job_id}_clean.wav"
        out48 = self.root / f"{job_id}_hq.wav"
        try:
            self._ffmpeg(job_id, ["-ss", str(start), "-i", str(preview), "-t", str(end - start), "-vn",
                                  "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(temp)], cancel)
            self._ffmpeg(job_id, ["-ss", str(start), "-i", str(original), "-t", str(end - start), "-vn",
                                  "-ac", "1", "-ar", "48000", "-c:a", "pcm_s24le", str(full)], cancel)
            notes: list[str] = []
            from music import dsp
            audio, rate = dsp.read(full)
            if segments:
                # Lines from the speaker analysis that were not selected (other voices) bound the padding.
                lines = (source.get("speakers") or {}).get("lines") or []
                exclude = [(l["start"], l["end"]) for l in lines
                           if not any(min(b, l["end"]) - max(a, l["start"]) > 0.05 for a, b in segments)]
                audio = self._join_segments(audio, rate, segments, offset=start, exclude=exclude)
                ref24, ref_rate = dsp.read(temp)
                dsp.write(temp, self._join_segments(ref24, ref_rate, segments, offset=start, exclude=exclude), ref_rate)
            if steps:
                def progress(message: str) -> None:
                    if cancel.is_set():
                        raise HarvestError("Cancelled.")
                    self._set(job_id, stage=message)
                try:
                    audio, rate, notes = clean(audio, rate, steps, _studio().run_tool, self.root / "cleanup-tmp",
                                               voice_prompt=data.get("voice_prompt"), progress=progress)
                except CleanupError as exc:
                    raise HarvestError(str(exc)) from exc
                except Exception as exc:
                    if exc.__class__.__name__ == "MusicRuntimeError":
                        raise HarvestError(str(exc)) from exc
                    raise
            if cancel.is_set():
                raise HarvestError("Cancelled.")
            dsp.write(out48, audio, rate)
            dsp.write(out24, dsp.resample(audio, rate, 24000), 24000)
            method = "-".join(steps) if steps else "none"   # also part of the file name, so URL-safe
            dur = self._probe(out24, cancel)
            return self.add_variant(job_id, cancel, source, out24, temp,
                                    data.get("name") or f"Clip {start:.1f}–{end:.1f}s", dur, method, start, end,
                                    hq_path=out48, extra={"cleanup_steps": steps, "notes": notes,
                                                          "segments": [[a, b] for a, b in segments] or None,
                                                          "voice_prompt": data.get("voice_prompt") if "isolate_voice" in steps else None})
        finally:
            for f in (temp, full, out24, out48):
                f.unlink(missing_ok=True)

    def _transcribe(self, job_id: str, data: dict[str, Any], cancel: threading.Event) -> dict[str, Any]:
        source = self.get_source(data["source_id"])
        variant = next((v for v in source["variants"] if v["id"] == data["variant_id"]), None)
        if not variant:
            raise HarvestError("This voice clip is unavailable.")
        path = _safe_child(self.root, source["id"], variant["file"])
        # Model calls cannot be stopped safely mid-kernel. Keep cancellation pending until it
        # returns, then discard its result before mutating the source record.
        from asr.service import get_transcriber
        from core.gpu_scheduler import release_tts_for_asr, speech_resource_lock
        transcriber = get_transcriber()
        try:
            problem = transcriber.runtime_problem()
            cached = transcriber.is_cached()
        except Exception:
            problem, cached = "Speech recognition is unavailable.", False
        if problem or not cached:
            raise HarvestError(problem or "The Whisper model is not cached. Configure an existing model cache before transcribing.")
        with speech_resource_lock:
            release_tts_for_asr()
            if not transcriber.loaded:
                transcriber.load()
            from asr.audio import decode_audio
            recognized = transcriber.transcribe(decode_audio(path), timestamps=False)["text"].strip()
        if cancel.is_set():
            raise HarvestError("Cancelled.")
        with self._lock:
            result = {"source_id": source["id"], "variant_id": variant["id"], "transcript": recognized}
            if cancel.is_set():
                raise HarvestError("Cancelled.")
            variant["transcript"] = recognized
            self._commit_locked(job_id, cancel, result)
        return result

    def _reference(self, job_id: str, data: dict[str, Any], cancel: threading.Event) -> dict[str, Any]:
        source = self.get_source(data["source_id"])
        variant = next((v for v in source["variants"] if v["id"] == data["variant_id"]), None)
        if not variant:
            raise HarvestError("This voice clip is unavailable.")
        if cancel.is_set():
            raise HarvestError("Cancelled.")
        from engines.audio_store import save_reference
        from core.paths import DATA_DIR
        import soundfile as sf
        path = _safe_child(self.root, source["id"], variant["file"])
        samples, rate = sf.read(str(path), dtype="float32", always_2d=False)
        if samples.ndim > 1:
            samples = samples.mean(axis=1)
        # save_reference takes mono samples and sample rate; clip files are 24 kHz mono already.
        text = data.get("transcript")
        if text is not None:
            text = str(text).strip()[:2000]
        effective_text = text if text is not None else variant.get("transcript", "")
        existing_id = variant.get("reference_id")
        if existing_id and re.fullmatch(r"[0-9a-f]{32}", existing_id):
            existing_json = _voices_dir() / f"{existing_id}.json"
            existing_wav = _voices_dir() / f"{existing_id}.wav"
            try:
                existing = json.loads(existing_json.read_text(encoding="utf-8"))
                if existing_wav.is_file() and existing.get("transcript", "") == effective_text:
                    existing.update({"source_id": source["id"], "variant_id": variant["id"]})
                    with self._lock:
                        self._commit_locked(job_id, cancel, existing)
                    return existing
            except (OSError, ValueError):
                pass
        hq_samples = hq_rate = None
        if variant.get("hq_file"):
            hq_path = _safe_child(self.root, source["id"], variant["hq_file"])
            if hq_path.is_file():
                from music import dsp
                hq, hq_rate_in = dsp.read(hq_path)
                hq_samples = dsp.resample(hq.mean(axis=1, keepdims=True), hq_rate_in, 44100)[:, 0]
                hq_rate = 44100
        record = save_reference(_voices_dir(), samples, int(rate), variant["name"], hq_samples, hq_rate)
        ref_json = _voices_dir() / f"{record['reference_id']}.json"
        try:
            saved = json.loads(ref_json.read_text(encoding="utf-8"))
            saved.update({"transcript": effective_text,
                          "source_id": source["id"], "variant_id": variant["id"],
                          "provenance": variant.get("provenance", {})})
            ref_json.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
            record = saved
        except (OSError, ValueError, TypeError) as exc:
            (_voices_dir() / f"{record['reference_id']}.wav").unlink(missing_ok=True)
            ref_json.unlink(missing_ok=True)
            log.exception("Could not enrich promoted Harvest reference metadata")
            raise HarvestError("Could not save the reference transcript and provenance. Try again.") from exc
        try:
            with self._lock:
                if cancel.is_set():
                    raise HarvestError("Cancelled.")
                if text is not None:
                    variant["transcript"] = text
                variant["reference_id"] = record["reference_id"]
                variant["promoted_at"] = _now()
                record.update({"source_id": source["id"], "variant_id": variant["id"]})
                self._commit_locked(job_id, cancel, record)
        except HarvestError:
            # This reference was created by this uncommitted job, so remove only its own files.
            (_voices_dir() / f"{record['reference_id']}.wav").unlink(missing_ok=True)
            (_voices_dir() / f"{record['reference_id']}.json").unlink(missing_ok=True)
            raise
        return record

