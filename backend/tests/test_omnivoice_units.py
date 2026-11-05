"""OmniVoice adapter logic with a fake model: discovery, mode selection, validation and errors. No GPU."""
import json

import numpy as np
import pytest

from engines.audio_io import write_wav
from engines.base import EngineError, ModelInfo, ModelState, SynthesisRequest
from engines.omnivoice import NONVERBAL_TAGS, VOICE_DESIGN_TERMS, OmniVoiceEngine


class FakeOmniVoice:
    """Records generate() calls and returns a short silent clip at 24 kHz."""

    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def generate(self, text, **kwargs):
        self.calls.append({"text": text, **kwargs})
        if self.error:
            raise self.error
        return [np.zeros(24000, dtype=np.float32)]


def _loaded_engine(transcribe=None, error=None):
    engine = OmniVoiceEngine(transcribe_reference=transcribe)
    engine._model = FakeOmniVoice(error=error)
    engine._device, engine._dtype = "cuda", "float16"
    return engine


def _clip(path, seconds):
    write_wav(path, np.zeros(int(seconds * 24000), dtype=np.float32) + 0.01, 24000)
    return path


def test_identify_needs_the_omnivoice_config_and_its_audio_tokenizer(tmp_path):
    good = tmp_path / "good"
    (good / "audio_tokenizer").mkdir(parents=True)
    (good / "config.json").write_text(json.dumps({"model_type": "omnivoice"}), encoding="utf-8")
    (good / "model.safetensors").write_bytes(b"\0" * 16)
    no_tokenizer = tmp_path / "no_tokenizer"
    no_tokenizer.mkdir()
    (no_tokenizer / "config.json").write_text(json.dumps({"model_type": "omnivoice"}), encoding="utf-8")
    (no_tokenizer / "model.safetensors").write_bytes(b"\0")
    other = tmp_path / "other"
    other.mkdir()
    (other / "config.json").write_text(json.dumps({"model_type": "llama"}), encoding="utf-8")

    engine = OmniVoiceEngine()

    info = engine.identify(good)
    assert info is not None and info.family == "OmniVoice" and info.state == ModelState.IDENTIFIED
    assert engine.identify(no_tokenizer) is None
    assert engine.identify(other) is None


def test_no_description_and_no_reference_is_automatic_mode():
    engine = _loaded_engine()

    result = engine.synthesize(SynthesisRequest(text="Hello there."))

    assert result.meta["mode"] == "automatic"
    assert engine._model.calls == [{"text": "Hello there."}]
    assert result.sample_rate == 24000


def test_a_description_is_passed_as_voice_design_instruct():
    engine = _loaded_engine()

    result = engine.synthesize(SynthesisRequest(text="Designed.", voice_description="female, young adult, british accent"))

    assert result.meta["mode"] == "design"
    assert engine._model.calls[0]["instruct"] == "female, young adult, british accent"


def test_cloning_uses_the_given_transcript(tmp_path):
    engine = _loaded_engine(transcribe=lambda p: "should not be used")
    ref = _clip(tmp_path / "ref.wav", 5)

    result = engine.synthesize(SynthesisRequest(text="Clone me.", reference_audio=ref, reference_text="Exact words."))

    call = engine._model.calls[0]
    assert result.meta["mode"] == "clone"
    assert call["ref_audio"] == str(ref)
    assert call["ref_text"] == "Exact words."


def test_cloning_without_a_transcript_uses_whisper_when_available(tmp_path):
    engine = _loaded_engine(transcribe=lambda path: "  transcribed by whisper  ")
    ref = _clip(tmp_path / "ref.wav", 5)

    engine.synthesize(SynthesisRequest(text="Clone me.", reference_audio=ref))

    assert engine._model.calls[0]["ref_text"] == "transcribed by whisper"


def test_cloning_without_any_transcript_is_a_clear_error(tmp_path):
    engine = _loaded_engine(transcribe=None)
    ref = _clip(tmp_path / "ref.wav", 5)

    with pytest.raises(EngineError, match="reference_text"):
        engine.synthesize(SynthesisRequest(text="Clone me.", reference_audio=ref))


@pytest.mark.parametrize("seconds", [1.0, 40.0])
def test_reference_outside_three_to_twenty_five_seconds_is_refused(tmp_path, seconds):
    engine = _loaded_engine()
    ref = _clip(tmp_path / f"ref_{seconds}.wav", seconds)

    with pytest.raises(EngineError, match="seconds"):
        engine.synthesize(SynthesisRequest(text="Clone me.", reference_audio=ref, reference_text="x"))
    assert engine._model.calls == []


def test_unknown_voice_design_term_becomes_an_engine_error():
    engine = _loaded_engine(error=ValueError("Unsupported instruct item 'purple'"))

    with pytest.raises(EngineError, match="purple"):
        engine.synthesize(SynthesisRequest(text="Hi.", voice_description="purple"))


def test_empty_text_and_unloaded_engine_are_refused():
    with pytest.raises(EngineError, match="not loaded"):
        OmniVoiceEngine().synthesize(SynthesisRequest(text="Hi."))
    with pytest.raises(EngineError, match="empty"):
        _loaded_engine().synthesize(SynthesisRequest(text="   "))


def test_vocabulary_matches_the_package():
    assert len(NONVERBAL_TAGS) == 14 and "laughter" in NONVERBAL_TAGS and "sigh" in NONVERBAL_TAGS
    assert "female" in VOICE_DESIGN_TERMS["gender"] and "whisper" in VOICE_DESIGN_TERMS["style"]


def test_model_info_is_identified_before_it_is_loaded(tmp_path):
    info = ModelInfo(id="x", engine_id="omnivoice", family="OmniVoice", architecture="", path=str(tmp_path),
                     size_bytes=0, state=ModelState.IDENTIFIED)

    assert info.to_dict()["state"] == "identified"
