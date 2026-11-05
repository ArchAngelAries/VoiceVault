"""User-installed runtimes (eSpeak NG) follow VOICEVAULT_RUNTIME_DIR in the installed desktop app."""
from pathlib import Path

from core import paths


def test_runtime_dir_defaults_to_project_runtime(monkeypatch):
    monkeypatch.delenv("VOICEVAULT_RUNTIME_DIR", raising=False)
    assert paths.runtime_dir() == paths.VOICEVAULT_ROOT / "runtime"


def test_runtime_dir_override_moves_espeak_lookup(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICEVAULT_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("AUDIOCPP_ESPEAK_LIBRARY", raising=False)
    for base in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
        monkeypatch.delenv(base, raising=False)
    assert paths.runtime_dir() == tmp_path
    folder = tmp_path / "espeak-ng" / "1.52.0" / "eSpeak NG"
    (folder / "espeak-ng-data").mkdir(parents=True)
    (folder / "libespeak-ng.dll").write_bytes(b"")
    (folder / "espeak-ng-data" / "phontab").write_bytes(b"")
    from engines.kokoro import find_espeak
    assert find_espeak() == (folder / "libespeak-ng.dll", folder / "espeak-ng-data")
