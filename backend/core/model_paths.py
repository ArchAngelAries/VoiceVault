"""Read-only, bounded model discovery. Never scans drives or manages other apps."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Mapping

from core import paths as app_paths
from core.paths import VOICEVAULT_ROOT

log = logging.getLogger(__name__)
COMFY_NAMES = ("ComfyUI", "comfyui", "comfyui-rocm")
FORGE_NAMES = ("stable-diffusion-webui-forge", "webui-forge", "forge")
# Categories whose parent is conventionally a shared model root.
MODEL_CATEGORIES = {"checkpoints", "stable-diffusion", "diffusers", "loras", "lora",
                    "lycoris", "vae", "clip", "text_encoder", "text_encoders",
                    "diffusionmodels", "diffusion_models", "unet", "tts", "vibevoice"}


def _paths(value) -> list[Path]:
    values = [value] if isinstance(value, (str, Path)) else value if isinstance(value, (list, tuple)) else []
    return [Path(os.path.expandvars(str(v).strip())).expanduser() for v in values
            if isinstance(v, (str, Path)) and str(v).strip()]


def _unique(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result = []
    for path in paths:
        absolute = path.resolve()
        if absolute == Path(absolute.anchor):
            log.warning("Ignoring filesystem root as a model search folder: %s", absolute)
            continue
        key = os.path.normcase(str(absolute))
        if key not in seen:
            seen.add(key)
            result.append(absolute)
    return result


def huggingface_cache() -> Path:
    # Library constants provide the standard default and deprecated env fallback.
    from huggingface_hub.constants import HF_HUB_CACHE

    for key, suffix in (("HF_HUB_CACHE", ()), ("HUGGINGFACE_HUB_CACHE", ()),
                        ("HF_HOME", ("hub",)), ("XDG_CACHE_HOME", ("huggingface", "hub"))):
        selected = _paths(os.environ.get(key))
        if selected:
            return selected[0].joinpath(*suffix)
    # If the library imported while an env override was blank, its cached constant
    # may itself contain whitespace. Treat those overrides as absent.
    if any(key in os.environ and not os.environ[key].strip()
           for key in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "HF_HOME", "XDG_CACHE_HOME")):
        return Path.home() / ".cache" / "huggingface" / "hub"
    return Path(HF_HUB_CACHE).expanduser()


def _comfy_roots(installation: Path) -> list[Path]:
    roots = [installation / "models"]
    config = installation / "extra_model_paths.yaml"
    if not config.is_file():
        return roots
    try:
        import yaml

        # Safe loader constructs only data. Configs are not trusted Python code.
        data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError("expected a mapping")
        for group in data.values():
            if not isinstance(group, dict):
                continue
            base_value = group.get("base_path")
            base = _paths(base_value)[0] if _paths(base_value) else installation
            if not base.is_absolute():
                base = installation / base
            for key, value in group.items():
                if key in {"base_path", "is_default", "custom_nodes", "datasets"} or not isinstance(value, str):
                    continue
                for line in value.splitlines():
                    if not line.strip():
                        continue
                    path = _paths(line.strip())[0]
                    if not path.is_absolute():
                        path = base / path
                    # Existing shared model configs often list category folders only.
                    # Use their immediate model parent, not a drive-wide search.
                    if path.name.lower() in MODEL_CATEGORIES and len(path.parent.parts) > 1:
                        roots.append(path.parent)
                    roots.append(path)
    except (OSError, ValueError, ImportError) as exc:
        log.warning("Could not read ComfyUI model paths at %s: %s", config, exc)
    except Exception as exc:  # includes safe-loader YAML errors
        log.warning("Invalid ComfyUI model path config at %s: %s", config, exc)
    return roots


def model_roots(cfg: Mapping, *, project_root: Path = VOICEVAULT_ROOT,
                home: Path | None = None) -> list[Path]:
    """Combine user settings, env and a few named siblings; no recursive app search."""
    roots = _paths(cfg.get("shared_models_dir")) + _paths(cfg.get("model_search_roots"))
    # Models downloaded by VoiceVault itself (Settings → Models).
    owned = Path(os.environ.get("VOICEVAULT_MODELS_DIR") or (project_root / "Models"))
    if owned.is_dir():
        roots.append(owned)
    roots += _paths(cfg.get("models_dir"))  # configured trained-model library
    # VoiceVault-owned checkpoints become visible only after explicit atomic publication.
    # Never scan training/jobs, where active and failed checkpoints may be incomplete.
    training_root = app_paths.DATA_DIR / "training"
    published_root = training_root / "published_models"
    if (not training_root.is_symlink() and published_root.is_dir()
            and not published_root.is_symlink()):
        roots.append(published_root)
    roots += _paths(os.environ.get("VOICEVAULT_MODEL_ROOTS", "").split(os.pathsep))
    # Put parent shared folders ahead of individual model categories to keep IDs stable.
    roots.append(huggingface_cache())
    candidate_parents = (project_root.parent, home or Path.home())
    for field, env, names in (("comfyui_dirs", "VOICEVAULT_COMFYUI_DIRS", COMFY_NAMES),
                              ("forge_dirs", "VOICEVAULT_FORGE_DIRS", FORGE_NAMES)):
        apps = _paths(cfg.get(field)) + _paths(os.environ.get(env, "").split(os.pathsep))
        apps += [parent / name for parent in candidate_parents for name in names if (parent / name).is_dir()]
        for app in _unique(apps):
            roots += _comfy_roots(app) if field == "comfyui_dirs" else [app / "models"]
    # Keep explicitly selected nested roots: they may be deeper than the scanner's
    # depth limit. The registry deduplicates actual model folders across roots.
    return _unique(roots)
