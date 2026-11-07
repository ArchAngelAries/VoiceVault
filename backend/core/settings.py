"""
core/settings.py — VoiceVault settings manager
Reads and writes settings to a local JSON file.
All paths and API keys live here.
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any
from core.paths import DEFAULT_SETTINGS_PATH

# Settings file lives in the backend folder: backend/voicevault_settings.json
SETTINGS_PATH = (Path(os.environ["VOICEVAULT_SETTINGS_PATH"]).expanduser()
                 if os.environ.get("VOICEVAULT_SETTINGS_PATH")
                 else DEFAULT_SETTINGS_PATH)

DEFAULTS: dict[str, Any] = {
    # ── Paths ────────────────────────────────────────────────
    "clips_dir":        "",   # root folder for all downloaded clips
    "models_dir":       "",   # root folder for trained voice models
    "outputs_dir":      "",   # root folder for generated audio
    "temp_dir":         "",   # temp folder for in-progress downloads

    # ── Clip organization ────────────────────────────────────
    "clip_folder_structure": "{media_title}/{character}/{clip_type}",

    # ── Harvest defaults ─────────────────────────────────────
    "harvest_max_duration":   1200,   # seconds (20 min)
    "harvest_download_workers": 3,
    "harvest_split_workers":    6,
    "harvest_min_silence_ms":   600,
    "harvest_silence_thresh":   -40,
    "harvest_min_clip_ms":      1500,
    "harvest_max_clip_ms":      30000,

    # ── Media API keys ───────────────────────────────────────
    "tmdb_api_key":     "",
    "igdb_client_id":   "",
    "igdb_client_secret": "",
    "google_client_id": "",

    # ── LLM backends ─────────────────────────────────────────
    "llm_default_backend": "lmstudio",
    "lmstudio_url":     "http://localhost:1234",
    "ollama_url":       "http://localhost:11434",
    "kobold_url":       "http://localhost:5001",
    "openai_api_key":   "",
    "anthropic_api_key": "",
    "gemini_api_key":   "",
    "chat_context_tokens": 8192,   # context size of the chat model; chat memory fits each prompt into it

    # ── TTS engines ──────────────────────────────────────────
    "shared_models_dir": "",  # legacy custom root; stored user choices remain unchanged
    "model_search_roots": [],  # additional folders; models are read in place
    "comfyui_dirs": [],       # installation folders (including extra_model_paths.yaml)
    "forge_dirs": [],         # installation folders
    "vibevoice_tokenizer_dir": "",
    "omnivoice_url":    "http://localhost:8001",
    "tts_default_engine": "omnivoice",
    "audiocpp_dir":     "",   # folder with audiocpp_server.exe; empty uses runtime/audiocpp/<version>
    "drama3_backend":   "auto",  # auto (Vulkan when the build has it), vulkan or cpu
    "drama3_cpu_expert_layers": -1,  # expert layers kept in system RAM; -1 = auto from free VRAM, 0 = all on the GPU
    "drama3_context_length": 4096,  # 60 s reference (~1,300 frames) + text + 1,024 generated frames per chunk
    "drama3_threads":   0,    # 0 = half the logical CPUs
    "fish_audio_api_key": "",
    "huggingface_token": "",  # optional read token for gated model downloads
    "kokoro_backend":   "auto",   # auto (Vulkan when the build has it, else CPU), vulkan or cpu  # optional Fish Audio cloud API (paid); read at request time

    # ── UI preferences ───────────────────────────────────────
    "theme":            "dark",
    "language":         "en",
    "experimental_features": False,  # shows Train in the sidebar
    "update_check":     True,   # once a day, ask GitHub for the newest release (nothing about the user is sent)
}


# Secrets never leave the backend: the API sends MASK in their place and keeps the stored
# value when MASK comes back unchanged.
SECRET_KEYS = ("tmdb_api_key", "igdb_client_secret", "openai_api_key", "anthropic_api_key",
               "gemini_api_key", "fish_audio_api_key", "huggingface_token")
MASK = "__saved_secret__"
PATH_KEYS = ("clips_dir", "models_dir", "outputs_dir", "temp_dir", "shared_models_dir",
             "vibevoice_tokenizer_dir", "audiocpp_dir")
LAST_RESET: dict[str, str] = {}   # set when an unreadable settings file was moved aside


def public(settings: dict) -> dict:
    """Settings as the UI may see them: secrets masked, plus which ones are set."""
    out = {k: (MASK if k in SECRET_KEYS and v else v) for k, v in settings.items()}
    if LAST_RESET:
        out["_notice"] = (f"Your settings file could not be read and was set aside as {LAST_RESET['file']}. "
                          "Defaults are in use; re-enter your keys or restore that file.")
    return out


def validate_update(data: dict, current: dict) -> dict:
    """Check a settings update from the UI and resolve masked secrets. Raises ValueError with a plain reason."""
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key.startswith("_"):
            continue  # UI-only fields such as _notice
        if key not in DEFAULTS:
            raise ValueError(f"Unknown setting '{key}'.")
        default = DEFAULTS[key]
        if key in SECRET_KEYS and value == MASK:
            out[key] = current.get(key, "")
            continue
        if isinstance(default, bool):
            ok = isinstance(value, bool)
        elif isinstance(default, int):
            ok = isinstance(value, int) and not isinstance(value, bool)
        elif isinstance(default, list):
            ok = isinstance(value, list) and all(isinstance(v, str) for v in value)
        else:
            ok = isinstance(value, str)
        if not ok:
            raise ValueError(f"Setting '{key}' must be a {type(default).__name__}.")
        if isinstance(value, str) and len(value) > 4000:
            raise ValueError(f"Setting '{key}' is too long.")
        if key in PATH_KEYS and value.strip() and not Path(value.strip()).expanduser().is_absolute():
            raise ValueError(f"Setting '{key}' must be a full folder path (or empty).")
        if key in ("model_search_roots", "comfyui_dirs", "forge_dirs") and any(
                v.strip() and not Path(v.strip()).expanduser().is_absolute() for v in value):
            raise ValueError(f"Every folder in '{key}' must be a full path.")
        out[key] = value
    return out


def load() -> dict:
    if not SETTINGS_PATH.exists():
        save(DEFAULTS.copy())
        return DEFAULTS.copy()
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            stored = json.load(f)
        if not isinstance(stored, dict):
            raise ValueError("settings file must contain a JSON object")
        # Merge with defaults so new keys always exist
        return {**DEFAULTS, **stored}
    except (OSError, ValueError) as e:
        # Move the unreadable file aside rather than returning defaults and letting the
        # next save() overwrite it, which would silently discard the stored API keys.
        backup = SETTINGS_PATH.with_name(f"{SETTINGS_PATH.stem}.corrupt-{int(time.time())}.json")
        try:
            SETTINGS_PATH.replace(backup)
            LAST_RESET["file"] = backup.name
            logging.error(f"Settings file unreadable ({e}); moved to {backup.name}, using defaults")
        except OSError as move_err:
            logging.error(f"Settings file unreadable ({e}) and could not be moved aside: {move_err}")
        return DEFAULTS.copy()


def save(data: dict) -> dict:
    merged = {**DEFAULTS, **data}
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Write a temp file and swap it in, so a crash mid-write cannot leave a truncated settings file.
    tmp = SETTINGS_PATH.with_name(SETTINGS_PATH.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, SETTINGS_PATH)
    return merged


def get(key: str, fallback: Any = None) -> Any:
    return load().get(key, fallback)
