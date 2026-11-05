"""Small, durable local store for successful discovery snapshots.

This module deliberately knows nothing about provider clients or scheduling. It
is a lazy, standard-library SQLite sidecar that callers may use after they
have validated an upstream response.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qsl, urlsplit

from .paths import DATA_DIR


_SCHEMA_VERSION = 1
_SENSITIVE_KEY = re.compile(r"(?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|secret|credential|password|headers?|oauth)", re.I)
_CREDENTIAL_URL = re.compile(r"https?://[^\s/@]+:[^\s/@]+@", re.I)
_SENSITIVE_QUERY = re.compile(r"(?:api.?key|access.?token|refresh.?token|token|secret|credential|signature|authorization|oauth|^key$)", re.I)
_MAX_ID = 300
_MAX_FIELD = 300
_MAX_SEARCH_LIMIT = 200


class SnapshotStoreError(RuntimeError):
    """Base class for safe, user-reportable snapshot storage failures."""


class SnapshotValidationError(SnapshotStoreError, ValueError):
    """Input is not safe or does not fit the configured bounds."""


class SnapshotCapacityError(SnapshotStoreError):
    """The optional cache cannot retain this item within its byte budget."""


class SnapshotDatabaseError(SnapshotStoreError):
    """The sidecar could not be used even after a recovery attempt."""


def _now() -> float:
    return time.time()


def _dump(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise SnapshotValidationError(f"Value must be finite, valid JSON data: {exc}") from exc


def _safe_json(value: Any, *, where: str = "value") -> Any:
    """Reject secrets and credential-bearing URLs instead of silently persisting them."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > _MAX_FIELD:
                raise SnapshotValidationError(f"{where} has an invalid field name")
            if _SENSITIVE_KEY.search(key):
                raise SnapshotValidationError(f"{where} contains a credential-like field")
            result[key] = _safe_json(item, where=where)
        return result
    if isinstance(value, (list, tuple)):
        return [_safe_json(item, where=where) for item in value]
    if isinstance(value, str):
        credential_url = bool(_CREDENTIAL_URL.search(value))
        if value.lower().startswith(("http://", "https://")):
            try:
                parts = urlsplit(value)
                credential_url = credential_url or bool(parts.username or parts.password) or any(
                    _SENSITIVE_QUERY.search(key) for key, _ in parse_qsl(parts.query, keep_blank_values=True)
                )
            except ValueError:
                credential_url = True
        if credential_url:
            raise SnapshotValidationError(f"{where} contains a credential-bearing URL")
        return value
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise SnapshotValidationError(f"{where} contains a value that cannot be stored as JSON")


def _required_id(value: str, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_ID or "\x00" in value:
        raise SnapshotValidationError(f"{name} must be a non-empty string of at most {_MAX_ID} characters")
    return value


def _scope_hash(scope: str | None) -> str:
    if scope is None:
        scope = ""
    if not isinstance(scope, str) or len(scope) > 2048:
        raise SnapshotValidationError("account_scope must be a string of at most 2048 characters")
    return hashlib.sha256(scope.encode("utf-8")).hexdigest()


def _merge_sparse(old: Any, new: Any) -> Any:
    """Merge provider metadata without letting sparse omissions erase detail."""
    if isinstance(old, dict) and isinstance(new, dict):
        merged = dict(old)
        for key, value in new.items():
            if value is None or value == {} or value == []:
                continue
            merged[key] = _merge_sparse(merged.get(key), value) if key in merged else value
        return merged
    return new


class DiscoverySnapshotStore:
    """Lazy SQLite storage for exact response pages and normalized entities.

    `data_dir` is injectable for tests. No filesystem access occurs until the
    first method call. Account scopes are hashed before storage.
    """

    def __init__(self, data_dir: str | os.PathLike[str] | None = None, *, max_requests: int = 256,
                 max_bytes: int = 32 * 1024 * 1024, max_entry_bytes: int = 2 * 1024 * 1024,
                 busy_timeout_ms: int = 5000):
        if max_requests < 1 or max_bytes < 1 or max_entry_bytes < 1 or busy_timeout_ms < 1:
            raise ValueError("snapshot storage limits must be positive")
        self.data_dir = Path(data_dir) if data_dir is not None else Path(DATA_DIR)
        self.max_requests = int(max_requests)
        self.max_bytes = int(max_bytes)
        self.max_entry_bytes = int(max_entry_bytes)
        self.busy_timeout_ms = int(busy_timeout_ms)
        self._lock = threading.RLock()
        self._initialized = False
        self._availability_error: str | None = None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "discovery" / "snapshots.sqlite3"

    @property
    def availability_error(self) -> str | None:
        return self._availability_error

    def _connect(self) -> sqlite3.Connection:
        path = self.db_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SnapshotDatabaseError(f"Could not create discovery cache directory: {exc}") from exc
        if path.is_symlink() or path.parent.is_symlink():
            raise SnapshotDatabaseError("Discovery snapshot path cannot be a symbolic link")
        conn = sqlite3.connect(path, timeout=self.busy_timeout_ms / 1000, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            self._ensure_schema(conn)
        except (sqlite3.DatabaseError, SnapshotDatabaseError) as exc:
            conn.close()
            if isinstance(exc, SnapshotDatabaseError) or self._looks_corrupt(exc):
                self._quarantine()
                conn = sqlite3.connect(path, timeout=self.busy_timeout_ms / 1000, isolation_level=None)
                conn.row_factory = sqlite3.Row
                conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
                conn.execute("PRAGMA foreign_keys=ON")
                try:
                    self._ensure_schema(conn, force_new=True)
                except sqlite3.DatabaseError as retry_exc:
                    conn.close()
                    raise SnapshotDatabaseError(f"Could not initialize discovery snapshot database: {retry_exc}") from retry_exc
            else:
                raise SnapshotDatabaseError(f"Could not initialize discovery snapshot database: {exc}") from exc
        self._initialized = True
        return conn

    @staticmethod
    def _looks_corrupt(exc: BaseException) -> bool:
        text = str(exc).lower()
        return any(word in text for word in ("malformed", "not a database", "disk image", "file is encrypted", "no such table"))

    def _quarantine(self) -> None:
        path = self.db_path
        stamp = f"{int(_now())}-{uuid.uuid4().hex[:8]}"
        found = False
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(path) + suffix)
            if candidate.exists():
                found = True
                try:
                    candidate.replace(candidate.with_name(candidate.name + f".quarantine-{stamp}"))
                except OSError as exc:
                    self._availability_error = f"Corrupt discovery database could not be quarantined: {exc}"
                    raise SnapshotDatabaseError(self._availability_error) from exc
        self._availability_error = "An incompatible or corrupt discovery cache was preserved in quarantine and rebuilt."
        if not found:
            raise SnapshotDatabaseError("Discovery database could not be recovered")

    def _ensure_schema(self, conn: sqlite3.Connection, *, force_new: bool = False) -> None:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version > _SCHEMA_VERSION:
            raise SnapshotDatabaseError(f"Unsupported discovery snapshot schema version {version}")
        if version == _SCHEMA_VERSION:
            # Fail promptly if a database claims this schema but is incomplete.
            conn.execute("SELECT 1 FROM request_snapshots LIMIT 1").fetchone()
            conn.execute("SELECT 1 FROM entities LIMIT 1").fetchone()
            return
        if version != 0 or (not force_new and self.db_path.exists() and self.db_path.stat().st_size > 0):
            raise SnapshotDatabaseError(f"Unsupported discovery snapshot schema version {version}")
        conn.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE request_snapshots (
                cache_key TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                operation TEXT NOT NULL,
                scope_hash TEXT NOT NULL,
                params_json TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                fetched_at REAL NOT NULL,
                checked_at REAL NOT NULL,
                refresh_due_at REAL,
                schema_version INTEGER NOT NULL,
                validators_json TEXT NOT NULL,
                byte_cost INTEGER NOT NULL,
                last_used_at REAL NOT NULL
            );
            CREATE INDEX request_lru ON request_snapshots(last_used_at);
            CREATE TABLE entities (
                provider TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                coverage_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                fetched_at REAL NOT NULL,
                byte_cost INTEGER NOT NULL,
                last_used_at REAL NOT NULL,
                PRIMARY KEY(provider, entity_type, source_id)
            );
            CREATE INDEX entity_lru ON entities(last_used_at);
            CREATE TABLE entity_protection (
                provider TEXT NOT NULL, entity_type TEXT NOT NULL, source_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                PRIMARY KEY(provider, entity_type, source_id, reason)
            );
            CREATE TABLE request_entities (
                cache_key TEXT NOT NULL, provider TEXT NOT NULL, entity_type TEXT NOT NULL, source_id TEXT NOT NULL,
                PRIMARY KEY(cache_key, provider, entity_type, source_id),
                FOREIGN KEY(cache_key) REFERENCES request_snapshots(cache_key) ON DELETE CASCADE
            );
            CREATE INDEX request_entity_lookup ON request_entities(provider, entity_type, source_id);
            CREATE TABLE entity_overrides (
                provider TEXT NOT NULL, entity_type TEXT NOT NULL, source_id TEXT NOT NULL,
                overrides_json TEXT NOT NULL, updated_at REAL NOT NULL,
                PRIMARY KEY(provider, entity_type, source_id)
            );
            CREATE TABLE provider_state (
                provider TEXT NOT NULL, scope_hash TEXT NOT NULL, next_allowed_at REAL,
                error TEXT, error_at REAL, updated_at REAL NOT NULL,
                PRIMARY KEY(provider, scope_hash)
            );
            PRAGMA user_version=1;
            COMMIT;
        """)

    def _transaction(self):
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        return conn

    @staticmethod
    def _key(provider: str, operation: str, params_json: str, scope_hash: str) -> str:
        raw = _dump([provider, operation, params_json, scope_hash]).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _usage(self, conn: sqlite3.Connection) -> int:
        a = conn.execute("SELECT COALESCE(SUM(byte_cost),0) FROM request_snapshots").fetchone()[0]
        b = conn.execute("SELECT COALESCE(SUM(byte_cost),0) FROM entities").fetchone()[0]
        return int(a) + int(b)

    def _gc_entities(self, conn: sqlite3.Connection) -> int:
        cur = conn.execute("""
            DELETE FROM entities WHERE NOT EXISTS (
                SELECT 1 FROM entity_protection p WHERE p.provider=entities.provider AND p.entity_type=entities.entity_type AND p.source_id=entities.source_id
            ) AND NOT EXISTS (
                SELECT 1 FROM request_entities r WHERE r.provider=entities.provider AND r.entity_type=entities.entity_type AND r.source_id=entities.source_id
            ) AND NOT EXISTS (
                SELECT 1 FROM entity_overrides o WHERE o.provider=entities.provider AND o.entity_type=entities.entity_type AND o.source_id=entities.source_id
            )
        """)
        return cur.rowcount

    def _evict_for(self, conn: sqlite3.Connection, additional: int, *, request_limit: int | None = None,
                   exclude_entity: tuple[str, str, str] | None = None) -> None:
        target_requests = request_limit if request_limit is not None else self.max_requests
        while (self._usage(conn) + additional > self.max_bytes or
               conn.execute("SELECT COUNT(*) FROM request_snapshots").fetchone()[0] > target_requests):
            row = conn.execute("SELECT cache_key FROM request_snapshots ORDER BY last_used_at ASC, cache_key ASC LIMIT 1").fetchone()
            if row is None:
                break
            conn.execute("DELETE FROM request_snapshots WHERE cache_key=?", (row[0],))
            self._gc_entities(conn)
        while self._usage(conn) + additional > self.max_bytes:
            exclude_sql = " AND NOT (e.provider=? AND e.entity_type=? AND e.source_id=?)" if exclude_entity else ""
            entity_args: tuple[str, ...] = exclude_entity or ()
            row = conn.execute("""
                SELECT e.provider,e.entity_type,e.source_id FROM entities e
                WHERE NOT EXISTS (SELECT 1 FROM entity_protection p WHERE p.provider=e.provider AND p.entity_type=e.entity_type AND p.source_id=e.source_id)
                  AND NOT EXISTS (SELECT 1 FROM request_entities r WHERE r.provider=e.provider AND r.entity_type=e.entity_type AND r.source_id=e.source_id)
                  AND NOT EXISTS (SELECT 1 FROM entity_overrides o WHERE o.provider=e.provider AND o.entity_type=e.entity_type AND o.source_id=e.source_id)
            """ + exclude_sql + " ORDER BY e.last_used_at ASC LIMIT 1", entity_args).fetchone()
            if row is None:
                break
            conn.execute("DELETE FROM entities WHERE provider=? AND entity_type=? AND source_id=?", tuple(row))
        if self._usage(conn) + additional > self.max_bytes:
            raise SnapshotCapacityError("Discovery cache is at its byte budget; protected and referenced records were kept")

    def get_request(self, provider: str, operation: str, params: Any, account_scope: str | None = None,
                    *, schema_version: int | None = None) -> dict[str, Any] | None:
        provider = _required_id(provider, "provider")
        operation = _required_id(operation, "operation")
        clean = _safe_json(params, where="request parameters")
        params_json = _dump(clean)
        scope = _scope_hash(account_scope)
        key = self._key(provider, operation, params_json, scope)
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT * FROM request_snapshots WHERE cache_key=?", (key,)).fetchone()
                if row is None:
                    return None
                if schema_version is not None and row["schema_version"] != schema_version:
                    return None
                conn.execute("UPDATE request_snapshots SET last_used_at=? WHERE cache_key=?", (_now(), key))
                payload = json.loads(row["payload_json"])
                if hashlib.sha256(row["payload_json"].encode("utf-8")).hexdigest() != row["payload_sha256"]:
                    return None
                return {"provider": row["provider"], "operation": row["operation"], "params": clean,
                        "payload": payload, "payload_sha256": row["payload_sha256"], "fetched_at": row["fetched_at"],
                        "checked_at": row["checked_at"], "refresh_due_at": row["refresh_due_at"],
                        "schema_version": row["schema_version"], "byte_cost": row["byte_cost"],
                        "content_hash": row["payload_sha256"], "last_accessed": _now(),
                        "validators": json.loads(row["validators_json"])}
            finally:
                conn.close()

    def remove_request(self, provider: str, operation: str, params: Any, account_scope: str | None = None) -> bool:
        """Remove one exact request snapshot, for provider no-store responses."""
        provider = _required_id(provider, "provider")
        operation = _required_id(operation, "operation")
        clean = _safe_json(params, where="request parameters")
        key = self._key(provider, operation, _dump(clean), _scope_hash(account_scope))
        with self._lock:
            conn = self._transaction()
            try:
                removed = conn.execute("DELETE FROM request_snapshots WHERE cache_key=?", (key,)).rowcount > 0
                self._gc_entities(conn)
                conn.commit()
                return removed
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def put_request(self, provider: str, operation: str, params: Any, payload: Any, *, account_scope: str | None = None,
                    fetched_at: float | None = None, checked_at: float | None = None,
                    refresh_due_at: float | None = None, schema_version: int = 1,
                    entity_refs: Iterable[tuple[str, str]] = (), validators: dict[str, Any] | None = None) -> dict[str, Any]:
        provider = _required_id(provider, "provider")
        operation = _required_id(operation, "operation")
        params = _safe_json(params, where="request parameters")
        payload = _safe_json(payload, where="response payload")
        params_json, payload_json = _dump(params), _dump(payload)
        validators = _safe_json(validators or {}, where="snapshot validators")
        if not isinstance(validators, dict):
            raise SnapshotValidationError("validators must be an object")
        validators_json = _dump(validators)
        size = len(params_json.encode()) + len(payload_json.encode()) + len(validators_json.encode())
        if size > self.max_entry_bytes:
            raise SnapshotValidationError(f"Snapshot entry exceeds {self.max_entry_bytes} byte limit")
        if not isinstance(schema_version, int) or schema_version < 1:
            raise SnapshotValidationError("schema_version must be a positive integer")
        timestamp = _now()
        fetched = timestamp if fetched_at is None else float(fetched_at)
        checked = timestamp if checked_at is None else float(checked_at)
        due = None if refresh_due_at is None else float(refresh_due_at)
        if not all(math.isfinite(x) for x in (fetched, checked, *(() if due is None else (due,)))):
            raise SnapshotValidationError("snapshot timestamps must be finite")
        scope = _scope_hash(account_scope)
        key = self._key(provider, operation, params_json, scope)
        digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        refs = list(dict.fromkeys((_required_id(t, "entity_type"), _required_id(i, "source_id")) for t, i in entity_refs))
        now = timestamp
        with self._lock:
            conn = self._transaction()
            try:
                old = conn.execute("SELECT payload_sha256,fetched_at FROM request_snapshots WHERE cache_key=?", (key,)).fetchone()
                keep_fetched = old["fetched_at"] if old and old["payload_sha256"] == digest else fetched
                additional = size
                if old:
                    additional -= int(conn.execute("SELECT byte_cost FROM request_snapshots WHERE cache_key=?", (key,)).fetchone()[0])
                self._evict_for(conn, max(0, additional), request_limit=self.max_requests - (0 if old else 1))
                conn.execute("""INSERT INTO request_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(cache_key) DO UPDATE SET params_json=excluded.params_json,payload_json=excluded.payload_json,
                    payload_sha256=excluded.payload_sha256,fetched_at=excluded.fetched_at,checked_at=excluded.checked_at,
                    refresh_due_at=excluded.refresh_due_at,schema_version=excluded.schema_version,validators_json=excluded.validators_json,
                    byte_cost=excluded.byte_cost,last_used_at=excluded.last_used_at""",
                    (key, provider, operation, scope, params_json, payload_json, digest, keep_fetched, checked, due, schema_version, validators_json, size, now))
                conn.execute("DELETE FROM request_entities WHERE cache_key=?", (key,))
                for entity_type, source_id in refs:
                    conn.execute("INSERT INTO request_entities VALUES(?,?,?,?)", (key, provider, entity_type, source_id))
                conn.commit()
                return {"provider": provider, "operation": operation, "params": params, "payload": payload,
                        "payload_sha256": digest, "fetched_at": keep_fetched, "checked_at": checked,
                        "refresh_due_at": due, "schema_version": schema_version, "byte_cost": size,
                        "content_hash": digest, "last_accessed": now, "validators": validators}
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def put_entity(self, provider: str, entity_type: str, source_id: str, metadata: dict[str, Any], *,
                   coverage: Iterable[str] | None = None, fetched_at: float | None = None,
                   _protection_reason: str | None = None) -> dict[str, Any]:
        provider, entity_type, source_id = (_required_id(provider, "provider"), _required_id(entity_type, "entity_type"), _required_id(source_id, "source_id"))
        incoming = _safe_json(metadata, where="entity metadata")
        if not isinstance(incoming, dict):
            raise SnapshotValidationError("entity metadata must be an object")
        coverage_values = _safe_json(list(coverage or []), where="coverage")
        if not all(isinstance(field, str) and len(field) <= _MAX_FIELD for field in coverage_values):
            raise SnapshotValidationError("coverage must contain short field names")
        incoming_coverage = _dump(sorted(set(coverage_values)))
        fetched = _now() if fetched_at is None else float(fetched_at)
        if not math.isfinite(fetched):
            raise SnapshotValidationError("fetched_at must be finite")
        now = _now()
        if _protection_reason is not None:
            _protection_reason = _required_id(_protection_reason, "protection reason")
        with self._lock:
            conn = self._transaction()
            try:
                old = conn.execute("SELECT metadata_json,coverage_json,byte_cost FROM entities WHERE provider=? AND entity_type=? AND source_id=?", (provider, entity_type, source_id)).fetchone()
                merged = _merge_sparse(json.loads(old[0]), incoming) if old else incoming
                old_coverage = set(json.loads(old[1])) if old else set()
                merged_coverage = sorted(old_coverage | set(coverage_values))
                metadata_json, coverage_json = _dump(merged), _dump(merged_coverage)
                size = len(metadata_json.encode()) + len(coverage_json.encode())
                if size > self.max_entry_bytes:
                    raise SnapshotValidationError(f"Entity entry exceeds {self.max_entry_bytes} byte limit")
                digest = hashlib.sha256(metadata_json.encode()).hexdigest()
                delta = size - (int(old[2]) if old else 0)
                temporary_reason = f"__snapshot_update__:{uuid.uuid4().hex}" if old else None
                if old:
                    # Keep the merge base out of LRU eviction until the update
                    # commits; other references must never observe it vanishing.
                    conn.execute("INSERT INTO entity_protection VALUES(?,?,?,?)", (provider, entity_type, source_id, temporary_reason))
                try:
                    self._evict_for(conn, max(0, delta), exclude_entity=(provider, entity_type, source_id) if old else None)
                except SnapshotCapacityError:
                    if _protection_reason is None:
                        raise
                if old:
                    conn.execute("DELETE FROM entity_protection WHERE provider=? AND entity_type=? AND source_id=? AND reason=?", (provider, entity_type, source_id, temporary_reason))
                conn.execute("""INSERT INTO entities VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(provider,entity_type,source_id)
                    DO UPDATE SET metadata_json=excluded.metadata_json,coverage_json=excluded.coverage_json,
                    payload_sha256=excluded.payload_sha256,fetched_at=excluded.fetched_at,byte_cost=excluded.byte_cost,last_used_at=excluded.last_used_at""",
                    (provider, entity_type, source_id, metadata_json, coverage_json, digest, fetched, size, now))
                if _protection_reason is not None:
                    conn.execute("INSERT OR IGNORE INTO entity_protection VALUES(?,?,?,?)", (provider, entity_type, source_id, _protection_reason))
                conn.commit()
                return {"provider": provider, "entity_type": entity_type, "source_id": source_id, "metadata": merged,
                        "coverage": merged_coverage, "payload_sha256": digest, "fetched_at": fetched, "byte_cost": size}
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def retain_entity(self, provider: str, entity_type: str, source_id: str, metadata: dict[str, Any], reason: str, *,
                      coverage: Iterable[str] | None = None, fetched_at: float | None = None) -> dict[str, Any]:
        """Atomically merge an entity and protect it from concurrent LRU eviction."""
        return self.put_entity(provider, entity_type, source_id, metadata, coverage=coverage,
                               fetched_at=fetched_at, _protection_reason=reason)

    def get_entity(self, provider: str, entity_type: str, source_id: str) -> dict[str, Any] | None:
        keys = tuple(_required_id(x, n) for x, n in ((provider, "provider"), (entity_type, "entity_type"), (source_id, "source_id")))
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT * FROM entities WHERE provider=? AND entity_type=? AND source_id=?", keys).fetchone()
                if row is None:
                    return None
                conn.execute("UPDATE entities SET last_used_at=? WHERE provider=? AND entity_type=? AND source_id=?", (_now(), *keys))
                return {"provider": row["provider"], "entity_type": row["entity_type"], "source_id": row["source_id"],
                        "metadata": json.loads(row["metadata_json"]), "coverage": json.loads(row["coverage_json"]),
                        "payload_sha256": row["payload_sha256"], "fetched_at": row["fetched_at"], "byte_cost": row["byte_cost"]}
            finally:
                conn.close()

    def search_local(self, *, provider: str | None = None, entity_type: str | None = None, query: str | None = None,
                     offset: int = 0, limit: int = 50) -> dict[str, Any]:
        if not isinstance(offset, int) or offset < 0 or not isinstance(limit, int) or not 1 <= limit <= _MAX_SEARCH_LIMIT:
            raise SnapshotValidationError(f"offset must be nonnegative and limit between 1 and {_MAX_SEARCH_LIMIT}")
        if query is not None and (not isinstance(query, str) or len(query) > 500):
            raise SnapshotValidationError("query must be at most 500 characters")
        clauses, args = [], []
        if provider is not None:
            clauses.append("provider=?"); args.append(_required_id(provider, "provider"))
        if entity_type is not None:
            clauses.append("entity_type=?"); args.append(_required_id(entity_type, "entity_type"))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute("SELECT * FROM entities" + where + " ORDER BY last_used_at DESC, source_id", args).fetchall()
                items = []
                term = query.casefold() if query else None
                for row in rows:
                    metadata = json.loads(row["metadata_json"])
                    if term and term not in _dump(metadata).casefold():
                        continue
                    items.append({"provider": row["provider"], "entity_type": row["entity_type"], "source_id": row["source_id"],
                                  "metadata": metadata, "coverage": json.loads(row["coverage_json"]),
                                  "payload_sha256": row["payload_sha256"], "fetched_at": row["fetched_at"]})
                return {"items": items[offset:offset + limit], "total": len(items), "offset": offset, "limit": limit}
            finally:
                conn.close()

    def protect_entity(self, provider: str, entity_type: str, source_id: str, reason: str, protected: bool = True) -> None:
        keys = tuple(_required_id(x, n) for x, n in ((provider, "provider"), (entity_type, "entity_type"), (source_id, "source_id"), (reason, "reason")))
        with self._lock:
            conn = self._transaction()
            try:
                if protected:
                    conn.execute("INSERT OR IGNORE INTO entity_protection VALUES(?,?,?,?)", keys)
                else:
                    conn.execute("DELETE FROM entity_protection WHERE provider=? AND entity_type=? AND source_id=? AND reason=?", keys)
                    self._gc_entities(conn)
                conn.commit()
            except Exception:
                conn.rollback(); raise
            finally:
                conn.close()

    def reconcile_saved_protections(self, protections: Mapping[str, Iterable[tuple[str, str, str]]]) -> None:
        """Replace only collection/tier protections from an authoritative library scan."""
        normalized: dict[str, set[tuple[str, str, str]]] = {}
        for reason, refs in protections.items():
            reason = _required_id(reason, "protection reason")
            if not (reason.startswith("collection:") or reason.startswith("tier:")):
                raise SnapshotValidationError("reconciliation only supports collection and tier protections")
            normalized[reason] = {tuple(_required_id(value, name) for value, name in zip(ref, ("provider", "entity_type", "source_id")))
                                  for ref in refs if len(ref) == 3}
        with self._lock:
            conn = self._transaction()
            try:
                conn.execute("DELETE FROM entity_protection WHERE reason GLOB 'collection:*' OR reason GLOB 'tier:*'")
                for reason, refs in normalized.items():
                    for provider, entity_type, source_id in refs:
                        conn.execute("INSERT INTO entity_protection VALUES(?,?,?,?)", (provider, entity_type, source_id, reason))
                self._gc_entities(conn)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def get_overrides(self, provider: str, entity_type: str, source_id: str) -> dict[str, Any]:
        keys = tuple(_required_id(x, n) for x, n in ((provider, "provider"), (entity_type, "entity_type"), (source_id, "source_id")))
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT overrides_json FROM entity_overrides WHERE provider=? AND entity_type=? AND source_id=?", keys).fetchone()
                return json.loads(row[0]) if row else {}
            finally:
                conn.close()

    def set_overrides(self, provider: str, entity_type: str, source_id: str, overrides: dict[str, Any]) -> None:
        keys = tuple(_required_id(x, n) for x, n in ((provider, "provider"), (entity_type, "entity_type"), (source_id, "source_id")))
        clean = _safe_json(overrides, where="user overrides")
        if not isinstance(clean, dict):
            raise SnapshotValidationError("user overrides must be an object")
        overrides_json = _dump(clean)
        if len(overrides_json.encode()) > self.max_entry_bytes:
            raise SnapshotValidationError(f"User overrides exceed {self.max_entry_bytes} byte limit")
        with self._lock:
            conn = self._transaction()
            try:
                conn.execute("INSERT INTO entity_overrides VALUES(?,?,?,?,?) ON CONFLICT(provider,entity_type,source_id) DO UPDATE SET overrides_json=excluded.overrides_json,updated_at=excluded.updated_at", (*keys, overrides_json, _now()))
                conn.commit()
            except Exception:
                conn.rollback(); raise
            finally:
                conn.close()

    def get_provider_state(self, provider: str, account_scope: str | None = None) -> dict[str, Any] | None:
        provider = _required_id(provider, "provider"); scope = _scope_hash(account_scope)
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT next_allowed_at,error,error_at,updated_at FROM provider_state WHERE provider=? AND scope_hash=?", (provider, scope)).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def set_provider_state(self, provider: str, account_scope: str | None = None, *, next_allowed_at: float | None = None,
                           error: str | None = None, error_at: float | None = None) -> dict[str, Any]:
        provider = _required_id(provider, "provider"); scope = _scope_hash(account_scope)
        for value in (next_allowed_at, error_at):
            if value is not None and not math.isfinite(float(value)):
                raise SnapshotValidationError("provider state timestamps must be finite")
        if error is not None and (not isinstance(error, str) or len(error) > 128 or not re.fullmatch(r"[A-Za-z0-9_.-]+", error)):
            raise SnapshotValidationError("error must be a short public-safe status code")
        values = (None if next_allowed_at is None else float(next_allowed_at), error,
                  None if error_at is None else float(error_at), _now())
        with self._lock:
            conn = self._transaction()
            try:
                conn.execute("INSERT INTO provider_state VALUES(?,?,?,?,?,?) ON CONFLICT(provider,scope_hash) DO UPDATE SET next_allowed_at=excluded.next_allowed_at,error=excluded.error,error_at=excluded.error_at,updated_at=excluded.updated_at", (provider, scope, *values))
                conn.commit()
                return {"next_allowed_at": values[0], "error": error, "error_at": values[2], "updated_at": values[3]}
            except Exception:
                conn.rollback(); raise
            finally:
                conn.close()

    def clear_recent(self) -> dict[str, int]:
        with self._lock:
            conn = self._transaction()
            try:
                request_count = conn.execute("SELECT COUNT(*) FROM request_snapshots").fetchone()[0]
                conn.execute("DELETE FROM request_snapshots")
                entity_count = self._gc_entities(conn)
                conn.commit()
                return {"requests_deleted": int(request_count), "entities_deleted": int(entity_count)}
            except Exception:
                conn.rollback(); raise
            finally:
                conn.close()

    def cache_stats(self) -> dict[str, Any]:
        """Return bounded cache inventory without exposing keys or provider scopes."""
        with self._lock:
            conn = self._connect()
            try:
                requests = int(conn.execute("SELECT COUNT(*) FROM request_snapshots").fetchone()[0])
                entities = int(conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0])
                protected = int(conn.execute("""
                    SELECT COUNT(*) FROM entities e WHERE EXISTS (
                        SELECT 1 FROM entity_protection p WHERE p.provider=e.provider
                          AND p.entity_type=e.entity_type AND p.source_id=e.source_id)
                """).fetchone()[0])
                edited = int(conn.execute("SELECT COUNT(*) FROM entity_overrides").fetchone()[0])
                linked = int(conn.execute("SELECT COUNT(*) FROM request_entities").fetchone()[0])
                logical = self._usage(conn)
                providers = {}
                for row in conn.execute("""
                    SELECT provider, COUNT(*) AS entity_count,
                           COALESCE(SUM((SELECT COUNT(*) FROM json_each(entities.coverage_json))),0) AS coverage_fields,
                           SUM(CASE WHEN EXISTS (SELECT 1 FROM entity_protection p WHERE p.provider=entities.provider
                               AND p.entity_type=entities.entity_type AND p.source_id=entities.source_id) THEN 1 ELSE 0 END) AS protected_entity_count
                    FROM entities GROUP BY provider ORDER BY provider
                """):
                    providers[row["provider"]] = {"provider": row["provider"], "entity_count": int(row["entity_count"]),
                        "coverage_fields": int(row["coverage_fields"]), "protected_entity_count": int(row["protected_entity_count"]), "request_count": 0}
                for row in conn.execute("SELECT provider,COUNT(*) AS request_count FROM request_snapshots GROUP BY provider"):
                    providers.setdefault(row["provider"], {"provider": row["provider"], "entity_count": 0,
                        "coverage_fields": 0, "protected_entity_count": 0, "request_count": 0})["request_count"] = int(row["request_count"])
            finally:
                conn.close()
        path = self.db_path
        disk = 0
        for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
            try:
                stat = candidate.stat()
                disk += int(getattr(stat, "st_blocks", 0) * 512 or stat.st_size)
            except OSError:
                pass
        return {"request_count": requests, "entity_count": entities, "request_entity_links": linked,
                "protected_entity_count": protected, "edited_entity_count": edited,
                "logical_bytes": logical, "disk_bytes": disk,
                "max_bytes": self.max_bytes, "max_requests": self.max_requests,
                "max_entry_bytes": self.max_entry_bytes, "providers": list(providers.values()),
                "warning": "A prior cache recovery issue was recorded." if self.availability_error else None}


SnapshotStore = DiscoverySnapshotStore

