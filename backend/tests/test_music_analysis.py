"""Song analysis for prompting: tempo, key, metre, suggestions, the studio job and the vocabulary. CPU only."""
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from music import analysis
from music.studio import ProjectStore, Studio

RATE = 22050


def _song(bpm=120.0, seconds=16.0, root=57, minor=True, rate=RATE):
    """Clicks on every beat plus a sustained root triad (A minor by default)."""
    n = int(seconds * rate)
    t = np.arange(n) / rate
    third = 3 if minor else 4
    chord = sum(np.sin(2 * np.pi * 440 * 2 ** ((m - 69) / 12) * t) for m in (root, root + third, root + 7, root + 12))
    y = 0.08 * chord
    beat = int(rate * 60 / bpm)
    click = np.exp(-np.arange(int(0.03 * rate)) / (0.004 * rate)) * np.sin(2 * np.pi * 1800 * np.arange(int(0.03 * rate)) / rate)
    for i, start in enumerate(range(0, n - len(click), beat)):
        y[start:start + len(click)] += click * (0.9 if i % 4 == 0 else 0.5)
    return np.stack([y, y], axis=1).astype(np.float32)


def test_measures_tempo_key_and_metre():
    res = analysis.analyze(_song(), RATE, use_tags=False)
    assert abs(res["tempo"]["bpm"] - 120) <= 3, res["tempo"]
    assert res["key"]["key"] == "A minor", res["key"]
    assert res["metre"]["label"] == "4/4"
    assert res["suggestion"]["bpm"] == res["tempo"]["bpm"] and res["suggestion"]["key"] == "A minor"
    assert res["suggestion"]["time_signature"] == "4" and res["feel"] == "upbeat"
    major = analysis.analyze(_song(bpm=90, root=60, minor=False), RATE, use_tags=False)
    assert major["key"]["key"] == "C major" and abs(major["tempo"]["bpm"] - 90) <= 3


def test_too_short_is_refused():
    with pytest.raises(ValueError):
        analysis.analyze(np.zeros((RATE, 2), np.float32), RATE, use_tags=False)


def _a(tags, stems=None, character=None):
    return {"tags": tags, "stems": stems, "character": character or {}, "feel": "mid-tempo",
            "tempo": {"bpm": 100}, "key": {"key": "D major"}, "metre": {"beats_per_bar": 3}}


def test_suggestion_rules():
    t = lambda *pairs: [{"label": l, "score": s} for l, s in pairs]
    tags = {"genre": t(("jazz", 0.7), ("blues", 0.2)), "mood": t(("happy", 0.4), ("sad", 0.3), ("calm", 0.2)),
            "instrument": t(("saxophone", 0.6), ("piano", 0.3)), "vocal": t(("instrumental", 0.5), ("male vocal", 0.3))}
    s = analysis.suggest(_a(tags, character={"dark": True, "dynamic_range_db": 18}))
    # Opposite moods are not both used; an unsure "instrumental" falls back to the vocal.
    assert s["style"] == "jazz, blues, saxophone, piano, male vocal, happy, calm, mid-tempo, dynamic mix, warm mix"
    assert s["time_signature"] == "3" and not s["instrumental"]
    # Stems decide: no piano energy removes piano; strong drums add drums; a silent vocal stem means instrumental.
    s = analysis.suggest(_a(tags, stems={"piano": -40.0, "drums": -8.0, "vocals": -45.0, "bass": -12.0}))
    assert "piano" not in s["style"] and "drums" in s["style"] and "bass guitar" in s["style"]
    assert "instrumental" in s["style"] and s["instrumental"]


def test_analyze_job_stores_result_on_take(tmp_path, monkeypatch):
    class Runtime:
        def stop(self):
            pass
    studio = Studio(ProjectStore(tmp_path / "projects"), Runtime(), tmp_path / "tmp")
    try:
        p = studio.store.create("Song")
        take = studio.store.add_take(p["id"], _song(rate=44100), 44100, op="import", label="Imported")
        job = studio.submit(p["id"], "analyze", {"take": take["id"], "tags": False})
        import time
        for _ in range(600):
            j = studio.job(job.id)
            if j["status"] in ("done", "failed"):
                break
            time.sleep(0.05)
        assert j["status"] == "done", j
        saved = studio.store.find_take(studio.store.get(p["id"]), take["id"])["analysis"]
        assert saved["key"]["key"] == "A minor" and saved["suggestion"]["bpm"] and saved["analyzed"]
        assert studio.store.get(p["id"])["active_take"] == take["id"] and len(studio.store.get(p["id"])["takes"]) == 1
    finally:
        studio.shutdown()


def test_vocabulary_endpoint():
    from api import music as music_api

    app = FastAPI()
    app.include_router(music_api.router, prefix="/api/music")
    data = TestClient(app).get("/api/music/style-vocabulary").json()
    assert set(data["groups"]) == {"genre", "mood", "instrument", "vocal", "production"}
    assert "jazz" in data["groups"]["genre"] and isinstance(data["tagger_ready"], bool)


def test_tagger_alone_never_calls_a_song_instrumental():
    t = lambda *pairs: [{"label": l, "score": sc} for l, sc in pairs]
    a = {"tags": {"genre": t(("pop", 0.4)), "mood": t(("hopeful", 0.3)), "instrument": t(("synth", 0.5)),
                  "vocal": t(("instrumental", 0.8))}, "tempo": {"bpm": 112}, "key": {"key": "D major"}}
    s = analysis.suggest(a)
    assert not s["instrumental"] and "instrumental" not in s["style"]
