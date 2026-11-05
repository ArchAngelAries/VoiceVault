"""Bounded, engine-aware structured speech delivery controls."""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


class Emotion(str, Enum):
    neutral = "neutral"
    cheerful = "cheerful"
    sad = "sad"
    angry = "angry"
    calm = "calm"


class DeliveryStyle(str, Enum):
    natural = "natural"
    whisper = "whisper"
    dramatic = "dramatic"


Expression = Literal[
    "laugh", "laugh_harder", "giggle", "chuckle", "cry", "sigh", "gasp", "whisper",
    "angry", "scream", "snort", "yawn", "cough", "sneeze", "breathing", "humming",
    "throat_clearing", "laughter", "sigh", "sniff", "confirmation-en", "question-en", "question-ah",
    "question-oh", "question-ei", "question-yi", "surprise-ah", "surprise-oh",
    "surprise-wa", "surprise-yo", "dissatisfaction-hnn", "clears throat",
]


class Delivery(BaseModel):
    """Small request contract suitable for user and generated selections."""

    model_config = ConfigDict(extra="forbid")
    emotion: Emotion = Emotion.neutral
    style: DeliveryStyle = DeliveryStyle.natural
    expression: Optional[Expression] = None


_BREEZE_DIRECTIONS = {
    "emotion": {
        "cheerful": "with a cheerful, upbeat emotion",
        "sad": "with a sad, subdued emotion",
        "angry": "with an angry, forceful emotion",
        "calm": "with a calm, relaxed emotion",
    },
    "style": {
        "whisper": "in a whisper",
        "dramatic": "with dramatic, expressive delivery",
    },
}


def resolve_delivery(delivery: Optional[Delivery], engine, *, reference_mode: bool,
                     text: str, voice_description: Optional[str]):
    """Return safe transformed text/description, accepted choices and warnings.

    Only tags declared by an adapter are inserted. Direction wording is a fixed
    mapping for Breeze's documented natural-language instruction interface.
    """
    if delivery is None:
        return text, voice_description, None, []
    caps = engine.capabilities
    warnings: list[str] = []
    applied: dict[str, Optional[str]] = {"emotion": None, "style": None, "expression": None}
    native_tags: list[str] = []
    description_parts: list[str] = []
    direction_allowed = (not reference_mode and caps.voice_description) or (
        reference_mode and caps.reference_direction and caps.delivery_reference)

    emotion = delivery.emotion.value
    if emotion != "neutral":
        if emotion not in caps.delivery_emotions or (reference_mode and not caps.delivery_reference):
            warnings.append(f"Emotion '{emotion}' is not supported by {engine.display_name} in this mode.")
        elif engine.engine_id == "breeze" and direction_allowed:
            description_parts.append(_BREEZE_DIRECTIONS["emotion"][emotion])
            applied["emotion"] = emotion
        else:
            tag = caps.delivery_tag_map.get(f"emotion:{emotion}")
            if tag:
                native_tags.append(caps.tag_template.format(tag=tag))
                applied["emotion"] = emotion
            else:
                warnings.append(f"Emotion '{emotion}' cannot be applied in this mode.")

    style = delivery.style.value
    if style != "natural":
        if style not in caps.delivery_styles or (reference_mode and not caps.delivery_reference):
            warnings.append(f"Style '{style}' is not supported by {engine.display_name} in this mode.")
        elif engine.engine_id == "breeze" and direction_allowed:
            description_parts.append(_BREEZE_DIRECTIONS["style"][style])
            applied["style"] = style
        elif style == "whisper" and not reference_mode and "whisper" in caps.voice_terms.get("style", []):
            description_parts.append("whisper")
            applied["style"] = style
        elif f"style:{style}" in caps.delivery_tag_map:
            native_tags.append(caps.tag_template.format(tag=caps.delivery_tag_map[f"style:{style}"]))
            applied["style"] = style
        else:
            warnings.append(f"Style '{style}' cannot be applied in this mode.")

    expression = delivery.expression
    if expression:
        if expression not in caps.delivery_expressions:
            warnings.append(f"Expression '{expression}' is not supported by {engine.display_name}.")
        else:
            native_tags.append(caps.tag_template.format(tag=expression))
            applied["expression"] = expression

    if native_tags:
        text = " ".join(native_tags) + " " + text
    if description_parts:
        # Retain the user's description and add only fixed, bounded directions.
        existing = (voice_description or "").strip()
        voice_description = ", ".join(part for part in (existing, *description_parts) if part)
    if not any(applied.values()):
        applied = None
    return text, voice_description, applied, warnings
