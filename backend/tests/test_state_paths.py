"""Opt-in VOICEVAULT_STATE_DIR path contract, isolated from imported app modules."""
import json
import os
from pathlib import Path
import subprocess
import sys
import importlib
import types
import pytest


BACKEND = Path(__file__).resolve().parents[1]
_PATH_ENV = ("VOICEVAULT_STATE_DIR", "VOICEVAULT_DATA_DIR", "VOICEVAULT_DB_URL",
            "VOICEVAULT_SETTINGS_PATH", "VOICEVAULT_BACKUP_DIR")


def _run_python(tmp_path, source, **variables):
    env = os.environ.copy()
    for key in _PATH_ENV:
        env.pop(key, None)
    for key, value in variables.items():
        env[key] = str(value)
    env["PYTHONPATH"] = str(BACKEND)
    return subprocess.run([sys.executable, "-c", source], cwd=BACKEND, env=env,
                          text=True, capture_output=True, check=False, timeout=15)


def _run_immediate(coro):
    """Run a coroutine whose fake adapter awaits only immediate async methods."""
    try:
        coro.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("fake Prisma adapter unexpectedly yielded control")


def test_defaults_keep_legacy_paths_when_state_dir_is_unset(tmp_path):
    result = _run_python(tmp_path, """
import json
from core import paths
from core import settings
print(json.dumps([str(paths.STATE_DIR), str(paths.DATA_DIR), str(paths.DEFAULT_DB_PATH),
                  str(paths.DEFAULT_SETTINGS_PATH), str(settings.SETTINGS_PATH),
                  str(paths.backup_dir())]))
""")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        "None", str(BACKEND.parent / "data"), str(BACKEND / "db" / "voicevault.db"),
        str(BACKEND / "voicevault_settings.json"), str(BACKEND / "voicevault_settings.json"),
        str(BACKEND.parent / "data" / "backups"),
    ]


def test_state_dir_layout_and_per_file_overrides(tmp_path):
    state = tmp_path / "state"
    data, db, settings, backup = (tmp_path / name for name in ("custom-data", "custom.db", "custom.json", "custom-backups"))
    result = _run_python(tmp_path, """
import json
from core import paths, settings as settings_module
print(json.dumps([str(paths.STATE_DIR), str(paths.DATA_DIR), str(paths.database_path()),
                  str(settings_module.SETTINGS_PATH), str(paths.backup_dir())]))
""", VOICEVAULT_STATE_DIR=state, VOICEVAULT_DATA_DIR=data,
        VOICEVAULT_DB_URL=f"file:{db}", VOICEVAULT_SETTINGS_PATH=settings,
        VOICEVAULT_BACKUP_DIR=backup)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == list(map(str, (state, data, db, settings, backup)))


def test_state_dir_uses_documented_defaults_without_overrides(tmp_path):
    state = tmp_path / "state"
    result = _run_python(tmp_path, """
import json
from core import paths, settings
print(json.dumps([str(paths.DATA_DIR), str(paths.database_path()),
                  str(settings.SETTINGS_PATH), str(paths.backup_dir())]))
""", VOICEVAULT_STATE_DIR=state)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == list(map(str, (state / "data", state / "library.db",
                                                       state / "settings.json", state / "data" / "backups")))
    assert not state.exists()


def test_relative_state_dir_is_rejected_even_with_file_overrides(tmp_path):
    result = _run_python(tmp_path, "from core import paths", VOICEVAULT_STATE_DIR="relative/state",
                         VOICEVAULT_DATA_DIR=tmp_path / "data",
                         VOICEVAULT_DB_URL=f"file:{tmp_path / 'db.sqlite'}",
                         VOICEVAULT_SETTINGS_PATH=tmp_path / "settings.json")
    assert result.returncode != 0
    assert "VOICEVAULT_STATE_DIR must be an absolute path" in result.stderr


def test_tilde_state_dir_is_rejected_as_non_absolute(tmp_path):
    result = _run_python(tmp_path, "from core import paths", VOICEVAULT_STATE_DIR="~/voicevault")
    assert result.returncode != 0
    assert "VOICEVAULT_STATE_DIR must be an absolute path" in result.stderr


def test_database_url_requires_a_plain_absolute_local_path_and_supports_spaces(tmp_path):
    database = tmp_path / "voice library.db"
    valid = _run_python(tmp_path, "from core.paths import database_url; print(database_url())",
                        VOICEVAULT_DB_URL=f"file:{database}")
    assert valid.returncode == 0, valid.stderr
    assert valid.stdout.strip() == f"file:{database.as_posix()}"

    for invalid in ("C:/relative.db", "file:relative.db", "file:///C:/ambiguous.db",
                    "file:C:/encoded%20name.db", "file:C:/name.db?mode=ro", "file:C:/name.db#fragment"):
        result = _run_python(tmp_path, "from core.paths import database_path; database_path()",
                             VOICEVAULT_DB_URL=invalid)
        assert result.returncode != 0, invalid
        assert "VOICEVAULT_DB_URL must use" in result.stderr


def test_prisma_uses_resolved_state_database_and_preserves_legacy_default(tmp_path, monkeypatch):
    from core import paths

    calls = []
    class FakePrisma:
        def __init__(self, **kwargs): calls.append(kwargs)
        async def connect(self): pass
        async def disconnect(self): pass

    fake_prisma = types.ModuleType("prisma")
    fake_prisma.Prisma = FakePrisma
    monkeypatch.setitem(sys.modules, "prisma", fake_prisma)
    # Load a separate module instance so a fake Prisma import cannot remain in
    # cached db.client and affect later real-database tests.
    spec = importlib.util.spec_from_file_location("state_contract_client_fixture", BACKEND / "db/client.py")
    client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client)
    state = tmp_path / "state"
    monkeypatch.setattr(paths, "STATE_DIR", state)
    monkeypatch.setattr(paths, "DEFAULT_DB_PATH", state / "library.db")
    monkeypatch.delenv("VOICEVAULT_DB_URL", raising=False)
    client._db = None
    _run_immediate(client.get_db())
    assert calls == [{"datasource": {"url": f"file:{(state / 'library.db').as_posix()}"}}]
    _run_immediate(client.disconnect())

    explicit = tmp_path / "explicit.db"
    monkeypatch.setenv("VOICEVAULT_DB_URL", f"file:{explicit}")
    client._db = None
    _run_immediate(client.get_db())
    assert calls[-1] == {"datasource": {"url": f"file:{explicit.as_posix()}"}}
    _run_immediate(client.disconnect())

    monkeypatch.delenv("VOICEVAULT_DB_URL", raising=False)
    monkeypatch.setattr(paths, "STATE_DIR", None)
    client._db = None
    _run_immediate(client.get_db())
    assert calls[-1] == {}
    _run_immediate(client.disconnect())
    count = len(calls)
    monkeypatch.setenv("VOICEVAULT_DB_URL", "https://invalid.example/secret")
    with pytest.raises(ValueError, match="VOICEVAULT_DB_URL must use"):
        _run_immediate(client.get_db())
    assert len(calls) == count
    assert client._db is None
