"""Lore research and AI crafting: sources, the quote check, and leads."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.cast as cast
import api.cast_craft as craft
from lore import research

ARTICLE = """Jean-Luc Picard is a fictional character in the Star Trek franchise. He is the captain of the USS Enterprise.

== Depiction ==
Picard is a diplomat and explorer who prefers negotiation to force. He is known for the command "Make it so" and for drinking Earl Grey tea, hot.
He has a deep love of archaeology and Shakespeare.

== Family ==
Jean-Luc Picard has an older brother, Robert Picard, who runs the family vineyard in France.

== Comics ==
In one comic series, Picard secretly fears horses and avoids every stable he passes.

== Reception ==
Critics praised the performance.
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(cast, "CAST_DIR", tmp_path / "cast")
    app = FastAPI()
    app.include_router(cast.router, prefix="/api/cast")

    async def resolve(name, variant="", identity=None, franchise="", article_title=None):
        if name == "Nobody":
            return {"choices": [{"title": "Nobody (film)", "snippet": "", "url": "https://en.wikipedia.org/wiki/Nobody"}]}
        return {"title": "Jean-Luc Picard", "how": "search", "choices": []}

    async def fetch(title):
        if title == "Picard's family":
            return {"title": title, "url": "https://en.wikipedia.org/wiki/Picard_family",
                    "text": "Jean-Luc Picard has an older brother, Robert Picard, who runs the family vineyard in France.",
                    "sections": []}
        return {"title": "Jean-Luc Picard", "url": "https://en.wikipedia.org/wiki/Jean-Luc_Picard",
                "text": ARTICLE, "sections": research.split_sections(ARTICLE)}

    async def search(query, limit=6):
        return [{"title": "Picard's family", "snippet": "", "url": "https://en.wikipedia.org/wiki/Picard_family"}]

    monkeypatch.setattr(research, "resolve", resolve)
    monkeypatch.setattr(research, "fetch_article", fetch)
    monkeypatch.setattr(research, "search", search)
    return TestClient(app)


def model_replies(monkeypatch, *replies):
    calls = []

    async def fake(conn, messages):
        calls.append(messages)
        return replies[len(calls) - 1] if isinstance(replies[len(calls) - 1], str) else json.dumps(replies[len(calls) - 1])
    monkeypatch.setattr(craft, "complete", fake)
    return calls


CONN = {"provider": "lmstudio", "model": "local"}


def test_excerpts_keep_the_intro_and_useful_sections_only():
    article = {"title": "Jean-Luc Picard", "url": "https://en.wikipedia.org/wiki/Jean-Luc_Picard",
               "sections": research.split_sections(ARTICLE)}
    picked = research.excerpts(article)
    assert [s["id"] for s in picked] == ["S1", "S2", "S3"]
    assert "Comics" not in " ".join(s["title"] for s in picked)
    assert "Reception" not in " ".join(s["title"] for s in picked)
    assert picked[1]["url"].endswith("#Depiction") and picked[0]["licence"] == "CC BY-SA 4.0"


def test_search_hits_are_trusted_only_on_an_exact_name():
    assert research.confident("Twelfth Doctor", "Twelfth Doctor")
    assert research.confident("Rick Sanchez (character)", "Rick Sanchez")
    assert not research.confident("Rick and Morty", "Rick Sanchez")
    assert not research.confident("Thirteenth Doctor", "Twelfth Doctor")


def test_quote_check_needs_the_words_to_really_be_in_the_source():
    source = 'He is known for the command "Make it so" and for drinking Earl Grey tea, hot.[12]'
    assert craft.quote_found("He is known for the command “Make it so”", source)
    assert not craft.quote_found("known for the command Engage and Earl Grey", source)
    assert not craft.quote_found("the command Make it so", source)   # under 6 words proves too little


def test_established_craft_keeps_only_quoted_claims_and_checks_leads(client, monkeypatch):
    draft = {"claims": [
        {"field": "personality", "text": "Prefers negotiation to force.", "sources": ["S2"]},
        {"field": "speaking_style", "text": "Says 'Make it so'.", "sources": ["S2"]},
        {"field": "background", "text": "Was born on Mars.", "sources": ["S1"]}],
        "leads": [{"field": "relationships", "text": "Has an older brother, Robert, who runs the family vineyard."},
                  {"field": "personality", "text": "Secretly fears horses."}],
        "example_lines": ["Make it so.", "Tea, Earl Grey, hot."],
        "voice_description": "A measured, commanding older man with a warm, resonant tone."}
    claim_check = {"verdicts": [
        {"id": "C1", "supported": True, "source": "S2", "quote": "a diplomat and explorer who prefers negotiation to force"},
        {"id": "C2", "supported": True, "source": "S2", "quote": "He is known for the command \"Make it so\""},
        # The model claims support with an invented quote; the code must reject it.
        {"id": "C3", "supported": True, "source": "S1", "quote": "Picard was born on the planet Mars in 2305"}]}
    lead_check = {"verdicts": [
        {"id": "K1", "supported": True, "source": "L1", "quote": "has an older brother, Robert Picard, who runs the family vineyard"},
        # Only in the Comics section, which is spin-off media and never used as evidence.
        {"id": "K2", "supported": True, "source": "L2", "quote": "Picard secretly fears horses and avoids every stable"}]}
    calls = model_replies(monkeypatch, "Here you go:\n```json\n" + json.dumps(draft) + "\n```", claim_check, lead_check)
    result = client.post("/api/cast/craft", json={"connection": CONN, "mode": "established", "name": "Jean-Luc Picard"}).json()
    profile = result["draft"]["profile"]
    assert profile["personality"] == "Prefers negotiation to force."
    assert "Mars" not in json.dumps(profile)
    assert "older brother" in profile["relationships"]
    assert result["draft"]["open_questions"] == ["Unconfirmed: Secretly fears horses."]
    assert result["report"]["claims_removed"] == ["Was born on Mars."]
    assert {s["url"] for s in result["draft"]["sources"]} <= {
        "https://en.wikipedia.org/wiki/Jean-Luc_Picard#Depiction", "https://en.wikipedia.org/wiki/Jean-Luc_Picard#Family"}
    assert not any("Comics" in s["title"] for s in result["draft"]["sources"])
    assert "horses" not in json.dumps(result["draft"]["profile"])
    assert profile["example_lines"] == "Make it so.\nTea, Earl Grey, hot."
    assert "Evidence" in calls[0][1]["content"] and "[S2]" in calls[0][1]["content"]
    # A crafted draft is valid input for saving to the Cast.
    saved = client.post("/api/cast", json={**result["draft"], "profile": {"name": "Jean-Luc Picard", **profile}})
    assert saved.status_code == 200


def test_ambiguous_names_ask_which_article(client, monkeypatch):
    model_replies(monkeypatch)
    result = client.post("/api/cast/craft", json={"connection": CONN, "mode": "established", "name": "Nobody"}).json()
    assert result["needs_choice"] is True and result["choices"][0]["title"] == "Nobody (film)"


def test_pasted_sources_work_without_wikipedia(client, monkeypatch):
    draft = {"claims": [{"field": "personality", "text": "Loves riddles.", "sources": ["P1"]}], "leads": []}
    check = {"verdicts": [{"id": "C1", "supported": True, "source": "P1", "quote": "she has always loved riddles more than anything"}]}
    model_replies(monkeypatch, draft, check)
    result = client.post("/api/cast/craft", json={"connection": CONN, "mode": "established", "name": "Mira",
        "use_wikipedia": False, "pasted_sources": [{"title": "My notes", "url": "https://example.com/mira",
        "excerpt": "Mira is a pilot, and she has always loved riddles more than anything."}]}).json()
    assert result["draft"]["profile"]["personality"] == "Loves riddles."
    assert result["draft"]["sources"][0]["title"] == "My notes"


def test_original_characters_and_personas(client, monkeypatch):
    model_replies(monkeypatch, {"name": "Captain Vela", "personality": "Bold", "example_lines": ["Hold fast!", "Again."],
                                "voice_description": "Raspy alto, quick pace"},
                  {"name": "Ren", "address": "Recruit", "description": "A new scout.", "notes": "Owes Rick money."})
    original = client.post("/api/cast/craft", json={"connection": CONN, "mode": "original", "idea": "a sky pirate"}).json()
    assert original["draft"]["profile"]["name"] == "Captain Vela"
    assert original["draft"]["profile"]["example_lines"] == "Hold fast!\nAgain."
    persona = client.post("/api/cast/craft", json={"connection": CONN, "mode": "persona", "idea": "a scout who owes Rick money"}).json()
    assert persona["persona"] == {"name": "Ren", "address": "Recruit", "description": "A new scout.", "personality": "", "notes": "Owes Rick money."}


def test_bad_model_output_is_retried_once_then_a_clear_error(client, monkeypatch):
    calls = model_replies(monkeypatch, "I can't help with that.", "Still no JSON.")
    response = client.post("/api/cast/craft", json={"connection": CONN, "mode": "original", "idea": "x"})
    assert response.status_code == 502 and "JSON" in response.json()["detail"]
    assert len(calls) == 2 and "wasn't valid JSON" in calls[1][-1]["content"]


def test_a_reply_cut_off_mid_way_keeps_its_complete_items(client, monkeypatch):
    model_replies(monkeypatch, '```json\n{"name": "Vela", "personality": "Bold", "background": "Grew up on')
    result = client.post("/api/cast/craft", json={"connection": CONN, "mode": "original", "idea": "x"}).json()
    assert result["draft"]["profile"]["name"] == "Vela" and result["draft"]["profile"]["personality"] == "Bold"


def test_quotes_with_a_slipped_word_pass_but_paraphrases_do_not():
    source = "Picard is a diplomat and explorer who prefers negotiation to force whenever he can."
    assert craft.quote_found("a diplomat and an explorer who prefers negotiation to force", source)
    assert not craft.quote_found("a peacemaker who would rather talk than fight with anyone", source)


def test_hedged_statements_never_reach_the_persona(client, monkeypatch):
    draft = {"claims": [{"field": "personality", "text": "Prefers negotiation to force.", "sources": ["S2"]},
                        {"field": "personality", "text": "He probably fears horses.", "sources": ["S2"]}],
             "leads": [{"field": "background", "text": "He may have studied archaeology."}]}
    check = {"verdicts": [{"id": "C1", "supported": True, "source": "S2", "quote": "a diplomat and explorer who prefers negotiation to force"}]}
    calls = model_replies(monkeypatch, draft, check)
    result = client.post("/api/cast/craft", json={"connection": CONN, "mode": "established", "name": "Jean-Luc Picard"}).json()
    assert result["draft"]["profile"]["personality"] == "Prefers negotiation to force."
    assert result["report"]["claims_kept"] == 1 and result["report"]["claims_removed"] == ["He probably fears horses."]
    assert result["draft"]["open_questions"] == ["Unconfirmed: He may have studied archaeology."]
    assert len(calls) == 2   # hedged items are never even sent to the checker



def test_a_near_match_may_never_flip_negation():
    source = "The Doctor has never carried a gun and he refuses to kill anyone, even his enemies."
    assert craft.quote_found("The Doctor has never carried a gun and he refuses to kill", source)
    assert not craft.quote_found("The Doctor has carried a gun and he refuses to kill anyone", source)
    assert not craft.quote_found("The Doctor has always carried a gun and he refuses to kill anyone", source)


def test_a_real_quote_must_also_say_what_the_claim_says():
    quote = "The Doctor has never carried a gun and he refuses to kill"
    assert craft.quote_supports("Refuses to carry a gun.", quote)
    assert not craft.quote_supports("Loves cooking omelettes for his companions.", quote)


def test_example_lines_cannot_bring_in_new_names():
    evidence = "Picard commands the Enterprise. He drinks Earl Grey tea."
    assert craft.safe_line("Tea, Earl Grey, hot.", evidence)
    assert craft.safe_line("I'll see you on the bridge. Make it so.", evidence)
    assert not craft.safe_line("As captain of the Stargazer, I learned patience.", evidence)


def test_long_pasted_sources_are_split_not_crashed(client, monkeypatch):
    text = ("Mira is a pilot. " * 300) + "In the end she has always loved riddles more than anything else in life."
    draft = {"claims": [{"field": "personality", "text": "Loves riddles more than anything.", "sources": ["P2"]}], "leads": []}
    check = {"verdicts": [{"id": "C1", "supported": True, "source": "P2", "quote": "she has always loved riddles more than anything else"}]}
    model_replies(monkeypatch, draft, check)
    response = client.post("/api/cast/craft", json={"connection": CONN, "mode": "established", "name": "Mira",
        "use_wikipedia": False, "pasted_sources": [{"title": "Notes", "url": "https://example.com/mira", "excerpt": text}]})
    assert response.status_code == 200, response.text
    assert response.json()["draft"]["profile"]["personality"] == "Loves riddles more than anything."
    assert all(len(s["excerpt"]) <= 4000 for s in response.json()["draft"]["sources"])
    bad = client.post("/api/cast/craft", json={"connection": CONN, "mode": "established", "name": "Mira", "use_wikipedia": False,
        "pasted_sources": [{"title": "Notes", "url": "file:///C:/notes.txt", "excerpt": "x" * 40}]})
    assert bad.status_code == 422


def test_typed_names_win_over_the_models(monkeypatch):
    import asyncio

    async def fake(conn, messages):
        return json.dumps({"name": "Someone Else", "address": "Pal"})
    monkeypatch.setattr(craft, "complete", fake)
    body = craft.CraftRequest(connection=CONN, mode="persona", name="Ren", idea="a scout")
    assert asyncio.run(craft.craft_persona(body))["persona"]["name"] == "Ren"
