"""Typed item IDs (core/item_ids.py)."""
import pytest

from core import item_ids


def test_make_and_parse_round_trip():
    typed = item_ids.make_id("person", "anilist", 95272)

    assert typed == "person:anilist:95272"
    assert item_ids.parse_id(typed) == ("person", "anilist", 95272)


def test_title_scoped_tmdb_role_ids_are_typed_and_parseable():
    role_id = "character:tmdb:movie-603-0123456789abcdef01234567"

    assert item_ids.is_typed_id(role_id)
    assert item_ids.parse_id(role_id) == ("character", "tmdb", "movie-603-0123456789abcdef01234567")
    assert not item_ids.is_typed_id("person:tmdb:movie-603-0123456789abcdef01234567")


def test_wikidata_character_qids_are_exact_typed_ids():
    value = item_ids.make_id("character", "wikidata", "Q123")

    assert value == "character:wikidata:Q123"
    assert item_ids.is_typed_id(value)
    assert item_ids.parse_id(value) == ("character", "wikidata", "Q123")


@pytest.mark.parametrize("value", [
    "character:wikidata:Q0", "character:wikidata:Q01", "character:wikidata:q1",
    "character:wikidata:1", "character:wikidata:Q1:extra", "character:wikidata:Q1\n",
])
def test_wikidata_qids_must_be_exact_positive_qids(value):
    assert not item_ids.is_typed_id(value)
    with pytest.raises(ValueError):
        item_ids.parse_id(value)


@pytest.mark.parametrize("item_type", ["anime", "movie", "tv", "game", "person"])
def test_wikidata_ids_are_only_valid_for_characters(item_type):
    assert not item_ids.is_typed_id(f"{item_type}:wikidata:Q123")
    with pytest.raises(ValueError):
        item_ids.make_id(item_type, "wikidata", "Q123")


@pytest.mark.parametrize("value", ["anilist_16498", "anime:anilist:", "anime:anilist:x", "anime:AniList:1",
                                   "nope:anilist:1", 4242, None, "anime:anilist:1:extra"])
def test_untyped_or_malformed_ids_are_not_typed(value):
    assert not item_ids.is_typed_id(value)


def test_parse_rejects_malformed_ids():
    with pytest.raises(ValueError):
        item_ids.parse_id("anilist_16498")


def test_make_id_rejects_unknown_types_and_sources():
    with pytest.raises(ValueError):
        item_ids.make_id("album", "anilist", 1)
    with pytest.raises(ValueError):
        item_ids.make_id("anime", "youtube", 1)


@pytest.mark.parametrize("item_type,legacy,expected", [
    ("anime", "anilist_16498", "anime:anilist:16498"),
    ("person", "anilist_16498", "person:anilist:16498"),
    ("character", "anilist_char_7", "character:anilist:7"),
    ("movie", "tmdb_movie_603", "movie:tmdb:603"),
    ("tv", "tmdb_tv_1396", "tv:tmdb:1396"),
    ("person", "tmdb_500", "person:tmdb:500"),
    ("character", "tmdb_char_5", "character:tmdb:5"),
    ("game", "igdb_1942", "game:igdb:1942"),
    ("character", "igdb_char_11", "character:igdb:11"),
])
def test_legacy_ids_convert_using_their_stored_type(item_type, legacy, expected):
    assert item_ids.legacy_to_typed(item_type, legacy) == expected


@pytest.mark.parametrize("item_type,legacy", [
    ("anime", "anilist_char_7"),   # a character stored as an anime is not a valid pair
    ("anime", "anilist_abc"),      # non-numeric remainder
    ("game", "igdb_"),             # empty remainder
    ("movie", "weird_1"),          # unknown prefix
])
def test_legacy_ids_that_do_not_match_a_rule_are_left_alone(item_type, legacy):
    assert item_ids.legacy_to_typed(item_type, legacy) is None
