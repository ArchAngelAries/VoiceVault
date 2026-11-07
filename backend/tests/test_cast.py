"""The Cast, user personas, and how a persona reaches a chat."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.cast as cast
import api.converse as converse


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(cast, "CAST_DIR", tmp_path / "cast")
    monkeypatch.setattr(cast, "PERSONA_DIR", tmp_path / "personas")
    monkeypatch.setattr(converse, "STORE_DIR", tmp_path / "conversations")
    monkeypatch.setattr(converse, "_active_turns", set())
    app = FastAPI()
    app.include_router(cast.router, prefix="/api/cast")
    app.include_router(cast.personas_router, prefix="/api/personas")
    app.include_router(converse.router, prefix="/api/converse")
    return TestClient(app)


PICARD = {"kind": "established", "variant": "", "profile": {
    "name": "Jean-Luc Picard", "catalog_identity": "character:wikidata:Q16341",
    "personality": "Principled, measured, diplomatic.", "speaking_style": "Formal; 'Make it so.'",
    "background": "Captain of the USS Enterprise-D."},
    "sources": [{"title": "Jean-Luc Picard - Wikipedia", "url": "https://en.wikipedia.org/wiki/Jean-Luc_Picard",
                 "licence": "CC BY-SA 4.0", "excerpt": "Picard is captain of the Enterprise."}],
    "open_questions": ["Which era: TNG or Picard?"]}


def test_create_links_profile_and_dedupes_catalog_characters(client):
    first = client.post("/api/cast", json=PICARD).json()
    assert first["existing"] is False and first["profile"]["cast_id"] == first["id"]
    assert first["profile"]["speaking_style"].startswith("Formal")
    again = client.post("/api/cast", json=PICARD).json()
    assert again["existing"] is True and again["id"] == first["id"]
    assert [m["id"] for m in client.get("/api/cast").json()["cast"]] == [first["id"]]
    assert client.get("/api/cast", params={"q": "picard"}).json()["cast"]
    assert not client.get("/api/cast", params={"q": "rick"}).json()["cast"]


def test_every_save_keeps_the_previous_version_and_restore_undoes(client):
    member = client.post("/api/cast", json=PICARD).json()
    rewrite = {**PICARD, "profile": {**PICARD["profile"], "personality": "AI rewrite"}, "note": "AI crafted"}
    updated = client.put(f"/api/cast/{member['id']}", json=rewrite).json()
    assert updated["profile"]["personality"] == "AI rewrite"
    versions = client.get(f"/api/cast/{member['id']}/versions").json()["versions"]
    assert len(versions) == 1
    restored = client.post(f"/api/cast/{member['id']}/restore", json={"version": versions[0]["version"]}).json()
    assert restored["profile"]["personality"] == "Principled, measured, diplomatic."
    assert client.get(f"/api/cast/{member['id']}").json()["profile"]["personality"].startswith("Principled")
    assert len(client.get(f"/api/cast/{member['id']}/versions").json()["versions"]) == 2


def test_history_is_capped(client, monkeypatch):
    monkeypatch.setattr(cast, "MAX_VERSIONS", 3)
    monkeypatch.setattr(cast, "MAX_USER_VERSIONS", 3)
    member = client.post("/api/cast", json=PICARD).json()
    for n in range(6):
        client.put(f"/api/cast/{member['id']}", json={**PICARD, "note": f"save {n}"})
    assert len(client.get(f"/api/cast/{member['id']}/versions").json()["versions"]) == 3


def test_bad_sources_ids_and_delete(client):
    bad = {**PICARD, "sources": [{"title": "x", "url": "file:///C:/secret.txt"}]}
    assert client.post("/api/cast", json=bad).status_code == 422
    assert client.get("/api/cast/not-a-uuid").status_code == 404
    assert client.post(f"/api/cast/{'0' * 8}-0000-0000-0000-{'0' * 12}/restore",
                       json={"version": "../../x"}).status_code == 422
    member = client.post("/api/cast", json=PICARD).json()
    client.put(f"/api/cast/{member['id']}", json=PICARD)
    assert client.delete(f"/api/cast/{member['id']}").json() == {"deleted": True}
    assert client.get(f"/api/cast/{member['id']}").status_code == 404
    assert not (cast.CAST_DIR / "history" / member["id"]).exists()


def test_personas_crud_and_default(client):
    assert client.post("/api/personas", json={"name": "  "}).status_code == 422
    ensign = client.post("/api/personas", json={"name": "Ensign Vale", "address": "Ensign",
                                                "avatar_url": "/api/tiers/images/vale.png"}).json()
    assert client.put("/api/personas/default", json={"id": ensign["id"]}).json()["default_id"] == ensign["id"]
    listing = client.get("/api/personas").json()
    assert listing["default_id"] == ensign["id"] and listing["personas"][0]["name"] == "Ensign Vale"
    edited = client.put(f"/api/personas/{ensign['id']}", json={"name": "Lt. Vale"}).json()
    assert edited["name"] == "Lt. Vale" and edited["created_at"] == ensign["created_at"]
    assert client.post("/api/personas", json={"name": "x", "avatar_url": "C:\\pics\\me.png"}).status_code == 422
    client.delete(f"/api/personas/{ensign['id']}")
    assert client.get("/api/personas").json() == {"personas": [], "default_id": None}


def test_chat_carries_persona_into_messages_and_prompt(client, monkeypatch):
    persona = {"name": "Ensign Vale", "address": "Ensign", "avatar_url": "/api/tiers/images/vale.png",
               "notes": "A junior officer under Picard's command."}
    session = client.post("/api/converse/sessions", json={
        "kind": "chat", "roleplay": True, "profile": PICARD["profile"], "user_persona": persona}).json()
    assert session["user_persona"]["name"] == "Ensign Vale"
    seen = {}

    async def fake_stream(_base, _model, messages, *args, **kwargs):
        seen["system"] = messages[0]["content"]
        yield "Make it so."

    monkeypatch.setattr(converse, "stream_chat", fake_stream)
    import asyncio

    async def run():
        response = await converse.send_message(session["id"], converse.MessageRequest(text="Captain?", model="local"))
        async for _ in response.body_iterator:
            pass
    asyncio.run(run())
    assert "The user is playing this persona" in seen["system"] and "Ensign Vale" in seen["system"]
    assert "Way of speaking: Formal" in seen["system"] and "Background and lore: Captain" in seen["system"]
    saved = client.get(f"/api/converse/sessions/{session['id']}").json()
    assert saved["messages"][0] == {"role": "user", "content": "Captain?", "persona_name": "Ensign Vale",
                                    "persona_avatar_url": "/api/tiers/images/vale.png"}
    cleared = client.patch(f"/api/converse/sessions/{session['id']}", json={"user_persona": None}).json()
    assert "user_persona" not in cleared


def test_empty_persona_fields_stay_out_of_saved_profiles(client):
    session = client.post("/api/converse/sessions", json={"profile": {"name": "Mira"}}).json()
    assert session["profile"] == {"name": "Mira", "description": "", "personality": "",
                                  "voice_description": "", "source_notes": "", "voice_settings": None}



def test_agent_saves_never_push_user_versions_out_of_history(client, monkeypatch):
    monkeypatch.setattr(cast, "MAX_VERSIONS", 2)
    member = client.post("/api/cast", json=PICARD).json()
    client.put(f"/api/cast/{member['id']}", json={**PICARD, "note": ""})                       # a user save
    for n in range(6):
        client.put(f"/api/cast/{member['id']}", json={**PICARD, "note": "Crafted by an agent"})
    notes = [v["note"] for v in client.get(f"/api/cast/{member['id']}/versions").json()["versions"]]
    assert notes.count("") >= 2                     # the original and the user's own save are still there
    assert notes.count("Crafted by an agent") <= 2


def test_restoring_a_missing_version_is_a_404(client):
    member = client.post("/api/cast", json=PICARD).json()
    assert client.post(f"/api/cast/{member['id']}/restore", json={"version": "20200101T000000.000000Z"}).status_code == 404
