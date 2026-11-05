"""The tags and description terms the Generate page offers match the handoff lists. No model is loaded."""
import dataclasses

from engines.maya1 import EMOTION_TAGS, Maya1Engine
from engines.omnivoice import NONVERBAL_TAGS, VOICE_DESIGN_TERMS, OmniVoiceEngine
from engines.breeze import BreezeEngine
from engines.vibevoice_engine import VibeVoiceEngine

OMNIVOICE_TAGS = [
    "laughter", "sigh", "sniff", "confirmation-en", "question-en", "question-ah", "question-oh",
    "question-ei", "question-yi", "surprise-ah", "surprise-oh", "surprise-wa", "surprise-yo",
    "dissatisfaction-hnn",
]
OMNIVOICE_TERMS = {
    "gender": ["male", "female"],
    "age": ["child", "teenager", "young adult", "middle-aged", "elderly"],
    "pitch": ["very low pitch", "low pitch", "moderate pitch", "high pitch", "very high pitch"],
    "style": ["whisper"],
    "accent": ["american accent", "british accent", "australian accent", "chinese accent",
               "canadian accent", "indian accent", "korean accent", "portuguese accent",
               "russian accent", "japanese accent"],
}
MAYA1_TAGS = [
    "laugh", "laugh_harder", "giggle", "chuckle", "cry", "sigh", "gasp", "whisper",
    "angry", "scream", "snort", "yawn", "cough", "sneeze", "breathing", "humming",
    "throat_clearing",
]


def test_omnivoice_tags_and_syntax_match_the_handoff():
    assert NONVERBAL_TAGS == OMNIVOICE_TAGS
    assert OmniVoiceEngine.capabilities.tag_template == "[{tag}]"


def test_omnivoice_description_terms_match_the_handoff():
    assert VOICE_DESIGN_TERMS == OMNIVOICE_TERMS
    assert OmniVoiceEngine.capabilities.voice_terms == OMNIVOICE_TERMS
    assert OmniVoiceEngine.capabilities.reference_transcript is True


def test_maya1_tags_and_syntax_match_the_handoff():
    assert EMOTION_TAGS == MAYA1_TAGS
    assert Maya1Engine.capabilities.tag_template == "<{tag}>"
    assert Maya1Engine.capabilities.reference_audio is False
    assert set(Maya1Engine.capabilities.supported_generation_controls) == {
        "temperature", "top_p", "repetition_penalty", "max_new_tokens", "seed"
    }


def test_vibevoice_has_no_tags_no_description_and_needs_a_clip():
    caps = VibeVoiceEngine.capabilities
    assert caps.emotion_tags == []
    assert caps.voice_description is False
    assert caps.reference_required is True
    assert caps.reference_transcript is False  # the adapter does not read the transcript
    assert caps.supported_generation_controls == ["max_new_tokens", "seed"]
    assert OmniVoiceEngine.capabilities.supported_generation_controls == ["seed"]


def test_breeze_advertises_only_implemented_controls():
    caps = BreezeEngine.capabilities
    assert caps.reference_audio and caps.reference_transcript and caps.voice_description
    assert caps.supported_generation_controls == ["temperature", "top_p", "repetition_penalty", "max_new_tokens", "seed"]
    assert caps.streaming is False and caps.multi_speaker is False


def test_capabilities_serialise_with_the_new_fields():
    data = dataclasses.asdict(OmniVoiceEngine.capabilities)
    assert data["voice_terms"] == OMNIVOICE_TERMS
    assert data["reference_required"] is False
    assert data["supported_generation_controls"] == ["seed"]
