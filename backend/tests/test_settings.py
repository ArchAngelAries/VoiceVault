"""Settings file load/save safety (core/settings.py)."""
import json

from core import settings


def _point_settings_at(monkeypatch, path):
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)


def test_corrupt_file_is_moved_aside_not_overwritten(tmp_path, monkeypatch):
    path = tmp_path / "voicevault_settings.json"
    path.write_text("{ not json", encoding="utf-8")
    _point_settings_at(monkeypatch, path)

    data = settings.load()

    assert data == settings.DEFAULTS
    assert not path.exists()
    moved = list(tmp_path.glob("voicevault_settings.corrupt-*.json"))
    assert len(moved) == 1
    assert moved[0].read_text(encoding="utf-8") == "{ not json"


def test_non_object_json_is_treated_as_corrupt(tmp_path, monkeypatch):
    path = tmp_path / "voicevault_settings.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    _point_settings_at(monkeypatch, path)

    assert settings.load() == settings.DEFAULTS
    assert len(list(tmp_path.glob("voicevault_settings.corrupt-*.json"))) == 1


def test_save_writes_through_a_temp_file(tmp_path, monkeypatch):
    path = tmp_path / "voicevault_settings.json"
    _point_settings_at(monkeypatch, path)

    settings.save({"theme": "light", "tmdb_api_key": "abc"})

    assert json.loads(path.read_text(encoding="utf-8"))["theme"] == "light"
    assert not (tmp_path / "voicevault_settings.json.tmp").exists()


def test_load_fills_in_missing_keys_from_defaults(tmp_path, monkeypatch):
    path = tmp_path / "voicevault_settings.json"
    path.write_text('{"theme": "light"}', encoding="utf-8")
    _point_settings_at(monkeypatch, path)

    data = settings.load()

    assert data["theme"] == "light"
    assert data["harvest_max_duration"] == settings.DEFAULTS["harvest_max_duration"]


def test_experimental_features_is_off_by_default_and_must_be_a_bool():
    import pytest
    assert settings.DEFAULTS["experimental_features"] is False
    assert settings.validate_update({"experimental_features": True}, dict(settings.DEFAULTS))["experimental_features"] is True
    with pytest.raises(ValueError, match="experimental_features"):
        settings.validate_update({"experimental_features": "yes"}, dict(settings.DEFAULTS))
    # Settings no longer shown in the UI still validate, so older files keep loading and saving.
    for key in ("clips_dir", "google_client_id", "tts_default_engine", "omnivoice_url", "harvest_max_duration"):
        settings.validate_update({key: settings.DEFAULTS[key]}, dict(settings.DEFAULTS))
