# Library — 2026-11-04

All of VoiceVault's audio lives in one folder with clear subfolders, and the in-app **Library** page (sidebar → Library) lists every item.

## Folders

```
<VoiceVault>/Library/
    Voices/              cloning voices: 24 kHz clip, full-band .hq44.wav copy, JSON (name, transcript, provenance)
    Generations/         speech from Generate, Converse/Scenes and saved Voice Lab results
    Harvest/             fetched and dropped sources, one folder per source with its clips
    Voice Lab/Uploads/   audio dropped into the Voice Lab
    Music/Projects/      songs and sound effects (takes and stems)
    Exports/Music/       exported mixes (WAV / FLAC / MP3)
    library.json         titles, characters/actors, labels, categories and notes
```

- Working files (Voice Lab previews, conversions in progress, caches) stay in `data/tmp`.
- `backend/core/library.py` defines the paths.
  - `VOICEVAULT_LIBRARY_DIR` overrides the location.
  - Isolated runs (a custom data dir, tests) get `<data dir>/Library`, so they never touch the real Library.
- A custom outputs folder set in Settings still wins for Generations.
- **Migration (done 2026-11-04):**
  - 13 files moved from `data/references`, `data/outputs` and `data/harvest`.
  - Backup: `data/backups/library-migration-20261104-105228.zip`.
  - `core.library.migrate_legacy()` never overwrites. It backs up to a ZIP first and removes only empty old folders.

## In-app Library

- **One list of everything:**
  - voices, harvested clips and their sources, and generations;
  - Voice Lab uploads, music takes and sound effects.
- **Filters:** search (title, labels, character, category, notes, transcript), plus filters by type, character/actor and label.
- **Row buttons:**
  - **Play** (double-click also plays);
  - **Show on disk**, which opens Explorer with the file selected;
  - **Edit**.
- **Edit:**
  - **Title.** Renaming a voice also renames it in Generate.
  - **Character or actor.** Suggestions come from saved Discover characters and people, plus names already used.
  - **Labels, category and notes.**
- **Music and sound effects** take labels, category and notes, but no character. The API refuses it.
- **Right-click menu:** Play, Show on disk, Rename / edit labels, Assign character or actor, Copy path, and Delete.
  - Delete works for voices, generations, music and sfx.
  - Harvest items are deleted from the Harvest page.
- **Open folder…** opens any Library folder.

## API (`/api/library`)

| Call | Purpose |
|---|---|
| `GET /items?type=&q=&character=&label=` | List items; also returns all labels and categories |
| `PATCH /items` | `{key, title?, character?{name,kind,item_id}, clear_character?, labels?, category?, notes?}` |
| `DELETE /items?key=` | Delete one item (voice, generation, music, sfx) |
| `POST /reveal` | `{key}` or `{folder}`; only files inside VoiceVault's own folders |
| `GET /characters` | Character/actor suggestions |
| `GET /folders` | Folder paths |

Item keys:
- `voice:<id>`
- `generation:<file>`
- `source:<sid>`
- `clip:<sid>:<vid>`
- `voicelab:<id>`
- `music:<pid>:<tid>`
- `sfx:<pid>:<tid>`

Tests: `backend/tests/test_library.py` has 7 tests: layout, listing, edit/assign/filter, music refusal, reveal guard, delete, and migration without overwrite.
