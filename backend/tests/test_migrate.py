"""Schema migrations (db/migrate.py): typed IDs, backups, dry run, refusal, rollback."""
import shutil
import sqlite3
from pathlib import Path

import pytest

from db import migrate

LEGACY_ITEMS = [
    (1, "anilist_16498", "anime"),
    (2, "anilist_16498", "person"),        # same number, different type: the old collision
    (1, "anilist_char_7", "character"),
    (1, "tmdb_movie_603", "movie"),
    (1, "tmdb_tv_1396", "tv"),
    (2, "tmdb_500", "person"),
    (1, "tmdb_char_5", "character"),
    (1, "igdb_1942", "game"),
    (2, "igdb_char_11", "character"),
]
TYPED_RESULT = sorted([
    "anime:anilist:16498", "person:anilist:16498", "character:anilist:7", "movie:tmdb:603",
    "tv:tmdb:1396", "person:tmdb:500", "character:tmdb:5", "game:igdb:1942", "character:igdb:11",
])


def _legacy_db(path: Path, version: int = 1) -> Path:
    """A database as it was before migration 002: initial schema, legacy item IDs."""
    conn = sqlite3.connect(path)
    conn.executescript((migrate.MIGRATIONS_DIR / "001_initial.sql").read_text(encoding="utf-8"))
    conn.execute("INSERT INTO Collection (id, slug, name, updated_at) VALUES "
                 "(1, 'favorites', 'Favorites', CURRENT_TIMESTAMP), (2, 'other', 'Other', CURRENT_TIMESTAMP)")
    for collection_id, item_id, item_type in LEGACY_ITEMS:
        conn.execute("INSERT INTO CollectionItem (collection_id, item_id, item_type, source, title) "
                     "VALUES (?, ?, ?, 'x', 'x')", (collection_id, item_id, item_type))
    conn.execute(f"PRAGMA user_version = {int(version)}")
    conn.commit()
    conn.close()
    return path


def _item_ids(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return sorted(r[0] for r in conn.execute("SELECT item_id FROM CollectionItem"))
    finally:
        conn.close()


def _version(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def test_legacy_ids_are_converted_using_their_type(tmp_path):
    db = _legacy_db(tmp_path / "lib.db")

    report = migrate.run_pending(db, tmp_path / "backups")

    assert report["from_version"] == 1
    assert report["applied"] == [2]
    assert _item_ids(db) == TYPED_RESULT
    assert _version(db) == 2


def test_running_again_is_a_no_op(tmp_path):
    db = _legacy_db(tmp_path / "lib.db")
    migrate.run_pending(db, tmp_path / "backups")

    report = migrate.run_pending(db, tmp_path / "backups")

    assert report["applied"] == [] and report["backup"] is None
    assert len(list((tmp_path / "backups").glob("*.db"))) == 1
    assert _item_ids(db) == TYPED_RESULT


def test_dry_run_changes_nothing(tmp_path):
    db = _legacy_db(tmp_path / "lib.db")

    report = migrate.run_pending(db, tmp_path / "backups", dry_run=True)

    assert report["pending"] == [2] and report["applied"] == []
    assert not (tmp_path / "backups").exists()
    assert "anilist_16498" in _item_ids(db)
    assert _version(db) == 1


def test_backup_is_made_first_and_keeps_the_legacy_data(tmp_path):
    db = _legacy_db(tmp_path / "lib.db")

    report = migrate.run_pending(db, tmp_path / "backups")

    backup = Path(report["backup"])
    assert backup.is_file() and backup.parent == tmp_path / "backups"
    assert "anilist_16498" in _item_ids(backup)
    assert _version(backup) == 1


def test_unconvertible_id_stops_the_migration_and_changes_nothing(tmp_path):
    db = _legacy_db(tmp_path / "lib.db")
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO CollectionItem (collection_id, item_id, item_type, source, title) "
                 "VALUES (1, 'weird_99', 'anime', 'x', 'x')")
    conn.commit()
    conn.close()

    with pytest.raises(migrate.MigrationError, match="cannot be converted"):
        migrate.run_pending(db, tmp_path / "backups")

    assert "anilist_16498" in _item_ids(db)
    assert _version(db) == 1


def test_status_reports_pending_and_unconvertible_rows(tmp_path):
    db = _legacy_db(tmp_path / "lib.db")
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO CollectionItem (collection_id, item_id, item_type, source, title) "
                 "VALUES (1, 'weird_99', 'anime', 'x', 'x')")
    conn.commit()
    conn.close()

    s = migrate.status(db)

    assert s["version"] == 1 and s["pending"] == [2]
    assert [row["item_id"] for row in s["unconvertible"]] == ["weird_99"]


def test_restoring_the_backup_is_the_rollback(tmp_path):
    db = _legacy_db(tmp_path / "lib.db")
    report = migrate.run_pending(db, tmp_path / "backups")

    shutil.copy2(report["backup"], db)  # the documented rollback: copy the backup back

    assert "anilist_16498" in _item_ids(db)
    assert _version(db) == 1


def test_fresh_database_is_created_at_the_current_version(tmp_path):
    db = tmp_path / "new.db"

    report = migrate.run_pending(db, tmp_path / "backups")

    assert report["applied"] == [1, 2]
    assert report["backup"] is None  # nothing existed to back up
    assert _version(db) == 2
    assert _item_ids(db) == []


def test_special_character_paths_keep_source_and_backup_counts_exact(tmp_path):
    folder = tmp_path / "literal %20 # Ω"
    folder.mkdir()
    db = _legacy_db(folder / "voice %20 # Ω.db")
    before_counts = migrate._row_counts(db)
    assert before_counts["Collection"] == 2
    assert before_counts["CollectionItem"] == len(LEGACY_ITEMS)
    report = migrate.run_pending(db, folder / "backup %20 # Ω")
    backup = Path(report["backup"])
    assert migrate._row_counts(backup) == before_counts
    assert migrate._row_counts(db) == before_counts
    assert _version(backup) == 1
    assert _version(db) == 2
    assert _item_ids(db) == TYPED_RESULT
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()) == sorted([
        db.relative_to(tmp_path), backup.relative_to(tmp_path),
    ])


def test_readonly_counts_never_create_a_missing_or_decoded_database(tmp_path):
    folder = tmp_path / "literal %20 # Ω"
    folder.mkdir()
    missing = folder / "missing %20 # Ω.db"
    with pytest.raises(sqlite3.OperationalError):
        migrate._row_counts(missing)
    assert not any(path.is_file() for path in tmp_path.rglob("*"))
