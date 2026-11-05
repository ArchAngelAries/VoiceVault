"""Saved library records retain public metadata without capturing user overrides."""

import sqlite3

from core import discovery_retention as retention
from core.discovery_snapshots import DiscoverySnapshotStore, SnapshotCapacityError


class RecordingStore:
    def __init__(self):
        self.entities = []
        self.protections = []

    def put_entity(self, *args, **kwargs):
        self.entities.append((args, kwargs))

    def retain_entity(self, *args, **kwargs):
        self.entities.append((args, kwargs))
        self.protections.append((args[:3], args[3], args[4]))

    def protect_entity(self, *args, **kwargs):
        self.protections.append((args[:3], args[3], kwargs.get("protected", True)))


def test_remember_public_item_keeps_only_whitelisted_catalog_fields():
    store = RecordingStore()
    ref = retention.remember_public_item(store, {
        "id": "character:tmdb:movie-603-0123456789abcdef01234567",
        "item_type": "character", "source": "tmdb", "title": "Spock",
        "cover_url": "https://image.tmdb.org/t/p/w342/spock.jpg", "year": 1966,
        "notes": "private", "personality": "private", "art": {"url": "file:///private"},
    })
    assert ref == ("tmdb", "character", "character:tmdb:movie-603-0123456789abcdef01234567")
    args, _ = store.entities[0]
    assert args[3] == {"id": ref[2], "media_type": "character", "source_id": "movie-603-0123456789abcdef01234567",
                       "type": "character", "source": "tmdb", "title": "Spock", "name": "Spock",
                       "cover": "https://image.tmdb.org/t/p/w342/spock.jpg", "image": "https://image.tmdb.org/t/p/w342/spock.jpg",
                       "cover_url": "https://image.tmdb.org/t/p/w342/spock.jpg", "year": 1966}
    assert retention.remember_public_item(store, {"id": "image:local:" + "a" * 32, "title": "Imported"}) is None
    assert retention.remember_public_item(store, {"id": "anime:anilist:1", "type": "movie", "title": "Mismatch"}) is None


def test_sparse_saved_card_does_not_overwrite_richer_public_profile(tmp_path):
    store = DiscoverySnapshotStore(tmp_path)
    profile = {"id": "anime:anilist:7", "title": "Seven", "description": "Public synopsis",
               "genres": ["Drama", "Mystery"], "notes": "private note"}
    retention.remember_public_item(store, profile)
    retention.remember_public_item(store, {"id": "anime:anilist:7", "title": "Seven"})
    retention.remember_public_item(store, {"id": "anime:anilist:7", "genres": []})
    metadata = store.get_entity("anilist", "anime", "anime:anilist:7")["metadata"]
    assert metadata["description"] == "Public synopsis"
    assert metadata["genres"] == ["Drama", "Mystery"]
    assert "notes" not in metadata


def test_saved_metadata_can_exceed_soft_budget_but_optional_cache_cannot(tmp_path):
    store = DiscoverySnapshotStore(tmp_path, max_bytes=10)
    store.retain_entity("anilist", "anime", "anime:anilist:1", {"title": "Protected one"}, "collection:1")
    store.retain_entity("anilist", "anime", "anime:anilist:2", {"title": "Protected two"}, "collection:2")
    assert store.get_entity("anilist", "anime", "anime:anilist:1") is not None
    assert store.get_entity("anilist", "anime", "anime:anilist:2") is not None
    try:
        store.put_entity("anilist", "anime", "anime:anilist:3", {"title": "Optional"})
    except SnapshotCapacityError:
        pass
    else:
        raise AssertionError("optional cache growth should stop at the soft budget")


def test_remove_request_invalidates_only_the_exact_scoped_snapshot(tmp_path):
    store = DiscoverySnapshotStore(tmp_path)
    store.put_request("provider", "search", {"q": "x"}, {"items": [1]}, account_scope="A")
    store.put_request("provider", "search", {"q": "x"}, {"items": [2]}, account_scope="B")
    assert store.remove_request("provider", "search", {"q": "x"}, account_scope="A")
    assert store.get_request("provider", "search", {"q": "x"}, account_scope="A") is None
    assert store.get_request("provider", "search", {"q": "x"}, account_scope="B")["payload"] == {"items": [2]}


def test_retention_and_removal_target_only_the_specific_reason(tmp_path):
    store = DiscoverySnapshotStore(tmp_path)
    ref = retention.protect_public_item(store, {"id": "anime:anilist:12", "title": "One"}, "collection:1")
    store.protect_entity(*ref, "tier:4")
    assert store.get_entity(*ref)["metadata"]["title"] == "One"

    retention._release_refs(store, [ref[2]], "collection:1")
    conn = sqlite3.connect(store.db_path)
    try:
        reasons = [row[0] for row in conn.execute("SELECT reason FROM entity_protection WHERE provider='anilist' AND entity_type='anime' AND source_id=?", (ref[2],))]
    finally:
        conn.close()
    assert reasons == ["tier:4"]


def test_reconciliation_replaces_only_collection_and_tier_protections(tmp_path):
    store = DiscoverySnapshotStore(tmp_path)
    store.retain_entity("igdb", "game", "game:igdb:99", {"title": "Old"}, "collection:99")
    store.retain_entity("anilist", "anime", "anime:anilist:8", {"title": "Saved"}, "collection:3")
    store.retain_entity("tmdb", "person", "person:tmdb:5", {"title": "Actor"}, "tier:6")
    store.reconcile_saved_protections({"collection:3": {("anilist", "anime", "anime:anilist:8")},
                                       "tier:6": {("tmdb", "person", "person:tmdb:5")}})
    assert store.get_entity("anilist", "anime", "anime:anilist:8")["metadata"]["title"] == "Saved"
    assert store.get_entity("tmdb", "person", "person:tmdb:5")["metadata"]["title"] == "Actor"
    conn = sqlite3.connect(store.db_path)
    try:
        reasons = {tuple(row) for row in conn.execute("SELECT source_id,reason FROM entity_protection")}
    finally:
        conn.close()
    assert reasons == {("anime:anilist:8", "collection:3"), ("person:tmdb:5", "tier:6")}


def test_sidecar_failure_is_detectable_by_nonfatal_api_wrapper():
    class BrokenStore:
        def retain_entity(self, *args, **kwargs):
            raise OSError("cache unavailable")

    try:
        retention.protect_public_item(BrokenStore(), {"id": "anime:anilist:444", "title": "Persisted"}, "collection:3")
    except OSError as exc:
        assert str(exc) == "cache unavailable"
    else:
        raise AssertionError("the sidecar failure fixture must be exercised")
