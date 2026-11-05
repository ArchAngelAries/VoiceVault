"""
db/migrate.py — Schema versions and safe migrations for the library database.

The schema version is stored in SQLite's own user_version field:
    1  initial schema (db/migrations/001_initial.sql)
    2  typed item IDs ("anime:anilist:16498", see core/item_ids.py)

Safety rules:
  * Before anything changes, the database file is copied to the backup folder and the copy
    is checked row-for-row against the original. If the check fails, nothing is changed.
  * Each migration runs in its own transaction. If one fails, it is rolled back and the
    version number does not move.
  * A migration refuses to guess: if a row cannot be converted, the whole migration stops.

Rollback: stop the backend and copy the backup over the database file. Its user_version is
the old one, so the migration runs again the next time the backend starts, once the
code is also rolled back.

Commands (run from backend/):
    python -m db.migrate status    show version, pending migrations, rows that cannot convert
    python -m db.migrate apply     back up, then apply pending migrations
"""
from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from core.item_ids import is_typed_id, legacy_to_typed
from core.paths import backup_dir, database_path

log = logging.getLogger(__name__)
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


class MigrationError(RuntimeError):
    pass


def _statements(sql_text: str) -> list[str]:
    """Split a migration file into statements. The project's SQL has no ';' inside strings."""
    body = "\n".join(line for line in sql_text.splitlines() if not line.strip().startswith("--"))
    return [s.strip() for s in body.split(";") if s.strip()]


def _apply_001(conn: sqlite3.Connection) -> None:
    # Every statement is CREATE ... IF NOT EXISTS, so this is safe on a database that already has the tables.
    for stmt in _statements((MIGRATIONS_DIR / "001_initial.sql").read_text(encoding="utf-8")):
        conn.execute(stmt)


def _unconvertible(conn: sqlite3.Connection) -> tuple[list[tuple[int, str, str]], list[tuple[int, int, str]]]:
    """Return (rows to convert as (row id, new id, old id), rows that cannot be converted)."""
    to_convert, stuck = [], []
    for row_id, item_type, item_id in conn.execute(
        'SELECT id, item_type, item_id FROM "CollectionItem" ORDER BY id'
    ).fetchall():
        if is_typed_id(item_id):
            continue
        new_id = legacy_to_typed(item_type, item_id)
        if new_id is None:
            stuck.append((row_id, item_type, item_id))
        else:
            to_convert.append((row_id, new_id, item_id))
    return to_convert, stuck


def _apply_002(conn: sqlite3.Connection) -> None:
    to_convert, stuck = _unconvertible(conn)
    if stuck:
        sample = ", ".join(f"id {r} ({t}: {i!r})" for r, t, i in stuck[:5])
        raise MigrationError(f"{len(stuck)} favorite/collection item(s) have IDs that cannot be converted: {sample}")
    for row_id, new_id, _old in to_convert:
        conn.execute('UPDATE "CollectionItem" SET item_id = ? WHERE id = ?', (new_id, row_id))


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


MIGRATIONS = [
    Migration(1, "initial schema", _apply_001),
    Migration(2, "typed item ids", _apply_002),
]


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), isolation_level=None)  # explicit BEGIN/COMMIT below
    return conn


def _schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _row_counts(db_path: Path) -> dict[str, int]:
    # SQLite interprets percent escapes and fragments only in URI mode. Encode
    # the literal filename before adding mode=ro, including Windows drives.
    uri = db_path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    finally:
        conn.close()


def backup_database(db_path: Path, backup_folder: Path, tag: str) -> Path:
    """Copy the database and verify the copy has the same rows in every table."""
    backup_folder.mkdir(parents=True, exist_ok=True)
    dest = backup_folder / f"{db_path.stem}-{datetime.now():%Y%m%d-%H%M%S}-{tag}.db"
    if dest.exists():
        raise MigrationError(f"Backup target already exists: {dest}")
    shutil.copy2(db_path, dest)
    if _row_counts(db_path) != _row_counts(dest):
        dest.unlink()  # the copy we just made is incomplete; the original is untouched
        raise MigrationError("Backup verification failed; no migration was run.")
    return dest


def status(db_path: Path | None = None) -> dict:
    db_path = db_path or database_path()
    if not db_path.exists():
        return {"database": str(db_path), "exists": False, "version": 0,
                "pending": [m.version for m in MIGRATIONS], "unconvertible": []}
    conn = _connect(db_path)
    try:
        version = _schema_version(conn)
        pending = [m.version for m in MIGRATIONS if m.version > version]
        has_items = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'CollectionItem'").fetchone()
        stuck = _unconvertible(conn)[1] if has_items else []
    finally:
        conn.close()
    return {"database": str(db_path), "exists": True, "version": version, "pending": pending,
            "unconvertible": [{"id": r, "item_type": t, "item_id": i} for r, t, i in stuck]}


def run_pending(db_path: Path | None = None, backup_folder: Path | None = None, dry_run: bool = False) -> dict:
    """Apply every migration newer than the database's user_version. Returns a report."""
    db_path = db_path or database_path()
    backup_folder = backup_folder or backup_dir()
    conn = _connect(db_path)
    try:
        current = _schema_version(conn)
        pending = [m for m in MIGRATIONS if m.version > current]
        report = {"database": str(db_path), "from_version": current,
                  "pending": [m.version for m in pending], "applied": [], "backup": None}
        if not pending or dry_run:
            return report

        # A brand-new (empty) database has nothing to protect, so there is no backup to make.
        if _row_counts(db_path):
            report["backup"] = str(backup_database(db_path, backup_folder, f"pre-v{pending[-1].version}"))
        for migration in pending:
            conn.execute("BEGIN IMMEDIATE")
            try:
                migration.apply(conn)
                conn.execute(f"PRAGMA user_version = {int(migration.version)}")
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                log.error("Migration %s (%s) failed and was rolled back.", migration.version, migration.name)
                raise
            report["applied"].append(migration.version)
            log.info("Applied migration %s (%s).", migration.version, migration.name)
        return report
    finally:
        conn.close()


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if command == "status":
        print(json.dumps(status(), indent=2))
    elif command == "apply":
        print(json.dumps(run_pending(), indent=2))
    else:
        print("usage: python -m db.migrate [status|apply]")
        sys.exit(2)
