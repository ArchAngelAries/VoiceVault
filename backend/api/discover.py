"""
api/discover.py — Media discovery endpoints with full filter/sort support
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from core.media import anilist, tmdb, igdb, people, wikidata
from core.discovery_cache import SnapshotRoute, cached_provider_query, clear_recent_discovery_cache as clear_recent_cache_after_drain
from core.discovery_retention import get_snapshot_store, sync_saved_entities
from db.client import get_db
import asyncio

router = APIRouter(route_class=SnapshotRoute)
_library_synced = False
_library_sync_lock = asyncio.Lock()


@router.get("/cache")
async def discovery_cache_stats():
    try:
        return await asyncio.to_thread(get_snapshot_store().cache_stats)
    except Exception:
        raise HTTPException(503, "Discovery cache storage could not be inspected.")


@router.post("/cache/clear-recent")
async def clear_recent_discovery_cache_route():
    store = get_snapshot_store()
    # Reconcile first: a failed DB read or a partial protection update must
    # never be followed by deleting potentially saved catalog records.
    try:
        db = await get_db()
        report = await sync_saved_entities(db, store, release_missing=False)
    except Exception:
        raise HTTPException(503, "Saved catalog protections could not be verified; cache was left unchanged.")
    if report.get("warnings"):
        raise HTTPException(503, "Saved catalog protections could not be verified; cache was left unchanged.")
    # Drain owned refreshes before clearing. clear_recent is synchronous here,
    # so once the drain completes the event loop cannot start another refresh
    # between deletion and this handler returning.
    try:
        return await clear_recent_cache_after_drain(store)
    except Exception:
        raise HTTPException(503, "Discovery cache could not be cleared safely.")


@router.get('/local-library')
async def local_library(provider: Optional[str] = None, entity_type: Optional[str] = None,
                        q: Optional[str] = None, offset: int = Query(0, ge=0),
                        limit: int = Query(24, ge=1, le=200)):
    global _library_synced
    store = get_snapshot_store()
    async with _library_sync_lock:
        if not _library_synced:
            try:
                db = await get_db()
                report = await sync_saved_entities(db, store)
                _library_synced = not report.get('warnings')
            except Exception:
                pass  # Previously retained catalog records remain usable without Prisma.
    try:
        result = await asyncio.to_thread(store.search_local, provider=provider,
                                        entity_type=entity_type, query=q, offset=offset, limit=limit)
    except Exception:
        raise HTTPException(503, 'The local catalog could not be read. Your saved library is unchanged.')
    result['items'] = [{**item['metadata'], 'id': item['source_id'],
                        '_local': {'fetched_at': item['fetched_at'], 'coverage': item['coverage'],
                                   'provider': item['provider']}} for item in result['items']]
    return result


@router.get('/local-library/entity')
async def local_entity(provider: str, entity_type: str, source_id: str):
    try:
        item = await asyncio.to_thread(get_snapshot_store().get_entity, provider, entity_type, source_id)
    except Exception:
        raise HTTPException(503, 'The local profile could not be read.')
    if item is None:
        raise HTTPException(404, 'This profile has not been retained in the local catalog.')
    return {**item['metadata'], 'id': item['source_id'],
            '_local': {'fetched_at': item['fetched_at'], 'coverage': item['coverage'], 'provider': provider},
            '_snapshot': {'provider': provider, 'state': 'paused' if item['metadata'].get('source_status') == 'missing' else 'stale', 'retained': True,
                          'local_only': True,
                          'fetched_at': item['fetched_at'], 'checked_at': None,
                          'refresh_due_at': None, 'next_allowed_at': None,
                          'error_code': 'not_found' if item['metadata'].get('source_status') == 'missing' else None,
                          'storage_warning': 'This local profile includes only previously retained fields.'}}


# ── Anime (AniList) ─────────────────────────────────────────────

@router.get("/anime/browse")
async def anime_browse(
    search: Optional[str]  = None,
    sort: str              = "popular",
    genres: Optional[str]  = None,
    tags: Optional[str]    = None,
    format: Optional[str]  = None,
    status: Optional[str]  = None,
    season: Optional[str]  = None,
    year: Optional[int]    = None,
    year_from: Optional[int] = None,
    year_to: Optional[int]   = None,
    episode_min: Optional[int] = None,
    episode_max: Optional[int] = None,
    country: Optional[str] = None,
    source: Optional[str]  = None,
    page: int = 1, per_page: int = 24,
):
    genre_list = [g.strip() for g in genres.split(",")] if genres else None
    tag_list   = [t.strip() for t in tags.split(",")]   if tags   else None
    return await anilist.browse(
        search=search, genres=genre_list, tags=tag_list,
        format=format, status=status, season=season,
        year=year, year_from=year_from, year_to=year_to,
        episode_min=episode_min, episode_max=episode_max,
        country=country, source=source, sort=sort,
        page=page, per_page=per_page,
    )

@router.get("/anime/genres")
async def anime_genres():
    return await anilist.genres()

@router.get("/anime/tags")
async def anime_tags():
    return await anilist.tags()

@router.get("/characters/browse")
async def characters_browse(
    search: Optional[str] = None,
    sort: str = "popular",
    category: str = "anime",    # anime | game | movie | tv
    source: str = "catalog",    # catalog | wikidata (direct character-name search)
    page: int = 1,
    per_page: int = 24,
):
    try:
        category = category.strip().lower()
        source = source.strip().lower()
        if category not in {"anime", "game", "movie", "tv"}:
            raise ValueError("Category must be anime, game, movie, or tv.")
        if source not in {"catalog", "wikidata"}:
            raise ValueError("Character source must be catalog or wikidata.")
        if source == "wikidata":
            return await wikidata.browse_characters(search=search, category=category, page=page, per_page=per_page)
        if category == "anime":
            return await anilist.browse_characters(search=search, sort=sort, page=page, per_page=per_page)
        if category == "game":
            return await igdb.browse_characters(search=search, sort=sort, page=page, per_page=per_page)
        if category in {"movie", "tv"}:
            if not (search or "").strip():
                return {"page_info": {"currentPage": page, "hasNextPage": False, "lastPage": 1, "total": 0},
                        "items": [], "notice": "TMDB finds roles within a movie or TV title. Search for a title to browse its credited cast."}
            browse = tmdb.browse_movies if category == "movie" else tmdb.browse_tv
            return await browse(search=search.strip(), page=page)
        raise ValueError("Category must be anime, game, movie, or tv.")
    except ValueError as e: raise HTTPException(400, str(e))

@router.get("/anime/{media_id}")
async def anime_detail(media_id: int):
    return await anilist.detail(media_id)


# ── Movies (TMDB) ────────────────────────────────────────────────

@router.get("/movies/browse")
async def movies_browse(
    search: Optional[str]        = None,
    sort: str                    = "popular",
    genres: Optional[str]        = None,
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
    page: int = 1,
):
    try:
        genre_list = [g.strip() for g in genres.split(",")] if genres else None
        return await tmdb.browse_movies(
            search=search, sort=sort, genres=genre_list,
            certification=certification, year_from=year_from, year_to=year_to,
            score_min=score_min, score_max=score_max, vote_min=vote_min,
            runtime_min=runtime_min, runtime_max=runtime_max,
            language=language, keywords=keywords, page=page,
        )
    except ValueError as e: raise HTTPException(400, str(e))

@router.get("/movies/genres")
async def movies_genres():
    try:    return await tmdb.movie_genres()
    except ValueError as e: raise HTTPException(400, str(e))

@router.get("/movies/{tmdb_id}")
async def movie_detail(tmdb_id: int):
    try:    return await tmdb.movie_detail(tmdb_id)
    except ValueError as e: raise HTTPException(400, str(e))


# ── TV (TMDB) ────────────────────────────────────────────────────

@router.get("/tv/browse")
async def tv_browse(
    search: Optional[str]        = None,
    sort: str                    = "popular",
    genres: Optional[str]        = None,
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
    page: int = 1,
):
    try:
        genre_list = [g.strip() for g in genres.split(",")] if genres else None
        return await tmdb.browse_tv(
            search=search, sort=sort, genres=genre_list,
            certification=certification, year_from=year_from, year_to=year_to,
            score_min=score_min, score_max=score_max, vote_min=vote_min,
            runtime_min=runtime_min, runtime_max=runtime_max,
            language=language, network=network, keywords=keywords, page=page,
        )
    except ValueError as e: raise HTTPException(400, str(e))

@router.get("/tv/genres")
async def tv_genres():
    try:    return await tmdb.tv_genres()
    except ValueError as e: raise HTTPException(400, str(e))

@router.get("/tv/{tmdb_id}")
async def tv_detail(tmdb_id: int):
    try:    return await tmdb.tv_detail(tmdb_id)
    except ValueError as e: raise HTTPException(400, str(e))


# ── Games (IGDB) ─────────────────────────────────────────────────
# All genre/theme/platform/mode/perspective filters use integer IDs.
# Frontend stores {id, name} objects; sends comma-separated IDs here.

@router.get("/games/browse")
async def games_browse(
    search:       Optional[str] = None,
    sort:         str           = "popular",
    genres:       Optional[str] = None,   # comma-separated int IDs
    themes:       Optional[str] = None,   # comma-separated int IDs
    platforms:    Optional[str] = None,   # comma-separated int IDs
    game_modes:   Optional[str] = None,   # comma-separated int IDs
    perspectives: Optional[str] = None,   # comma-separated int IDs
    year_from:    Optional[int] = None,
    year_to:      Optional[int] = None,
    rating_min:   Optional[int] = None,
    rating_max:   Optional[int] = None,
    page:    int = 1,
    per_page: int = 24,
):
    try:
        def _ids(s): return [int(x.strip()) for x in s.split(",") if x.strip().isdigit()] if s else None
        return await igdb.browse(
            search=search, sort=sort,
            genres=_ids(genres), themes=_ids(themes), platforms=_ids(platforms),
            game_modes=_ids(game_modes), perspectives=_ids(perspectives),
            year_from=year_from, year_to=year_to,
            rating_min=rating_min, rating_max=rating_max,
            page=page, per_page=per_page,
        )
    except ValueError as e: raise HTTPException(400, str(e))

@router.get("/games/genres")
async def games_genres():
    try:    return await igdb.genres()
    except ValueError as e: raise HTTPException(400, str(e))

@router.get("/games/themes")
async def games_themes():
    try:    return await igdb.themes()
    except ValueError as e: raise HTTPException(400, str(e))

@router.get("/games/platforms/search")
async def games_platforms_search(q: str = ""):
    """Live IGDB platform search for custom platform entry field."""
    try:    return await igdb.search_platforms(q)
    except ValueError as e: raise HTTPException(400, str(e))

@router.get("/games/platforms")
async def games_platforms():
    return igdb.platforms()

@router.get("/games/modes")
async def games_modes():
    return igdb.game_modes()

@router.get("/games/perspectives")
async def games_perspectives():
    return igdb.perspectives()

@router.get("/games/{game_id}")
async def game_detail(game_id: int):
    try:    return await igdb.detail(game_id)
    except ValueError as e: raise HTTPException(400, str(e))


# ── People (TMDB + AniList) ──────────────────────────────────────

@router.get("/people/browse")
async def people_browse(
    search: Optional[str]      = None,
    source: str                = "tmdb",        # "tmdb" | "anilist"
    department: Optional[str]  = None,          # TMDB: Acting, Directing, etc
    language: Optional[str]    = None,          # AniList: Japanese, English, etc
    sort: str                  = "popular",
    page: int                  = 1,
    per_page: int              = 24,
):
    try:
        return await people.browse(
            source=source, search=search, department=department,
            language=language, sort=sort, page=page, per_page=per_page,
        )
    except ValueError as e: raise HTTPException(400, str(e))

@router.get("/people/{person_id:path}")
async def person_detail(person_id: str):
    """person_id is typed: 'person:tmdb:12345' or 'person:anilist:67890'"""
    try:    return await people.detail(person_id)
    except ValueError as e: raise HTTPException(400, str(e))


# ── Unified search ───────────────────────────────────────────────

@router.get("/search")
async def unified_search(q: str, page: int = 1):
    results = {"anime": [], "movies": [], "tv": [], "games": [], "_snapshots": {}, "_errors": {}}
    sources = [('anime', 'anilist', lambda: anilist.browse(search=q, page=page, per_page=6)),
               ('movies', 'tmdb', lambda: tmdb.browse_movies(search=q, page=page)),
               ('tv', 'tmdb', lambda: tmdb.browse_tv(search=q, page=page)),
               ('games', 'igdb', lambda: igdb.browse(search=q, page=page, per_page=6))]
    for category, provider, callback in sources:
        try:
            status, payload = await cached_provider_query(provider, f'v1:search:{category}',
                                                           {'q': q, 'page': page}, callback)
            results['_snapshots'][category] = payload.get('_snapshot')
            if status == 200:
                results[category] = payload['items'][:6]
            else:
                results['_errors'][category] = payload.get('detail', 'Provider unavailable.')
        except Exception:
            results['_errors'][category] = 'This provider could not complete the search.'
    return results
