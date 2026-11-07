"""Update check: picking the newest release, the daily schedule, and the verified download."""
import asyncio
import hashlib

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.updates as up

PAYLOAD = b"MZ" + b"\x00" * 4096
SHA = hashlib.sha256(PAYLOAD).hexdigest()


def release(tag, prerelease=True, draft=False, digest=f"sha256:{SHA}", size=len(PAYLOAD)):
    version = tag.lstrip("v")
    return {"tag_name": tag, "name": f"VoiceVault {version}", "body": "What's new", "draft": draft, "prerelease": prerelease,
            "html_url": f"https://github.com/ArchAngelAries/VoiceVault/releases/tag/{tag}", "published_at": "2026-11-08T00:00:00Z",
            "assets": [{"name": f"VoiceVault-Setup-{version}.exe", "size": size, "digest": digest,
                        "browser_download_url": f"https://github.com/ArchAngelAries/VoiceVault/releases/download/{tag}/VoiceVault-Setup-{version}.exe"}]}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(up, "STATE_FILE", tmp_path / "updates.json")
    monkeypatch.setattr(up, "DOWNLOAD_DIR", tmp_path / "updates")
    monkeypatch.setattr(up, "APP_VERSION", "0.1.1")
    monkeypatch.setattr(up, "_download", {"state": "idle"})
    monkeypatch.setattr(up, "_verified", {})
    settings = {"update_check": True}
    monkeypatch.setattr(up, "get_setting", lambda key, default=None: settings.get(key, default))
    calls = []

    async def fake_fetch(client=None):
        calls.append(1)
        return [release("v0.1.1"), release("v0.3.0", draft=True), release("v0.2.0"), release("v0.1.0")]
    monkeypatch.setattr(up, "fetch_releases", fake_fetch)
    app = FastAPI()
    app.include_router(up.router, prefix="/api/updates")
    c = TestClient(app)
    c.calls, c.settings = calls, settings
    return c


def test_versions_and_newest_release():
    assert up.parse_version("v0.2.0") == (0, 2, 0) and up.parse_version("0.10.1-beta") == (0, 10, 1)
    assert up.parse_version("nightly") is None
    releases = [release("v0.1.1"), release("v0.3.0", draft=True), release("v0.2.0"), release("v0.10.0")]
    assert up.newest(releases, "0.1.1")["tag_name"] == "v0.10.0"      # numeric, not alphabetical
    assert up.newest([release("v0.1.1")], "0.1.1") is None
    assert up.summarise(release("v0.2.0", digest="md5:abc"))["installer"] is None   # no SHA-256, no in-app download


def test_auto_check_runs_once_a_day_and_respects_the_setting(client):
    first = client.get("/api/updates/status", params={"auto": 1}).json()
    assert first["update_available"] and first["latest"]["version"] == "0.2.0"
    assert first["latest"]["installer"]["sha256"] == SHA and "path" not in str(first["download"])
    client.get("/api/updates/status", params={"auto": 1})
    assert len(client.calls) == 1                         # cached for a day
    client.post("/api/updates/check")
    assert len(client.calls) == 2                         # "Check now" always asks
    client.settings["update_check"] = False
    up.STATE_FILE.unlink()
    off = client.get("/api/updates/status", params={"auto": 1}).json()
    assert len(client.calls) == 2 and not off["update_available"] and off["enabled"] is False


def test_skip_and_catching_up(client, monkeypatch):
    client.post("/api/updates/check")
    assert client.post("/api/updates/skip", json={"version": "0.2.0"}).json()["skipped"] is True
    assert client.get("/api/updates/status").json()["skipped"] is True
    assert client.post("/api/updates/check").json()["skipped"] is False      # asking by hand shows it again
    monkeypatch.setattr(up, "APP_VERSION", "0.2.0")       # after installing, the banner goes away
    assert client.get("/api/updates/status").json()["update_available"] is False


class FakeGitHub:
    """GitHub's download: a redirect to the asset host, then the file."""
    def __init__(self, body=PAYLOAD, redirect="https://release-assets.githubusercontent.com/file"):
        self.body, self.redirect = body, redirect

    def __call__(self, request):
        if request.url.host == "github.com":
            return httpx.Response(302, headers={"location": self.redirect})
        return httpx.Response(200, content=self.body)


def run_download(handler, tmp_path, monkeypatch, size=len(PAYLOAD)):
    monkeypatch.setattr(up, "DOWNLOAD_DIR", tmp_path / "updates")
    monkeypatch.setattr(up, "_download", {"state": "idle"})
    info = up.summarise(release("v0.2.0", size=size))["installer"]

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await up.download_installer(info, "0.2.0", client)
    asyncio.run(go())
    return up._download, tmp_path / "updates" / "VoiceVault-Setup-0.2.0.exe"


def test_download_keeps_only_a_verified_installer(tmp_path, monkeypatch):
    state, path = run_download(FakeGitHub(), tmp_path, monkeypatch)
    assert state["state"] == "ready" and path.read_bytes() == PAYLOAD


def test_tampered_download_is_deleted(tmp_path, monkeypatch):
    state, path = run_download(FakeGitHub(body=b"MZ" + b"\x01" * 4096), tmp_path, monkeypatch)
    assert state["state"] == "error" and "SHA-256" in state["error"]
    assert not path.exists() and not path.with_name(path.name + ".part").exists()


def test_redirect_away_from_github_is_refused(tmp_path, monkeypatch):
    state, path = run_download(FakeGitHub(redirect="https://evil.example/file.exe"), tmp_path, monkeypatch)
    assert state["state"] == "error" and "GitHub" in state["error"] and not path.exists()


def test_oversized_download_stops(tmp_path, monkeypatch):
    state, path = run_download(FakeGitHub(body=PAYLOAD + b"extra"), tmp_path, monkeypatch)
    assert state["state"] == "error" and not path.exists()


def test_installer_endpoint_only_answers_for_a_verified_file(client, tmp_path):
    client.post("/api/updates/check")
    assert client.get("/api/updates/installer").status_code == 404
    (up.DOWNLOAD_DIR).mkdir(parents=True, exist_ok=True)
    (up.DOWNLOAD_DIR / "VoiceVault-Setup-0.2.0.exe").write_bytes(PAYLOAD)
    ready = client.get("/api/updates/installer").json()
    assert ready["sha256"] == SHA and ready["path"].endswith("VoiceVault-Setup-0.2.0.exe")
    assert client.get("/api/updates/status").json()["download"]["state"] == "ready"
