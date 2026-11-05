"""Normalizers for external media sources (core/media/*.py)."""
from core.media import anilist, tmdb


def test_tmdb_cast_identity_includes_title_and_role():
    cast = {"id": 5, "credit_id": "credit-a", "character": "Spock", "name": "Leonard Nimoy", "profile_path": None}
    out = tmdb._normalize_cast(cast, "movie", 10, "Star Trek")
    other_title = tmdb._normalize_cast(cast, "movie", 11, "Star Trek II")
    other_role = tmdb._normalize_cast({**cast, "character": "Mirror Spock"}, "movie", 10, "Star Trek")

    assert out["id"].startswith("character:tmdb:movie-10-")
    assert len({out["id"], other_title["id"], other_role["id"]}) == 3
    assert out["source"] == "tmdb"
    assert out["legacy_id"] == "character:tmdb:5"
    assert out["media_title"] == "Star Trek"


def test_anilist_character_is_tagged_with_its_source():
    out = anilist._normalize_char({
        "id": 9,
        "name": {"full": "Mikasa Ackerman"},
        "image": {"large": None},
        "description": "",
        "gender": "Female",
        "age": "",
        "voiceActors": [],
    })

    assert out["id"] == "character:anilist:9"
    assert out["source"] == "anilist"


def test_anilist_voice_actors_are_read_from_the_character_edge():
    media = {
        "id": 16498,
        "title": {"romaji": "Shingeki no Kyojin"},
        "characters": {"edges": [{
            "node": {"id": 7, "name": {"full": "Mikasa Ackerman"}, "image": {"large": None},
                     "description": "", "gender": "Female", "age": ""},
            "voiceActors": [{"id": 3, "name": {"full": "Yui Ishikawa"}, "image": {"large": None}}],
        }]},
    }

    out = anilist._normalize_media(media)

    char = out["characters"][0]
    assert char["id"] == "character:anilist:7"
    assert char["voice_actors"][0]["name"] == "Yui Ishikawa"


def test_anilist_staff_detail_query_has_no_nested_media_block():
    # AniList answers 500 when characters are expanded with their own media list (verified 2026-10-09).
    from core.media import people

    assert "media(perPage" not in people.STAFF_DETAIL_QUERY


def test_media_and_staff_ids_are_typed_so_they_cannot_collide():
    anime = anilist._normalize_media({"id": 5, "title": {"romaji": "A"}, "characters": {"edges": []}})
    from core.media import people
    staff = people._normalize_anilist_staff({"id": 5, "name": {"full": "B"}})

    assert anime["id"] == "anime:anilist:5"
    assert staff["id"] == "person:anilist:5"


def test_tmdb_movie_tv_and_person_ids_are_typed():
    from core.media import people
    movie = tmdb._normalize_item({"id": 603, "title": "The Matrix", "media_type": "movie"})
    show = tmdb._normalize_item({"id": 1396, "name": "Breaking Bad", "media_type": "tv"})
    person = people._normalize_tmdb_person({"id": 500, "name": "Tom Cruise"})

    assert movie["id"] == "movie:tmdb:603"
    assert show["id"] == "tv:tmdb:1396"
    assert person["id"] == "person:tmdb:500"


def test_igdb_game_ids_are_typed():
    from core.media import igdb
    game = igdb._normalize({"id": 1942, "name": "The Witcher 3"})

    assert game["id"] == "game:igdb:1942"
