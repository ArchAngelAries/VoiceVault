"""Collections and favorites API (api/collections.py). Item IDs are typed: "anime:anilist:16498"."""


async def _favorites_id(api) -> int:
    cols = (await api.get("/api/collections")).json()
    return next(c["id"] for c in cols if c["slug"] == "favorites")


def _anime(i: int, **overrides) -> dict:
    payload = {
        "item_id": f"anime:anilist:{100 + i}",
        "item_type": "anime",
        "source": "anilist",
        "title": f"Show {i}",
        "cover_url": f"https://img.example/{i}.jpg",
        "year": 2000 + i,
    }
    payload.update(overrides)
    return payload


async def test_list_reports_real_item_count_and_limits_preview(api):
    fav = await _favorites_id(api)
    for i in range(5):
        assert (await api.post(f"/api/collections/{fav}/items", json=_anime(i))).status_code == 200

    row = next(c for c in (await api.get("/api/collections")).json() if c["id"] == fav)

    assert row["item_count"] == 5
    assert len(row["items"]) == 4  # preview for the mosaic only


async def test_detail_returns_every_item(api):
    fav = await _favorites_id(api)
    for i in range(5):
        await api.post(f"/api/collections/{fav}/items", json=_anime(i))

    detail = (await api.get(f"/api/collections/{fav}")).json()

    assert detail["item_count"] == 5
    assert len(detail["items"]) == 5


async def test_duplicate_collection_names_get_numbered_slugs(api):
    slugs = []
    for _ in range(3):
        slugs.append((await api.post("/api/collections", json={"name": "Dup Test"})).json()["slug"])

    assert slugs == ["dup-test", "dup-test-2", "dup-test-3"]


async def test_untyped_item_ids_are_rejected(api):
    fav = await _favorites_id(api)

    numeric = await api.post(f"/api/collections/{fav}/items",
                             json={"item_id": 4242, "item_type": "anime", "source": "anilist", "title": "Numeric"})
    legacy = await api.post(f"/api/collections/{fav}/items",
                            json={"item_id": "anilist_16498", "item_type": "anime", "source": "anilist", "title": "Old"})

    assert numeric.status_code == 422
    assert legacy.status_code == 422
    assert (await api.get("/api/collections/favorites/all")).json()["item_ids"] == []


async def test_same_number_for_an_anime_and_a_staff_member_do_not_collide(api):
    fav = await _favorites_id(api)
    await api.post(f"/api/collections/{fav}/items",
                   json={"item_id": "anime:anilist:5", "item_type": "anime", "source": "anilist", "title": "Anime 5"})
    await api.post(f"/api/collections/{fav}/items",
                   json={"item_id": "person:anilist:5", "item_type": "person", "source": "anilist", "title": "Staff 5"})

    ids = sorted((await api.get("/api/collections/favorites/all")).json()["item_ids"])

    assert ids == ["anime:anilist:5", "person:anilist:5"]


async def test_title_scoped_tmdb_roles_coexist_with_saved_actor_identity(api):
    fav = await _favorites_id(api)
    legacy_actor_item = {"item_id": "character:tmdb:5", "item_type": "character", "source": "tmdb", "title": "Spock"}
    role_item = {"item_id": "character:tmdb:movie-603-0123456789abcdef01234567", "item_type": "character",
                 "source": "tmdb", "title": "Spock"}

    assert (await api.post(f"/api/collections/{fav}/items", json=legacy_actor_item)).status_code == 200
    assert (await api.post(f"/api/collections/{fav}/items", json=role_item)).status_code == 200
    ids = (await api.get("/api/collections/favorites/all")).json()["item_ids"]

    assert "character:tmdb:5" in ids
    assert role_item["item_id"] in ids


async def test_wikidata_identity_rejects_conflicting_supplied_source_or_type(api):
    fav = await _favorites_id(api)
    for overrides in ({"source": "anilist"}, {"item_type": "person"}):
        item = {"item_id": "character:wikidata:Q42", "item_type": "character",
                "source": "wikidata", "title": "Alex", **overrides}
        response = await api.post(f"/api/collections/{fav}/items", json=item)
        assert response.status_code == 422

    assert (await api.get("/api/collections/favorites/all")).json()["item_ids"] == []


async def test_item_id_with_colons_can_be_removed_when_url_encoded(api):
    fav = await _favorites_id(api)
    await api.post(f"/api/collections/{fav}/items", json=_anime(7, item_id="anime:anilist:7"))

    r = await api.delete(f"/api/collections/{fav}/items/anime%3Aanilist%3A7")

    assert r.status_code == 200
    assert (await api.get("/api/collections/favorites/all")).json()["item_ids"] == []


async def test_favorites_toggle_round_trip(api):
    item = {"item_id": "anime:anilist:555", "item_type": "anime", "source": "anilist", "title": "Toggle Me"}

    await api.post("/api/collections/favorites/toggle", json={"favorited": True, "item": item})
    assert (await api.get("/api/collections/favorites/all")).json()["item_ids"] == ["anime:anilist:555"]
    assert (await api.get("/api/collections/favorites/check/anime:anilist:555")).json() == {"favorited": True}

    await api.post("/api/collections/favorites/toggle", json={"favorited": False, "item": item})
    assert (await api.get("/api/collections/favorites/all")).json()["item_ids"] == []


async def test_rename_keeps_item_count(api):
    col = (await api.post("/api/collections", json={"name": "Old"})).json()
    await api.post(f"/api/collections/{col['id']}/items", json=_anime(1))

    r = await api.patch(f"/api/collections/{col['id']}", json={"name": "New"})

    assert r.json()["name"] == "New"
    assert r.json()["item_count"] == 1


async def test_system_collections_cannot_be_deleted(api):
    fav = await _favorites_id(api)

    assert (await api.delete(f"/api/collections/{fav}")).status_code == 400
