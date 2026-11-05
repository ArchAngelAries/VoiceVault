"""
db/client.py — Prisma async database client + startup helpers

Set VOICEVAULT_DB_URL (e.g. "file:C:/tmp/test.db") to point the client at a
different SQLite file. The tests use this so they never touch the real library.
"""
import json, os, re, logging
from prisma import Prisma
from core import paths

_db: Prisma | None = None


async def get_db() -> Prisma:
    global _db
    if _db is None:
        url = os.environ.get("VOICEVAULT_DB_URL")
        if url:
            _db = Prisma(datasource={"url": paths.database_url()})
        elif paths.STATE_DIR is not None:
            # Match the path used by startup migrations when state relocation is enabled.
            _db = Prisma(datasource={"url": paths.database_url()})
        else:
            # Preserve Prisma's schema-relative default when the contract is unused.
            _db = Prisma()
        await _db.connect()
    return _db


async def disconnect():
    global _db
    if _db:
        await _db.disconnect()
        _db = None


async def ensure_defaults():
    """Create built-in system collections if they don't exist yet."""
    db = await get_db()
    defaults = [
        {"slug": "favorites",  "name": "Favorites",  "is_system": True,  "sort_order": 0},
        {"slug": "want-to-grab","name": "Want to Grab","is_system": True, "sort_order": 1},
        {"slug": "have-clips",  "name": "Have Clips", "is_system": True,  "sort_order": 2},
    ]
    for d in defaults:
        existing = await db.collection.find_unique(where={"slug": d["slug"]})
        if not existing:
            await db.collection.create(data=d)
            logging.info(f"Created system collection: {d['name']}")


def slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r'[^\w\s-]', '', s)
    s = re.sub(r'[\s_-]+', '-', s)
    return s[:60]
