"""IGDB reference data and query construction (core/media/igdb.py)."""
import pytest

from core.media import igdb

# Platform IDs confirmed against the live IGDB platforms endpoint (2026-10-09).
VERIFIED_PLATFORMS = {
    130: "Nintendo Switch",
    508: "Nintendo Switch 2",
    410: "Atari Jaguar CD",
    63: "Atari ST/STE",
    61: "Atari Lynx",
    67: "Intellivision",
    128: "PC Engine SuperGrafx",
    119: "Neo Geo Pocket",
    120: "Neo Geo Pocket Color",
}


def test_platform_ids_are_unique():
    ids = [p["id"] for p in igdb.PLATFORM_LIST]

    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("platform_id,name", sorted(VERIFIED_PLATFORMS.items()))
def test_platform_ids_match_igdb(platform_id, name):
    entry = next(p for p in igdb.PLATFORM_LIST if p["id"] == platform_id)

    assert entry["name"] == name


def test_switch_lite_is_not_listed_separately():
    # IGDB has no Switch Lite platform; the old entry pointed at Commodore CDTV.
    assert all(p["name"] != "Nintendo Switch Lite" for p in igdb.PLATFORM_LIST)


async def test_platform_search_filters_on_platform_type(monkeypatch):
    calls = []

    async def fake_query(endpoint, body):
        calls.append((endpoint, body))
        return [{"id": 1000, "name": "Custom Console"}]

    monkeypatch.setattr(igdb, "_query", fake_query)

    result = await igdb.search_platforms("Custom")

    assert result == [{"id": 1000, "name": "Custom Console"}]
    body = calls[0][1]
    assert "where platform_type = (1,5,6)" in body
    assert "category" not in body


async def test_search_text_cannot_break_out_of_the_quoted_query(monkeypatch):
    seen = {}

    async def fake_query(endpoint, body):
        seen["body"] = body
        return []

    monkeypatch.setattr(igdb, "_query", fake_query)

    await igdb.browse(search='zelda"; delete games; x')

    assert 'search "zelda delete games x"' in seen["body"]


async def test_game_detail_fetches_characters_by_game_from_the_characters_endpoint(monkeypatch):
    calls = []

    async def fake_query(endpoint, body):
        calls.append((endpoint, body))
        if endpoint == "games":
            return [{"id": 1942, "name": "The Witcher 3", "cover": {"image_id": "abc"}}]
        return [{"id": 11, "name": "Geralt", "description": "A witcher.", "mug_shot": {"image_id": "m1"}},
                {"id": 12, "name": "Ciri", "description": "", "mug_shot": None}]

    monkeypatch.setattr(igdb, "_query", fake_query)

    out = await igdb.detail(1942)

    assert [c for c, _ in calls] == ["games", "characters"]
    assert "where games = 1942" in calls[1][1]
    assert "characters" not in igdb.DETAIL_FIELDS.split(",")
    assert [c["name"] for c in out["characters"]] == ["Geralt", "Ciri"]
    assert out["characters"][0]["source"] == "igdb"
    assert out["characters"][1]["image"] is None


async def test_game_detail_with_no_characters_returns_an_empty_list(monkeypatch):
    async def fake_query(endpoint, body):
        return [{"id": 36, "name": "No Cast"}] if endpoint == "games" else []

    monkeypatch.setattr(igdb, "_query", fake_query)

    out = await igdb.detail(36)

    assert out["characters"] == []


async def test_character_catalog_searches_and_normalizes_title_context(monkeypatch):
    seen = {}

    async def fake_query(endpoint, body):
        seen.update(endpoint=endpoint, body=body)
        return [{"id": 11, "name": "Ciri", "description": "Princess", "mug_shot": {"image_id": "m1"},
                 "games": [{"id": 1942, "name": "The Witcher 3"}]}]

    monkeypatch.setattr(igdb, "_query", fake_query)
    result = await igdb.browse_characters(search='Ciri"; delete games;')

    assert seen["endpoint"] == "characters"
    assert 'search "Ciri delete games"' in seen["body"]
    assert result["items"][0]["id"] == "character:igdb:11"
    assert result["items"][0]["media_titles"] == ["The Witcher 3"]
