import pytest
from pydantic import ValidationError

from engines.breeze import BreezeEngine
from engines.delivery import Delivery, resolve_delivery
from engines.maya1 import Maya1Engine
from engines.omnivoice import OmniVoiceEngine


def test_delivery_contract_is_bounded_and_rejects_extra_controls():
    with pytest.raises(ValidationError):
        Delivery(emotion="excited")
    with pytest.raises(ValidationError):
        Delivery(style="shout")
    with pytest.raises(ValidationError):
        Delivery(expression="<angry> arbitrary text")
    with pytest.raises(ValidationError):
        Delivery.model_validate({"emotion": "calm", "custom": "<control>"})


def test_maya_delivery_maps_only_declared_native_tags():
    engine = Maya1Engine()
    text, description, applied, warnings = resolve_delivery(
        Delivery(emotion="angry", style="whisper", expression="gasp"), engine,
        reference_mode=False, text="Hello.", voice_description="warm voice",
    )
    assert text == "<angry> <whisper> <gasp> Hello."
    assert description == "warm voice"
    assert applied == {"emotion": "angry", "style": "whisper", "expression": "gasp"}
    assert warnings == []


def test_unsupported_choices_are_omitted_and_natural_defaults_are_noops():
    text, desc, applied, warnings = resolve_delivery(
        Delivery(emotion="cheerful", style="dramatic", expression="giggle"),
        OmniVoiceEngine(), reference_mode=True, text="Hello.", voice_description="",
    )
    assert (text, desc, applied) == ("Hello.", "", None)
    assert len(warnings) == 3
    text, desc, applied, warnings = resolve_delivery(
        Delivery(), BreezeEngine(), reference_mode=False, text="Hello.", voice_description="",
    )
    assert (text, desc, applied, warnings) == ("Hello.", "", None, [])


def test_breeze_directions_retain_base_description_and_allow_reference_mode():
    text, description, applied, warnings = resolve_delivery(
        Delivery(emotion="calm", style="dramatic", expression="sigh"), BreezeEngine(),
        reference_mode=True, text="Hello.", voice_description="older voice",
    )
    assert text == "(sigh) Hello."
    assert description == "older voice, with a calm, relaxed emotion, with dramatic, expressive delivery"
    assert applied == {"emotion": "calm", "style": "dramatic", "expression": "sigh"}
    assert warnings == []


def test_omnivoice_whisper_is_limited_to_voice_design():
    text, description, applied, warnings = resolve_delivery(
        Delivery(style="whisper"), OmniVoiceEngine(), reference_mode=False,
        text="Hello.", voice_description="female, young adult",
    )
    assert text == "Hello."
    assert description == "female, young adult, whisper"
    assert applied["style"] == "whisper" and warnings == []
