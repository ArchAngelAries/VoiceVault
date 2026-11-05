-- VoiceVault initial migration
-- Run automatically by Prisma, or manually: prisma migrate dev

CREATE TABLE IF NOT EXISTS "Collection" (
    "id"          INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    "slug"        TEXT NOT NULL UNIQUE,
    "name"        TEXT NOT NULL,
    "description" TEXT,
    "cover_url"   TEXT,
    "is_system"   BOOLEAN NOT NULL DEFAULT false,
    "sort_order"  INTEGER NOT NULL DEFAULT 0,
    "created_at"  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at"  DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS "CollectionItem" (
    "id"            INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    "collection_id" INTEGER NOT NULL,
    "item_id"       TEXT NOT NULL,
    "item_type"     TEXT NOT NULL,
    "source"        TEXT NOT NULL,
    "title"         TEXT NOT NULL,
    "cover_url"     TEXT,
    "year"          INTEGER,
    "added_at"      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "sort_order"    INTEGER NOT NULL DEFAULT 0,
    "notes"         TEXT,
    CONSTRAINT "CollectionItem_collection_id_fkey"
        FOREIGN KEY ("collection_id") REFERENCES "Collection"("id") ON DELETE CASCADE ON UPDATE CASCADE,
    UNIQUE("collection_id", "item_id")
);

CREATE TABLE IF NOT EXISTS "TierList" (
    "id"            INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    "name"          TEXT NOT NULL,
    "description"   TEXT,
    "template"      TEXT NOT NULL DEFAULT 'SABC',
    "collection_id" INTEGER,
    "created_at"    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at"    DATETIME NOT NULL,
    CONSTRAINT "TierList_collection_id_fkey"
        FOREIGN KEY ("collection_id") REFERENCES "Collection"("id") ON DELETE SET NULL ON UPDATE CASCADE
);

CREATE TABLE IF NOT EXISTS "TierRow" (
    "id"           INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    "tier_list_id" INTEGER NOT NULL,
    "label"        TEXT NOT NULL,
    "color"        TEXT NOT NULL,
    "sort_order"   INTEGER NOT NULL,
    "items"        TEXT NOT NULL,
    CONSTRAINT "TierRow_tier_list_id_fkey"
        FOREIGN KEY ("tier_list_id") REFERENCES "TierList"("id") ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE TABLE IF NOT EXISTS "Character" (
    "id"              INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    "item_id"         TEXT NOT NULL UNIQUE,
    "source"          TEXT NOT NULL,
    "name"            TEXT NOT NULL,
    "name_native"     TEXT,
    "image_url"       TEXT,
    "description"     TEXT,
    "media_title"     TEXT,
    "media_item_id"   TEXT,
    "tags"            TEXT,
    "speech_patterns" TEXT,
    "created_at"      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at"      DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS "Clip" (
    "id"             INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    "character_id"   INTEGER,
    "file_path"      TEXT NOT NULL UNIQUE,
    "duration_ms"    INTEGER,
    "transcript"     TEXT,
    "quality_score"  REAL,
    "emotion_tag"    TEXT,
    "source_url"     TEXT,
    "source_ts_start" REAL,
    "source_ts_end"  REAL,
    "created_at"     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "Clip_character_id_fkey"
        FOREIGN KEY ("character_id") REFERENCES "Character"("id") ON DELETE SET NULL ON UPDATE CASCADE
);

CREATE INDEX IF NOT EXISTS "Collection_is_system_idx"    ON "Collection"("is_system");
CREATE INDEX IF NOT EXISTS "Collection_sort_order_idx"   ON "Collection"("sort_order");
CREATE INDEX IF NOT EXISTS "CollectionItem_cid_idx"      ON "CollectionItem"("collection_id");
CREATE INDEX IF NOT EXISTS "CollectionItem_item_id_idx"  ON "CollectionItem"("item_id");
CREATE INDEX IF NOT EXISTS "CollectionItem_type_idx"     ON "CollectionItem"("item_type");
CREATE INDEX IF NOT EXISTS "TierList_cid_idx"            ON "TierList"("collection_id");
CREATE INDEX IF NOT EXISTS "TierRow_tlid_idx"            ON "TierRow"("tier_list_id");
CREATE INDEX IF NOT EXISTS "Character_source_idx"        ON "Character"("source");
CREATE INDEX IF NOT EXISTS "Clip_character_id_idx"       ON "Clip"("character_id");
CREATE INDEX IF NOT EXISTS "Clip_emotion_tag_idx"        ON "Clip"("emotion_tag");
