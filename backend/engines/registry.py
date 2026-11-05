"""
engines/registry.py — Finds speech models in the shared models folders.

Discovery only reads config.json files and file sizes. It never opens checkpoints.
A model's state moves through the ModelState values. Anything that is not integrated
or fails its runtime check is reported as BLOCKED, with the reason.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from engines.base import ModelInfo, ModelState, TTSEngine

log = logging.getLogger(__name__)

MAX_DEPTH = 5
SKIP_DIRS = {".cache", ".no_exist", "blobs", "refs", "__pycache__", ".git"}


def _candidate_dirs(root: Path, max_depth: int = MAX_DEPTH) -> Iterable[Path]:
    """Yield folders under root that contain a config.json, without descending into skipped names."""
    root_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        depth = len(here.parts) - root_depth
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and depth < max_depth]
        if "config.json" in filenames:
            yield here


class ModelRegistry:
    """
    Holds the discovered models. Call discover() to scan, get() to look one up.

    verification_file is a JSON map of model id -> {"status": "passed", ...}, written by
    the GPU smoke test. A passing record upgrades an integrated model to TESTED. New records
    may include model_path to bind the result to one physical model directory.
    """

    def __init__(self, engines: list[TTSEngine], roots: list[Path], verification_file: Optional[Path] = None):
        self._engines = engines
        self._roots = [Path(r) for r in roots]
        self._verification_file = verification_file
        self._models: dict[str, ModelInfo] = {}
        self.last_scan: Optional[str] = None

    @property
    def roots(self) -> list[Path]:
        return list(self._roots)

    def set_roots(self, roots: list[Path]) -> None:
        self._roots = [Path(r) for r in roots]

    def discover(self) -> list[ModelInfo]:
        verified = self._load_verifications()
        found: dict[str, ModelInfo] = {}
        seen_paths: set[Path] = set()
        for root in self._roots:
            if not root.is_dir():
                log.warning("Models root not found, skipping: %s", root)
                continue
            for model_dir in _candidate_dirs(root):
                resolved = model_dir.resolve()
                if resolved in seen_paths:
                    continue
                seen_paths.add(resolved)
                info = self._identify(model_dir, root)
                if info is None:
                    continue
                if info.id in found:
                    # Two distinct roots may both contain maya1/model, for example.
                    # Preserve existing IDs and disambiguate only actual collisions.
                    from hashlib import sha256

                    info.id += ":" + sha256(str(resolved).encode()).hexdigest()[:10]
                verification = verified.get(info.id, {})
                if info.state == ModelState.INTEGRATED and self._verification_matches(info, verification):
                    info.state = ModelState.TESTED
                found[info.id] = info
            # Single-file packages (GGUF) have no config.json; engines that support them scan here.
            for engine in self._engines:
                finder = getattr(engine, "identify_files", None)
                if finder is None:
                    continue
                for info in finder(root):
                    resolved = Path(info.path).resolve()
                    if resolved in seen_paths:
                        continue
                    seen_paths.add(resolved)
                    info.id = f"{engine.engine_id}:{Path(info.path).relative_to(root).as_posix()}"
                    if info.id in found:
                        from hashlib import sha256

                        info.id += ":" + sha256(str(resolved).encode()).hexdigest()[:10]
                    self._finish(engine, info)
                    if info.state == ModelState.INTEGRATED and self._verification_matches(info, verified.get(info.id, {})):
                        info.state = ModelState.TESTED
                    found[info.id] = info
        # Cloud services appear as models without files; their state comes from the engine (e.g. API key set).
        for engine in self._engines:
            virtual = getattr(engine, "virtual_models", None)
            if virtual is not None:
                for info in virtual():
                    found[info.id] = info
        self._models = found
        self.last_scan = datetime.now(timezone.utc).isoformat()
        return list(self._models.values())

    def models(self) -> list[ModelInfo]:
        return list(self._models.values())

    def get(self, model_id: str) -> Optional[ModelInfo]:
        return self._models.get(model_id)

    def _identify(self, model_dir: Path, root: Path) -> Optional[ModelInfo]:
        for engine in self._engines:
            info = engine.identify(model_dir)
            if info is None:
                continue
            rel = model_dir.relative_to(root).as_posix()
            info.id = f"{engine.engine_id}:{rel}"
            if any(part.startswith("models--") for part in model_dir.relative_to(root).parts):
                note = "Stored in the Hugging Face cache."
                info.notes = f"{info.notes} {note}".strip()
            return self._finish(engine, info)
        return None

    @staticmethod
    def _finish(engine: TTSEngine, info: ModelInfo) -> ModelInfo:
        if info.state == ModelState.BLOCKED:
            return info
        problem = engine.runtime_problem()
        if problem:
            info.state = ModelState.BLOCKED
            info.reason = problem
            return info
        info.state = ModelState.INTEGRATED
        return info

    def _load_verifications(self) -> dict:
        if not self._verification_file or not self._verification_file.is_file():
            return {}
        try:
            return json.loads(self._verification_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("Ignoring unreadable verification file: %s", self._verification_file)
            return {}

    @staticmethod
    def _verification_matches(info: ModelInfo, record: dict) -> bool:
        if not isinstance(record, dict) or record.get("status") != "passed":
            return False
        recorded_path = record.get("model_path")
        if recorded_path is None:
            return True  # Legacy verification records were keyed only by model id.
        if not isinstance(recorded_path, str) or not recorded_path.strip():
            return False
        try:
            return Path(recorded_path).expanduser().resolve() == Path(info.path).resolve()
        except (OSError, RuntimeError, ValueError):
            return False

