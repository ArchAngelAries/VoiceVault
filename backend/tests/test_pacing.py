"""Pacing: exact pauses, faster or slower phrases, and the gaps between sentences."""
from dataclasses import dataclass

import numpy as np
import pytest

from engines import pacing
from engines.base import SynthesisResult

RATE = 24000


def tone(seconds, rate=RATE, freq=180.0):
    t = np.arange(int(seconds * rate)) / rate
    return (0.4 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def speech_with_gaps(gaps, word=0.4):
    """word, gap, word, gap, ... like sentences separated by silence."""
    out = [tone(word)]
    for g in gaps:
        out += [np.zeros(int(g * RATE), np.float32), tone(word)]
    return np.concatenate(out)


@dataclass
class Req:
    text: str
    seed: int = 1


def test_parse_tags():
    parts = pacing.parse("Hello there. [pause 0.8s] Wait. [pause] [slow]Very slowly now.[/slow] [speed 1.5]quick[/speed] [pause 250ms] end")
    kinds = [(p.kind, p.text or p.seconds, p.speed) for p in parts]
    assert kinds == [("text", "Hello there.", 1.0), ("pause", 0.8, 1.0), ("text", "Wait.", 1.0), ("pause", 0.5, 1.0),
                     ("text", "Very slowly now.", 0.8), ("text", "quick", 1.5), ("pause", 0.25, 1.0), ("text", "end", 1.0)]
    assert pacing.parse("[fast]a[/fast]")[0].speed == 1.2
    assert pacing.parse("[pause 99]")[0].seconds == pacing.MAX_PAUSE_S
    assert pacing.parse("[speed 9]x[/speed]")[0].speed == 2.0
    assert pacing.parse("[laughs] Ha! [whispers] secret")[0].text == "[laughs] Ha! [whispers] secret"   # engine cues untouched
    assert not pacing.needed("Plain text.", "natural") and pacing.needed("Plain text.", "relaxed")


def test_gaps_change_but_words_stay():
    audio = speech_with_gaps([0.3, 0.6, 0.06])     # the 0.06 s one is inside a "word" and must not change
    words_before = int((np.abs(audio) > 0.05).sum())
    tight = pacing.set_gaps(audio, RATE, "tight")
    relaxed = pacing.set_gaps(audio, RATE, "relaxed")
    dramatic = pacing.set_gaps(audio, RATE, "dramatic")
    assert len(tight) < len(audio) < len(relaxed) < len(dramatic)
    for out in (tight, relaxed, dramatic):
        assert abs(int((np.abs(out) > 0.05).sum()) - words_before) < RATE * 0.01   # speech kept
    gaps = [round((b - a) / RATE, 2) for a, b in pacing.silences(dramatic, RATE)]
    assert gaps[0] >= 0.78 and gaps[1] >= 0.78
    assert pacing.set_gaps(audio, RATE, "natural") is audio


def test_synthesize_joins_parts_with_exact_pauses(monkeypatch):
    calls = []

    def run(req):
        calls.append(req.text)
        return SynthesisResult(samples=tone(0.5), sample_rate=RATE, meta={"engine": "fake"})

    monkeypatch.setattr(pacing, "change_speed", lambda s, r, speed: s if speed == 1 else s[: int(len(s) / speed)])
    result = pacing.synthesize(run, Req("One. [pause 1s] Two. [fast]three[/fast]"), "natural")
    assert calls == ["One.", "Two.", "three"]
    gaps = [(b - a) / RATE for a, b in pacing.silences(result.samples, RATE)]
    assert 0.98 <= gaps[0] <= 1.1                      # exactly the asked pause (plus tiny trims)
    assert result.meta["pacing"]["parts"] == 3 and result.meta["pacing"]["speeds"] == [1.0, 1.0, 1.2]
    with pytest.raises(ValueError, match="nothing to say"):
        pacing.synthesize(run, Req("[pause 1s]"), "natural")


def test_speed_change_keeps_pitch():
    from voice import shaper
    voice = tone(1.0)
    slow = pacing.change_speed(voice, RATE, 0.8)
    assert 1.15 < len(slow) / len(voice) < 1.35
    assert abs(shaper.analyse(slow, RATE, key=None).median_f0 - 180) < 15


def test_punctuation_alone_is_never_voiced():
    parts = pacing.parse("This is [fast]quick quick[/fast]. And [slow]slow[/slow]!")
    assert [p.text for p in parts] == ["This is", "quick quick.", "And", "slow!"]


def test_comma_pauses_are_not_stretched_only_sentence_ends():
    audio = speech_with_gaps([0.18, 0.4])            # a comma-length pause, then a sentence-end pause
    out = pacing.set_gaps(audio, RATE, "dramatic")
    gaps = [round((b - a) / RATE, 2) for a, b in pacing.silences(out, RATE)]
    assert abs(gaps[0] - 0.18) < 0.02 and gaps[1] >= 0.78


def test_a_later_part_hitting_the_length_limit_is_reported(monkeypatch):
    results = iter([{"engine": "fake"}, {"engine": "fake", "truncated": True}])

    def run(req):
        return SynthesisResult(samples=tone(0.5), sample_rate=RATE, meta=next(results))
    result = pacing.synthesize(run, Req("One. [pause 0.5s] Two."), "natural")
    assert result.meta["truncated"] is True
