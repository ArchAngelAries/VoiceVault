from __future__ import annotations

import pytest

from core.media import wikidata


@pytest.mark.asyncio
async def test_shared_character_subtype_without_redundant_base_claim_is_retained(monkeypatch):
    entities = {
        "Q100": _entity("Q100", "Same Name", p31=["Q15773347"]),
        "Q101": _entity("Q101", "Same Name", p31=["Q15773347"]),
        "Q15773347": _entity("Q15773347", "film character", p279=["Q95074"]),
        "Q95074": _entity("Q95074", "character", p279=["Q900"]),
    }
    async def fake_api(params):
        if params["action"] == "wbsearchentities":
            return {"search": [{"id": "Q100"}, {"id": "Q101"}]}
        return {"entities": {qid: entities[qid] for qid in params["ids"].split("|") if qid in entities}}
    monkeypatch.setattr(wikidata, "_api", fake_api)
    result = await wikidata.browse_characters("Same Name", category="movie")
    assert [item["source_id"] for item in result["items"]] == ["Q100", "Q101"]
    assert all(item["categories"] == ["movie"] and item["originating_works"] == [] for item in result["items"])


def _value(qid):
    return {"mainsnak": {"datavalue": {"value": {"id": qid}}}}


def _entity(qid, label, *, p31=(), p279=(), p1441=(), aliases=(), description=None):
    claims = {}
    if p31:
        claims["P31"] = [_value(value) for value in p31]
    if p279:
        claims["P279"] = [_value(value) for value in p279]
    if p1441:
        claims["P1441"] = [_value(value) for value in p1441]
    entity = {"id": qid, "labels": {"en": {"value": label}}, "claims": claims,
              "aliases": {"en": [{"value": value} for value in aliases]}}
    if description:
        entity["descriptions"] = {"en": {"value": description}}
    return entity


@pytest.mark.asyncio
async def test_search_uses_bounded_label_search_and_returns_structured_character(monkeypatch):
    seen = []
    entities = {
        "Q1": _entity("Q1", "Mira", p31=["Q95074"], p1441=["Q20"], aliases=["Mira Vale"],
                       description="An adventurer."),
        "Q95074": _entity("Q95074", "character", p279=["Q999"]),
        "Q999": _entity("Q999", "fictional entity"),
        "Q20": _entity("Q20", "Moon Story", p31=["Q30"]),
        "Q30": _entity("Q30", "anime", p279=["Q1107"]),
        "Q1107": _entity("Q1107", "anime"),
    }

    async def fake_api(params):
        seen.append(params)
        if params["action"] == "wbsearchentities":
            return {"search": [{"id": "Q1"}]}
        ids = params["ids"].split("|")
        return {"entities": {qid: entities[qid] for qid in ids if qid in entities}}

    monkeypatch.setattr(wikidata, "_api", fake_api)
    result = await wikidata.browse_characters("Mira", category="anime", page=2, per_page=50)
    assert seen[0]["continue"] == 24
    assert seen[0]["limit"] == 24
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["id"] == "character:wikidata:Q1"
    assert item["aliases"] == ["Mira Vale"]
    assert item["description"] == "An adventurer."
    assert item["originating_works"] == [{"qid": "Q20", "name": "Moon Story",
                                            "url": "https://www.wikidata.org/wiki/Q20"}]
    assert item["categories"] == ["anime"]
    assert item["image"] is None and item["voice_actors"] == []
    assert result["page_info"]["total"] is None


@pytest.mark.asyncio
async def test_human_ancestry_and_description_text_do_not_establish_fictionality(monkeypatch):
    async def fake_api(params):
        if params["action"] == "wbsearchentities":
            return {"search": [{"id": "Q1"}, {"id": "Q2"}]}
        ids = params["ids"].split("|")
        entities = {
            "Q1": _entity("Q1", "Actor One", p31=["Q95074", "Q5"], p1441=["Q20"]),
            "Q2": _entity("Q2", "Fictional sounding", description="A fictional character from an anime."),
            "Q95074": _entity("Q95074", "character"),
            "Q5": _entity("Q5", "human"),
            "Q20": _entity("Q20", "Show", p31=["Q1107"]),
            "Q1107": _entity("Q1107", "anime"),
        }
        return {"entities": {qid: entities[qid] for qid in ids if qid in entities}}

    monkeypatch.setattr(wikidata, "_api", fake_api)
    result = await wikidata.browse_characters("Actor One", category="anime")
    assert result["items"] == []


@pytest.mark.asyncio
async def test_distinct_same_name_qids_remain_distinct_and_unknown_category_is_empty(monkeypatch):
    async def fake_api(params):
        if params["action"] == "wbsearchentities":
            return {"search": [{"id": "Q1"}, {"id": "Q2"}]}
        ids = params["ids"].split("|")
        entities = {qid: _entity(qid, "Alex", p31=["Q95074"], p1441=["Q20"]) for qid in ("Q1", "Q2")}
        entities["Q95074"] = _entity("Q95074", "character")
        entities["Q20"] = _entity("Q20", "Untyped work")
        return {"entities": {qid: entities[qid] for qid in ids if qid in entities}}

    monkeypatch.setattr(wikidata, "_api", fake_api)
    result = await wikidata.browse_characters("Alex", category="unsupported")
    assert result["items"] == []
    assert result["page_info"]["total"] is None


@pytest.mark.asyncio
async def test_all_four_categories_use_each_characters_own_subtype_or_originating_work(monkeypatch):
    characters = {
        "Q1": ("Anime Hero", "Q80447738", None),
        "Q2": ("Game Hero", None, "Q20"),
        "Q3": ("Film Hero", None, "Q30"),
        "Q4": ("TV Hero", None, "Q40"),
    }
    work_classes = {"Q20": "Q7889", "Q30": "Q11424", "Q40": "Q15416"}
    async def fake_api(params):
        if params["action"] == "wbsearchentities":
            return {"search": [{"id": qid} for qid in characters]}
        all_entities = {"Q95074": _entity("Q95074", "character")}
        for qid, (name, subtype, work) in characters.items():
            all_entities[qid] = _entity(qid, name, p31=["Q95074"] + ([subtype] if subtype else []),
                                        p1441=[work] if work else [])
        for work, class_id in work_classes.items():
            all_entities[work] = _entity(work, f"Work {work}", p31=[class_id])
            all_entities[class_id] = _entity(class_id, f"Class {class_id}")
        ids = params["ids"].split("|")
        return {"entities": {qid: all_entities[qid] for qid in ids if qid in all_entities}}

    monkeypatch.setattr(wikidata, "_api", fake_api)
    for category, expected in (("anime", "Q1"), ("game", "Q2"), ("movie", "Q3"), ("tv", "Q4")):
        result = await wikidata.browse_characters("Hero", category=category)
        assert [item["source_id"] for item in result["items"]] == [expected]


@pytest.mark.asyncio
async def test_deprecated_type_claim_is_not_classification_evidence(monkeypatch):
    async def fake_api(params):
        if params["action"] == "wbsearchentities":
            return {"search": [{"id": "Q1"}]}
        ids = params["ids"].split("|")
        values = {"Q1": _entity("Q1", "Maybe Hero"),
                  "Q95074": _entity("Q95074", "character")}
        values["Q1"]["claims"] = {"P31": [{**_value("Q95074"), "rank": "deprecated"}]}
        return {"entities": {qid: values[qid] for qid in ids if qid in values}}

    monkeypatch.setattr(wikidata, "_api", fake_api)
    result = await wikidata.browse_characters("Maybe Hero")
    assert result["items"] == []


@pytest.mark.asyncio
async def test_distinct_page_classes_and_shared_work_are_loaded_in_bounded_batches(monkeypatch):
    character_ids = [f"Q{1000 + i}" for i in range(24)]
    class_ids = [f"Q{2000 + i}" for i in range(24)]
    calls = []

    async def fake_api(params):
        if params["action"] == "wbsearchentities":
            return {"search": [{"id": qid} for qid in character_ids]}
        requested = params["ids"].split("|")
        calls.append(requested)
        values = {"Q95074": _entity("Q95074", "character"),
                  "Q100": _entity("Q100", "Shared anime", p31=["Q1107"]),
                  "Q1107": _entity("Q1107", "anime")}
        for qid, class_id in zip(character_ids, class_ids):
            values[qid] = _entity(qid, "Same Name", p31=["Q95074", class_id], p1441=["Q100"])
        for class_id in class_ids:
            values[class_id] = _entity(class_id, f"Subtype {class_id}", p279=["Q95074"])
        return {"entities": {qid: values[qid] for qid in requested if qid in values}}

    monkeypatch.setattr(wikidata, "_api", fake_api)
    result = await wikidata.browse_characters("Same Name", category="anime")
    assert len(result["items"]) == 24
    assert len(calls) <= 8
    assert all(len(batch) <= wikidata.BATCH_SIZE for batch in calls)
    assert len({item["id"] for item in result["items"]}) == 24
    assert all(item["categories"] == ["anime"] for item in result["items"])


@pytest.mark.asyncio
async def test_character_with_incomplete_actor_exclusion_graph_is_omitted(monkeypatch):
    async def fake_api(params):
        if params["action"] == "wbsearchentities":
            return {"search": [{"id": "Q1"}]}
        values = {"Q1": _entity("Q1", "Bounded Hero", p31=[f"Q{2000 + i}" for i in range(121)]),
                  "Q95074": _entity("Q95074", "character"),
                  "Q1107": _entity("Q1107", "anime")}
        for index in range(121):
            values[f"Q{2000 + index}"] = _entity(f"Q{2000 + index}", "type", p279=["Q95074", "Q1107"])
        ids = params["ids"].split("|")
        return {"entities": {qid: values[qid] for qid in ids if qid in values}}

    monkeypatch.setattr(wikidata, "_api", fake_api)
    result = await wikidata.browse_characters("Bounded Hero", category="anime")
    assert result["items"] == []
    assert "limits were reached" in result["notice"]


@pytest.mark.asyncio
async def test_authoritative_character_type_is_a_leaf_but_hidden_human_path_is_incomplete(monkeypatch):
    async def fake_api(params):
        if params["action"] == "wbsearchentities":
            return {"search": [{"id": "Q1"}, {"id": "Q2"}]}
        values = {
            "Q1": _entity("Q1", "Supported Hero", p31=["Q3000", "Q80447738"]),
            "Q2": _entity("Q2", "Ambiguous Hero", p31=["Q95074", "Q4000"]),
            "Q3000": _entity("Q3000", "fictional subclass", p279=["Q95074"]),
            "Q4000": _entity("Q4000", "unclassified A", p279=["Q4100"]),
            "Q4100": _entity("Q4100", "unclassified B", p279=["Q4200"]),
            "Q4200": _entity("Q4200", "unclassified C", p279=["Q4300"]),
            "Q4300": _entity("Q4300", "unclassified D", p279=["Q5"]),
            "Q95074": _entity("Q95074", "character", p279=["Q5000"]),
            "Q5000": _entity("Q5000", "broad agent", p279=["Q5001"]),
            "Q5001": _entity("Q5001", "entity"),
            "Q80447738": _entity("Q80447738", "anime character"),
            "Q5": _entity("Q5", "human"),
        }
        ids = params["ids"].split("|")
        return {"entities": {qid: values[qid] for qid in ids if qid in values}}

    monkeypatch.setattr(wikidata, "_api", fake_api)
    result = await wikidata.browse_characters("Hero", category="anime")
    assert [item["source_id"] for item in result["items"]] == ["Q1"]


@pytest.mark.asyncio
async def test_wikidata_api_error_json_is_explicit_and_safe(monkeypatch):
    async def fake_request(*args, **kwargs):
        return {"error": {"code": "badvalue", "info": "private request details"}}

    monkeypatch.setattr(wikidata, "request_json", fake_request)
    with pytest.raises(wikidata.ProviderError, match="API error") as caught:
        await wikidata._api({"action": "wbsearchentities"})
    assert "private request details" not in str(caught.value)
