"""
core/media/igdb.py — IGDB API connector via Twitch OAuth

Filter/sort notes:
  - IGDB filters use integer IDs, never name strings.
  - All reference data returns {id, name} objects so frontend displays names but sends IDs.
  - Platform list is hardcoded (59 platforms) — covers every mainstream console/handheld
    from Atari 2600 to PlayStation 5, grouped by family.
  - Game modes and player perspectives are hardcoded (small static sets).
  - search + sort cannot be combined in IGDB (API limitation).
  - aggregated_rating = critic avg; rating = user avg. We filter on critic rating.
"""

import time, logging
from typing import Optional
from core.settings import get as get_setting
from core.item_ids import make_id
from core.media.provider_transport import ProviderError, credential_scope, request_json

IGDB_BASE    = "https://api.igdb.com/v4"
CHARACTER_LIMIT = 12   # characters fetched per game detail
TWITCH_TOKEN = "https://id.twitch.tv/oauth2/token"
IMG_BASE     = "https://images.igdb.com/igdb/image/upload"

_token_cache = {"token": None, "expires_at": 0}


async def _get_token() -> str:
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["token"]
    cid = get_setting("igdb_client_id", "")
    cs  = get_setting("igdb_client_secret", "")
    if not cid or not cs:
        raise ProviderError("IGDB credentials are not configured.", code="auth")
    scope = credential_scope("igdb", cid, cs)
    d = await request_json("igdb", "POST", TWITCH_TOKEN, params={
        "client_id": cid, "client_secret": cs, "grant_type": "client_credentials"
    }, account_scope=scope)
    if not isinstance(d, dict) or not isinstance(d.get("access_token"), str) or not d["access_token"]:
        raise ProviderError("IGDB authorization returned an invalid response.", code="invalid_response")
    _token_cache["token"]      = d["access_token"]
    _token_cache["expires_at"] = now + d.get("expires_in", 3600)
    return _token_cache["token"]


async def _query(endpoint: str, body: str) -> list:
    token = await _get_token()
    cid   = get_setting("igdb_client_id", "")
    secret = get_setting("igdb_client_secret", "")
    scope = credential_scope("igdb", cid, secret)
    result = await request_json(
        "igdb", "POST", f"{IGDB_BASE}/{endpoint}", content=body.strip(),
        headers={"Client-ID": cid, "Authorization": f"Bearer {token}",
                 "Accept": "application/json", "Content-Type": "text/plain"},
        account_scope=scope,
    )
    if not isinstance(result, list):
        raise ProviderError("IGDB returned an invalid response shape.", code="invalid_response")
    logging.info("IGDB /%s returned %s items", endpoint, len(result))
    return result


def _img(image_id: Optional[str], size: str = "cover_big") -> Optional[str]:
    if not image_id: return None
    return f"{IMG_BASE}/t_{size}/{image_id}.jpg"


def _safe_search(text: str) -> str:
    """IGDB query text is not escaped; quotes and semicolons would break or inject into the query."""
    return text.replace('"', '').replace(';', '').strip()[:100]


# ── Sort map ─────────────────────────────────────────────────────
SORT_MAP = {
    "popular": ("aggregated_rating_count desc", None),
    "rating":  ("aggregated_rating desc",       "aggregated_rating != null"),
    "newest":  ("first_release_date desc",      "first_release_date != null"),
    "oldest":  ("first_release_date asc",       "first_release_date != null"),
    "az":      ("name asc",                     None),
    "za":      ("name desc",                    None),
}

# ── Field sets ───────────────────────────────────────────────────
BROWSE_FIELDS = (
    "name,cover.image_id,summary,genres.name,themes.name,"
    "first_release_date,aggregated_rating,aggregated_rating_count,rating,"
    "platforms.id,platforms.name,game_modes.id,player_perspectives.id,"
    "involved_companies.company.name,involved_companies.developer"
)
DETAIL_FIELDS = (
    "name,cover.image_id,summary,storyline,genres.name,themes.name,"
    "first_release_date,aggregated_rating,aggregated_rating_count,rating,rating_count,"
    "platforms.id,platforms.name,game_modes.id,game_modes.name,"
    "player_perspectives.id,player_perspectives.name,"
    "involved_companies.company.name,involved_companies.developer,"
    "screenshots.image_id,"
    "age_ratings.category,age_ratings.rating"
)

# ── Hardcoded reference data ─────────────────────────────────────
# 59 platforms covering every mainstream console/handheld from 1977–2026.
# IDs are stable IGDB constants. Grouped for UI display.
PLATFORM_LIST = [
    # Current Gen
    {"id": 167, "name": "PlayStation 5",                       "group": "Current Gen"},
    {"id": 169, "name": "Xbox Series X|S",                     "group": "Current Gen"},
    {"id": 130, "name": "Nintendo Switch",                     "group": "Current Gen"},
    {"id": 508, "name": "Nintendo Switch 2",                   "group": "Current Gen"},
    # PC & Desktop
    {"id": 6,   "name": "PC (Microsoft Windows)",              "group": "PC & Desktop"},
    {"id": 14,  "name": "Mac",                                 "group": "PC & Desktop"},
    {"id": 3,   "name": "Linux",                               "group": "PC & Desktop"},
    # Mobile
    {"id": 34,  "name": "Android",                             "group": "Mobile"},
    {"id": 39,  "name": "iOS",                                 "group": "Mobile"},
    # PlayStation
    {"id": 48,  "name": "PlayStation 4",                       "group": "PlayStation"},
    {"id": 9,   "name": "PlayStation 3",                       "group": "PlayStation"},
    {"id": 8,   "name": "PlayStation 2",                       "group": "PlayStation"},
    {"id": 7,   "name": "PlayStation",                         "group": "PlayStation"},
    {"id": 46,  "name": "PlayStation Vita",                    "group": "PlayStation"},
    {"id": 38,  "name": "PlayStation Portable",                "group": "PlayStation"},
    # Xbox
    {"id": 49,  "name": "Xbox One",                            "group": "Xbox"},
    {"id": 12,  "name": "Xbox 360",                            "group": "Xbox"},
    {"id": 11,  "name": "Xbox",                                "group": "Xbox"},
    # Nintendo Home
    {"id": 41,  "name": "Wii U",                               "group": "Nintendo"},
    {"id": 5,   "name": "Wii",                                 "group": "Nintendo"},
    {"id": 21,  "name": "Nintendo GameCube",                   "group": "Nintendo"},
    {"id": 4,   "name": "Nintendo 64",                         "group": "Nintendo"},
    {"id": 19,  "name": "Super Nintendo Entertainment System", "group": "Nintendo"},
    {"id": 18,  "name": "Nintendo Entertainment System",       "group": "Nintendo"},
    # Nintendo Handheld
    {"id": 137, "name": "New Nintendo 3DS",                    "group": "Nintendo Handheld"},
    {"id": 37,  "name": "Nintendo 3DS",                        "group": "Nintendo Handheld"},
    {"id": 159, "name": "Nintendo DSi",                        "group": "Nintendo Handheld"},
    {"id": 20,  "name": "Nintendo DS",                         "group": "Nintendo Handheld"},
    {"id": 24,  "name": "Game Boy Advance",                    "group": "Nintendo Handheld"},
    {"id": 22,  "name": "Game Boy Color",                      "group": "Nintendo Handheld"},
    {"id": 33,  "name": "Game Boy",                            "group": "Nintendo Handheld"},
    {"id": 87,  "name": "Virtual Boy",                         "group": "Nintendo Handheld"},
    # Sega
    {"id": 23,  "name": "Dreamcast",                           "group": "Sega"},
    {"id": 32,  "name": "Sega Saturn",                         "group": "Sega"},
    {"id": 30,  "name": "Sega 32X",                            "group": "Sega"},
    {"id": 78,  "name": "Sega CD",                             "group": "Sega"},
    {"id": 29,  "name": "Sega Mega Drive/Genesis",             "group": "Sega"},
    {"id": 64,  "name": "Sega Master System/Mark III",         "group": "Sega"},
    {"id": 84,  "name": "SG-1000",                             "group": "Sega"},
    {"id": 35,  "name": "Game Gear",                           "group": "Sega"},
    # Atari
    {"id": 62,  "name": "Atari Jaguar",                        "group": "Atari"},
    {"id": 410, "name": "Atari Jaguar CD",                     "group": "Atari"},
    {"id": 60,  "name": "Atari 7800",                          "group": "Atari"},
    {"id": 66,  "name": "Atari 5200",                          "group": "Atari"},
    {"id": 59,  "name": "Atari 2600",                          "group": "Atari"},
    {"id": 63,  "name": "Atari ST/STE",                        "group": "Atari"},
    {"id": 61,  "name": "Atari Lynx",                          "group": "Atari"},
    # NEC
    {"id": 86,  "name": "PC Engine/TurboGrafx-16",             "group": "NEC"},
    {"id": 128, "name": "PC Engine SuperGrafx",                "group": "NEC"},
    {"id": 274, "name": "PC-FX",                               "group": "NEC"},
    # SNK
    {"id": 80,  "name": "Neo Geo AES",                         "group": "SNK"},
    {"id": 136, "name": "Neo Geo CD",                          "group": "SNK"},
    {"id": 119, "name": "Neo Geo Pocket",                      "group": "SNK"},
    {"id": 120, "name": "Neo Geo Pocket Color",                "group": "SNK"},
    # Bandai
    {"id": 57,  "name": "WonderSwan",                          "group": "Bandai"},
    {"id": 123, "name": "WonderSwan Color",                    "group": "Bandai"},
    # Classic / Other
    {"id": 68,  "name": "ColecoVision",                        "group": "Classic"},
    {"id": 67,  "name": "Intellivision",                       "group": "Classic"},
    {"id": 50,  "name": "3DO Interactive Multiplayer",         "group": "Classic"},
]

GAME_MODE_LIST = [
    {"id": 1,   "name": "Single Player"},
    {"id": 2,   "name": "Multiplayer"},
    {"id": 3,   "name": "Co-operative"},
    {"id": 4,   "name": "Split Screen"},
    {"id": 5,   "name": "Massively Multiplayer Online (MMO)"},
    {"id": 6,   "name": "Battle Royale"},
]

PERSPECTIVE_LIST = [
    {"id": 1,   "name": "First Person"},
    {"id": 2,   "name": "Third Person"},
    {"id": 3,   "name": "Bird view / Isometric"},
    {"id": 4,   "name": "Side View"},
    {"id": 5,   "name": "Text"},
    {"id": 6,   "name": "Auditory"},
    {"id": 7,   "name": "Virtual Reality"},
]

AGE_RATING_CATEGORY = {1: "ESRB", 2: "PEGI", 3: "CERO", 4: "USK", 5: "GRAC", 6: "CLASS_IND", 7: "ACB"}
ESRB_RATING  = {1: "Rating Pending", 2: "Early Childhood", 3: "Everyone", 4: "Everyone 10+", 5: "Teen", 6: "Mature 17+", 7: "Adults Only 18+"}
PEGI_RATING  = {1: "3", 2: "7", 3: "12", 4: "16", 5: "18"}
CERO_RATING  = {1: "A", 2: "B", 3: "C", 4: "D", 5: "Z"}
USK_RATING   = {1: "0", 2: "6", 3: "12", 4: "16", 5: "18"}
GRAC_RATING  = {1: "ALL", 2: "12+", 3: "15+", 4: "18+", 5: "Testing"}
ACB_RATING   = {1: "G", 2: "PG", 3: "M", 4: "MA 15+", 5: "R 18+", 6: "RC"}


# ── Reference endpoints ──────────────────────────────────────────

async def genres() -> list:
    items = await _query("genres", "fields id,name; limit 50; sort name asc;")
    return [{"id": g["id"], "name": g["name"]} for g in items if g.get("name")]

async def themes() -> list:
    items = await _query("themes", "fields id,name; limit 50; sort name asc;")
    return [{"id": t["id"], "name": t["name"]} for t in items if t.get("name")]

def platforms() -> list:
    return PLATFORM_LIST

def game_modes() -> list:
    return GAME_MODE_LIST

def perspectives() -> list:
    return PERSPECTIVE_LIST

async def search_platforms(query: str) -> list:
    """Live platform search for the custom platform entry field.
    Returns {id, name} matches from IGDB for platforms not in the hardcoded list."""
    if not query or len(query.strip()) < 2:
        return []
    safe = _safe_search(query)[:50]
    # IGDB retired platforms.category; platform_type (1 Console, 5 Portable, 6 Computer) replaces it.
    items = await _query(
        "platforms",
        f'search "{safe}"; fields id,name,platform_type; where platform_type = (1,5,6); limit 15;'
    )
    # Filter out IDs already in hardcoded list so we don't show dupes
    known_ids = {p["id"] for p in PLATFORM_LIST}
    return [
        {"id": p["id"], "name": p["name"]}
        for p in items
        if p.get("name") and p["id"] not in known_ids
    ]


# ── Browse ───────────────────────────────────────────────────────

async def browse(
    search:       Optional[str]  = None,
    sort:         str            = "popular",
    genres:       Optional[list] = None,
    themes:       Optional[list] = None,
    platforms:    Optional[list] = None,
    game_modes:   Optional[list] = None,
    perspectives: Optional[list] = None,
    year_from:    Optional[int]  = None,
    year_to:      Optional[int]  = None,
    rating_min:   Optional[int]  = None,
    rating_max:   Optional[int]  = None,
    page:         int            = 1,
    per_page:     int            = 24,
) -> dict:
    per_page = min(per_page, 50)
    offset   = min((page - 1) * per_page, 450)

    where = ["cover != null", "version_parent = null"]

    sort_clause = sort_required = None
    if sort in SORT_MAP:
        sort_clause, sort_required = SORT_MAP[sort]
        if sort_required:
            where.append(sort_required)

    if rating_min is not None: where.append(f"aggregated_rating >= {rating_min}")
    if rating_max is not None: where.append(f"aggregated_rating <= {rating_max}")
    if year_from:
        ts = int(time.mktime(time.strptime(f"{year_from}-01-01", "%Y-%m-%d")))
        where.append(f"first_release_date >= {ts}")
    if year_to:
        ts = int(time.mktime(time.strptime(f"{year_to}-12-31", "%Y-%m-%d")))
        where.append(f"first_release_date <= {ts}")

    def _ids(lst):
        result = []
        for v in (lst or []):
            try: result.append(int(v))
            except (TypeError, ValueError): pass
        return result

    if ids := _ids(genres):      where.append(f"genres = ({','.join(map(str,ids))})")
    if ids := _ids(themes):      where.append(f"themes = ({','.join(map(str,ids))})")
    if ids := _ids(platforms):   where.append(f"platforms = ({','.join(map(str,ids))})")
    if ids := _ids(game_modes):  where.append(f"game_modes = ({','.join(map(str,ids))})")
    if ids := _ids(perspectives): where.append(f"player_perspectives = ({','.join(map(str,ids))})")

    where_str = " & ".join(where)

    if search:
        body = f'search "{_safe_search(search)}"; fields {BROWSE_FIELDS}; where {where_str}; limit {per_page}; offset {offset};'
    elif sort_clause:
        body = f"fields {BROWSE_FIELDS}; where {where_str}; sort {sort_clause}; limit {per_page}; offset {offset};"
    else:
        body = f"fields {BROWSE_FIELDS}; sort aggregated_rating_count desc; where {where_str}; limit {per_page}; offset {offset};"

    items = await _query("games", body)
    return {
        "page_info": {"currentPage": page, "hasNextPage": len(items) == per_page},
        "items":     [_normalize(g) for g in items],
    }


async def detail(game_id: int) -> dict:
    body  = f"fields {DETAIL_FIELDS}; where id = {game_id}; limit 1;"
    items = await _query("games", body)
    if not items: raise ValueError(f"Game {game_id} not found")
    # The games endpoint has no usable characters field. Characters are queried from their own
    # endpoint by the games they appear in.
    characters = await _query(
        "characters",
        f"fields name,description,mug_shot.image_id; where games = {int(game_id)}; limit {CHARACTER_LIMIT};",
    )
    return _normalize_detail(items[0], characters)


async def browse_characters(search: Optional[str] = None, sort: str = "popular", page: int = 1, per_page: int = 24) -> dict:
    """Browse IGDB's global character catalog, optionally searching character names."""
    page = max(1, page)
    per_page = min(max(1, per_page), 50)
    offset = (page - 1) * per_page
    fields = "name,description,mug_shot.image_id,games.id,games.name"
    if search and search.strip():
        body = f'search "{_safe_search(search)}"; fields {fields}; limit {per_page + 1}; offset {offset};'
    else:
        sort_clause = {"az": "name asc", "za": "name desc", "newest": "created_at desc"}.get(sort, "created_at desc")
        body = f"fields {fields}; sort {sort_clause}; limit {per_page + 1}; offset {offset};"
    rows = await _query("characters", body)
    has_next = len(rows) > per_page
    items = [_normalize_character(row) for row in rows[:per_page]]
    return {"page_info": {"currentPage": page, "hasNextPage": has_next, "lastPage": page + (1 if has_next else 0),
                           "total": None}, "items": items}


# ── Normalizers ──────────────────────────────────────────────────

def _normalize(g: dict) -> dict:
    year = None
    if ts := g.get("first_release_date"):
        try: year = int(time.strftime("%Y", time.gmtime(ts)))
        except: pass
    devs  = [c["company"]["name"] for c in (g.get("involved_companies") or [])
             if c.get("developer") and isinstance(c.get("company"), dict)]
    score = round(g["aggregated_rating"]) if g.get("aggregated_rating") else (
            round(g["rating"]) if g.get("rating") else None)
    return {
        "id":          make_id("game", "igdb", g["id"]),
        "source_id":   g["id"],
        "source":      "igdb",
        "media_type":  "game",
        "title":       g.get("name", "Unknown"),
        "cover":       _img((g.get("cover") or {}).get("image_id")),
        "banner":      None,
        "description": (g.get("summary") or "")[:500],
        "genres":      [gn["name"] for gn in (g.get("genres") or []) if isinstance(gn, dict)],
        "themes":      [t["name"]  for t  in (g.get("themes")  or []) if isinstance(t,  dict)],
        "score":       score,
        "popularity":  g.get("aggregated_rating_count"),
        "year":        year,
        "platforms":   [p["name"] for p in (g.get("platforms") or []) if isinstance(p, dict)],
        "developer":   devs[0] if devs else None,
        "characters":  [],
    }


def _normalize_detail(g: dict, characters: list) -> dict:
    base  = _normalize(g)
    chars = [{**_normalize_character(c), "media_title": base["title"], "media_id": base["source_id"], "media_type": "game"}
             for c in characters if isinstance(c, dict)]
    shots = [_img(s["image_id"], "screenshot_big") for s in (g.get("screenshots") or [])[:5]
             if isinstance(s, dict) and s.get("image_id")]
    age_ratings = []
    for ar in (g.get("age_ratings") or []):
        if not isinstance(ar, dict): continue
        org_id, rate_id = ar.get("category"), ar.get("rating")
        org    = AGE_RATING_CATEGORY.get(org_id, "")
        maps   = {1: ESRB_RATING, 2: PEGI_RATING, 3: CERO_RATING,
                  4: USK_RATING,  5: GRAC_RATING,  7: ACB_RATING}
        label  = maps.get(org_id, {}).get(rate_id, str(rate_id))
        if org: age_ratings.append({"org": org, "rating": label})
    return {
        **base,
        "characters":   chars,
        "screenshots":  shots,
        "storyline":    g.get("storyline"),
        "rating_count": g.get("rating_count"),
        "game_modes":   [m["name"] for m in (g.get("game_modes") or []) if isinstance(m, dict)],
        "perspectives": [p["name"] for p in (g.get("player_perspectives") or []) if isinstance(p, dict)],
        "age_ratings":  age_ratings,
    }


def _normalize_character(c: dict) -> dict:
    games = c.get("games") or []
    return {
        "id": make_id("character", "igdb", c.get("id", 0)),
        "source_id": c.get("id", 0),
        "source": "igdb",
        "media_type": "character",
        "name": c.get("name", "Unknown"),
        "title": c.get("name", "Unknown"),
        "image": _img((c.get("mug_shot") or {}).get("image_id"), "thumb"),
        "cover": _img((c.get("mug_shot") or {}).get("image_id"), "thumb"),
        "description": (c.get("description") or "")[:300],
        "media_titles": [g.get("name") for g in games if isinstance(g, dict) and g.get("name")][:5],
        "voice_actors": [],
    }
