"""Check for updates: compare this build with the newest VoiceVault release on GitHub.

Privacy: the check is one plain request to GitHub's public releases API. Nothing about the user,
their PC or their library is sent. It runs at most once a day and can be turned off in Settings
(`update_check`).

Downloading: only the `VoiceVault-Setup-<version>.exe` asset of this project's own release, only
from GitHub's download hosts, and the file is kept only when its SHA-256 matches the digest GitHub
publishes for it. Running the installer is the desktop app's job, after the user clicks.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.paths import DATA_DIR
from core.settings import get as get_setting
from core.version import APP_VERSION

router = APIRouter()
REPO = "ArchAngelAries/VoiceVault"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases?per_page=20"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
USER_AGENT = f"VoiceVault/{APP_VERSION} (update check; https://github.com/{REPO})"
CHECK_EVERY_SECONDS = 24 * 3600
STATE_FILE = DATA_DIR / "updates.json"
DOWNLOAD_DIR = DATA_DIR / "updates"
ASSET_NAME = re.compile(r"VoiceVault-Setup-(\d+\.\d+\.\d+)\.exe")
DOWNLOAD_HOSTS = {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
MAX_INSTALLER_BYTES = 2 * 1024 ** 3
MAX_NOTES_CHARS = 6000

_download: dict = {"state": "idle"}     # idle | downloading | ready | error
_download_task: asyncio.Task | None = None
_check_lock = asyncio.Lock()


# ── Versions ─────────────────────────────────────────────────────

def parse_version(text: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", (text or "").strip())
    return tuple(int(x) for x in match.groups()) if match else None


def newest(releases: list, current: str) -> dict | None:
    """The newest published release (pre-releases count; drafts don't) that is newer than `current`."""
    mine = parse_version(current) or (0, 0, 0)
    best, best_version = None, mine
    for release in releases if isinstance(releases, list) else []:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        version = parse_version(str(release.get("tag_name") or ""))
        if version and version > best_version:
            best, best_version = release, version
    return best


def summarise(release: dict) -> dict:
    version = ".".join(map(str, parse_version(release["tag_name"])))
    asset = next((a for a in release.get("assets") or []
                  if isinstance(a, dict) and a.get("name") == f"VoiceVault-Setup-{version}.exe"), None)
    page = str(release.get("html_url") or "")
    if not page.startswith(f"https://github.com/{REPO}/releases/"):
        page = RELEASES_PAGE
    out = {"version": version, "tag": release["tag_name"], "name": str(release.get("name") or "")[:200],
           "notes": str(release.get("body") or "")[:MAX_NOTES_CHARS], "url": page,
           "published_at": release.get("published_at"), "prerelease": bool(release.get("prerelease")),
           "installer": None}
    digest = str((asset or {}).get("digest") or "")
    if asset and re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        out["installer"] = {"name": asset["name"], "size": int(asset.get("size") or 0),
                            "sha256": digest.split(":", 1)[1], "download_url": str(asset.get("browser_download_url") or "")}
    return out


# ── State ────────────────────────────────────────────────────────

def _read_state() -> dict:
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(value: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=1), encoding="utf-8")
    os.replace(tmp, STATE_FILE)


async def fetch_releases(client: httpx.AsyncClient | None = None) -> list:
    own = client is None
    client = client or httpx.AsyncClient(timeout=15, follow_redirects=False)
    try:
        response = await client.get(RELEASES_API, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
        if response.status_code != 200:
            raise HTTPException(502, f"GitHub answered {response.status_code}; try again later.")
        return response.json()
    except httpx.HTTPError:
        raise HTTPException(502, "Couldn't reach GitHub. Check your internet connection and try again.") from None
    finally:
        if own:
            await client.aclose()


async def check(force: bool = False) -> dict:
    async with _check_lock:
        state = _read_state()
        if not force and time.time() - float(state.get("checked_at") or 0) < CHECK_EVERY_SECONDS:
            return state
        try:
            release = newest(await fetch_releases(), APP_VERSION)
            state = {**state, "checked_at": time.time(), "error": None,
                     "latest": summarise(release) if release else None}
        except HTTPException as exc:
            state = {**state, "checked_at": time.time(), "error": exc.detail}
        _write_state(state)
        return state


def _status(state: dict) -> dict:
    latest = state.get("latest")
    if latest and not (parse_version(latest.get("version", "")) or (0, 0, 0)) > (parse_version(APP_VERSION) or (0, 0, 0)):
        latest = None   # this build already caught up (for example after installing)
    download = dict(_download)
    if latest and download.get("version") not in (None, latest["version"]):
        download = {"state": "idle"}
    if download.get("state") == "idle" and latest and latest.get("installer") and _ready_file(latest):
        download = {"state": "ready", "version": latest["version"]}
    download.pop("path", None)   # never send file paths to the page
    checked = state.get("checked_at")
    return {"current": APP_VERSION, "enabled": bool(get_setting("update_check", True)),
            "update_available": bool(latest), "latest": latest,
            "skipped": latest is not None and state.get("skipped") == latest["version"],
            "checked_at": datetime.fromtimestamp(checked, timezone.utc).isoformat() if checked else None,
            "error": state.get("error"), "download": download, "releases_page": RELEASES_PAGE}


# ── Download ─────────────────────────────────────────────────────

def _target(installer: dict) -> Path:
    if not ASSET_NAME.fullmatch(installer["name"]):
        raise HTTPException(422, "Unexpected installer name.")
    return DOWNLOAD_DIR / installer["name"]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


_verified: dict[str, tuple] = {}   # path -> (size, mtime, sha256) of files already hashed


def _ready_file(latest: dict) -> Path | None:
    """The verified installer for `latest`, if it is already downloaded (checked by size and hash)."""
    installer = latest.get("installer")
    if not installer:
        return None
    path = _target(installer)
    try:
        if not path.is_file():
            return None
        info = path.stat()
        if info.st_size != installer["size"]:
            return None
        seen = _verified.get(str(path))
        if not seen or seen[:2] != (info.st_size, info.st_mtime):
            seen = (info.st_size, info.st_mtime, sha256_file(path))
            _verified[str(path)] = seen
        return path if seen[2] == installer["sha256"] else None
    except OSError:
        return None


def _allowed(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" and (parts.hostname or "") in DOWNLOAD_HOSTS


async def download_installer(installer: dict, version: str, client: httpx.AsyncClient | None = None) -> None:
    """Stream the installer to a .part file while hashing it; keep it only when size and SHA-256 match."""
    target = _target(installer)
    part = target.with_name(target.name + ".part")
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    _download.clear()
    _download.update(state="downloading", version=version, received=0, total=installer["size"])
    own = client is None
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(30, read=120), follow_redirects=False)
    try:
        url = installer["download_url"]
        for _hop in range(5):   # follow GitHub's redirects by hand, checking every host
            if not _allowed(url):
                raise ValueError("The download was redirected somewhere other than GitHub.")
            async with client.stream("GET", url, headers={"User-Agent": USER_AGENT}) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    url = str(httpx.URL(url).join(response.headers.get("location", "")))
                    continue
                if response.status_code != 200:
                    raise ValueError(f"GitHub answered {response.status_code}.")
                digest, received = hashlib.sha256(), 0
                with part.open("wb") as out:
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        received += len(chunk)
                        if received > min(installer["size"], MAX_INSTALLER_BYTES):
                            raise ValueError("The download is larger than GitHub said it would be.")
                        digest.update(chunk)
                        out.write(chunk)
                        _download["received"] = received
                if received != installer["size"]:
                    raise ValueError("The download was incomplete.")
                if digest.hexdigest() != installer["sha256"]:
                    raise ValueError("The download didn't match GitHub's SHA-256 fingerprint, so it was deleted.")
                os.replace(part, target)
                _download.update(state="ready", path=str(target))
                return
        raise ValueError("Too many redirects.")
    except (ValueError, OSError, httpx.HTTPError) as exc:
        part.unlink(missing_ok=True)
        message = str(exc) if isinstance(exc, ValueError) else "The download failed. Check your connection and try again."
        _download.clear()
        _download.update(state="error", version=version, error=message)
    finally:
        if own:
            await client.aclose()


# ── Routes ───────────────────────────────────────────────────────

class SkipRequest(BaseModel):
    version: str | None = Field(None, max_length=40)


@router.get("/status")
async def status(auto: bool = False):
    """The update state. With auto=1 (app start), checks GitHub if enabled and a day has passed."""
    state = _read_state()
    if auto and get_setting("update_check", True):
        state = await check(force=False)
    return _status(state)


@router.post("/check")
async def check_now():
    """Check GitHub now (the Settings button), whatever the daily schedule says.

    Asking by hand also brings back a version the user skipped earlier.
    """
    state = _read_state()
    if state.get("skipped"):
        state["skipped"] = None
        _write_state(state)
    return _status(await check(force=True))


@router.post("/skip")
async def skip(body: SkipRequest):
    state = _read_state()
    state["skipped"] = body.version
    _write_state(state)
    return _status(state)


@router.post("/download")
async def start_download():
    global _download_task
    latest = _status(_read_state()).get("latest")
    if not latest:
        raise HTTPException(409, "There is no newer version to download.")
    if not latest.get("installer"):
        raise HTTPException(409, "This release has no verified installer. Download it from the release page instead.")
    if _download_task and not _download_task.done():
        return _status(_read_state())
    if _ready_file(latest):
        _download.clear()
        _download.update(state="ready", version=latest["version"], path=str(_target(latest["installer"])))
        return _status(_read_state())
    _download_task = asyncio.create_task(download_installer(latest["installer"], latest["version"]))
    await asyncio.sleep(0)
    return _status(_read_state())


@router.get("/installer")
async def installer_for_desktop():
    """For the desktop app only (it runs the installer): the verified file's path and hash."""
    latest = _status(_read_state()).get("latest")
    path = _ready_file(latest) if latest else None
    if not path:
        raise HTTPException(404, "No verified installer is ready.")
    return {"path": str(path), "sha256": latest["installer"]["sha256"], "version": latest["version"]}
