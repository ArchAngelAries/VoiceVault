"""
core/media/anilist.py — AniList GraphQL API connector
Full filter/sort support matching AniList API capabilities.
"""

import asyncio
import html
import re
import time
from typing import Optional

import httpx

from core.item_ids import make_id

ANILIST_URL = "https://graphql.anilist.co"

MEDIA_FIELDS = """
  id title { romaji english native }
  coverImage { large extraLarge color }
  bannerImage description(asHtml: false)
  genres averageScore popularity seasonYear season
  status episodes format studios(isMain: true) { nodes { name } }
"""

# Voice actors belong to the character *edge* (the link between media and character), not the
# character node. Querying voiceActors on Character fails with HTTP 400 for every title.
CHAR_FIELDS = """
  characters(role: MAIN, perPage: 12) {
    edges {
      node {
        id name { full native } image { large }
        description(asHtml: false) gender age
      }
      voiceActors(language: JAPANESE) { id name { full } image { large } }
    }
  }
"""

BROWSE_QUERY = """
query (
  $page: Int, $perPage: Int, $search: String,
  $genres: [String], $excludedGenres: [String],
  $tags: [String], $excludedTags: [String],
  $minimumTagRank: Int,
  $format: [MediaFormat], $status: MediaStatus,
  $season: MediaSeason, $seasonYear: Int,
  $yearGreater: FuzzyDateInt, $yearLesser: FuzzyDateInt,
  $episodeGreater: Int, $episodeLesser: Int,
  $durationGreater: Int, $durationLesser: Int,
  $countryOfOrigin: CountryCode, $source: MediaSource,
  $isAdult: Boolean, $sort: [MediaSort]
) {
  Page(page: $page, perPage: $perPage) {
    pageInfo { total currentPage lastPage hasNextPage }
    media(
      type: ANIME, search: $search,
      genre_in: $genres, genre_not_in: $excludedGenres,
      tag_in: $tags, tag_not_in: $excludedTags,
      minimumTagRank: $minimumTagRank,
      format_in: $format, status: $status,
      season: $season, seasonYear: $seasonYear,
      startDate_greater: $yearGreater, startDate_lesser: $yearLesser,
      episodes_greater: $episodeGreater, episodes_lesser: $episodeLesser,
      duration_greater: $durationGreater, duration_lesser: $durationLesser,
      countryOfOrigin: $countryOfOrigin, source: $source,
      isAdult: $isAdult, sort: $sort
    ) { """ + MEDIA_FIELDS + """ }
  }
}
"""

DETAIL_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    """ + MEDIA_FIELDS + CHAR_FIELDS + """
  }
}
"""

GENRES_QUERY  = "query { GenreCollection }"
TAGS_QUERY    = "query { MediaTagCollection { name category isAdult } }"

# AniList's reference lists change infrequently. Keep a short-lived process cache and
# share concurrent fetches so StrictMode/remounts do not stampede the provider.
_REFERENCE_TTL_SECONDS = 6 * 60 * 60
_reference_cache: dict[str, tuple[float, list]] = {}
_reference_inflight: dict[str, asyncio.Task] = {}


async def _reference_list(key: str, query: str, normalize) -> list:
    cached = _reference_cache.get(key)
    if cached and cached[0] > time.monotonic():
        return list(cached[1])

    task = _reference_inflight.get(key)
    if task is None:
        async def fetch() -> list:
            try:
                data = await _query(query)
                result = normalize(data)
                _reference_cache[key] = (time.monotonic() + _REFERENCE_TTL_SECONDS, result)
                return result
            finally:
                _reference_inflight.pop(key, None)

        task = asyncio.create_task(fetch())
        _reference_inflight[key] = task

    return list(await task)


def _clear_reference_cache() -> None:
    """Clear static reference data (mainly useful for isolated tests)."""
    _reference_cache.clear()


async def _query(q: str, variables: dict = {}) -> dict:
    # Strip None values — AniList rejects null variables
    clean = {k: v for k, v in variables.items() if v is not None}
    from core.media.provider_transport import request_json
    return await request_json(
        "anilist", "POST", ANILIST_URL,
        json={"query": q, "variables": clean},
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        graphql=True,
    )


# ── Sort mapping ─────────────────────────────────────────────────
SORT_MAP = {
    "popular":  ["POPULARITY_DESC"],
    "popular_asc": ["POPULARITY"],
    "rating":   ["SCORE_DESC"],
    "rating_asc": ["SCORE"],
    "newest":   ["START_DATE_DESC"],
    "oldest":   ["START_DATE"],
    "trending": ["TRENDING_DESC"],
    "az":       ["TITLE_ROMAJI"],
    "za":       ["TITLE_ROMAJI_DESC"],
    "favorites":["FAVOURITES_DESC"],
}


async def browse(
    search: Optional[str]       = None,
    genres: Optional[list]      = None,
    excluded_genres: Optional[list] = None,
    tags: Optional[list]        = None,
    excluded_tags: Optional[list] = None,
    min_tag_rank: Optional[int] = None,
    format: Optional[str]       = None,
    status: Optional[str]       = None,
    season: Optional[str]       = None,
    year: Optional[int]         = None,
    year_from: Optional[int]    = None,
    year_to: Optional[int]      = None,
    episode_min: Optional[int]  = None,
    episode_max: Optional[int]  = None,
    duration_min: Optional[int] = None,
    duration_max: Optional[int] = None,
    country: Optional[str]      = None,
    source: Optional[str]       = None,
    is_adult: bool              = False,
    sort: str                   = "popular",
    page: int                   = 1,
    per_page: int               = 24,
) -> dict:
    # Convert year range to AniList FuzzyDateInt (YYYYMMDD)
    year_greater = int(f"{year_from}0000") if year_from else None
    year_lesser  = int(f"{year_to}1231")   if year_to   else None
    sort_val     = SORT_MAP.get(sort, ["POPULARITY_DESC"])

    vars = {
        "page": page, "perPage": per_page,
        "search":         search or None,
        "genres":         genres or None,
        "excludedGenres": excluded_genres or None,
        "tags":           tags or None,
        "excludedTags":   excluded_tags or None,
        "minimumTagRank": min_tag_rank,
        "format":         [format] if format else None,
        "status":         status,
        "season":         season,
        "seasonYear":     year,
        "yearGreater":    year_greater,
        "yearLesser":     year_lesser,
        "episodeGreater": episode_min,
        "episodeLesser":  episode_max,
        "durationGreater": duration_min,
        "durationLesser":  duration_max,
        "countryOfOrigin": country,
        "source":          source,
        "isAdult":         is_adult if is_adult else None,
        "sort":            sort_val,
    }
    data = await _query(BROWSE_QUERY, vars)
    return _normalize_page(data["data"]["Page"])


CHARACTER_BROWSE_QUERY = """
query ($page: Int, $perPage: Int, $search: String, $sort: [CharacterSort]) {
  Page(page: $page, perPage: $perPage) {
    pageInfo { currentPage hasNextPage lastPage total perPage }
    characters(search: $search, sort: $sort) {
      id
      name { full native }
      image { large }
      description(asHtml: false)
      gender
      age
      favourites
      media(sort: [POPULARITY_DESC], page: 1, perPage: 3) {
        nodes { id title { english romaji } coverImage { large } }
      }
    }
  }
}
"""

# Fictional characters, most favourited first, or newest added. AniList has no name sort for characters.
CHARACTER_SORT_MAP = {
    "popular": ["FAVOURITES_DESC"],
    "newest": ["ID_DESC"],
}


async def browse_characters(
    search: Optional[str] = None,
    sort: str = "popular",
    page: int = 1,
    per_page: int = 24,
) -> dict:
    """Character cards for the Characters tab. Each item is typed as character:anilist:<id>."""
    data = await _query(CHARACTER_BROWSE_QUERY, {
        "page": page,
        "perPage": min(max(per_page, 1), 50),
        "search": (search or "").strip() or None,
        "sort": CHARACTER_SORT_MAP.get(sort, CHARACTER_SORT_MAP["popular"]),
    })
    page_data = data["data"]["Page"]
    items = []
    for c in page_data.get("characters") or []:
        card = _normalize_char(c)
        card.update({"media_type": "character", "title": card["name"], "cover": card["image"],
                     "favourites": c.get("favourites")})
        items.append(card)
    return {"page_info": page_data["pageInfo"], "items": items}


async def detail(media_id: int) -> dict:
    data = await _query(DETAIL_QUERY, {"id": media_id})
    return _normalize_media(data["data"]["Media"])


async def genres() -> list:
    return await _reference_list("genres", GENRES_QUERY, lambda data: data["data"]["GenreCollection"])


async def tags() -> list:
    def normalize(data: dict) -> list:
        return [
            {"name": t["name"], "category": t["category"]}
            for t in data["data"]["MediaTagCollection"]
            if not t.get("isAdult")
        ]

    return await _reference_list("tags", TAGS_QUERY, normalize)


def _normalize_page(page: dict) -> dict:
    return {
        "page_info": page["pageInfo"],
        "items": [_normalize_media(m) for m in page["media"]],
    }


def _normalize_media(m: dict) -> dict:
    title = m.get("title") or {}
    return {
        "id":           make_id("anime", "anilist", m["id"]),
        "source_id":    m["id"],
        "source":       "anilist",
        "media_type":   "anime",
        "title":        title.get("english") or title.get("romaji") or title.get("native") or "Unknown",
        "title_native": title.get("native"),
        "title_romaji": title.get("romaji"),
        "cover":        (m.get("coverImage") or {}).get("extraLarge") or (m.get("coverImage") or {}).get("large"),
        "banner":       m.get("bannerImage"),
        "color":        (m.get("coverImage") or {}).get("color"),
        "description":  (m.get("description") or "")[:500],
        "genres":       m.get("genres") or [],
        "score":        m.get("averageScore"),
        "popularity":   m.get("popularity"),
        "year":         m.get("seasonYear"),
        "season":       m.get("season"),
        "status":       m.get("status"),
        "episodes":     m.get("episodes"),
        "format":       m.get("format"),
        "studio":       ((m.get("studios") or {}).get("nodes") or [{}])[0].get("name"),
        "characters":   [_normalize_char_edge(e) for e in ((m.get("characters") or {}).get("edges") or [])],
    }


def _normalize_char_edge(edge: dict) -> dict:
    """A character edge carries the character node plus the voice actors for this title."""
    return _normalize_char({**(edge.get("node") or {}), "voiceActors": edge.get("voiceActors") or []})


_SPOILER = re.compile(r"~!.*?!~", re.S)


def plain_text(text: Optional[str], limit: int = 300) -> str:
    """Convert AniList's occasionally-marked-up descriptions into readable plain text."""
    if not text:
        return ""
    cleaned = _SPOILER.sub("", text).replace("__", "")
    cleaned = re.sub(r"<br\s*/?>", " ", cleaned)
    cleaned = re.sub(r"<[^>]*>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"\s+([.,!?;:])", r"\1", cleaned)
    return cleaned.strip()[:limit]


def _normalize_char(c: dict) -> dict:
    name = c.get("name") or {}
    vas  = c.get("voiceActors") or []
    media = (c.get("media") or {}).get("nodes", [])
    return {
        "id":           make_id("character", "anilist", c["id"]),
        "source_id":    c["id"],
        "source":       "anilist",
        "name":         name.get("full") or "Unknown",
        "name_native":  name.get("native"),
        "image":        (c.get("image") or {}).get("large"),
        "description":  plain_text(c.get("description"), 300),
        "gender":       c.get("gender"),
        "age":          c.get("age"),
        "voice_actors": [
            {"id": va["id"], "name": (va.get("name") or {}).get("full", ""), "image": (va.get("image") or {}).get("large")}
            for va in vas
        ],
        "media_titles": [
            (m.get("title") or {}).get("english") or (m.get("title") or {}).get("romaji")
            for m in media if (m.get("title") or {}).get("english") or (m.get("title") or {}).get("romaji")
        ],
    }
