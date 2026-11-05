"""Characters tab: AniList character browse, typed IDs and card fields. AniList is stubbed; no network."""
import asyncio

import pytest

from core.media import anilist


def _char(i: int, name: str) -> dict:
    return {
        "id": i,
        "name": {"full": name, "native": f"{name} (native)"},
        "image": {"large": f"https://s4.anilist.co/char/{i}.jpg"},
        "description": f"{name} is a fictional test character.",
        "gender": "Female",
        "age": "17",
        "favourites": 1200 - i,
        "media": {"nodes": [{"id": 200 + i, "title": {"english": f"Anime {i}", "romaji": None}}]},
    }


@pytest.fixture
def anilist_stub(monkeypatch):
    calls = []

    async def fake_query(q, variables=None):
        calls.append(variables)
        return {"data": {"Page": {
            "pageInfo": {"currentPage": 1, "hasNextPage": False, "lastPage": 1, "total": 2, "perPage": 24},
            "characters": [_char(101, "Alice Test"), _char(102, "Bob Test")],
        }}}

    monkeypatch.setattr(anilist, "_query", fake_query)
    return calls


async def test_characters_are_typed_and_carry_their_info(api, anilist_stub):
    r = await api.get("/api/discover/characters/browse")

    assert r.status_code == 200
    body = r.json()
    assert body["page_info"]["total"] == 2
    first = body["items"][0]
    assert first["id"] == "character:anilist:101"
    assert first["media_type"] == "character" and first["source"] == "anilist"
    assert first["title"] == "Alice Test" and first["name_native"] == "Alice Test (native)"
    assert first["cover"] == first["image"] == "https://s4.anilist.co/char/101.jpg"
    assert first["description"].startswith("Alice Test is a fictional")
    assert first["favourites"] == 1099 and first["gender"] == "Female" and first["age"] == "17"
    assert first["media_titles"] == ["Anime 101"]


async def test_popular_and_newest_map_to_anilist_sorts_and_blank_search_is_dropped(api, anilist_stub):
    await api.get("/api/discover/characters/browse")
    await api.get("/api/discover/characters/browse", params={"sort": "newest", "search": "  alice  "})
    await api.get("/api/discover/characters/browse", params={"sort": "not-a-sort", "search": "   "})

    assert anilist_stub[0]["sort"] == ["FAVOURITES_DESC"] and anilist_stub[0]["search"] is None
    assert anilist_stub[1]["sort"] == ["ID_DESC"] and anilist_stub[1]["search"] == "alice"
    assert anilist_stub[2]["sort"] == ["FAVOURITES_DESC"] and anilist_stub[2]["search"] is None


async def test_a_page_size_is_capped_for_anilist(api, anilist_stub):
    await api.get("/api/discover/characters/browse", params={"per_page": 500})

    assert anilist_stub[0]["perPage"] == 50


async def test_game_character_search_uses_igdb_global_catalog(api, monkeypatch):
    calls = []

    async def fake_browse(**kwargs):
        calls.append(kwargs)
        return {"page_info": {"currentPage": 1, "hasNextPage": False}, "items": [{"id": "character:igdb:7"}]}

    monkeypatch.setattr("api.discover.igdb.browse_characters", fake_browse)
    response = await api.get("/api/discover/characters/browse", params={"category": "game", "search": "  Ciri ", "sort": "az"})

    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == "character:igdb:7"
    assert calls == [{"search": "  Ciri ", "sort": "az", "page": 1, "per_page": 24}]


async def test_tmdb_character_category_requires_title_search_and_returns_titles(api, monkeypatch):
    calls = []

    async def fake_movies(**kwargs):
        calls.append(kwargs)
        return {"page_info": {"currentPage": 1, "hasNextPage": False}, "items": [{"id": "movie:tmdb:11", "media_type": "movie", "title": "Star Trek"}]}

    monkeypatch.setattr("api.discover.tmdb.browse_movies", fake_movies)
    empty = await api.get("/api/discover/characters/browse", params={"category": "movie"})
    found = await api.get("/api/discover/characters/browse", params={"category": "movie", "search": "Star Trek"})

    assert empty.json()["items"] == []
    assert "within a movie" in empty.json()["notice"]
    assert found.json()["items"][0]["title"] == "Star Trek"
    assert calls == [{"search": "Star Trek", "page": 1}]


def test_descriptions_lose_spoilers_and_markup_but_keep_the_facts():
    raw = "__Height:__ 160 cm\n\nSquad <i>Captain</i>. ~!He is secretly the king!~ Blunt.<br>Clean-freak &amp; tidy."

    text = anilist.plain_text(raw)

    assert text == "Height: 160 cm Squad Captain. Blunt. Clean-freak & tidy."
    assert "secretly" not in text and "__" not in text and "<br" not in text and "<i>" not in text


async def test_reference_lists_share_concurrent_fetch_and_cache_result(monkeypatch):
    monkeypatch.setattr(anilist, "_reference_cache", {})
    monkeypatch.setattr(anilist, "_reference_inflight", {})
    calls = []

    async def fake_query(query, variables=None):
        calls.append(query)
        await asyncio.sleep(0)
        return {"data": {"GenreCollection": ["Action", "Drama"]}}

    monkeypatch.setattr(anilist, "_query", fake_query)
    first, second = await asyncio.gather(anilist.genres(), anilist.genres())
    third = await anilist.genres()

    assert first == second == third == ["Action", "Drama"]
    assert calls == [anilist.GENRES_QUERY]


async def test_failed_reference_fetch_is_retried(monkeypatch):
    monkeypatch.setattr(anilist, "_reference_cache", {})
    monkeypatch.setattr(anilist, "_reference_inflight", {})
    calls = 0

    async def fail_once(query, variables=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary AniList failure")
        return {"data": {"MediaTagCollection": [{"name": "Adventure", "category": "Theme", "isAdult": False}]}}

    monkeypatch.setattr(anilist, "_query", fail_once)
    with pytest.raises(RuntimeError, match="temporary AniList failure"):
        await anilist.tags()

    assert await anilist.tags() == [{"name": "Adventure", "category": "Theme"}]
    assert calls == 2
