"""
engines/base.py — Shared types for TTS engine adapters.

Every speech engine implements TTSEngine. Optional features are declared in
EngineCapabilities, so the UI only shows controls an engine actually supports.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional
from threading import Event

import numpy as np


class ModelState(str, Enum):
    """Lifecycle of a discovered model. A model being found does not mean it works."""

    DISCOVERED = "discovered"    # a folder with weights/config exists
    IDENTIFIED = "identified"    # family and architecture recognised
    COMPATIBLE = "compatible"    # files complete and runtime packages available
    INTEGRATED = "integrated"    # an adapter in VoiceVault can load and run it
    TESTED = "tested"            # a recorded end-to-end run succeeded (see data/tts_verification.json)
    BLOCKED = "blocked"          # cannot run here; ModelInfo.reason says why


class EngineError(RuntimeError):
    """Raised for user-correctable problems (bad input, unsupported option, missing files)."""


class SynthesisCancelled(EngineError):
    """Cooperative cancellation, after the engine stops its current decode step."""


@dataclass
class EngineCapabilities:
    languages: list[str]
    voice_description: bool = False   # voice designed from a text description
    voice_terms: dict[str, list[str]] = field(default_factory=dict)  # the description's fixed vocabulary, by group
    reference_audio: bool = False     # zero-shot cloning from a reference clip
    reference_required: bool = False  # the voice comes only from a reference clip (no description)
    reference_transcript: bool = False  # the engine uses the transcript of the reference clip
    reference_direction: bool = False  # a text direction can accompany reference cloning
    max_reference_seconds: float = 25.0  # longest clip the engine accepts (uploads allow up to 60 s)
    emotion_tags: list[str] = field(default_factory=list)  # inline tags such as <laugh>
    delivery_emotions: list[str] = field(default_factory=list)
    delivery_styles: list[str] = field(default_factory=list)
    delivery_expressions: list[str] = field(default_factory=list)
    delivery_reference: bool = False  # structured controls can direct reference cloning
    delivery_tag_map: dict[str, str] = field(default_factory=dict)  # e.g. emotion:angry -> native tag name
    multi_speaker: bool = False
    max_speakers: int = 1             # voices one request can carry (speaker tags select them)
    long_form: bool = False           # the engine splits long text itself; VoiceVault does not chunk
    remote: bool = False              # runs outside VoiceVault's GPU (cloud service); no local model load
    streaming: bool = False
    seed: bool = True
    supported_generation_controls: list[str] = field(default_factory=list)
    multilingual: bool = False
    tag_template: str = "<{tag}>"     # how an expression tag is written, e.g. "[{tag}]"
    cpu_resident: bool = False        # runs in its own CPU process; never takes the GPU slot or unloads other models
    # Can people sell what they make with this engine? "yes", "no" (non-commercial weights) or "limited"
    # (conditions apply); license_note says which licence and what to check. Shown wherever the engine is chosen.
    commercial_use: str = "unknown"
    license_note: str = ""
    notes: str = ""


@dataclass
class ModelInfo:
    id: str                    # stable: "<engine>:<path relative to the models root>"
    engine_id: str
    family: str
    architecture: str
    path: str
    size_bytes: int
    state: ModelState
    reason: Optional[str] = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "engine_id": self.engine_id,
            "family": self.family,
            "architecture": self.architecture,
            "path": self.path,
            "size_gb": round(self.size_bytes / 1e9, 2),
            "state": self.state.value,
            "reason": self.reason,
            "notes": self.notes,
        }


@dataclass
class SpeakerReference:
    """One voice in a multi-speaker request; its index is the speaker tag number."""
    audio: Path
    text: Optional[str] = None             # exact transcript of the clip


@dataclass
class SynthesisRequest:
    text: str
    voice_description: Optional[str] = None
    reference_audio: Optional[Path] = None
    reference_text: Optional[str] = None   # transcript of reference_audio, when the engine needs it
    language: Optional[str] = None         # e.g. "English"; None lets the engine decide
    temperature: float = 0.4
    top_p: float = 0.9
    repetition_penalty: float = 1.1
    max_new_tokens: int = 4000
    seed: Optional[int] = 0
    speakers: list[SpeakerReference] = field(default_factory=list)  # multi-speaker voices, in tag order


@dataclass
class SynthesisResult:
    samples: np.ndarray        # float32, mono
    sample_rate: int
    meta: dict[str, Any]       # token counts, timings, device, truncation flags


class TTSEngine(ABC):
    engine_id: str
    display_name: str
    capabilities: EngineCapabilities

    @abstractmethod
    def identify(self, model_dir: Path) -> Optional[ModelInfo]:
        """Return a ModelInfo if model_dir is this engine's model, else None. Must not load weights."""

    @abstractmethod
    def runtime_problem(self) -> Optional[str]:
        """Return a human-readable reason if this engine cannot run here, else None."""

    @abstractmethod
    def load(self, model: ModelInfo, device: str = "cuda", dtype: str = "bfloat16") -> None:
        ...

    @abstractmethod
    def unload(self) -> None:
        ...

    @abstractmethod
    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        ...

    def synthesize_stream(
        self, request: SynthesisRequest, on_audio: Callable[[np.ndarray, int], None], cancel: Event,
    ) -> SynthesisResult:
        """Emit mono float32 chunks synchronously during inference, then return its full result.

        The manager calls this on one worker under the shared speech lock. Engines
        must check cancellation between decode steps and emit no audio after cancel.
        """
        raise EngineError(f"{self.display_name} does not support incremental speech.")
