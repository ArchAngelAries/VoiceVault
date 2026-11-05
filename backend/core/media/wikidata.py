"""Bounded label/alias search for fictional Wikidata characters.

Only structured Wikibase claims establish character and work types. Descriptions
are returned as metadata and never used as classification evidence.
"""
from __future__ import annotations

import re
from typing import Any

from core.media.provider_transport import ProviderError, request_json

API_URL = "https://www.wikidata.org/w/api.php"
USER_AGENT = "VoiceVault/1.0 (local media catalog; https://www.wikidata.org/wiki/Wikidata:Licensing)"
MAX_PAGE = 24
MAX_PAGES = 10
MAX_ANCESTRY_ENTITIES = 120
MAX_DEPTH = 3
BATCH_SIZE = 50

# Item QIDs checked against the current Wikidata item pages. Q1107 is anime
# (audiovisual-work genre), Q7889 video game, Q11424 film, Q15416 TV program.
CHARACTER_QID = "Q95074"
HUMAN_QID = "Q5"
CATEGORY_QIDS = {
    "anime": {"Q1107"},
    "game": {"Q7889"},
    "movie": {"Q11424"},
    "tv": {"Q15416", "Q5398426"},  # television series
}
CHARACTER_CATEGORY_QIDS = {
    "anime": {"Q80447738"},
    "game": {"Q1569167"},
    "movie": {"Q15773347"},
    "tv": {"Q15773317"},
}
VALID_CATEGORIES = frozenset(CATEGORY_QIDS)
_QID_RE = re.compile(r"Q[1-9][0-9]*\Z")


def _error_json(data: Any) -> None:
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        # Never expose provider-supplied info; it may contain request details.
        raise ProviderError("Wikidata returned an API error.", code="upstream")
    if not isinstance(data, dict):
        raise ProviderError("Wikidata returned an invalid response shape.", code="invalid_response")
    if data.get("success") == 0:
        raise ProviderError("Wikidata reported an unsuccessful API response.", code="invalid_response")


async def _api(params: dict[str, Any]) -> dict:
    response = await request_json("wikidata", "GET", API_URL,
                                  params={**params, "format": "json", "formatversion": 2},
                                  headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    _error_json(response)
    return response


def _entity_qid(claim: Any) -> str | None:
    try:
        qid = claim["mainsnak"]["datavalue"]["value"]["id"]
    except (TypeError, KeyError):
        return None
    return qid if isinstance(qid, str) and _QID_RE.fullmatch(qid) else None


def _claim_ids(entity: dict, prop: str) -> list[str]:
    claims = entity.get("claims")
    values = claims.get(prop, []) if isinstance(claims, dict) else []
    if not isinstance(values, list):
        return []
    result = []
    for claim in values[:MAX_ANCESTRY_ENTITIES]:
        if not isinstance(claim, dict) or claim.get("rank") == "deprecated":
            continue
        qid = _entity_qid(claim)
        if qid and qid not in result:
            result.append(qid)
    return result


def _claims_over_limit(entity: dict, prop: str) -> bool:
    claims = entity.get("claims")
    values = claims.get(prop, []) if isinstance(claims, dict) else []
    return isinstance(values, list) and len(values) > MAX_ANCESTRY_ENTITIES


def _label(entity: dict) -> str | None:
    labels = entity.get("labels")
    value = labels.get("en") if isinstance(labels, dict) else None
    return value.get("value") if isinstance(value, dict) and isinstance(value.get("value"), str) else None


def _description(entity: dict) -> str | None:
    descs = entity.get("descriptions")
    value = descs.get("en") if isinstance(descs, dict) else None
    return value.get("value") if isinstance(value, dict) and isinstance(value.get("value"), str) else None


def _aliases(entity: dict, name: str) -> list[str]:
    aliases = entity.get("aliases")
    values = aliases.get("en", []) if isinstance(aliases, dict) else []
    result = []
    if isinstance(values, list):
        for entry in values[:30]:
            text = entry.get("value") if isinstance(entry, dict) else None
            if isinstance(text, str) and text and text.casefold() != name.casefold() and text not in result:
                result.append(text)
    return result[:30]


async def _get_entities(qids: list[str]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for start in range(0, len(qids), BATCH_SIZE):
        batch = qids[start:start + BATCH_SIZE]
        if not batch:
            continue
        data = await _api({"action": "wbgetentities", "ids": "|".join(batch),
                           "props": "labels|descriptions|aliases|claims", "languages": "en"})
        entities = data.get("entities")
        if not isinstance(entities, dict):
            raise ProviderError("Wikidata returned invalid entity data.", code="invalid_response")
        for qid, entity in entities.items():
            if (qid in batch and isinstance(entity, dict) and "missing" not in entity
                    and entity.get("id", qid) == qid):
                result[qid] = entity
    return result


def _works(entity: dict) -> list[str]:
    # P1441 = present in work. Never infer works from labels or unrelated links.
    return _claim_ids(entity, "P1441")


async def browse_characters(search: str, category: str = "anime", page: int = 1,
                            per_page: int = 24) -> dict:
    """Search exact Wikidata entities and return only explicitly fictional characters.

    Search uses English labels/aliases. A requested unknown category returns no
    matches, while its page metadata still describes the bounded search window.
    """
    query = search.strip() if isinstance(search, str) else ""
    if not query:
        return {"items": [], "page_info": {"currentPage": 1, "hasNextPage": False, "lastPage": 1, "total": 0},
                "notice": "Enter a name to search Wikidata labels and aliases."}
    try:
        page = min(MAX_PAGES, max(1, int(page or 1)))
        per_page = min(MAX_PAGE, max(1, int(per_page or MAX_PAGE)))
    except (ValueError, TypeError):
        page, per_page = 1, MAX_PAGE
    category_key = category.strip().lower() if isinstance(category, str) else ""
    known_category = category_key in VALID_CATEGORIES
    offset = (page - 1) * per_page
    data = await _api({"action": "wbsearchentities", "search": query[:200], "language": "en",
                       "uselang": "en", "type": "item", "limit": per_page,
                       "continue": offset})
    matches = data.get("search")
    if not isinstance(matches, list):
        raise ProviderError("Wikidata returned invalid search data.", code="invalid_response")
    candidates = []
    for match in matches[:per_page]:
        qid = match.get("id") if isinstance(match, dict) else None
        if isinstance(qid, str) and _QID_RE.fullmatch(qid) and qid not in candidates:
            candidates.append(qid)
    entities = await _get_entities(candidates) if candidates else {}
    fetched_ids = set(entities)
    remaining_budget = MAX_ANCESTRY_ENTITIES
    truncated = False

    async def fetch(ids: set[str]) -> list[str]:
        nonlocal remaining_budget, truncated
        known = ids.intersection(fetched_ids)
        ordered = sorted(ids - fetched_ids)
        if len(ordered) > remaining_budget:
            ordered = ordered[:remaining_budget]
            truncated = True
        if ordered:
            remaining_budget -= len(ordered)
            fetched_ids.update(ordered)
            entities.update(await _get_entities(ordered))
        return sorted(known) + ordered

    async def type_graphs(roots_by_owner: dict[str, set[str]], targets_by_owner: dict[str, set[str]]) -> tuple[dict[str, set[str]], dict[str, bool]]:
        """Resolve P31/P279 roots and three P279 levels in shared batches."""
        nonlocal truncated
        reached = {owner: set() for owner in roots_by_owner}
        complete = {owner: True for owner in roots_by_owner}
        frontier = {owner: set(roots) for owner, roots in roots_by_owner.items()}
        for depth in range(MAX_DEPTH + 1):
            union = set().union(*frontier.values()) if frontier else set()
            available = await fetch(union)
            available_set = set(available)
            for owner, ids in frontier.items():
                missing = ids - available_set
                if missing:
                    complete[owner] = False
                reached[owner].update(ids & available_set)
                # Authoritative targets are evidence leaves; broad ontology
                # parents above them cannot add useful classification evidence.
                frontier[owner].difference_update(targets_by_owner.get(owner, set()))
                if any(_claims_over_limit(entities.get(node, {}), "P279") for node in frontier[owner] & available_set):
                    complete[owner] = False
                    truncated = True
            if depth == MAX_DEPTH:
                for owner, ids in frontier.items():
                    if any(_claim_ids(entities.get(node, {}), "P279")
                           for node in ids & available_set):
                        complete[owner] = False
                        truncated = True
                break
            next_frontier = {}
            for owner, ids in frontier.items():
                parents = set()
                for node in ids & available_set:
                    parent = entities.get(node)
                    if parent:
                        parents.update(_claim_ids(parent, "P279"))
                parents.difference_update(reached[owner])
                next_frontier[owner] = parents
            frontier = next_frontier
            if not any(frontier.values()):
                break
        return reached, complete

    char_roots = {}
    char_targets = {}
    char_claims_complete = {}
    for qid in candidates:
        entity = entities.get(qid)
        char_roots[qid] = (set(_claim_ids(entity, "P31")) | set(_claim_ids(entity, "P279"))) if entity else set()
        char_targets[qid] = {CHARACTER_QID, HUMAN_QID}
        char_claims_complete[qid] = not entity or not (
            _claims_over_limit(entity, "P31") or _claims_over_limit(entity, "P279"))
    char_types_by_id, char_type_complete = await type_graphs(char_roots, char_targets)
    for qid, claims_complete in char_claims_complete.items():
        char_type_complete[qid] = char_type_complete.get(qid, False) and claims_complete
    eligible = [qid for qid in candidates if CHARACTER_QID in char_types_by_id.get(qid, set())
                and HUMAN_QID not in char_types_by_id.get(qid, set())
                and char_type_complete.get(qid, False)]
    works_by_char = {qid: _works(entities[qid])[:24] for qid in eligible if qid in entities}
    await fetch(set(w for values in works_by_char.values() for w in values))
    work_roots = {}
    for qid, work_ids in works_by_char.items():
        roots = set()
        for work_id in work_ids:
            work = entities.get(work_id)
            if work:
                roots.update(_claim_ids(work, "P31"))
                roots.update(_claim_ids(work, "P279"))
        work_roots[qid] = roots
    work_targets = {qid: set().union(*CATEGORY_QIDS.values()) for qid in work_roots}
    work_types_by_char, _ = await type_graphs(work_roots, work_targets)
    items = []
    if known_category:
        for qid in eligible:
            entity = entities.get(qid)
            name = _label(entity) if entity else None
            if not name:
                continue
            associated_works = []
            for wqid in works_by_char.get(qid, [])[:24]:
                work = entities.get(wqid)
                if not work:
                    continue
                work_name = _label(work)
                if work_name:
                    associated_works.append({"qid": wqid, "name": work_name[:200],
                                             "url": f"https://www.wikidata.org/wiki/{wqid}"})
            char_types = char_types_by_id.get(qid, set())
            work_types = work_types_by_char.get(qid, set())
            categories = [kind for kind in ("anime", "game", "movie", "tv")
                          if char_types.intersection(CHARACTER_CATEGORY_QIDS[kind])
                          or work_types.intersection(CATEGORY_QIDS[kind])]
            if category_key not in categories:
                continue
            items.append({
                "id": f"character:wikidata:{qid}", "source": "wikidata", "source_id": qid,
                "media_type": "character", "name": name[:200],
                "description": (_description(entity) or "")[:500] or None,
                "aliases": [alias[:200] for alias in _aliases(entity, name)],
                "media_titles": [work["name"][:200] for work in associated_works],
                "originating_works": associated_works, "categories": categories,
                "type_evidence": sorted(char_types | work_types)[:MAX_ANCESTRY_ENTITIES],
                "source_url": f"https://www.wikidata.org/wiki/{qid}",
                "source_notes": "Labels, aliases and structured Wikidata claims; descriptions are Wikidata metadata. Originating works are limited to 24.",
                "license": "CC0", "image": None, "voice_actors": [],
            })
    has_more = isinstance(data.get("search-continue"), int) and page < MAX_PAGES
    notice = ("Wikidata search is bounded; type ancestry limits were reached, so category or actor exclusion evidence may be incomplete."
              if truncated else "Wikidata search is bounded and incomplete; only explicit fictional-character and work-type claims are included. Missing or unclassified originating works may be absent.")
    return {"items": items, "page_info": {"currentPage": page, "hasNextPage": has_more,
            "lastPage": MAX_PAGES if has_more else page, "total": None},
            "notice": notice}
