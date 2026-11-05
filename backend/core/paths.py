"""
core/paths.py — Where VoiceVault keeps its own files.

Shared model weights are never copied here. They are referenced in place from the
shared models folder (see the shared_models_dir setting).
"""
import os
from pathlib import Path

VOICEVAULT_ROOT = Path(__file__).resolve().parents[2]
# An explicit state root is opt-in. Keep import side-effect free: callers such as
# startup own directory creation before opening the selected database.
_state_value = os.environ.get("VOICEVAULT_STATE_DIR", "").strip()
if _state_value:
    _state_candidate = Path(_state_value)
    if not _state_candidate.is_absolute():
        raise ValueError("VOICEVAULT_STATE_DIR must be an absolute path")
    STATE_DIR: Path | None = _state_candidate.expanduser()
    if not STATE_DIR.is_absolute():
        raise ValueError("VOICEVAULT_STATE_DIR must be an absolute path")
else:
    STATE_DIR = None

# VOICEVAULT_DATA_DIR moves outputs, references and the verification record elsewhere. Browser checks
# of destructive features use it, so the real library is never touched.
_data_override = os.environ.get("VOICEVAULT_DATA_DIR")
DATA_DIR = (Path(_data_override) if _data_override else
            STATE_DIR / "data" if STATE_DIR is not None else VOICEVAULT_ROOT / "data")
# Same file the Prisma schema points at (schema says file:./voicevault.db, relative to backend/db)
DEFAULT_DB_PATH = (STATE_DIR / "library.db" if STATE_DIR is not None else
                   VOICEVAULT_ROOT / "backend" / "db" / "voicevault.db")
DEFAULT_SETTINGS_PATH = (STATE_DIR / "settings.json" if STATE_DIR is not None else
                         VOICEVAULT_ROOT / "backend" / "voicevault_settings.json")
DEFAULT_OUTPUTS_DIR = DATA_DIR / "outputs"
VERIFICATION_FILE = DATA_DIR / "tts_verification.json"


def database_path() -> Path:
    """Return the library database path, validating the simple local file URL override."""
    url = os.environ.get("VOICEVAULT_DB_URL")
    if url is None or url == "":
        return DEFAULT_DB_PATH
    if not url.startswith("file:"):
        raise ValueError("VOICEVAULT_DB_URL must use file:<absolute local path>")
    raw_path = url[len("file:"):]
    if (not raw_path or raw_path.startswith("//") or
            any(char in raw_path for char in ("%", "?", "#"))):
        raise ValueError("VOICEVAULT_DB_URL must use an unencoded file:<absolute local path>")
    # Reject URI forms such as file:///C:/...; accept native absolute drive and UNC paths.
    path = Path(raw_path)
    if not path.is_absolute():
        raise ValueError("VOICEVAULT_DB_URL must use file:<absolute local path>")
    return path


def database_url() -> str:
    """Prisma URL for the exact database_path() used by startup migrations."""
    return f"file:{database_path().as_posix()}"


def runtime_dir() -> Path:
    """Runtimes the user installs from inside the app (eSpeak NG). The installed desktop app sets
    VOICEVAULT_RUNTIME_DIR to its writable state folder, because the program folder is replaced on update.
    Bundled runtimes (audio.cpp) stay under VOICEVAULT_ROOT/runtime."""
    override = os.environ.get("VOICEVAULT_RUNTIME_DIR", "").strip()
    return Path(override) if override else VOICEVAULT_ROOT / "runtime"


def backup_dir() -> Path:
    """Database backups made before migrations. VOICEVAULT_BACKUP_DIR overrides; tests use it."""
    override = os.environ.get("VOICEVAULT_BACKUP_DIR")
    return Path(override) if override else DATA_DIR / "backups"
