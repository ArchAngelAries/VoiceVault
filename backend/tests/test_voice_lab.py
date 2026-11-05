"""Voice Lab: shaping (WORLD), effects chain, presets and the API. CPU only; conversion uses a fake runtime."""
import io
import json

import httpx
import numpy as np
import pytest
import soundfile as sf

from voice import effects as fx
from voice import shaper

RATE = 24000


def _voice(f0=110.0, seconds=2.0, rate=RATE):
    """A crude voiced signal: harmonic series with a formant-like envelope and slight pitch drift."""
    t = np.arange(int(seconds * rate)) / rate
    pitch = f0 * (1 + 0.03 * np.sin(2 * np.pi * 0.7 * t))
    phase = 2 * np.pi * np.cumsum(pitch) / rate
    y = sum((1 / k) * np.sin(k * phase) * np.exp(-((k * f0 - 700) / 900) ** 2) for k in range(1, 30))
    return (0.3 * y / np.max(np.abs(y))).astype(np.float32)


def _median_f0(y, rate=RATE):
    return shaper.analyse(y, rate, key=None).median_f0


def test_gender_swap_moves_pitch_into_target_range_both_ways():
    male = _voice(110)
    out, meta = shaper.shape(male, RATE, {"gender": "female"}, key="m1")
    assert meta["analysis"]["gender_guess"] == "male"
    assert 180 <= _median_f0(out) <= 235 and meta["params"]["formant_shift"] > 10
    female = _voice(215)
    out, meta = shaper.shape(female, RATE, {"gender": "male"}, key="f1")
    assert 95 <= _median_f0(out) <= 130 and meta["params"]["formant_shift"] < -10
    half, _ = shaper.shape(male, RATE, {"gender": "female", "gender_strength": 0.5}, key="m1")
    assert 130 <= _median_f0(half) <= 175


def test_age_child_and_elder():
    v = _voice(110)
    child, meta = shaper.shape(v, RATE, {"age": -1}, key="m2")
    assert 240 <= _median_f0(child) <= 320 and meta["params"]["formant_shift"] == pytest.approx(28)
    elder, meta = shaper.shape(v, RATE, {"age": 1}, key="m2")
    p = meta["params"]
    assert p["breathiness"] > 0 and p["roughness"] > 0 and p["tremor_depth"] > 0 and p["speed"] < 1
    assert len(elder) > len(v)                       # slower speech
    beyond, meta = shaper.shape(v, RATE, {"age": 2.5}, key="m2")   # no cap
    assert meta["params"]["speed"] < p["speed"]


def test_pitch_keeps_length_and_speed_keeps_pitch():
    v = _voice(120)
    up, _ = shaper.shape(v, RATE, {"pitch": 12}, key="m3")
    assert abs(len(up) - len(v)) < RATE * 0.02 and 220 <= _median_f0(up) <= 260
    fast, _ = shaper.shape(v, RATE, {"speed": 1.5}, key="m3")
    assert len(fast) == pytest.approx(len(v) / 1.5, rel=0.03) and 105 <= _median_f0(fast) <= 135


def test_advanced_adds_to_simple_and_special_modes():
    v = _voice(120)
    _, meta = shaper.shape(v, RATE, {"pitch": 3}, {"pitch_semitones": 2, "pitch_range": 0.5}, key="m4")
    assert meta["params"]["pitch_semitones"] == 5 and meta["params"]["pitch_range"] == 0.5
    whisper, _ = shaper.shape(v, RATE, {}, {"whisper": True}, key="m4")
    assert np.isfinite(whisper).all() and np.max(np.abs(whisper)) > 0.01
    robot, _ = shaper.shape(v, RATE, {}, {"robot": True}, key="m4")
    assert np.isfinite(robot).all()
    layered, _ = shaper.shape(v, RATE, {}, {"layers": [{"semitones": -12, "gain": 0.7}]}, key="m4")
    assert np.max(np.abs(layered)) <= 0.971


def test_every_preset_renders():
    v = _voice(130, seconds=1.0)
    for name, preset in fx.PRESETS.items():
        out, _ = shaper.shape(v, RATE, preset.get("simple"), preset.get("advanced"), key="m5")
        out = fx.apply_chain(out, RATE, preset.get("effects", []))
        assert np.isfinite(out).all() and 0.005 < float(np.max(np.abs(out))) <= 0.971, name


def test_effect_chain_validation_and_disable():
    v = _voice(130, seconds=0.5)
    with pytest.raises(ValueError, match="Unknown effect"):
        fx.apply_chain(v, RATE, [{"type": "explode"}])
    same = fx.apply_chain(v, RATE, [{"type": "distortion", "drive": 9, "enabled": False}])
    assert np.allclose(same, v, atol=1e-6)
    longer = fx.apply_chain(v, RATE, [{"type": "echo", "delay": 0.2, "repeats": 3}])
    assert len(longer) == len(v) + int(0.2 * RATE) * 3


@pytest.fixture
def client(tmp_path, monkeypatch):
    import api.voice as av
    monkeypatch.setattr(av, "ROOT", tmp_path / "voice")
    monkeypatch.setattr(av, "SOURCES", tmp_path / "voice" / "sources")
    monkeypatch.setattr(av, "PREVIEWS", tmp_path / "voice" / "previews")
    monkeypatch.setattr(av, "DATA_DIR", tmp_path)
    monkeypatch.setattr(av, "_outputs_dir", lambda: tmp_path / "outputs")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(av.router, prefix="/api/voice")
    return TestClient(app), tmp_path


def _wav_bytes(y, rate=RATE):
    buf = io.BytesIO()
    sf.write(buf, y, rate, format="WAV")
    return buf.getvalue()


def test_api_upload_shape_save_as_reference_and_generation(client):
    c, tmp = client
    up = c.post("/api/voice/sources", files={"file": ("me.wav", _wav_bytes(_voice(110, 4.0)), "audio/wav")})
    assert up.status_code == 200, up.text
    src = {"kind": "upload", "id": up.json()["id"]}
    a = c.post("/api/voice/analyze", json=src).json()
    assert a["gender_guess"] == "male"
    r = c.post("/api/voice/shape", json={"source": src, "simple": {"gender": "female", "age": -0.3},
                                        "effects": [{"type": "reverb", "size": 0.2, "mix": 0.2}]})
    assert r.status_code == 200, r.text
    pid = r.json()["preview_id"]
    assert c.get(f"/api/voice/previews/{pid}").status_code == 200
    ref = c.post("/api/voice/save", json={"preview_id": pid, "as": "reference", "name": "Female me", "transcript": "hello"})
    assert ref.status_code == 200, ref.text
    rid = ref.json()["reference_id"]
    assert (tmp / "references" / f"{rid}.hq44.wav").is_file() and ref.json()["transcript"] == "hello"
    gen = c.post("/api/voice/save", json={"preview_id": pid, "as": "generation", "name": "Female me"})
    assert gen.status_code == 200 and (tmp / "outputs" / gen.json()["file"]).is_file()
    # a saved reference can itself be shaped again
    again = c.post("/api/voice/shape", json={"source": {"kind": "reference", "id": rid}, "advanced": {"pitch_semitones": -2}})
    assert again.status_code == 200
    assert c.post("/api/voice/shape", json={"source": {"kind": "upload", "id": "0" * 32}}).status_code == 404
    assert c.post("/api/voice/shape", json={"source": src, "effects": [{"type": "nope"}]}).status_code == 400
    presets = c.get("/api/voice/presets").json()
    assert "female" in json.dumps(presets["presets"]) and "responsible" in presets["disclaimer"]
