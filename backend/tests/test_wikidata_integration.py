"""Wikidata character identity through the real ASGI and SQLite-backed API stack."""
import json

import pytest


QID = "character:wikidata:Q42"
SAME_NAME_QID = "character:wikidata:Q99"


def _card(item_id, title="Alex Example"):
    qid = item_id.rsplit(":", 1)[-1]
    return {"id": item_id, "item_id": item_id, "media_type": "character", "item_type": "character",
            "source": "wikidata", "title": title, "name": title,
            "description": f"Evidence for {qid}",
            "source_url": f"https://www.wikidata.org/wiki/{qid}", "license": "CC0"}


async def test_wikidata_browse_dispatches_each_category_without_catalog_calls(api, monkeypatch):
    import api.discover as discover

    calls = []

    async def fake_browse(**kwargs):
        calls.append(kwargs)
        return {"page_info": {"currentPage": kwargs["page"], "hasNextPage": False},
                "items": [_card(QID)]}

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("catalog providers must not run for Wikidata source")

    monkeypatch.setattr(discover.wikidata, "browse_characters", fake_browse)
    monkeypatch.setattr(discover.anilist, "browse_characters", forbidden)
    monkeypatch.setattr(discover.igdb, "browse_characters", forbidden)
    monkeypatch.setattr(discover.tmdb, "browse_movies", forbidden)
    monkeypatch.setattr(discover.tmdb, "browse_tv", forbidden)

    for category in ("anime", "game", "movie", "tv"):
        response = await api.get("/api/discover/characters/browse", params={
            "source": "wikidata", "category": category, "search": "Alex", "page": 2, "per_page": 12,
        })
        assert response.status_code == 200, response.text
        assert response.json()["items"][0]["id"] == QID

    assert calls == [
        {"search": "Alex", "category": category, "page": 2, "per_page": 12}
        for category in ("anime", "game", "movie", "tv")
    ]


async def test_wikidata_and_catalog_browse_have_distinct_cached_results(api, monkeypatch):
    import api.discover as discover

    wikidata_calls = []
    catalog_calls = []

    async def wiki(**kwargs):
        wikidata_calls.append(kwargs)
        return {"page_info": {"currentPage": 1, "hasNextPage": False}, "items": [_card(QID)]}

    async def catalog(**kwargs):
        catalog_calls.append(kwargs)
        return {"page_info": {"currentPage": 1, "hasNextPage": False},
                "items": [{"id": "character:anilist:42", "title": "Alex Example"}]}

    monkeypatch.setattr(discover.wikidata, "browse_characters", wiki)
    monkeypatch.setattr(discover.anilist, "browse_characters", catalog)

    wiki_first = await api.get("/api/discover/characters/browse", params={"search": "Alex", "source": "wikidata"})
    catalog_result = await api.get("/api/discover/characters/browse", params={"search": "Alex", "source": "catalog"})
    wiki_again = await api.get("/api/discover/characters/browse", params={"search": "Alex", "source": "wikidata"})

    assert wiki_first.json()["items"][0]["id"] == wiki_again.json()["items"][0]["id"] == QID
    assert catalog_result.json()["items"][0]["id"] == "character:anilist:42"
    assert len(wikidata_calls) == 1
    assert len(catalog_calls) == 1


async def test_same_name_qids_remain_distinct_in_collection_favorites_and_after_reload(api):
    cards = [_card(QID), _card(SAME_NAME_QID)]
    for card in cards:
        saved = await api.post("/api/collections/favorites/toggle", json={"favorited": True, "item": card})
        assert saved.status_code == 200, saved.text

    collections = (await api.get("/api/collections")).json()
    favorite_id = next(collection["id"] for collection in collections if collection["slug"] == "favorites")
    detail = (await api.get(f"/api/collections/{favorite_id}")).json()
    assert {item["item_id"] for item in detail["items"]} == {QID, SAME_NAME_QID}
    assert {item["title"] for item in detail["items"]} == {"Alex Example"}

    local = await api.get("/api/discover/local-library", params={"provider": "wikidata", "entity_type": "character"})
    assert local.status_code == 200, local.text
    assert {item["id"] for item in local.json()["items"]} >= {QID, SAME_NAME_QID}


async def test_tier_board_round_trips_exact_wikidata_identity(api):
    created = await api.post("/api/tiers", json={"name": "Wikidata identities"})
    assert created.status_code == 200, created.text
    board = created.json()
    card = _card(QID)
    rows = board["rows"]
    rows[0]["items"] = [card]
    saved = await api.put(f"/api/tiers/{board['id']}/board", json={"rows": rows})
    assert saved.status_code == 200, saved.text

    loaded = (await api.get(f"/api/tiers/{board['id']}" )).json()
    assert loaded["rows"][0]["items"][0]["id"] == QID
    assert loaded["rows"][0]["items"][0]["source"] == "wikidata"


async def test_tier_tiles_reject_conflicting_wikidata_source_or_type(api):
    created = await api.post("/api/tiers", json={"name": "Reject mismatched identity"})
    board = created.json()
    row = board["rows"][0]
    for overrides in ({"source": "anilist"}, {"item_type": "person"}):
        row_with_mismatch = {**row, "items": [{**_card(QID), **overrides}]}
        response = await api.put(f"/api/tiers/{board['id']}/board", json={"rows": [row_with_mismatch]})
        assert response.status_code == 422


async def test_local_library_never_calls_live_wikidata_provider(api, monkeypatch):
    import api.discover as discover

    async def forbidden(**_kwargs):
        raise AssertionError("local library must use retained data only")

    monkeypatch.setattr(discover.wikidata, "browse_characters", forbidden)
    response = await api.get("/api/discover/local-library", params={"provider": "wikidata"})

    assert response.status_code == 200


async def test_wikidata_saved_metadata_survives_recent_cache_clear(api, monkeypatch):
    import api.discover as discover
    from core.discovery_retention import get_snapshot_store

    async def fake_browse(**_kwargs):
        return {"page_info": {"currentPage": 1, "hasNextPage": False}, "items": [_card(QID)]}

    monkeypatch.setattr(discover.wikidata, "browse_characters", fake_browse)
    fetched = await api.get("/api/discover/characters/browse", params={"source": "wikidata", "search": "Alex"})
    assert fetched.status_code == 200, fetched.text
    await api.post("/api/collections/favorites/toggle", json={"favorited": True, "item": _card(QID)})
    store = get_snapshot_store()
    before = store.get_entity("wikidata", "character", QID)
    assert before and before["metadata"]["source_url"] == "https://www.wikidata.org/wiki/Q42"

    cleared = await api.post("/api/discover/cache/clear-recent")

    assert cleared.status_code == 200, cleared.text
    after = store.get_entity("wikidata", "character", QID)
    assert after and after["metadata"]["source_url"] == before["metadata"]["source_url"]
    assert after["metadata"]["license"] == "CC0"
    assert after["metadata"]["title"] == "Alex Example"
    assert after["metadata"]["source"] == "wikidata"


def test_wikidata_save_serializer_keeps_bounded_public_claims_and_merges_sparse_cards(tmp_path):
    from core.discovery_retention import remember_public_item
    from core.discovery_snapshots import DiscoverySnapshotStore

    store = DiscoverySnapshotStore(tmp_path)
    rich_card = {
        **_card(QID), "aliases": ["Alex", "A. Example"], "categories": ["anime", "unknown"],
        "type_evidence": ["Q95074", "Q01", "not-a-qid"],
        "originating_works": [
            {"qid": "Q123", "name": "Example Series", "url": "https://attacker.example/"},
            {"qid": "Q0", "name": "Invalid work"},
            {"qid": "Q456", "name": "Safe TV", "url": "https://user:pass@evil.example/"},
        ],
        "source_notes": "Public Wikidata claims", "private_notes": "keep private",
        "creative_direction": "do not persist this profile data",
    }

    assert remember_public_item(store, rich_card) == ("wikidata", "character", QID)
    stored = store.get_entity("wikidata", "character", QID)["metadata"]
    assert stored["aliases"] == ["Alex", "A. Example"]
    assert stored["categories"] == ["anime"]
    assert stored["type_evidence"] == ["Q95074"]
    assert stored["originating_works"] == [
        {"qid": "Q123", "name": "Example Series", "url": "https://www.wikidata.org/wiki/Q123"},
        {"qid": "Q456", "name": "Safe TV", "url": "https://www.wikidata.org/wiki/Q456"},
    ]
    assert stored["source_url"] == "https://www.wikidata.org/wiki/Q42"
    assert stored["license"] == "CC0"
    assert "private_notes" not in stored and "creative_direction" not in stored

    remember_public_item(store, {"id": QID, "item_type": "character", "source": "wikidata", "title": "Alex Example"})
    reloaded = DiscoverySnapshotStore(tmp_path).get_entity("wikidata", "character", QID)["metadata"]
    assert reloaded["aliases"] == ["Alex", "A. Example"]
    assert reloaded["originating_works"][0]["url"] == "https://www.wikidata.org/wiki/Q123"


async def test_profile_evidence_and_converse_save_preserve_exact_wikidata_qid(api, monkeypatch, tmp_path):
    import api.converse as converse
    import api.profile_suggestions as suggestions
    from core.discovery_retention import get_snapshot_store

    get_snapshot_store().put_entity("wikidata", "character", QID, {
        "id": QID, "title": "Alex Example", "description": "Public retained facts",
        "source_url": "https://www.wikidata.org/wiki/Q42", "license": "CC0",
    }, coverage=("title", "description", "source_url", "license"))
    async def stream(*_args):
        yield json.dumps({"personality": "Thoughtful", "voice_description": "Measured",
                          "preset_id": None, "rationale": "Fits the supplied context.", "uncertainty": []})
    monkeypatch.setattr(converse, "_stream_for_provider", stream)
    suggested = await api.post("/api/converse/profile-suggestions", json={
        "provider": "ollama", "model": "local-model", "name": "Alex Example",
        "catalog_identity": QID,
    })
    assert suggested.status_code == 200, suggested.text
    evidence = suggested.json()["catalog_evidence"]
    assert evidence["identity"] == QID
    assert evidence["provider"] == "wikidata"
    assert {field["label"]: field["text"] for field in evidence["fields"]}["Title"] == "Alex Example"

    created = await api.post("/api/converse/sessions", json={
        "kind": "chat", "profile": {"name": "Alex Example", "catalog_identity": QID},
    })
    assert created.status_code == 200, created.text
    loaded = await api.get(f"/api/converse/sessions/{created.json()['id']}")
    assert loaded.status_code == 200
    assert loaded.json()["profile"]["catalog_identity"] == QID
