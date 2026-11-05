"""
Shared fixtures. Each test gets a fresh SQLite database built from the project's own migration
files, so the real library in backend/db/voicevault.db is never touched.
"""
import os
import sqlite3

os.environ.setdefault("VOICEVAULT_ALLOWED_HOSTS", "testserver,test")
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

BACKEND = Path(__file__).resolve().parent.parent
MIGRATION = BACKEND / "db" / "migrations" / "001_initial.sql"


@pytest.fixture(autouse=True)
def isolated_discovery_sidecar(tmp_path, monkeypatch):
    # Discovery now persists independently of Prisma. Keep every API fixture out
    # of the user's real sidecar and prevent cache hits from crossing tests.
    from core import discovery_retention
    from core.discovery_snapshots import DiscoverySnapshotStore
    monkeypatch.setattr(discovery_retention, '_DEFAULT_STORE', DiscoverySnapshotStore(tmp_path))


@pytest.fixture
def db_file(tmp_path, monkeypatch):
    from db.migrate import run_pending

    path = tmp_path / "test.db"
    backups = tmp_path / "backups"
    monkeypatch.setenv("VOICEVAULT_DB_URL", "file:" + path.as_posix())
    monkeypatch.setenv("VOICEVAULT_BACKUP_DIR", str(backups))
    conn = sqlite3.connect(path)
    conn.executescript(MIGRATION.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()
    run_pending(path, backups)  # bring the fixture database to the current schema version
    return path


@pytest_asyncio.fixture
async def db(db_file):
    import db.client as dc

    dc._db = None
    await dc.ensure_defaults()  # creates the three system collections
    yield dc
    await dc.disconnect()


@pytest_asyncio.fixture
async def api(db):
    from main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
