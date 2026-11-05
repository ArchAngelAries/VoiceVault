import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.core.discovery_snapshots import (
    DiscoverySnapshotStore,
    SnapshotCapacityError,
    SnapshotValidationError,
)


class DiscoverySnapshotTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path.cwd() / ".snapshot-test-tmp"
        temp_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temp_root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def store(self, **kwargs):
        return DiscoverySnapshotStore(self.root, **kwargs)

    def test_exact_request_key_preserves_array_order_and_account_scope(self):
        store = self.store()
        store.put_request("catalog", "search-v1", {"filters": ["a", "b"]}, {"items": [1]}, account_scope="acct-one")
        self.assertIsNone(store.get_request("catalog", "search-v1", {"filters": ["b", "a"]}, account_scope="acct-one"))
        self.assertIsNone(store.get_request("catalog", "search-v1", {"filters": ["a", "b"]}, account_scope="acct-two"))
        found = store.get_request("catalog", "search-v1", {"filters": ["a", "b"]}, account_scope="acct-one")
        self.assertEqual(found["payload"], {"items": [1]})
        self.assertEqual(found["content_hash"], found["payload_sha256"])
        self.assertEqual(found["validators"], {})
        dbbytes = store.db_path.read_bytes()
        self.assertNotIn(b"acct-one", dbbytes)

    def test_entities_merge_richer_fields_and_keep_user_overrides_separate(self):
        store = self.store()
        store.put_entity("catalog", "character", "42", {"name": "Rhea", "detail": {"bio": "Known"}}, coverage=["detail"])
        store.set_overrides("catalog", "character", "42", {"display_name": "My Rhea"})
        store.put_entity("catalog", "character", "42", {"name": None, "detail": {"aliases": ["R"]}}, coverage=["browse"])
        record = store.get_entity("catalog", "character", "42")
        self.assertEqual(record["metadata"], {"name": "Rhea", "detail": {"bio": "Known", "aliases": ["R"]}})
        self.assertEqual(record["coverage"], ["browse", "detail"])
        self.assertEqual(store.get_overrides("catalog", "character", "42"), {"display_name": "My Rhea"})

    def test_protected_entities_survive_clear_and_budget_pressure(self):
        store = self.store(max_bytes=200, max_entry_bytes=250)
        store.put_entity("catalog", "show", "kept", {"title": "saved"})
        store.protect_entity("catalog", "show", "kept", "favorite")
        with self.assertRaises(SnapshotCapacityError):
            store.put_entity("catalog", "show", "too-large", {"blob": "x" * 220})
        store.put_request("catalog", "page", {}, {"items": []})
        result = store.clear_recent()
        self.assertEqual(result["requests_deleted"], 1)
        self.assertIsNotNone(store.get_entity("catalog", "show", "kept"))

    def test_cache_stats_report_budgets_counts_coverage_and_physical_file(self):
        store = self.store(max_requests=7, max_bytes=4096)
        store.put_entity("catalog", "show", "saved", {"title": "Saved"}, coverage=["title", "overview"])
        store.protect_entity("catalog", "show", "saved", "favorite")
        store.protect_entity("catalog", "show", "saved", "tier:12")
        store.set_overrides("catalog", "show", "saved", {"display_name": "My title"})
        store.put_request("catalog", "browse", {}, {"items": []})
        stats = store.cache_stats()
        self.assertEqual((stats["request_count"], stats["entity_count"]), (1, 1))
        self.assertEqual((stats["protected_entity_count"], stats["edited_entity_count"]), (1, 1))
        self.assertEqual(stats["providers"][0]["protected_entity_count"], 1)
        self.assertEqual((stats["max_requests"], stats["max_bytes"]), (7, 4096))
        self.assertGreater(stats["logical_bytes"], 0)
        self.assertGreater(stats["disk_bytes"], 0)
        self.assertEqual(stats["providers"][0]["coverage_fields"], 2)
        cleared = store.clear_recent()
        self.assertEqual(cleared["requests_deleted"], 1)
        after = store.cache_stats()
        self.assertEqual((after["protected_entity_count"], after["edited_entity_count"]), (1, 1))

    def test_request_lru_and_schema_mismatch_are_cache_misses(self):
        store = self.store(max_requests=1)
        store.put_request("p", "one", {}, {"x": 1})
        first = store.get_request("p", "one", {})
        self.assertIsNotNone(first)
        self.assertIsNone(store.get_request("p", "one", {}, schema_version=99))
        store.put_request("p", "two", {}, {"x": 2})
        self.assertIsNone(store.get_request("p", "one", {}))
        self.assertEqual(store.get_request("p", "two", {})["payload"], {"x": 2})

    def test_failed_transaction_keeps_previous_good_snapshot(self):
        store = self.store()
        store.put_request("p", "op", {}, {"value": "old"})
        conn = sqlite3.connect(store.db_path)
        try:
            conn.execute("CREATE TRIGGER fail_snapshot BEFORE UPDATE ON request_snapshots BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
            conn.commit()
        finally:
            conn.close()
        with self.assertRaises(sqlite3.IntegrityError):
            store.put_request("p", "op", {}, {"value": "new"})
        conn = sqlite3.connect(store.db_path)
        try:
            conn.execute("DROP TRIGGER fail_snapshot")
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(store.get_request("p", "op", {})["payload"], {"value": "old"})

    def test_credential_fields_are_rejected_before_persistence(self):
        store = self.store()
        with self.assertRaises(SnapshotValidationError):
            store.put_request("p", "op", {"api_key": "private-secret"}, {"ok": True})
        with self.assertRaises(SnapshotValidationError):
            store.put_request("p", "op", {}, {"url": "https://user:pass@example.test/x"})
        with self.assertRaises(SnapshotValidationError):
            store.put_request("p", "op", {}, {"url": "https://example.test/list?X-Amz-Credential=private"})
        self.assertFalse(store.db_path.exists())

    def test_corrupt_database_is_preserved_and_rebuilt(self):
        store = self.store()
        store.db_path.parent.mkdir(parents=True)
        store.db_path.write_bytes(b"this is not sqlite")
        store.put_request("p", "op", {}, {"recovered": True})
        self.assertTrue(store.availability_error)
        quarantine = list(store.db_path.parent.glob("snapshots.sqlite3.quarantine-*"))
        self.assertEqual(len(quarantine), 1)
        self.assertEqual(quarantine[0].read_bytes(), b"this is not sqlite")
        self.assertEqual(store.get_request("p", "op", {})["payload"], {"recovered": True})
        conn = sqlite3.connect(store.db_path)
        try:
            conn.execute("PRAGMA user_version=88")
            conn.commit()
        finally:
            conn.close()
        store.put_request("p", "after-incompatible", {}, {"recovered": True})
        self.assertEqual(len(list(store.db_path.parent.glob("snapshots.sqlite3.quarantine-*"))), 2)
        self.assertEqual(store.get_request("p", "after-incompatible", {})["payload"], {"recovered": True})

    def test_cooldown_state_is_scoped_and_errors_are_codes(self):
        store = self.store()
        store.set_provider_state("catalog", "one", next_allowed_at=123.0, error="RATE_LIMIT")
        self.assertEqual(store.get_provider_state("catalog", "one")["error"], "RATE_LIMIT")
        self.assertIsNone(store.get_provider_state("catalog", "two"))
        with self.assertRaises(SnapshotValidationError):
            store.set_provider_state("catalog", "one", error="Authorization: private")


if __name__ == "__main__":
    unittest.main()
