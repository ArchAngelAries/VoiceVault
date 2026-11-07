"""MCP tools for the Cast and personas: favourites get crafted, saves only change what is passed."""
import json

import pytest

import mcp_server as vv

PICARD = {"id": "11111111-1111-1111-1111-111111111111", "kind": "established", "variant": "", "review_state": "reviewed",
          "profile": {"name": "Jean-Luc Picard", "personality": "Principled.", "background": "Captain.",
                      "voice_settings": {"model_id": "kokoro:x"}, "catalog_identity": "character:wikidata:Q16341"},
          "sources": [{"title": "W", "url": "https://en.wikipedia.org/wiki/Jean-Luc_Picard", "licence": "CC BY-SA 4.0", "excerpt": "x"}],
          "open_questions": []}
DRAFT = {"draft": {"kind": "established", "variant": "", "profile": {"name": "Rick", "personality": "Cynical genius.",
                                                                      "speaking_style": "Burps mid-sentence.", "background": ""},
                   "sources": [{"title": "Rick Sanchez — Personality (Wikipedia)", "url": "https://en.wikipedia.org/wiki/Rick_Sanchez#Personality",
                                "licence": "CC BY-SA 4.0", "excerpt": "...", "fetched_at": "2026-11-07"}],
                   "open_questions": ["Unconfirmed: Likes pickles."]},
         "article": {"title": "Rick Sanchez", "url": "https://en.wikipedia.org/wiki/Rick_Sanchez"},
         "report": {"claims_kept": 5, "claims_removed": ["x"], "leads_confirmed": 1, "leads_unconfirmed": ["Likes pickles."]}}


@pytest.fixture
def calls(monkeypatch):
    log = []
    personas = [{"id": "p1", "name": "Ensign Vale", "address": "Ensign", "description": "", "personality": "", "notes": ""}]

    def fake(method, path, body=None, params=None, timeout=60):
        log.append((method, path, body, timeout))
        if path == "/api/cast" and method == "GET":
            return {"cast": [PICARD]}
        if path == "/api/cast/candidates":
            return {"candidates": [{"item_id": "character:anilist:1", "name": "Rick Sanchez", "source": "anilist", "image_url": "https://img/x.png"}]}
        if path == f"/api/cast/{PICARD['id']}" and method == "GET":
            return PICARD
        if path == "/api/converse/connection":
            return {"provider": "lmstudio", "base_url": "http://localhost:1234"}
        if path == "/api/converse/models":
            return {"models": [{"id": "text-embedding-nomic"}, {"id": "gemma"}]}
        if path == "/api/cast/craft":
            return DRAFT
        if path in ("/api/cast", f"/api/cast/{PICARD['id']}") and method in ("POST", "PUT"):
            return {"id": PICARD["id"] if method == "PUT" else "22222222-2222-2222-2222-222222222222", **body}
        if path == "/api/personas" and method == "GET":
            return {"personas": personas, "default_id": None}
        if path.startswith("/api/personas"):
            return {"id": "p1", **(body or {})}
        raise AssertionError(f"unexpected {method} {path}")
    monkeypatch.setattr(vv, "api", fake)
    return log


def call(name, args):
    return vv.TOOLS[name]["fn"](vv.check(args, vv.TOOLS[name]["schema"]))


def test_list_shows_cast_and_favourites_to_craft(calls):
    result = call("vv_cast_list", {})
    assert result["cast"][0]["cast"] == "Jean-Luc Picard" and result["cast"][0]["has_voice"] is True
    assert result["favourites_not_in_cast"] == [{"name": "Rick Sanchez", "catalog_id": "character:anilist:1"}]


def test_crafting_a_favourite_adds_it_to_the_cast_as_a_draft(calls):
    result = call("vv_cast_craft", {"character": "rick"})
    craft = next(c for c in calls if c[1] == "/api/cast/craft")
    assert craft[2]["connection"]["model"] == "gemma" and craft[2]["catalog_identity"] == "character:anilist:1"
    assert craft[3] >= 600
    saved = next(c for c in calls if c[1] == "/api/cast" and c[0] == "POST")[2]
    assert saved["profile"]["name"] == "Rick Sanchez"          # the catalog name wins over the model's
    assert saved["profile"]["image_url"] == "https://img/x.png"
    assert saved["profile"]["personality"] == "Cynical genius." and saved["profile"]["background"] == ""
    assert saved["review_state"] == "draft" and saved["open_questions"] == ["Unconfirmed: Likes pickles."]
    assert result["saved"] and result["statements_kept"] == 6 and result["unconfirmed"] == ["Likes pickles."]


def test_crafting_a_cast_member_keeps_voice_and_updates_in_place(calls):
    call("vv_cast_craft", {"character": "picard"})
    saved = next(c for c in calls if c[0] == "PUT")[2]
    assert saved["profile"]["voice_settings"] == {"model_id": "kokoro:x"}
    # An established re-craft replaces every lore field, so old unchecked text can't stay in the persona;
    # what it replaced is listed as an open question (the full version is in history).
    assert saved["profile"]["background"] == "" and saved["profile"]["speaking_style"] == "Burps mid-sentence."
    assert any(q.startswith("Previous unchecked background") for q in saved["open_questions"])
    assert saved["sources"] == DRAFT["draft"]["sources"]


def test_save_changes_only_passed_fields(calls):
    call("vv_cast_save", {"character": "Jean-Luc Picard", "speaking_style": "Formal.", "open_questions": ["Era?"],
                          "sources_json": json.dumps([{"title": "Memory Alpha", "url": "https://memory-alpha.example/Picard", "excerpt": "x"}])})
    saved = next(c for c in calls if c[0] == "PUT")[2]
    assert saved["profile"]["personality"] == "Principled." and saved["profile"]["speaking_style"] == "Formal."
    assert saved["sources"][0]["title"] == "Memory Alpha" and saved["open_questions"] == ["Era?"]
    with pytest.raises(vv.ToolError):
        call("vv_cast_save", {"character": "Picard", "sources_json": "{not json"})


def test_persona_save_and_default(calls):
    result = call("vv_persona_save", {"persona": "Ensign Vale", "notes": "Serves under Picard.", "make_default": True})
    put = next(c for c in calls if c[0] == "PUT" and c[1] == "/api/personas/p1")[2]
    assert put["address"] == "Ensign" and put["notes"] == "Serves under Picard."
    assert ("PUT", "/api/personas/default", {"id": "p1"}, 60) in calls and result["default"] is True


def test_cast_tools_never_delete():
    import inspect
    for name in ("vv_cast_craft", "vv_cast_save", "vv_persona_save"):
        assert "DELETE" not in inspect.getsource(vv.TOOLS[name]["fn"])
