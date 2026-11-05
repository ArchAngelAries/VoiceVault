"""
music/analysis.py — Song analysis for prompting: tempo, key, metre, loudness and style tags.

Measured with signal processing (librosa, CPU):
  tempo      beat tracking on the onset envelope, with the half/double alternatives
  key        Krumhansl-Schmuckler profiles matched against the averaged chroma
  metre      beat-level accent pattern (3 vs 4 beats per bar), a rough guess
  character  loudness, brightness and dynamics, used for production words

Suggested with zero-shot audio tagging (LAION CLAP music, CPU, optional):
  genre, mood, instruments, vocal type and production, scored against STYLE_VOCAB.
When a take already has stems, the stems decide which instruments and vocals are present.

The result is advice for the style box, not ground truth. Every value carries a confidence
so the UI can say how sure it is.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

CLAP_REPO = "laion/clap-htsat-unfused"  # larger_clap_music embeds every text alike (checked 2026-11-04)
ANALYSIS_RATE = 22050
CLAP_RATE = 48000
CLAP_WINDOW_S = 10.0
MAX_WINDOWS = 4

# One vocabulary drives the tagger, the style builder chips and the prompt builder.
STYLE_VOCAB: dict[str, list[str]] = {
    "genre": ["pop", "indie pop", "rock", "alternative rock", "hard rock", "heavy metal", "punk", "hip hop", "trap",
              "R&B", "soul", "funk", "jazz", "blues", "country", "folk", "electronic", "EDM", "house", "techno",
              "drum and bass", "dubstep", "synthwave", "ambient", "lo-fi hip hop", "classical", "cinematic orchestral",
              "reggae", "latin", "K-pop", "J-pop", "gospel", "disco", "chiptune", "musical theatre"],
    "mood": ["happy", "uplifting", "energetic", "melancholic", "sad", "romantic", "dark", "aggressive", "calm",
             "dreamy", "epic", "nostalgic", "playful", "tense", "triumphant", "mysterious"],
    "instrument": ["acoustic guitar", "electric guitar", "distorted guitar", "bass guitar", "synth bass", "piano",
                   "electric piano", "synthesizer", "strings", "violin", "cello", "brass", "saxophone", "flute",
                   "choir", "drums", "drum machine", "808", "organ", "harp", "ukulele", "banjo", "accordion"],
    "vocal": ["male vocal", "female vocal", "rap vocal", "choir vocals", "duet", "instrumental"],
    "production": ["polished studio mix", "lo-fi", "live recording", "vintage", "heavy reverb", "minimal",
                   "wall of sound", "punchy mix"],
}
_TEMPLATES = {
    "genre": "This is a {} song.",
    "mood": "This song sounds {}.",
    "instrument": "This song features {}.",
    "vocal": "This song has {}.",
    "production": "This song has a {} sound.",
}
_TOP = {"genre": 3, "mood": 3, "instrument": 5, "vocal": 2}
# Production words come from measurements: the tagger called almost everything "lo-fi".
# "duet" is left to the user for the same reason.
TAG_GROUPS = tuple(_TOP)
UNTAGGED = {"duet"}
OPPOSITES = [{"happy", "sad"}, {"happy", "melancholic"}, {"calm", "aggressive"}, {"calm", "energetic"},
             {"uplifting", "dark"}, {"uplifting", "sad"}, {"playful", "dark"}, {"triumphant", "sad"}]

NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

# Stem → style words when that stem carries real energy in the mix.
STEM_WORDS = {"drums": "drums", "bass": "bass guitar", "guitar": "electric guitar", "piano": "piano"}
STEM_FAMILIES = {"drums": ("drums", "drum machine"), "bass": ("bass guitar", "synth bass", "808"),
                 "guitar": ("acoustic guitar", "electric guitar", "distorted guitar"),
                 "piano": ("piano", "electric piano")}
STEM_PRESENT_DB = -26.0


# ── Signal analysis ─────────────────────────────────────────────────────────

def _mono(audio: np.ndarray, rate: int, target: int) -> np.ndarray:
    import librosa

    y = audio.mean(axis=1) if audio.ndim == 2 else audio
    y = np.asarray(y, dtype=np.float32)
    return librosa.resample(y, orig_sr=rate, target_sr=target) if rate != target else y


def tempo(y: np.ndarray, sr: int) -> dict:
    import librosa

    env = librosa.onset.onset_strength(y=y, sr=sr)
    bpm, beats = librosa.beat.beat_track(onset_envelope=env, sr=sr)
    bpm = float(np.atleast_1d(bpm)[0])
    if bpm <= 0 or len(beats) < 4:
        return {"bpm": None, "confidence": 0.0, "alternatives": [], "beats": []}
    times = librosa.frames_to_time(beats, sr=sr)
    ibi = np.diff(times)
    # Steady beat intervals → confident tempo.
    steadiness = float(np.clip(1.0 - np.std(ibi) / max(np.mean(ibi), 1e-6) * 4, 0, 1))
    alts = sorted({round(bpm / 2), round(bpm * 2)} - {round(bpm)})
    alts = [a for a in alts if 40 <= a <= 240]
    return {"bpm": int(round(bpm)), "confidence": round(steadiness, 2), "alternatives": alts,
            "beats": times.tolist(), "_env": env, "_beat_frames": beats}


def key(y: np.ndarray, sr: int) -> dict:
    import librosa

    harmonic = librosa.effects.harmonic(y)
    chroma = librosa.feature.chroma_cqt(y=harmonic, sr=sr).mean(axis=1)
    if not np.isfinite(chroma).all() or chroma.sum() <= 0:
        return {"key": None, "confidence": 0.0, "alternatives": []}
    scores = []
    for i in range(12):
        for mode, profile in (("major", _MAJOR), ("minor", _MINOR)):
            scores.append((float(np.corrcoef(np.roll(profile, i), chroma)[0, 1]), f"{NOTES[i]} {mode}"))
    scores.sort(reverse=True)
    best, second = scores[0][0], scores[1][0]
    conf = float(np.clip((best - second) * 6 + max(best, 0) * 0.5, 0, 1))
    return {"key": scores[0][1], "confidence": round(conf, 2), "correlation": round(best, 3),
            "alternatives": [s[1] for s in scores[1:3]]}


def metre(env: np.ndarray, beat_frames: np.ndarray) -> dict:
    """Beats per bar from how strongly accents repeat every 3 or 4 beats. A rough guess."""
    if beat_frames is None or len(beat_frames) < 16:
        return {"beats_per_bar": 4, "label": "4/4", "confidence": 0.0}
    strength = env[np.clip(beat_frames, 0, len(env) - 1)]
    strength = (strength - strength.mean()) / (strength.std() + 1e-6)

    def ac(lag):
        return float(np.mean(strength[:-lag] * strength[lag:]))
    three, four = ac(3), ac(4)
    if three > four + 0.15:
        return {"beats_per_bar": 3, "label": "3/4", "confidence": round(float(np.clip(three - four, 0, 1)), 2)}
    return {"beats_per_bar": 4, "label": "4/4", "confidence": round(float(np.clip(four - three + 0.3, 0, 1)), 2)}


def character(y: np.ndarray, sr: int) -> dict:
    import librosa

    rms = librosa.feature.rms(y=y)[0]
    db = 20 * np.log10(np.maximum(rms, 1e-6))
    loud = db[db > db.max() - 40] if db.size else db
    centroid = float(np.median(librosa.feature.spectral_centroid(y=y, sr=sr)[0]))
    dynamics = float(np.percentile(loud, 95) - np.percentile(loud, 10)) if loud.size else 0.0
    return {"loudness_db": round(float(np.mean(loud)) if loud.size else -90.0, 1),
            "brightness_hz": int(centroid), "dynamic_range_db": round(dynamics, 1),
            "bright": centroid > 2600, "dark": centroid < 1400, "compressed": dynamics < 8}


def production_words(c: dict) -> list[str]:
    words = []
    if c.get("compressed") and (c.get("loudness_db") or -90) > -15:
        words.append("punchy mix")
    elif (c.get("dynamic_range_db") or 0) > 15:
        words.append("dynamic mix")
    if c.get("bright"):
        words.append("bright mix")
    elif c.get("dark"):
        words.append("warm mix")
    return words[:2]


def feel(bpm: Optional[int]) -> Optional[str]:
    if not bpm:
        return None
    return "slow" if bpm < 80 else "mid-tempo" if bpm < 112 else "upbeat" if bpm < 140 else "fast"


# ── Stems ───────────────────────────────────────────────────────────────────

def stem_presence(mix: np.ndarray, stems: dict[str, np.ndarray]) -> dict[str, float]:
    """Each stem's level relative to the full mix, in dB."""
    def level(a):
        return 10 * np.log10(float(np.mean(np.square(a))) + 1e-12)
    ref = level(mix)
    return {name: round(level(a) - ref, 1) for name, a in stems.items()}


# ── Zero-shot tags (CLAP) ───────────────────────────────────────────────────

class _Clap:
    def __init__(self):
        self._lock = threading.Lock()
        self.model = None
        self.processor = None
        self.text = None
        self.error: Optional[str] = None

    def available(self) -> bool:
        try:
            from huggingface_hub import try_to_load_from_cache
            return isinstance(try_to_load_from_cache(CLAP_REPO, "config.json"), str) and \
                isinstance(try_to_load_from_cache(CLAP_REPO, "pytorch_model.bin"), str)
        except Exception:
            return False

    def load(self):
        with self._lock:
            if self.model is None:
                import torch
                from transformers import ClapModel, ClapProcessor

                self.processor = ClapProcessor.from_pretrained(CLAP_REPO, local_files_only=True)
                self.model = ClapModel.from_pretrained(CLAP_REPO, local_files_only=True).eval()
                prompts, index = [], []
                for group in TAG_GROUPS:
                    for label in STYLE_VOCAB[group]:
                        if label in UNTAGGED:
                            continue
                        prompts.append(_TEMPLATES[group].format(label))
                        index.append((group, label))
                with torch.no_grad():
                    t = self.processor(text=prompts, return_tensors="pt", padding=True)
                    emb = self.model.get_text_features(**t)
                    emb = getattr(emb, "pooler_output", emb)
                self.text = (torch.nn.functional.normalize(emb, dim=-1), index)
        return self

    def tags(self, y48: np.ndarray) -> dict[str, list[dict]]:
        import torch

        self.load()
        win = int(CLAP_WINDOW_S * CLAP_RATE)
        if len(y48) <= win:
            windows = [y48]
        else:
            starts = np.linspace(0, len(y48) - win, min(MAX_WINDOWS, max(1, len(y48) // win))).astype(int)
            windows = [y48[s:s + win] for s in starts]
        with torch.no_grad():
            a = self.processor(audio=windows, sampling_rate=CLAP_RATE, return_tensors="pt")
            emb = self.model.get_audio_features(**a)
            emb = getattr(emb, "pooler_output", emb)
            emb = torch.nn.functional.normalize(emb, dim=-1).mean(dim=0, keepdim=True)
            emb = torch.nn.functional.normalize(emb, dim=-1)
        text, index = self.text
        sims = (emb @ text.T)[0].numpy()
        out: dict[str, list[dict]] = {}
        for group in TAG_GROUPS:
            ids = [i for i, (g, _) in enumerate(index) if g == group]
            s = sims[ids]
            p = np.exp((s - s.max()) * 40.0)
            p = p / p.sum()
            order = np.argsort(-p)
            picked = []
            for j in order[:_TOP[group]]:
                if picked and p[j] < 0.45 * p[order[0]]:
                    break
                picked.append({"label": index[ids[j]][1], "score": round(float(p[j]), 3)})
            out[group] = picked
        return out


CLAP = _Clap()


# ── Whole analysis ──────────────────────────────────────────────────────────

def analyze(audio: np.ndarray, rate: int, stems: Optional[dict[str, np.ndarray]] = None,
            use_tags: bool = True) -> dict:
    y = _mono(audio, rate, ANALYSIS_RATE)
    if len(y) < ANALYSIS_RATE * 3:
        raise ValueError("The audio is too short to analyse (needs at least 3 seconds).")
    t = tempo(y, ANALYSIS_RATE)
    m = metre(t.pop("_env", None), t.pop("_beat_frames", None)) if t.get("bpm") else \
        {"beats_per_bar": 4, "label": "4/4", "confidence": 0.0}
    t.pop("_env", None), t.pop("_beat_frames", None)
    t["beats"] = [round(b, 3) for b in t.get("beats", [])[:2000]]
    result = {"tempo": t, "key": key(y, ANALYSIS_RATE), "metre": m, "character": character(y, ANALYSIS_RATE),
              "feel": feel(t.get("bpm")), "duration": round(len(y) / ANALYSIS_RATE, 2),
              "tags": {}, "tagger": None, "stems": None}
    if stems:
        result["stems"] = stem_presence(audio, stems)
    if use_tags:
        if CLAP.available():
            try:
                result["tags"] = CLAP.tags(_mono(audio, rate, CLAP_RATE))
                result["tagger"] = CLAP_REPO
            except Exception as exc:  # tags are a bonus; the measured values still count
                log.warning("CLAP tagging failed: %s", exc)
                result["tagger_error"] = f"{exc.__class__.__name__}: {exc}"[:300]
        else:
            result["tagger_error"] = f"Style tags need the {CLAP_REPO} model in the Hugging Face cache."
    result["suggestion"] = suggest(result)
    return result


def suggest(a: dict) -> dict:
    """Turn an analysis into prompt fields: a style line plus ACE-Step metadata."""
    tags = a.get("tags") or {}
    pick = lambda g, n: [t["label"] for t in tags.get(g, [])[:n]]
    genres = pick("genre", 2)
    moods = []
    for m in pick("mood", 3):
        if len(moods) < 2 and not any({m, o} in OPPOSITES for o in moods):
            moods.append(m)
    instruments = pick("instrument", 4)
    # The tagger hears "instrumental" in sung songs too, even with high scores (0.8 on a clearly sung
    # ACE-Step song, 2026-11-05). Only a measured silent vocal stem may call a song instrumental.
    vocal_tags = [t for t in tags.get("vocal", []) if t["label"] != "instrumental"]
    vocals = [t["label"] for t in vocal_tags[:1]]
    stems = a.get("stems") or {}
    if stems:
        present = {s for s, db in stems.items() if db >= STEM_PRESENT_DB}
        # Stems are measured, so they win over the tagger for the families they cover.
        absent = {s for s in STEM_FAMILIES if s in stems and s not in present}
        instruments = [i for i in instruments if not any(i in STEM_FAMILIES[s] for s in absent)]
        for s, words in STEM_FAMILIES.items():
            if s in present and not any(i in words for i in instruments):
                instruments.append(STEM_WORDS[s])
        instruments = instruments[:5]
        if "vocals" in stems:
            if stems["vocals"] < STEM_PRESENT_DB:
                vocals = ["instrumental"]
            elif vocals == ["instrumental"]:
                vocals = []
    c = a.get("character") or {}
    production = production_words(c)
    feel_word = a.get("feel")
    parts = genres + instruments + vocals + moods + ([feel_word] if feel_word else []) + production
    style = ", ".join(dict.fromkeys(p for p in parts if p))
    k, t, m = a.get("key") or {}, a.get("tempo") or {}, a.get("metre") or {}
    return {"style": style, "bpm": t.get("bpm"), "key": k.get("key"),
            "time_signature": str(m.get("beats_per_bar") or 4), "instrumental": vocals == ["instrumental"]}
