"""Long-text splitting, chunk joining and the model-free quality check. CPU only."""
import numpy as np
import pytest

from engines import longform
from engines.base import EngineCapabilities, SynthesisRequest, SynthesisResult


def test_short_text_is_one_chunk_and_not_chunked():
    assert longform.split_text("Hello there.") == ["Hello there."]
    assert not longform.needs_chunking("x" * 600, False)
    assert longform.needs_chunking("x" * 601, False)
    assert not longform.needs_chunking("x" * 5000, True)


def test_sentences_pack_up_to_the_limit_and_respect_abbreviations():
    text = "Dr. Smith arrived at 3.5 hours past noon. " * 3 + "Then he left!"
    chunks = longform.split_text(text, max_chars=90)
    assert all(len(c) <= 90 for c in chunks)
    assert chunks[0].startswith("Dr. Smith arrived at 3.5 hours")
    assert " ".join(chunks) == " ".join(text.split())


def test_paragraphs_are_never_merged():
    chunks = longform.split_text("One short.\n\nTwo short.", max_chars=400)
    assert chunks == ["One short.", "Two short."]


def test_tags_are_never_cut_and_bare_tags_join_a_neighbour():
    text = "Hello there, my friend [sighs heavily] and then " + "word " * 30 + "[laughs]"
    chunks = longform.split_text(text, max_chars=60)
    joined = " ".join(chunks)
    assert "[sighs heavily]" in joined and "[laughs]" in joined
    assert all(c.count("[") == c.count("]") for c in chunks)
    assert not any(c.strip() == "[laughs]" for c in chunks)


def test_long_unpunctuated_text_is_hard_split_at_spaces():
    chunks = longform.split_text("word " * 200, max_chars=50)
    assert all(len(c) <= 50 for c in chunks) and all(c.split() for c in chunks)


def test_join_trims_silence_and_crossfades():
    rate = 1000
    tone = np.ones(500, np.float32) * 0.5
    padded = np.concatenate([np.zeros(300, np.float32), tone, np.zeros(300, np.float32)])
    out = longform.join_chunks([padded, padded], rate, fade_ms=20, pause_ms=100)
    # each chunk trimmed to ~500 + 2*40 ms margin; pause 100 samples
    assert 1100 <= len(out) <= 1300
    mid = len(out) // 2
    assert np.all(out[mid - 20:mid + 20] == 0)  # a quiet pause sits between the chunks
    fade_out = out[mid - 120:mid - 50]
    assert np.all(np.diff(np.abs(fade_out[-20:])) <= 1e-6)  # level falls smoothly into the pause


def test_quality_warnings():
    rate = 16000
    assert "silent" in " ".join(longform.analyze(np.zeros(rate * 3, np.float32), rate)["warnings"])
    clipped = np.ones(rate, np.float32)
    assert any("clips" in w for w in longform.analyze(clipped, rate)["warnings"])
    speech = (0.2 * np.sin(np.arange(rate) / 5)).astype(np.float32)
    short = longform.analyze(speech, rate, "This is a long sentence with many many words in it today")
    assert any("shorter than the text" in w for w in short["warnings"])
    ok = longform.analyze(np.tile(speech, 3), rate, "Hello there my friend.")
    assert ok["warnings"] == [] and ok["words_per_second"] > 0
    assert "words_per_second" not in longform.analyze(speech, rate, "こんにちは世界、元気ですか")


def test_synthesize_text_chunks_only_for_engines_without_long_form():
    from api.tts import synthesize_text

    class Manager:
        def __init__(self):
            self.texts = []

        def synthesize(self, model_id, request, device, dtype):
            self.texts.append(request.text)
            return SynthesisResult(np.full(2400, 0.3, np.float32), 24000, {"completion": "eos"})

    class Engine:
        capabilities = EngineCapabilities(languages=["en"])

    text = ("This sentence is about forty characters. " * 30).strip()
    m = Manager()
    result = synthesize_text(m, "x", SynthesisRequest(text=text, seed=5), Engine())
    assert len(m.texts) == result.meta["chunks"]["count"] > 1
    assert result.meta["chunks"]["completion"] == ["eos"] * len(m.texts)

    Engine.capabilities = EngineCapabilities(languages=["en"], long_form=True)
    m2 = Manager()
    synthesize_text(m2, "x", SynthesisRequest(text=text), Engine())
    assert m2.texts == [text]
