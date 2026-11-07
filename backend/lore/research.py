"""Lore research for established characters: find the right Wikipedia article and pull useful excerpts.

Wikipedia text is CC BY-SA 4.0; every excerpt keeps its article link for attribution. Fan wikis
hosted on Fandom are not read automatically because Fandom's terms forbid automated access;
users can paste lore from any source themselves (see api/cast_craft.py).
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import quote

from core.media.provider_transport import ProviderError, request_json

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
USER_AGENT = "VoiceVault/0.2 (local character studio; https://github.com/ArchAngelAries/VoiceVault)"
LICENCE = "CC BY-SA 4.0"
MAX_EXCERPT_CHARS = 2400
MAX_EVIDENCE_CHARS = 12_000
MAX_ARTICLE_CHARS = 60_000
# Section headings worth reading for a persona, best first. Others are skipped.
USEFUL = ("personality", "characteri", "character", "depiction", "portrayal", "backstory", "biography",
          "fictional", "history", "background", "television", "series", "season", "films", "story", "arc",
          "relationship", "appearance", "skills", "abilities", "overview", "role", "development")
# Spin-off media (novels, comics, audio, games) often contradicts the main canon, so it is skipped too.
SKIP = ("reception", "merchandis", "references", "external links", "see also", "notes", "further reading",
        "bibliography", "sources", "legacy", "in popular culture", "awards", "box office", "toys", "casting",
        "costume", "departure", "literature", "audio", "video game", "comic", "novel", "non-canon",
        "other actors", "other media", "parodies", "cultural impact", "criticism")


def _headers() -> dict:
    return {"User-Agent": USER_AGENT, "Accept": "application/json"}


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"\([^)]*\)", " ", text.lower())
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def article_url(title: str) -> str:
    return "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_"), safe="()_,'-")


async def _wiki(params: dict) -> dict:
    data = await request_json("wikipedia", "GET", WIKIPEDIA_API,
                              params={**params, "format": "json", "formatversion": 2}, headers=_headers())
    if not isinstance(data, dict) or "error" in data:
        raise ProviderError("Wikipedia returned an API error.", code="upstream")
    return data


async def title_for_qid(qid: str) -> str | None:
    """The English Wikipedia article linked from a Wikidata item, if there is one."""
    if not re.fullmatch(r"Q[1-9][0-9]*", qid or ""):
        return None
    data = await request_json("wikidata", "GET", WIKIDATA_API, headers=_headers(), params={
        "action": "wbgetentities", "ids": qid, "props": "sitelinks", "sitefilter": "enwiki",
        "format": "json", "formatversion": 2})
    entity = (data.get("entities") or {}).get(qid) if isinstance(data, dict) else None
    link = ((entity or {}).get("sitelinks") or {}).get("enwiki") if isinstance(entity, dict) else None
    title = link.get("title") if isinstance(link, dict) else None
    return title if isinstance(title, str) and title else None


async def search(query: str, limit: int = 6) -> list[dict]:
    data = await _wiki({"action": "query", "list": "search", "srsearch": query[:300], "srlimit": limit,
                        "srprop": "snippet"})
    results = []
    for row in (data.get("query") or {}).get("search") or []:
        title = row.get("title")
        if not isinstance(title, str):
            continue
        snippet = re.sub(r"<[^>]+>", "", row.get("snippet") or "")
        results.append({"title": title, "snippet": snippet[:300], "url": article_url(title)})
    return results


def split_sections(text: str) -> list[dict]:
    """TextExtracts plain text with '== Heading ==' markers → [{heading, level, text}]."""
    sections, heading, level, lines = [], "Introduction", 1, []
    for line in text.splitlines():
        match = re.fullmatch(r"\s*(=+)\s*(.*?)\s*\1\s*", line)
        if match:
            if any(part.strip() for part in lines):
                sections.append({"heading": heading, "level": level, "text": "\n".join(lines).strip()})
            heading, level, lines = match.group(2), len(match.group(1)), []
        else:
            lines.append(line)
    if any(part.strip() for part in lines):
        sections.append({"heading": heading, "level": level, "text": "\n".join(lines).strip()})
    return sections


async def fetch_article(title: str) -> dict | None:
    data = await _wiki({"action": "query", "prop": "extracts|info", "explaintext": 1, "exsectionformat": "wiki",
                        "inprop": "url", "redirects": 1, "titles": title[:300]})
    pages = (data.get("query") or {}).get("pages") or []
    page = pages[0] if pages else None
    if not isinstance(page, dict) or page.get("missing") or not page.get("extract"):
        return None
    text = page["extract"][:MAX_ARTICLE_CHARS]
    return {"title": page.get("title") or title, "url": page.get("fullurl") or article_url(page.get("title") or title),
            "text": text, "sections": split_sections(text)}


def _rank(heading: str) -> int | None:
    low = heading.lower()
    if any(word in low for word in SKIP):
        return None
    for index, word in enumerate(USEFUL):
        if word in low:
            return index
    return len(USEFUL)


def excerpts(article: dict, budget: int = MAX_EVIDENCE_CHARS) -> list[dict]:
    """The introduction plus the most persona-relevant sections, as cited sources S1, S2, ..."""
    fetched = datetime.now(timezone.utc).date().isoformat()
    ranked = []
    for order, section in enumerate(article["sections"]):
        rank = 0 if order == 0 else _rank(section["heading"])
        if rank is not None and len(section["text"]) >= 80:
            ranked.append((rank, order, section))
    ranked.sort(key=lambda item: (item[0], item[1]))
    chosen, used = [], 0
    for _rank_value, order, section in ranked:
        text = section["text"]
        for start in range(0, len(text), MAX_EXCERPT_CHARS):
            piece = text[start:start + MAX_EXCERPT_CHARS].strip()
            if used + len(piece) > budget or len(chosen) >= 12:
                break
            chosen.append((order, start, section["heading"], piece))
            used += len(piece)
    chosen.sort()
    return [{"id": f"S{index}", "title": f"{article['title']} — {heading} (Wikipedia)",
             "url": article["url"] + ("" if heading == "Introduction" else "#" + quote(heading.replace(" ", "_"))),
             "licence": LICENCE, "excerpt": piece, "fetched_at": fetched}
            for index, (_order, _start, heading, piece) in enumerate(chosen, 1)]


def confident(title: str, wanted: str) -> bool:
    """A search hit is trusted only when its title is the character's name (ignoring '(character)')."""
    a, b = _norm(title), _norm(wanted)
    return bool(a and b) and (a == b or a.startswith(b + " ") and len(a) - len(b) < 12)


async def resolve(name: str, variant: str = "", catalog_identity: str | None = None,
                  franchise: str = "", article_title: str | None = None) -> dict:
    """Pick the article to read. Returns {"title"} when sure, otherwise {"choices"} for the user."""
    if article_title:
        return {"title": article_title, "how": "chosen"}
    if catalog_identity and catalog_identity.startswith("character:wikidata:"):
        try:
            title = await title_for_qid(catalog_identity.rsplit(":", 1)[-1])
        except ProviderError:
            title = None
        if title:
            return {"title": title, "how": "wikidata"}
    wanted = (variant or name).strip()
    query = " ".join(part for part in (wanted, franchise, "character") if part)
    hits = await search(query)
    if variant and not hits:
        hits = await search(f"{name} {variant} {franchise}".strip())
    sure = [hit for hit in hits if confident(hit["title"], wanted)]
    if len(sure) == 1:
        return {"title": sure[0]["title"], "how": "search", "choices": hits}
    return {"choices": hits}
