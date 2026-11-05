"""
core/media/tmdb.py — TMDB API connector
Uses /discover endpoints for full filter/sort support.
"""

import hashlib
from typing import Optional
from core.settings import get as get_setting
from core.item_ids import make_id
from core.media.provider_transport import ProviderError, credential_scope, request_json

TMDB_BASE = "https://api.themoviedb.org/3"
IMG_BASE  = "https://image.tmdb.org/t/p"

SORT_MAP = {
    "popular":      "popularity.desc",
    "popular_asc":  "popularity.asc",
    "rating":       "vote_average.desc",
    "rating_asc":   "vote_average.asc",
    "newest":       "primary_release_date.desc",
    "oldest":       "primary_release_date.asc",
    "az":           "title.asc",
    "za":           "title.desc",
    "revenue":      "revenue.desc",
}

SORT_MAP_TV = {
    **SORT_MAP,
    "newest": "first_air_date.desc",
    "oldest": "first_air_date.asc",
    "az":     "name.asc",
    "za":     "name.desc",
}


def _key() -> str:
    return get_setting("tmdb_api_key", "")

def _img(path: Optional[str], size: str = "w500") -> Optional[str]:
    if not path: return None
    return f"{IMG_BASE}/{size}{path}"

def _is_jwt(key: str) -> bool:
    return key.startswith("eyJ")


async def _get(endpoint: str, params: dict = {}) -> dict:
    key = _key()
    if not key:
        raise ProviderError("TMDB API credentials are not configured.", code="auth")
    if _is_jwt(key):
        result = await request_json(
            "tmdb", "GET", f"{TMDB_BASE}{endpoint}",
            params={"language": "en-US", **params},
            headers={"Authorization": f"Bearer {key}", "accept": "application/json"},
            account_scope=credential_scope("tmdb", key),
        )
    else:
        result = await request_json(
            "tmdb", "GET", f"{TMDB_BASE}{endpoint}",
            params={"api_key": key, "language": "en-US", **params},
            account_scope=credential_scope("tmdb", key),
        )
    if not isinstance(result, dict):
        raise ProviderError("TMDB returned an invalid response shape.", code="invalid_response")
    return result


# ── Movies ───────────────────────────────────────────────────────

async def browse_movies(
    search: Optional[str]        = None,
    sort: str                    = "popular",
    genres: Optional[list]       = None,
    certification: Optional[str] = None,
    year_from: Optional[int]     = None,
    year_to: Optional[int]       = None,
    score_min: Optional[float]   = None,
    score_max: Optional[float]   = None,
    vote_min: Optional[int]      = None,
    runtime_min: Optional[int]   = None,
    runtime_max: Optional[int]   = None,
    language: Optional[str]      = None,
    keywords: Optional[str]      = None,
    page: int                    = 1,
) -> dict:
    if search:
        params = {"query": search, "page": page}
        data = await _get("/search/movie", params)
        return _normalize_page(data, "movie")

    params = {
        "page":              page,
        "sort_by":           SORT_MAP.get(sort, "popularity.desc"),
        "vote_count.gte":    vote_min or 50,
    }
    if genres:          params["with_genres"]           = ",".join(str(g) for g in genres)
    if certification:   params["certification"]         = certification; params["certification_country"] = "US"
    if year_from:       params["primary_release_date.gte"] = f"{year_from}-01-01"
    if year_to:         params["primary_release_date.lte"] = f"{year_to}-12-31"
    if score_min:       params["vote_average.gte"]      = score_min
    if score_max:       params["vote_average.lte"]      = score_max
    if runtime_min:     params["with_runtime.gte"]      = runtime_min
    if runtime_max:     params["with_runtime.lte"]      = runtime_max
    if language:        params["with_original_language"] = language
    if keywords:        params["with_keywords"]         = keywords

    data = await _get("/discover/movie", params)
    return _normalize_page(data, "movie")


async def browse_tv(
    search: Optional[str]        = None,
    sort: str                    = "popular",
    genres: Optional[list]       = None,
    certification: Optional[str] = None,
    year_from: Optional[int]     = None,
    year_to: Optional[int]       = None,
    score_min: Optional[float]   = None,
    score_max: Optional[float]   = None,
    vote_min: Optional[int]      = None,
    runtime_min: Optional[int]   = None,
    runtime_max: Optional[int]   = None,
    language: Optional[str]      = None,
    network: Optional[str]       = None,
    keywords: Optional[str]      = None,
    page: int                    = 1,
) -> dict:
    if search:
        data = await _get("/search/tv", {"query": search, "page": page})
        return _normalize_page(data, "tv")

    params = {
        "page":           page,
        "sort_by":        SORT_MAP_TV.get(sort, "popularity.desc"),
        "vote_count.gte": vote_min or 20,
    }
    if genres:        params["with_genres"]          = ",".join(str(g) for g in genres)
    if certification: params["certification"]        = certification; params["certification_country"] = "US"
    if year_from:     params["first_air_date.gte"]   = f"{year_from}-01-01"
    if year_to:       params["first_air_date.lte"]   = f"{year_to}-12-31"
    if score_min:     params["vote_average.gte"]     = score_min
    if score_max:     params["vote_average.lte"]     = score_max
    if runtime_min:   params["with_runtime.gte"]     = runtime_min
    if runtime_max:   params["with_runtime.lte"]     = runtime_max
    if language:      params["with_original_language"] = language
    if network:       params["with_networks"]        = network
    if keywords:      params["with_keywords"]        = keywords

    data = await _get("/discover/tv", params)
    return _normalize_page(data, "tv")


async def movie_genres() -> list:
    data = await _get("/genre/movie/list")
    return data.get("genres", [])

async def tv_genres() -> list:
    data = await _get("/genre/tv/list")
    return data.get("genres", [])

async def movie_detail(tmdb_id: int) -> dict:
    data = await _get(f"/movie/{tmdb_id}", {"append_to_response": "credits"})
    return _normalize_movie_detail(data)

async def tv_detail(tmdb_id: int) -> dict:
    data = await _get(f"/tv/{tmdb_id}", {"append_to_response": "credits"})
    return _normalize_tv_detail(data)


# ── Normalize ────────────────────────────────────────────────────

def _normalize_page(data: dict, mt: str) -> dict:
    return {
        "page_info": {
            "currentPage": data.get("page", 1),
            "lastPage":    data.get("total_pages", 1),
            "total":       data.get("total_results", 0),
            "hasNextPage": data.get("page", 1) < data.get("total_pages", 1),
        },
        "items": [_normalize_item({**r, "media_type": mt}) for r in data.get("results", [])],
    }

def _normalize_item(r: dict) -> dict:
    mt = r.get("media_type", "movie")
    return {
        "id":          make_id(mt, "tmdb", r["id"]),
        "source_id":   r["id"],
        "source":      "tmdb",
        "media_type":  mt,
        "title":       r.get("title") or r.get("name") or "Unknown",
        "cover":       _img(r.get("poster_path")),
        "banner":      _img(r.get("backdrop_path"), "original"),
        "description": (r.get("overview") or "")[:500],
        "genres":      [],
        "score":       round(r.get("vote_average", 0) * 10) if r.get("vote_average") else None,
        "popularity":  r.get("popularity"),
        "year":        int((r.get("release_date") or r.get("first_air_date") or "0")[:4] or 0) or None,
        "characters":  [],
    }

def _normalize_movie_detail(r: dict) -> dict:
    cast = r.get("credits", {}).get("cast", [])[:15]
    return {
        **_normalize_item({**r, "media_type": "movie"}),
        "genres":     [g["name"] for g in r.get("genres", [])],
        "characters": [_normalize_cast(c, "movie", r["id"], r.get("title") or "") for c in cast],
        "runtime":    r.get("runtime"),
        "tagline":    r.get("tagline"),
    }

def _normalize_tv_detail(r: dict) -> dict:
    cast = r.get("credits", {}).get("cast", [])[:15]
    return {
        **_normalize_item({**r, "media_type": "tv"}),
        "genres":     [g["name"] for g in r.get("genres", [])],
        "characters": [_normalize_cast(c, "tv", r["id"], r.get("name") or "") for c in cast],
        "episodes":   r.get("number_of_episodes"),
        "seasons":    r.get("number_of_seasons"),
        "status":     r.get("status"),
        "tagline":    r.get("tagline"),
    }

def _normalize_cast(c: dict, media_type: str = "movie", media_id: int = 0, media_title: str = "") -> dict:
    role = c.get("character") or "Unknown"
    fingerprint = "|".join((media_type, str(media_id), str(c.get("credit_id") or ""),
                            str(c.get("id") or ""), role.strip().casefold()))
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:24]
    role_id = f"character:tmdb:{media_type}-{int(media_id)}-{digest}"
    return {
        "id": role_id,
        "source": "tmdb",
        "source_id": role_id.split(":", 2)[2],
        "media_type": "character",
        "media_id": media_id,
        "media_title": media_title,
        "name": role,
        "title": role,
        "actor": c.get("name"),
        "actor_id": c.get("id"),
        "legacy_id": make_id("character", "tmdb", c["id"]) if c.get("id") else None,
        "image": _img(c.get("profile_path")),
        "cover": _img(c.get("profile_path")),
    }
