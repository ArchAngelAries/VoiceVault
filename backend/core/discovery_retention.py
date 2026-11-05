"""Best-effort retention bridge from saved library items to public discovery data."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlsplit

from core.discovery_snapshots import DiscoverySnapshotStore
from core.item_ids import parse_id

logger = logging.getLogger(__name__)

_PROVIDERS = {"anilist", "igdb", "tmdb", "wikidata"}
_COVER_HOST = {
    "anilist": re.compile(r"^s[0-9]+\.anilist\.co$", re.I),
    "igdb": re.compile(r"^images\.igdb\.com$", re.I),
    "tmdb": re.compile(r"^image\.tmdb\.org$", re.I),
}
_SENSITIVE_QUERY = re.compile(r"(?:api.?key|access.?token|refresh.?token|token|secret|credential|signature|authorization|oauth|^key$)", re.I)
_PUBLIC_FIELDS = {
    "description", "overview", "summary", "format", "status", "episodes", "genres", "platforms",
    "themes", "game_modes", "player_perspectives", "roles", "studios", "developers", "publishers",
    "release_date", "first_release_date", "first_air_date", "season", "season_year", "site_url",
    "average_score", "rating", "country", "language", "runtime", "media_type", "site_url",
}
_DEFAULT_STORE: DiscoverySnapshotStore | None = None


def get_snapshot_store() -> DiscoverySnapshotStore:
    """Return the lazy process-wide sidecar used by save integrations."""
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        def bounded_env(name: str, default: int, low: int, high: int) -> int:
            try:
                value = int(os.environ.get(name, ""))
                return value if low <= value <= high else default
            except (TypeError, ValueError):
                return default
        _DEFAULT_STORE = DiscoverySnapshotStore(
            max_bytes=bounded_env("VOICEVAULT_DISCOVERY_CACHE_MAX_BYTES", 32 * 1024 * 1024, 1024 * 1024, 2 * 1024 * 1024 * 1024),
            max_requests=bounded_env("VOICEVAULT_DISCOVERY_CACHE_MAX_REQUESTS", 256, 1, 100_000),
        )
    return _DEFAULT_STORE


def _as_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    if hasattr(item, "model_dump"):
        return item.model_dump()
    if hasattr(item, "__dict__"):
        return vars(item)
    return {}


def public_entity_ref(item: Any) -> tuple[str, str, str] | None:
    """Return the safe public entity identity for an existing typed card."""
    data = _as_dict(item)
    item_id = data.get("id") or data.get("item_id")
    if not isinstance(item_id, str):
        return None
    try:
        item_type, provider, _ = parse_id(item_id)
    except (TypeError, ValueError):
        return None
    if provider not in _PROVIDERS:
        return None
    supplied_type = data.get("type") or data.get("item_type") or data.get("media_type")
    supplied_source = data.get("source")
    if supplied_type and supplied_type != item_type:
        return None
    if supplied_source and supplied_source != provider:
        return None
    return provider, item_type, item_id


def _public_metadata(item: Any, ref: tuple[str, str, str]) -> dict[str, Any]:
    data = _as_dict(item)
    provider, item_type, _ = ref
    _, _, raw_id = parse_id(ref[2])
    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        title = data.get("name")
    metadata: dict[str, Any] = {"id": ref[2], "media_type": item_type, "source_id": str(raw_id),
                               "type": item_type, "source": provider}
    if provider == "wikidata":
        # Identity-derived provenance also survives a saved-card-only fallback.
        # Richer claim evidence remains in the sidecar and is never invented here.
        metadata.update(source_url=f"https://www.wikidata.org/wiki/{raw_id}", license="CC0")
    if isinstance(title, str) and title.strip():
        metadata["title"] = title.strip()[:200]
        if item_type in {'person', 'character'}:
            metadata['name'] = metadata['title']
    cover = data.get("cover_url")
    if isinstance(cover, str) and len(cover) <= 500:
        try:
            parts = urlsplit(cover)
            host_ok = provider in _COVER_HOST and bool(_COVER_HOST[provider].fullmatch(parts.hostname or ""))
            no_userinfo = not parts.username and not parts.password and parts.port in (None, 443)
            safe_query = not any(_SENSITIVE_QUERY.search(key) for key, _ in parse_qsl(parts.query, keep_blank_values=True))
            if parts.scheme == "https" and host_ok and no_userinfo and safe_query:
                metadata["cover_url"] = cover
                metadata['cover'] = cover
                if item_type == 'character': metadata['image'] = cover
                if item_type == 'person': metadata['photo'] = cover
        except ValueError:
            pass
    year = data.get("year")
    if isinstance(year, int) and not isinstance(year, bool) and 1800 <= year <= 3000:
        metadata["year"] = year
    # Snapshot callers may explicitly provide richer normalized public fields.
    # Saved-library cards naturally contain only a few keys, so sparse values
    # don't erase detail already merged in the sidecar.
    for field in _PUBLIC_FIELDS:
        value = data.get(field)
        if isinstance(value, str) and value.strip() and len(value) <= 12000:
            if field == "site_url":
                try:
                    parts = urlsplit(value)
                    safe_url = parts.scheme == "https" and not parts.username and not parts.password and parts.port in (None, 443)
                    safe_url = safe_url and not any(_SENSITIVE_QUERY.search(key) for key, _ in parse_qsl(parts.query, keep_blank_values=True))
                    if safe_url:
                        metadata[field] = value.strip()
                except ValueError:
                    pass
            else:
                metadata[field] = value.strip()
        elif (isinstance(value, (int, bool)) or (isinstance(value, float) and value == value and abs(value) != float("inf"))):
            metadata[field] = value
        elif isinstance(value, list) and len(value) <= 80:
            safe_values = [entry[:160] for entry in value if isinstance(entry, str) and entry.strip()]
            if safe_values:
                metadata[field] = safe_values
    if provider == "wikidata":
        for field, limit in (("aliases", 30), ("media_titles", 24), ("categories", 4), ("type_evidence", 120)):
            values = data.get(field)
            if isinstance(values, list):
                safe = [value.strip()[:200] for value in values[:limit] if isinstance(value, str) and value.strip()]
                if field == "categories":
                    safe = [value for value in safe if value in {"anime", "game", "movie", "tv"}]
                if field == "type_evidence":
                    safe = [value for value in safe if re.fullmatch(r"Q[1-9][0-9]*", value)]
                if safe:
                    metadata[field] = list(dict.fromkeys(safe))
        works = data.get("originating_works")
        if isinstance(works, list):
            safe_works = []
            for work in works[:24]:
                if not isinstance(work, dict) or not isinstance(work.get("qid"), str) or not re.fullmatch(r"Q[1-9][0-9]*", work["qid"]):
                    continue
                qid = work["qid"]
                name = work.get("name")
                safe_works.append({"qid": qid, "name": name.strip()[:200] if isinstance(name, str) and name.strip() else qid,
                                   "url": f"https://www.wikidata.org/wiki/{qid}"})
            if safe_works:
                metadata["originating_works"] = safe_works
    return metadata


def remember_public_item(store: DiscoverySnapshotStore, item: Any) -> tuple[str, str, str] | None:
    """Persist only the public card fields needed for a saved offline listing."""
    ref = public_entity_ref(item)
    if ref is None:
        return None
    metadata = _public_metadata(item, ref)
    store.put_entity(ref[0], ref[1], ref[2], metadata, coverage=metadata.keys())
    return ref


def protect_public_item(store: DiscoverySnapshotStore, item: Any, reason: str, protected: bool = True) -> tuple[str, str, str] | None:
    ref = public_entity_ref(item)
    if ref is None:
        return None
    if protected:
        metadata = _public_metadata(item, ref)
        store.retain_entity(*ref, metadata, reason, coverage=metadata.keys())
    else:
        store.protect_entity(*ref, reason, protected=False)
    return ref


def _release_refs(store: DiscoverySnapshotStore, item_ids: Iterable[str], reason: str) -> None:
    for item_id in set(item_ids):
        ref = public_entity_ref({"id": item_id})
        if ref is not None:
            store.protect_entity(*ref, reason, protected=False)


async def retain_saved_item(item: Any, reason: str, *, store: DiscoverySnapshotStore | None = None) -> str | None:
    """Retain/protect a public item without blocking the async API event loop."""
    try:
        await asyncio.to_thread(protect_public_item, store or get_snapshot_store(), item, reason, True)
        return None
    except Exception as exc:  # sidecar is optional; a successful library save remains successful
        logger.warning("Could not retain saved discovery item: %s", exc)
        return "Saved successfully, but its offline catalog copy could not be retained."


async def release_saved_items(item_ids: Iterable[str], reason: str, *, store: DiscoverySnapshotStore | None = None) -> str | None:
    try:
        await asyncio.to_thread(_release_refs, store or get_snapshot_store(), tuple(item_ids), reason)
        return None
    except Exception as exc:
        logger.warning("Could not release saved discovery item protection: %s", exc)
        return "Saved changes succeeded, but offline catalog retention could not be updated."


async def release_saved_item(item_id: str, reason: str, *, store: DiscoverySnapshotStore | None = None) -> str | None:
    return await release_saved_items((item_id,), reason, store=store)


async def sync_saved_entities(db: Any, store: DiscoverySnapshotStore | None = None, *,
                              release_missing: bool = True) -> dict[str, Any]:
    """Retain current saves, optionally removing protections absent from the scan.

    Callers preparing a destructive cache operation should set
    ``release_missing=False``: an additive scan cannot race a concurrent save
    into releasing that item's protection.
    """
    active_store = store or get_snapshot_store()
    retained = 0
    warnings: list[str] = []
    active_protections: dict[str, set[tuple[str, str, str]]] = {}
    try:
        collections = await db.collection.find_many(include={"items": True})
        for collection in collections:
            reason = f"collection:{collection.id}"
            for item in collection.items or []:
                card = {"id": item.item_id, "item_type": item.item_type, "source": item.source,
                        "title": item.title, "cover_url": item.cover_url, "year": getattr(item, "year", None)}
                try:
                    ref = await asyncio.to_thread(protect_public_item, active_store, card, reason, True)
                    retained += int(ref is not None)
                    if ref is not None: active_protections.setdefault(reason, set()).add(ref)
                except Exception as exc:
                    logger.warning("Could not reconcile a saved collection item: %s", exc)
                    warnings.append("Some saved collection metadata could not be retained.")
        tiers = await db.tierlist.find_many(include={"tiers": True})
        for tier in tiers:
            reason = f"tier:{tier.id}"
            for row in tier.tiers or []:
                try:
                    tiles = json.loads(row.items or "[]")
                except (TypeError, json.JSONDecodeError):
                    continue
                for tile in tiles:
                    item_id = tile.get("id") if isinstance(tile, dict) else None
                    if not isinstance(item_id, str):
                        continue
                    card = {"id": item_id, "title": tile.get("title"), "cover_url": tile.get("cover_url"),
                            "item_type": tile.get("item_type"), "source": tile.get("source")}
                    try:
                        ref = await asyncio.to_thread(protect_public_item, active_store, card, reason, True)
                        retained += int(ref is not None)
                        if ref is not None: active_protections.setdefault(reason, set()).add(ref)
                    except Exception as exc:
                        logger.warning("Could not reconcile a saved tier item: %s", exc)
                        warnings.append("Some saved tier metadata could not be retained.")
    except Exception as exc:
        logger.warning("Could not reconcile saved discovery metadata: %s", exc)
        warnings.append("Saved catalog retention could not be reconciled.")
    if not warnings and release_missing:
        try:
            await asyncio.to_thread(active_store.reconcile_saved_protections, active_protections)
        except Exception as exc:
            logger.warning("Could not reconcile saved item protections: %s", exc)
            warnings.append("Saved catalog retention could not be reconciled.")
    return {"retained": retained, "warnings": sorted(set(warnings))}
