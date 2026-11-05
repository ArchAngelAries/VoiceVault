"""
core/media/people.py — People/Voice Actors connector

Sort reality (documented from API testing):
  TMDB /person/popular  — zero sort/filter params. We fetch 5 pages in parallel
                          (100 results) and sort entirely app-side.
  AniList staff browse  — NAME/NAME_DESC sort requires a search query to be
                          present or AniList returns 500. Without search, only
                          FAVOURITES_DESC is safe. So: always fetch with
                          FAVOURITES_DESC and sort app-side for everything.
                          With search, SEARCH_MATCH is used (AniList default).

Gender filter:
  TMDB individual person pages include gender (0=unset, 1=female, 2=male, 3=nb)
  but /person/popular does not return gender in list results. We fetch gender
  from the detail endpoint only, so gender filtering on browse is not feasible
  without individual API calls per person. We surface gender in the detail modal
  only (via detail endpoint).
  AniList staff returns gender in the detail query but not in browse Page results.
"""

import asyncio
from typing import Optional
from core.media.tmdb import _get as tmdb_get, _img
from core.media.provider_transport import request_json
from core.item_ids import make_id, parse_id

ANILIST_URL  = "https://graphql.anilist.co"
PER_PAGE     = 24
TMDB_PAGES   = 5   # 5 × 20 = 100 results to sort over
ANILIST_SIZE = 50  # single large fetch, sort app-side


# ── AniList GraphQL ──────────────────────────────────────────────
# Browse: no sort param sent (avoid AniList 500 on NAME sort without search)
# Detail: full nested query is safe — single staff node, not a Page

STAFF_BROWSE_QUERY = """
query ($page: Int, $perPage: Int, $search: String) {
  Page(page: $page, perPage: $perPage) {
    pageInfo { total currentPage lastPage hasNextPage }
    staff(search: $search, sort: FAVOURITES_DESC) {
      id
      name { full native userPreferred }
      image { large }
      primaryOccupations
      languageV2
      favourites
    }
  }
}
"""

STAFF_BROWSE_QUERY_SORTED = """
query ($page: Int, $perPage: Int, $search: String, $sort: [StaffSort]) {
  Page(page: $page, perPage: $perPage) {
    pageInfo { total currentPage lastPage hasNextPage }
    staff(search: $search, sort: $sort) {
      id
      name { full native userPreferred }
      image { large }
      primaryOccupations
      languageV2
      favourites
    }
  }
}
"""

STAFF_DETAIL_QUERY = """
query ($id: Int) {
  Staff(id: $id) {
    id
    name { full native userPreferred }
    image { large }
    description(asHtml: false)
    primaryOccupations
    languageV2
    yearsActive
    homeTown
    gender
    age
    dateOfBirth { year month day }
    characters(perPage: 20, sort: [FAVOURITES_DESC]) {
      nodes {
        id name { full }
        image { large }
      }
    }
    staffMedia(perPage: 20, sort: [START_DATE_DESC], onList: false) {
      nodes { id title { romaji english } coverImage { large } seasonYear format }
    }
    favourites
  }
}
"""


async def _anilist_query(q: str, variables: dict) -> dict:
    clean = {k: v for k, v in variables.items() if v is not None}
    data = await request_json(
        "anilist", "POST", ANILIST_URL,
        json={"query": q, "variables": clean},
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        graphql=True,
    )
    if not isinstance(data, dict):
        raise ValueError("AniList returned an invalid response shape.")
    return data


# ── App-side sort ────────────────────────────────────────────────

def _sort_people(items: list, sort: str) -> list:
    if sort == "popular":
        return sorted(items, key=lambda x: x.get("popularity", 0), reverse=True)
    if sort == "az":
        return sorted(items, key=lambda x: (x.get("name") or "").lower())
    if sort == "za":
        return sorted(items, key=lambda x: (x.get("name") or "").lower(), reverse=True)
    return items  # default: keep API order


def _paginate(items: list, page: int, api_has_more: bool = False) -> dict:
    total     = len(items)
    last_page = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    start     = (page - 1) * PER_PAGE
    sliced    = items[start : start + PER_PAGE]
    has_next  = (page < last_page) or (page == last_page and api_has_more)
    return {
        "page_info": {
            "currentPage": page,
            "lastPage":    last_page + (1 if api_has_more else 0),
            "total":       total,
            "hasNextPage": has_next,
        },
        "items": sliced,
    }


# ── TMDB People ──────────────────────────────────────────────────

async def _fetch_tmdb_page(search: Optional[str], api_page: int) -> list:
    try:
        if search:
            data = await tmdb_get("/search/person", {"query": search, "page": api_page})
        else:
            data = await tmdb_get("/person/popular", {"page": api_page})
        return [_normalize_tmdb_person(p) for p in data.get("results", [])]
    except Exception:
        return []


async def browse_tmdb_people(
    search: Optional[str]     = None,
    sort: str                 = "popular",
    department: Optional[str] = None,
    page: int                 = 1,
) -> dict:
    pages = TMDB_PAGES if not search else 3
    batches = await asyncio.gather(*[_fetch_tmdb_page(search, p) for p in range(1, pages + 1)])

    # Flatten + deduplicate
    seen, all_items = set(), []
    for batch in batches:
        for item in batch:
            if item["source_id"] not in seen:
                seen.add(item["source_id"])
                all_items.append(item)

    if department:
        all_items = [i for i in all_items if i.get("department", "").lower() == department.lower()]

    all_items = _sort_people(all_items, sort)
    return _paginate(all_items, page)


async def detail_tmdb_person(person_id: int) -> dict:
    data = await tmdb_get(
        f"/person/{person_id}",
        {"append_to_response": "combined_credits,external_ids"},
    )
    return _normalize_tmdb_person_detail(data)


# ── AniList Staff ────────────────────────────────────────────────

async def browse_anilist_staff(
    search: Optional[str]   = None,
    sort: str               = "popular",
    language: Optional[str] = None,
    page: int               = 1,
) -> dict:
    # With search: SEARCH_MATCH is safe (AniList default when search is present)
    # Without search: only FAVOURITES_DESC is stable — always use that, sort app-side
    if search:
        query = STAFF_BROWSE_QUERY_SORTED
        variables = {"page": 1, "perPage": ANILIST_SIZE, "search": search, "sort": ["SEARCH_MATCH"]}
    else:
        query = STAFF_BROWSE_QUERY
        variables = {"page": 1, "perPage": ANILIST_SIZE}

    data      = await _anilist_query(query, variables)
    page_data = data["data"]["Page"]
    all_items = [_normalize_anilist_staff(s) for s in page_data.get("staff", [])]

    if language:
        all_items = [i for i in all_items if i.get("language", "").lower() == language.lower()]

    all_items = _sort_people(all_items, sort)

    api_has_more = page_data["pageInfo"]["hasNextPage"]
    result       = _paginate(all_items, page, api_has_more)
    result["page_info"]["total"] = page_data["pageInfo"]["total"]
    return result


async def detail_anilist_staff(staff_id: int) -> dict:
    data = await _anilist_query(STAFF_DETAIL_QUERY, {"id": staff_id})
    return _normalize_anilist_staff_detail(data["data"]["Staff"])


# ── Combined entry points ────────────────────────────────────────

async def browse(
    source: str               = "tmdb",
    search: Optional[str]     = None,
    sort: str                 = "popular",
    department: Optional[str] = None,
    language: Optional[str]   = None,
    page: int                 = 1,
    per_page: int             = 24,
) -> dict:
    if source == "anilist":
        return await browse_anilist_staff(search=search, sort=sort, language=language, page=page)
    return await browse_tmdb_people(search=search, sort=sort, department=department, page=page)


async def detail(person_id: str) -> dict:
    item_type, source, raw_id = parse_id(person_id)  # e.g. "person:anilist:95272"
    if item_type != "person":
        raise ValueError(f"Not a person id: {person_id}")
    if source == "anilist":
        return await detail_anilist_staff(raw_id)
    if source == "tmdb":
        return await detail_tmdb_person(raw_id)
    raise ValueError(f"Unsupported person source: {source}")


# ── Normalizers ──────────────────────────────────────────────────

def _normalize_tmdb_person(p: dict) -> dict:
    known_for = p.get("known_for", [])
    roles = [r.get("title") or r.get("name") for r in known_for if r.get("title") or r.get("name")]
    return {
        "id":          make_id("person", "tmdb", p["id"]),
        "source_id":   p["id"],
        "source":      "tmdb",
        "media_type":  "person",
        "name":        p.get("name", "Unknown"),
        "photo":       _img(p.get("profile_path"), "w500"),
        "department":  p.get("known_for_department", ""),
        "known_for":   roles[:3],
        "popularity":  p.get("popularity", 0),
        "language":    None,
        "characters":  [],
        "occupations": [p.get("known_for_department")] if p.get("known_for_department") else [],
        # gender not available in list results — only in detail endpoint
        "gender":      None,
    }


def _normalize_tmdb_person_detail(p: dict) -> dict:
    base    = _normalize_tmdb_person(p)
    credits = p.get("combined_credits", {})
    cast    = credits.get("cast", [])[:20]
    crew    = credits.get("crew", [])[:10]
    filmography = []
    for c in cast:
        filmography.append({
            "id":         c.get("id"),
            "title":      c.get("title") or c.get("name", ""),
            "character":  c.get("character", ""),
            "cover":      _img(c.get("poster_path")),
            "year":       int((c.get("release_date") or c.get("first_air_date") or "0")[:4] or 0) or None,
            "media_type": c.get("media_type", "movie"),
            "type":       "acting",
        })
    # TMDB gender: 0=not set, 1=female, 2=male, 3=non-binary
    gender_map = {0: None, 1: "Female", 2: "Male", 3: "Non-binary"}
    return {
        **base,
        "bio":           (p.get("biography") or "")[:1000],
        "birthday":      p.get("birthday"),
        "birthplace":    p.get("place_of_birth"),
        "also_known_as": p.get("also_known_as", [])[:5],
        "homepage":      p.get("homepage"),
        "gender":        gender_map.get(p.get("gender", 0)),
        "filmography":   filmography,
        "crew_credits":  [{"title": c.get("title") or c.get("name", ""), "job": c.get("job", "")} for c in crew],
    }


def _normalize_anilist_staff(s: dict) -> dict:
    name = s.get("name") or {}
    return {
        "id":          make_id("person", "anilist", s["id"]),
        "source_id":   s["id"],
        "source":      "anilist",
        "media_type":  "person",
        "name":        name.get("userPreferred") or name.get("full") or "Unknown",
        "name_native": name.get("native"),
        "photo":       (s.get("image") or {}).get("large"),
        "department":  "Voice Acting",
        "language":    s.get("languageV2", ""),
        "known_for":   [],
        "popularity":  s.get("favourites", 0),
        "occupations": s.get("primaryOccupations") or ["Voice Actor"],
        "characters":  [],
        "gender":      None,   # not in browse response; available in detail
    }


def _normalize_anilist_staff_detail(s: dict) -> dict:
    base  = _normalize_anilist_staff(s)
    chars = s.get("characters", {}).get("nodes", [])
    media = s.get("staffMedia", {}).get("nodes", [])
    dob   = s.get("dateOfBirth") or {}
    parts = [str(dob.get("year") or ""), str(dob.get("month") or "").zfill(2), str(dob.get("day") or "").zfill(2)]
    birthday = "-".join(p for p in parts if p.strip("0")) if any(dob.values()) else None
    return {
        **base,
        "bio":          (s.get("description") or "")[:1000],
        "birthday":     birthday,
        "birthplace":   s.get("homeTown"),
        "gender":       s.get("gender"),   # string from AniList: "Male", "Female", "Non-binary", etc.
        "age":          s.get("age"),
        "years_active": s.get("yearsActive", []),
        "characters":   [_normalize_anilist_char_preview(c) for c in chars[:20]],
        "filmography":  [
            {
                "id":         m.get("id"),
                "title":      (m.get("title") or {}).get("english") or (m.get("title") or {}).get("romaji") or "",
                "cover":      (m.get("coverImage") or {}).get("large"),
                "year":       m.get("seasonYear"),
                "media_type": "anime",
                "format":     m.get("format"),
                "type":       "staff",
            }
            for m in media
        ],
    }


def _normalize_anilist_char_preview(c: dict) -> dict:
    media_nodes = (c.get("media") or {}).get("nodes", [])
    media_title = media_cover = None
    if media_nodes:
        t           = media_nodes[0].get("title") or {}
        media_title = t.get("english") or t.get("romaji")
        media_cover = (media_nodes[0].get("coverImage") or {}).get("large")
    return {
        "id": make_id("character", "anilist", c.get("id")),
        "name":        (c.get("name") or {}).get("full", "Unknown"),
        "image":       (c.get("image") or {}).get("large"),
        "media_title": media_title,
        "media_cover": media_cover,
    }
