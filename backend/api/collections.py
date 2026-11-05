"""
api/collections.py — Collections & Favorites CRUD endpoints
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator, model_validator
from typing import Optional
from core.discovery_retention import release_saved_items, retain_saved_item
from core.item_ids import is_typed_id, parse_id
from db.client import get_db, slugify

router = APIRouter()

# Items returned with each collection in the list view (enough for the 2x2 mosaic).
# The full item list is served by GET /collections/{id}.
PREVIEW_ITEMS = 4


# ── Pydantic models ───────────────────────────────────────────────

class CollectionCreate(BaseModel):
    name: str
    description: Optional[str] = None
    cover_url:   Optional[str] = None

class CollectionUpdate(BaseModel):
    name:        Optional[str] = None
    description: Optional[str] = None
    cover_url:   Optional[str] = None
    sort_order:  Optional[int] = None

class ItemPayload(BaseModel):
    item_id:   str          # typed; TMDB cast roles include their title context
    item_type: str          # anime|movie|tv|game|person|character
    source:    str          # anilist|tmdb|igdb|wikidata
    title:     str
    cover_url: Optional[str] = None
    year:      Optional[int] = None
    @field_validator("item_id")
    @classmethod
    def _must_be_typed(cls, value: str) -> str:
        if not is_typed_id(value):
            raise ValueError("item_id must be a supported typed media, person, or character ID")
        return value

    @model_validator(mode="after")
    def _identity_matches_metadata(self):
        item_type, source, _ = parse_id(self.item_id)
        if self.item_type != item_type or self.source != source:
            raise ValueError("Item type and source must match the typed item ID.")
        return self


class BulkFavoritePayload(BaseModel):
    item: ItemPayload
    favorited: bool


# ── Collections CRUD ─────────────────────────────────────────────

@router.get("")
async def list_collections():
    db = await get_db()
    cols = await db.collection.find_many(
        order={"sort_order": "asc"},
        include={"items": {"take": PREVIEW_ITEMS, "order_by": {"added_at": "desc"}}},
    )
    # One grouped COUNT for every collection instead of one query per collection.
    grouped = await db.collectionitem.group_by(by=["collection_id"], count=True)
    counts = {row["collection_id"]: (row.get("_count") or {}).get("_all", 0) for row in grouped}
    return [_serialize_collection(c, counts.get(c.id, 0)) for c in cols]


@router.post("")
async def create_collection(body: CollectionCreate):
    db   = await get_db()
    name = body.name.strip()
    if not name: raise HTTPException(422, "Collection name is required")
    slug = await _unique_slug(slugify(name) or "collection")
    col = await db.collection.create(data={
        "name":        name,
        "slug":        slug,
        "description": body.description,
        "cover_url":   body.cover_url,
        "is_system":   False,
        "sort_order":  99,
    })
    return _serialize_collection(col, 0)


@router.get("/{collection_id}")
async def get_collection(collection_id: int):
    db  = await get_db()
    col = await db.collection.find_unique(
        where={"id": collection_id},
        include={"items": {"order_by": {"sort_order": "asc"}}},
    )
    if not col: raise HTTPException(404, "Collection not found")
    return _serialize_collection(col, len(col.items or []))


@router.patch("/{collection_id}")
async def update_collection(collection_id: int, body: CollectionUpdate):
    db  = await get_db()
    col = await db.collection.find_unique(where={"id": collection_id})
    if not col: raise HTTPException(404, "Collection not found")
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    if "name" in data:
        data["name"] = data["name"].strip()
        if not data["name"]: raise HTTPException(422, "Collection name is required")
    updated = await db.collection.update(where={"id": collection_id}, data=data)
    count = await db.collectionitem.count(where={"collection_id": collection_id})
    return _serialize_collection(updated, count)


@router.delete("/{collection_id}")
async def delete_collection(collection_id: int):
    db  = await get_db()
    col = await db.collection.find_unique(where={"id": collection_id})
    if not col: raise HTTPException(404, "Collection not found")
    if col.is_system: raise HTTPException(400, "Cannot delete a system collection")
    saved_items = await db.collectionitem.find_many(where={"collection_id": collection_id})
    await db.collection.delete(where={"id": collection_id})
    warning = await release_saved_items((item.item_id for item in saved_items), f"collection:{collection_id}")
    result = {"ok": True}
    if warning: result["warning"] = warning
    return result


# ── Collection items ─────────────────────────────────────────────

@router.post("/{collection_id}/items")
async def add_item(collection_id: int, body: ItemPayload):
    db  = await get_db()
    col = await db.collection.find_unique(where={"id": collection_id})
    if not col: raise HTTPException(404, "Collection not found")
    try:
        item = await db.collectionitem.upsert(
            where={"collection_id_item_id": {"collection_id": collection_id, "item_id": body.item_id}},
            data={
                "create": {
                    "collection_id": collection_id,
                    "item_id":   body.item_id,
                    "item_type": body.item_type,
                    "source":    body.source,
                    "title":     body.title,
                    "cover_url": body.cover_url,
                    "year":      body.year,
                },
                "update": {"title": body.title, "cover_url": body.cover_url},
            },
        )
        # Auto-set collection cover to first item if none set
        if not col.cover_url and body.cover_url:
            await db.collection.update(where={"id": collection_id},
                                        data={"cover_url": body.cover_url})
    except Exception as e:
        raise HTTPException(400, str(e))
    warning = await retain_saved_item(body.model_dump(), f"collection:{collection_id}")
    result = {"ok": True, "item_id": body.item_id}
    if warning: result["warning"] = warning
    return result


@router.delete("/{collection_id}/items/{item_id:path}")
async def remove_item(collection_id: int, item_id: str):
    db = await get_db()
    await db.collectionitem.delete_many(
        where={"collection_id": collection_id, "item_id": item_id}
    )
    warning = await release_saved_items((item_id,), f"collection:{collection_id}")
    result = {"ok": True}
    if warning: result["warning"] = warning
    return result


# ── Favorites convenience endpoints ─────────────────────────────

@router.get("/favorites/check/{item_id:path}")
async def check_favorite(item_id: str):
    db  = await get_db()
    fav = await db.collection.find_unique(where={"slug": "favorites"})
    if not fav: return {"favorited": False}
    hit = await db.collectionitem.find_unique(
        where={"collection_id_item_id": {"collection_id": fav.id, "item_id": item_id}}
    )
    return {"favorited": bool(hit)}


@router.post("/favorites/toggle")
async def toggle_favorite(body: BulkFavoritePayload):
    db  = await get_db()
    fav = await db.collection.find_unique(where={"slug": "favorites"})
    if not fav: raise HTTPException(500, "Favorites collection missing — run startup init")

    if body.favorited:
        await db.collectionitem.upsert(
            where={"collection_id_item_id": {"collection_id": fav.id, "item_id": body.item.item_id}},
            data={
                "create": {
                    "collection_id": fav.id,
                    "item_id":   body.item.item_id,
                    "item_type": body.item.item_type,
                    "source":    body.item.source,
                    "title":     body.item.title,
                    "cover_url": body.item.cover_url,
                    "year":      body.item.year,
                },
                "update": {"title": body.item.title},
            },
        )
        warning = await retain_saved_item(body.item.model_dump(), f"collection:{fav.id}")
    else:
        await db.collectionitem.delete_many(
            where={"collection_id": fav.id, "item_id": body.item.item_id}
        )
        warning = await release_saved_items((body.item.item_id,), f"collection:{fav.id}")
    result = {"favorited": body.favorited}
    if warning: result["warning"] = warning
    return result


@router.get("/favorites/all")
async def get_all_favorites():
    """Return all favorited item_ids for bulk hydrating the UI on load."""
    db  = await get_db()
    fav = await db.collection.find_unique(where={"slug": "favorites"})
    if not fav: return {"item_ids": []}
    items = await db.collectionitem.find_many(where={"collection_id": fav.id})
    return {"item_ids": [i.item_id for i in items]}


# ── Item membership check (which collections contain an item) ────

@router.get("/item/{item_id:path}/collections")
async def item_collections(item_id: str):
    db    = await get_db()
    items = await db.collectionitem.find_many(
        where={"item_id": item_id},
        include={"collection": True},
    )
    return [{"id": i.collection.id, "name": i.collection.name, "slug": i.collection.slug}
            for i in items if i.collection]


# ── Helpers ──────────────────────────────────────────────────────

def _serialize_collection(c, item_count: int) -> dict:
    items = getattr(c, "items", None) or []
    return {
        "id":          c.id,
        "slug":        c.slug,
        "name":        c.name,
        "description": c.description,
        "cover_url":   c.cover_url,
        "is_system":   c.is_system,
        "sort_order":  c.sort_order,
        "item_count":  item_count,
        "items":       [_serialize_item(i) for i in items],
        "created_at":  c.created_at.isoformat() if c.created_at else None,
    }


def _serialize_item(i) -> dict:
    return {
        "id":          i.id,
        "item_id":     i.item_id,
        "item_type":   i.item_type,
        "source":      i.source,
        "title":       i.title,
        "cover_url":   i.cover_url,
        "year":        i.year,
        "added_at":    i.added_at.isoformat() if i.added_at else None,
        "sort_order":  i.sort_order,
        "notes":       i.notes,
    }


async def _unique_slug(base_slug: str) -> str:
    """Return base_slug, or base_slug-2, -3, ... for the first free slot."""
    db = await get_db()
    slug, n = base_slug, 2
    while await db.collection.find_unique(where={"slug": slug}):
        slug = f"{base_slug}-{n}"
        n += 1
    return slug
