"""
api/tiers.py — Tier list boards: ranking boards that take items from a collection and imported images.

GET    /api/tiers                      list boards, most recently changed first
POST   /api/tiers                      create a board (SABC template by default), attached to Favorites unless told otherwise
GET    /api/tiers/{id}                 one board: its rows in order, each with its tiles in order
PATCH  /api/tiers/{id}                 name, notes, attached collection (collection_id null detaches it)
PUT    /api/tiers/{id}/board           save every row and tile at once; the stored board is replaced as a whole
DELETE /api/tiers/{id}                 delete a board
GET    /api/tiers/images               imported images (the warehouse)
POST   /api/tiers/images               import one or more images (multipart field "files")
GET    /api/tiers/images/{hex}/file    one imported image
PATCH  /api/tiers/images/{hex}         its text label
DELETE /api/tiers/images/{hex}         delete one image; it is removed from every board too
POST   /api/tiers/images/bulk-delete   delete selected images, or all of them with the typed confirmation
GET    /api/tiers/image-proxy?url=     a cover from AniList, TMDB or IGDB, so export can draw it (export only)

A tile's ID is a typed item ID ("anime:anilist:16498") or an image ID ("image:local:<hex>").
Each tile keeps a snapshot of its title and cover, so a board still reads correctly after the
item leaves its collection. Image tiles read their label and file from the image store.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Literal, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from core import tier_images
from core.discovery_retention import release_saved_items, retain_saved_item
from core.item_ids import is_typed_id, parse_id
from db.client import get_db

router = APIRouter()

DEFAULT_SABC = [("S", "#ff7f7f"), ("A", "#ffbf7f"), ("B", "#ffdf7f"), ("C", "#ffff7f")]
MAX_ROWS = 30
MAX_TILES_PER_ROW = 300
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
# Hosts the cover proxy will fetch from: AniList (s1..sN), TMDB and IGDB image CDNs.
PROXY_HOSTS = re.compile(r"^(s[0-9]+\.anilist\.co|image\.tmdb\.org|images\.igdb\.com)$")
PROXY_MAX_BYTES = 8 * 1024 * 1024


# ── Request models ───────────────────────────────────────────────

class TierCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    notes: Optional[str] = Field(default=None, max_length=5000)
    collection_id: Optional[int] = None
    template: Literal["SABC", "blank"] = "SABC"


class TierUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=80)
    notes: Optional[str] = Field(default=None, max_length=5000)
    collection_id: Optional[int] = None   # send null to detach; leave the key out to keep it


class TileIn(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    title: Optional[str] = Field(default=None, max_length=200)
    item_type: Optional[str] = Field(default=None, max_length=20)
    source: Optional[str] = Field(default=None, max_length=20)
    cover_url: Optional[str] = Field(default=None, max_length=500)


class RowIn(BaseModel):
    label: str = Field(min_length=1, max_length=24)
    color: str = Field(max_length=7)
    items: list[TileIn] = Field(default_factory=list, max_length=MAX_TILES_PER_ROW)


class BoardIn(BaseModel):
    rows: list[RowIn] = Field(max_length=MAX_ROWS)
    notes: Optional[str] = Field(default=None, max_length=5000)


class ImageLabel(BaseModel):
    label: str = Field(default="", max_length=tier_images.MAX_LABEL)


class BulkImageDelete(BaseModel):
    ids: Optional[list[str]] = Field(default=None, max_length=tier_images.MAX_BULK)
    all: bool = False
    confirm: Optional[str] = Field(default=None, max_length=40)


# ── Helpers ──────────────────────────────────────────────────────

def _check_tile(tile: TileIn) -> None:
    if tile.cover_url is not None and not tile.cover_url.startswith(("https://", "http://")):
        raise HTTPException(422, "Cover links must be http or https.")
    if tile.id.startswith("image:"):
        if not tier_images.is_image_id(tile.id):
            raise HTTPException(422, f"'{tile.id}' is not a valid image id.")
        if tier_images.get_image(tier_images.hex_of(tile.id)) is None:
            raise HTTPException(422, "An image on this board was deleted. Reload the board and try again.")
    elif not is_typed_id(tile.id):
        raise HTTPException(422, f"'{tile.id}' is not an item id (for example anime:anilist:16498).")
    else:
        item_type, source, _ = parse_id(tile.id)
        if (tile.item_type is not None and tile.item_type != item_type) or (tile.source is not None and tile.source != source):
            raise HTTPException(422, "Tile type and source must match its typed item ID.")


def _stored_tile(tile: TileIn) -> dict:
    if tile.id.startswith("image:"):
        return {"id": tile.id}                      # the label lives in the image store
    return {key: value for key, value in tile.model_dump().items() if value is not None}


def _tile_out(stored: dict, images: dict[str, dict]) -> Optional[dict]:
    tile_id = stored.get("id", "")
    if tile_id.startswith("image:"):
        image = images.get(tile_id)
        if image is None:
            return None                              # deleted image: left out of the view
        return {"id": tile_id, "kind": "image", "title": image["label"], "cover_url": image["url"],
                "item_type": "image", "source": "local"}
    return {"id": tile_id, "kind": "item", "title": stored.get("title") or tile_id,
            "cover_url": stored.get("cover_url"), "item_type": stored.get("item_type"),
            "source": stored.get("source")}


async def _favorites_id(db) -> Optional[int]:
    favorites = await db.collection.find_unique(where={"slug": "favorites"})
    return favorites.id if favorites else None


async def _require_collection(db, collection_id: Optional[int]) -> None:
    if collection_id is not None and await db.collection.find_unique(where={"id": collection_id}) is None:
        raise HTTPException(422, "That collection does not exist.")


async def _board(db, tier_id: int) -> dict:
    tier = await db.tierlist.find_unique(where={"id": tier_id}, include={"collection": True})
    if tier is None:
        raise HTTPException(404, "Tier list not found")
    rows = await db.tierrow.find_many(where={"tier_list_id": tier_id}, order={"sort_order": "asc"})
    images = {image["id"]: image for image in tier_images.list_images()}
    return {
        "id": tier.id,
        "name": tier.name,
        "notes": tier.description or "",
        "template": tier.template,
        "collection": {"id": tier.collection.id, "name": tier.collection.name} if tier.collection else None,
        "updated_at": tier.updated_at.isoformat(),
        "rows": [
            {
                "label": row.label,
                "color": row.color,
                "items": [t for t in (_tile_out(s, images) for s in json.loads(row.items or "[]")) if t],
            }
            for row in rows
        ],
    }


def _summary(tier, row_count: int, tile_count: int) -> dict:
    return {
        "id": tier.id,
        "name": tier.name,
        "template": tier.template,
        "collection": {"id": tier.collection.id, "name": tier.collection.name} if tier.collection else None,
        "row_count": row_count,
        "tile_count": tile_count,
        "updated_at": tier.updated_at.isoformat(),
    }


def _store_call(action):
    try:
        return action()
    except tier_images.ImageError as exc:
        raise HTTPException(422, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


# ── Boards ───────────────────────────────────────────────────────

@router.get("")
async def list_tier_lists():
    db = await get_db()
    tiers = await db.tierlist.find_many(include={"collection": True, "tiers": True}, order={"updated_at": "desc"})
    out = []
    for tier in tiers:
        tile_count = sum(len(json.loads(row.items or "[]")) for row in tier.tiers or [])
        out.append(_summary(tier, len(tier.tiers or []), tile_count))
    return {"items": out}


@router.post("")
async def create_tier_list(body: TierCreate):
    db = await get_db()
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Give the tier list a name.")
    collection_id = body.collection_id if body.collection_id is not None else await _favorites_id(db)
    await _require_collection(db, collection_id)
    rows = DEFAULT_SABC if body.template == "SABC" else []
    async with db.tx() as tx:
        tier = await tx.tierlist.create(data={
            "name": name,
            "description": body.notes or None,
            "template": "SABC" if body.template == "SABC" else "custom",
            "collection_id": collection_id,
        })
        for index, (label, color) in enumerate(rows):
            await tx.tierrow.create(data={"tier_list_id": tier.id, "label": label, "color": color,
                                          "sort_order": index, "items": "[]"})
    return await _board(db, tier.id)


@router.get("/{tier_id:int}")
async def get_tier_list(tier_id: int):
    return await _board(await get_db(), tier_id)


@router.patch("/{tier_id:int}")
async def update_tier_list(tier_id: int, body: TierUpdate):
    db = await get_db()
    if await db.tierlist.find_unique(where={"id": tier_id}) is None:
        raise HTTPException(404, "Tier list not found")
    data: dict = {}
    if "name" in body.model_fields_set and body.name is not None:
        if not body.name.strip():
            raise HTTPException(422, "Give the tier list a name.")
        data["name"] = body.name.strip()
    if "notes" in body.model_fields_set:
        data["description"] = body.notes or None
    if "collection_id" in body.model_fields_set:
        await _require_collection(db, body.collection_id)
        data["collection_id"] = body.collection_id
    if data:
        await db.tierlist.update(where={"id": tier_id}, data=data)
    return await _board(db, tier_id)


@router.put("/{tier_id:int}/board")
async def save_board(tier_id: int, body: BoardIn):
    db = await get_db()
    if await db.tierlist.find_unique(where={"id": tier_id}) is None:
        raise HTTPException(404, "Tier list not found")
    placed: set[str] = set()
    for row in body.rows:
        if not _COLOR.match(row.color):
            raise HTTPException(422, f"'{row.color}' is not a colour (use #rrggbb).")
        for tile in row.items:
            _check_tile(tile)
            if tile.id in placed:
                raise HTTPException(422, f"'{tile.id}' is in more than one place on the board.")
            placed.add(tile.id)

    previous_rows = await db.tierrow.find_many(where={"tier_list_id": tier_id})
    previous_ids: set[str] = set()
    for old_row in previous_rows:
        try:
            previous_tiles = json.loads(old_row.items or "[]")
        except (TypeError, json.JSONDecodeError):
            continue
        previous_ids.update(tile["id"] for tile in previous_tiles if isinstance(tile, dict) and isinstance(tile.get("id"), str) and not tile["id"].startswith("image:"))

    async with db.tx() as tx:
        await tx.tierrow.delete_many(where={"tier_list_id": tier_id})
        for index, row in enumerate(body.rows):
            await tx.tierrow.create(data={
                "tier_list_id": tier_id,
                "label": row.label.strip() or "Row",
                "color": row.color,
                "sort_order": index,
                "items": json.dumps([_stored_tile(t) for t in row.items]),
            })
        current = await tx.tierlist.find_unique(where={"id": tier_id})
        notes = body.notes if body.notes is not None else current.description
        await tx.tierlist.update(where={"id": tier_id}, data={"description": notes or None})  # bumps updated_at
    current_tiles = [tile for row in body.rows for tile in row.items if not tile.id.startswith("image:")]
    current_ids = {tile.id for tile in current_tiles}
    warnings = set()
    for tile in current_tiles:
        warning = await retain_saved_item(tile.model_dump(), f"tier:{tier_id}")
        if warning: warnings.add(warning)
    warning = await release_saved_items(previous_ids - current_ids, f"tier:{tier_id}")
    if warning: warnings.add(warning)
    result = await _board(db, tier_id)
    if warnings: result["warning"] = " ".join(sorted(warnings))
    return result


@router.delete("/{tier_id:int}")
async def delete_tier_list(tier_id: int):
    db = await get_db()
    if await db.tierlist.find_unique(where={"id": tier_id}) is None:
        raise HTTPException(404, "Tier list not found")
    rows = await db.tierrow.find_many(where={"tier_list_id": tier_id})
    item_ids: set[str] = set()
    for row in rows:
        try:
            tiles = json.loads(row.items or "[]")
        except (TypeError, json.JSONDecodeError):
            continue
        item_ids.update(tile["id"] for tile in tiles if isinstance(tile, dict) and isinstance(tile.get("id"), str) and not tile["id"].startswith("image:"))
    await db.tierlist.delete(where={"id": tier_id})   # rows go with it (ON DELETE CASCADE)
    warning = await release_saved_items(item_ids, f"tier:{tier_id}")
    result = {"ok": True}
    if warning: result["warning"] = warning
    return result


# ── Imported images (warehouse) ──────────────────────────────────

@router.get("/image-proxy")
async def proxy_cover(url: str = Query(max_length=500)):
    """Fetch a cover from a known image host so export can draw it (those hosts do not allow cross-origin reads)."""
    parts = urlparse(url)
    if parts.scheme != "https" or not PROXY_HOSTS.match(parts.hostname or ""):
        raise HTTPException(400, "Only covers from AniList, TMDB and IGDB can be fetched through the proxy.")
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        upstream = await client.get(url)
    if upstream.status_code != 200:
        raise HTTPException(502, f"The image host answered HTTP {upstream.status_code}.")
    kind = upstream.headers.get("content-type", "").split(";")[0].strip()
    if not kind.startswith("image/"):
        raise HTTPException(502, "The image host did not return an image.")
    if len(upstream.content) > PROXY_MAX_BYTES:
        raise HTTPException(413, "The image is larger than 8 MB.")
    return Response(content=upstream.content, media_type=kind, headers={"Cache-Control": "public, max-age=86400"})


@router.get("/images")
async def list_images():
    return {"items": tier_images.list_images()}


@router.post("/images")
async def import_images(files: list[UploadFile] = File(...)):
    if len(files) > 50:
        raise HTTPException(422, "Import at most 50 images at a time.")
    imported, failed = [], []
    for upload in files:
        data = await upload.read(tier_images.MAX_BYTES + 1)
        try:
            imported.append(await asyncio.to_thread(tier_images.save_image, data, upload.filename or ""))
        except tier_images.ImageError as exc:
            failed.append({"name": upload.filename or "image", "reason": str(exc)})
    return {"imported": imported, "failed": failed}


@router.get("/images/{hex_id}/file")
async def get_image_file(hex_id: str):
    path = tier_images.image_path(hex_id) if tier_images.HEX_ID.match(hex_id) else None
    if path is None:
        raise HTTPException(404, "Image not found")
    return FileResponse(path)


@router.patch("/images/{hex_id}")
async def relabel_image(hex_id: str, body: ImageLabel):
    if not tier_images.HEX_ID.match(hex_id):
        raise HTTPException(400, "Invalid image id.")
    image = tier_images.set_label(hex_id, body.label)
    if image is None:
        raise HTTPException(404, "Image not found")
    return image


async def _strip_from_boards(image_ids: list[str]) -> None:
    if not image_ids:
        return
    db = await get_db()
    for row in await db.tierrow.find_many():
        stored = json.loads(row.items or "[]")
        kept = [tile for tile in stored if tile.get("id") not in image_ids]
        if len(kept) != len(stored):
            await db.tierrow.update(where={"id": row.id}, data={"items": json.dumps(kept)})


@router.delete("/images/{hex_id}")
async def delete_image(hex_id: str):
    if not tier_images.HEX_ID.match(hex_id):
        raise HTTPException(400, "Invalid image id.")
    result = tier_images.delete_images([tier_images.image_id(hex_id)])
    if result["failed"]:
        raise HTTPException(409, result["failed"][0]["reason"])
    if not result["deleted"]:
        raise HTTPException(404, "Image not found")
    await _strip_from_boards(result["deleted"])
    return result


@router.post("/images/bulk-delete")
async def bulk_delete_images(body: BulkImageDelete):
    result = _store_call(lambda: tier_images.delete_images(body.ids, delete_all=body.all, confirm=body.confirm))
    await _strip_from_boards(result["deleted"])
    return result
