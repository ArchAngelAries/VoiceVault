"""
Typed item IDs; TMDB role IDs include title and credit context.

The type is part of the ID, so an anime and a staff member with the same AniList number
can no longer collide in favorites and collections. Legacy IDs (for example "anilist_16498")
are converted once by db/migrate.py and are not accepted by the API any more.
"""
import re
from typing import Optional

ITEM_TYPES = ("anime", "movie", "tv", "game", "person", "character")
SOURCES = ("anilist", "tmdb", "igdb", "wikidata")

_TYPED_RE = re.compile(r"^(anime|movie|tv|game|person|character):(anilist|tmdb|igdb):([0-9]+)$")
_TMDB_ROLE_RE = re.compile(r"^character:tmdb:((?:movie|tv)-[0-9]+-[a-f0-9]{24})$")
_WIKIDATA_RE = re.compile(r"^character:wikidata:(Q[1-9][0-9]*)$")

# Legacy prefix -> (required item_type, typed item_type, source). Checked in order.
# Only used by the 002 migration. Each rule also requires an all-digit remainder.
_LEGACY_RULES = [
    ("anilist_char_", "character", "character", "anilist"),
    ("anilist_", "anime", "anime", "anilist"),
    ("anilist_", "person", "person", "anilist"),
    ("tmdb_movie_", "movie", "movie", "tmdb"),
    ("tmdb_tv_", "tv", "tv", "tmdb"),
    ("tmdb_char_", "character", "character", "tmdb"),
    ("tmdb_", "person", "person", "tmdb"),
    ("igdb_char_", "character", "character", "igdb"),
    ("igdb_", "game", "game", "igdb"),
]


def make_id(item_type: str, source: str, raw_id) -> str:
    if item_type not in ITEM_TYPES:
        raise ValueError(f"Unknown item type: {item_type!r}")
    if source not in SOURCES:
        raise ValueError(f"Unknown source: {source!r}")
    if source == "wikidata":
        value = f"{item_type}:{source}:{raw_id}"
        if not _WIKIDATA_RE.fullmatch(value):
            raise ValueError("Wikidata IDs require a character and an exact positive QID.")
        return value
    return f"{item_type}:{source}:{int(raw_id)}"


def is_typed_id(value) -> bool:
    return isinstance(value, str) and any(pattern.fullmatch(value) for pattern in (_TYPED_RE, _TMDB_ROLE_RE, _WIKIDATA_RE))


def parse_id(value: str) -> tuple[str, str, int | str]:
    match = _TYPED_RE.fullmatch(value or "")
    if match:
        return match.group(1), match.group(2), int(match.group(3))
    role = _TMDB_ROLE_RE.fullmatch(value or "")
    if role:
        return "character", "tmdb", role.group(1)
    wikidata = _WIKIDATA_RE.fullmatch(value or "")
    if wikidata:
        return "character", "wikidata", wikidata.group(1)
    raise ValueError(f"Not a typed item id: {value!r}")


def legacy_to_typed(item_type: str, legacy_id: str) -> Optional[str]:
    """Convert a pre-typed ID using the stored item_type. Returns None if no rule applies."""
    for prefix, required_type, out_type, source in _LEGACY_RULES:
        if item_type == required_type and legacy_id.startswith(prefix):
            rest = legacy_id[len(prefix):]
            return f"{out_type}:{source}:{int(rest)}" if rest.isdigit() else None
    return None
