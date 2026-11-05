"""Kokoro: discovery, voice choice, CPU-resident streaming that never takes the GPU slot."""
import base64
import io
import json
import threading

import numpy as np
import soundfile as sf

from engines.base import ModelInfo, ModelState, SynthesisRequest
from engines.kokoro import ALL_VOICES, DEFAULT_VOICE, KokoroEngine, voice_for
from engines.manager import EngineManager


def test_voices_and_choice():
    assert len(ALL_VOICES) == 54 and len(set(ALL_VOICES)) == 54
    assert voice_for(SynthesisRequest(text="x", voice_description="warm, use bm_george")) == "bm_george"
    assert voice_for(SynthesisRequest(text="x")) == DEFAULT_VOICE
    caps = KokoroEngine.capabilities
    assert caps.cpu_resident and caps.streaming and caps.commercial_use == "yes"


class _Resp:
    def __init__(self, lines):
        self.status_code, self._lines = 200, lines

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_lines(self):
        yield from self._lines


class _Http:
    def __init__(self):
        self.calls = []

    def stream(self, method, url, json=None, timeout=None):
        self.calls.append(json)
        pcm = (np.ones(2400) * 1000).astype("<i2").tobytes()
        ev = {"type": "speech.audio.delta", "audio": base64.b64encode(pcm).decode(), "sample_rate": 24000}
        return _Resp([f"data: {__import__('json').dumps(ev)}", f"data: {__import__('json').dumps(ev)}", "data: [DONE]"])


class _Server:
    running, base_url = True, "http://x"

    def __init__(self):
        self.http, self.stopped, self.configs = _Http(), 0, []

    def ensure(self, exe, cfg):
        self.configs.append(cfg)
        return self.base_url

    def stop(self):
        self.stopped += 1


class _Registry:
    def __init__(self, info):
        self.info = info

    def get(self, mid):
        return self.info if mid == self.info.id else None


def test_streams_on_cpu_without_unloading_the_gpu_voice(tmp_path, monkeypatch):
    model = tmp_path / "kokoro-82m-q8_0.gguf"
    model.write_bytes(b"GGUF")
    server = _Server()
    eng = KokoroEngine(server=server, settings_loader=lambda: {})
    monkeypatch.setattr(eng, "_exe", lambda: tmp_path / "audiocpp_server.exe")
    monkeypatch.setattr(eng, "runtime_problem", lambda: None)
    monkeypatch.setattr("engines.kokoro.find_espeak", lambda: (tmp_path / "libespeak-ng.dll", tmp_path))
    info = ModelInfo(id="kokoro:k", engine_id="kokoro", family="Kokoro", architecture="", path=str(model),
                     size_bytes=4, state=ModelState.INTEGRATED)
    mgr = EngineManager({"kokoro": eng}, _Registry(info))
    released = []
    monkeypatch.setattr(mgr, "_release", lambda: released.append(1))
    got = []
    res = mgr.synthesize_stream("kokoro:k", SynthesisRequest(text="Hi", voice_description="af_bella", seed=2),
                                lambda pcm, rate: got.append((len(pcm), rate)), threading.Event())
    assert got == [(2400, 24000), (2400, 24000)] and res.meta["voice"] == "af_bella"
    assert server.configs[0]["backend"] in ("cpu", "vulkan") and server.configs[0]["models"][0]["family"] == "kokoro_tts"
    body = server.http.calls[0]
    assert body["stream"] and body["options"]["voice_id"] == "af_bella" and body["options"]["seed"] == 2
    assert released == []   # the resident GPU model stays loaded


def test_warm_loads_cpu_resident_voice_without_touching_the_gpu_slot(tmp_path, monkeypatch):
    model = tmp_path / "kokoro.gguf"
    model.write_bytes(b"GGUF")
    server = _Server()
    eng = KokoroEngine(server=server, settings_loader=lambda: {})
    monkeypatch.setattr(eng, "_exe", lambda: tmp_path / "audiocpp_server.exe")
    monkeypatch.setattr(eng, "runtime_problem", lambda: None)
    monkeypatch.setattr("engines.kokoro.find_espeak", lambda: (tmp_path / "libespeak-ng.dll", tmp_path))
    info = ModelInfo(id="kokoro:k", engine_id="kokoro", family="Kokoro", architecture="", path=str(model),
                     size_bytes=4, state=ModelState.INTEGRATED)
    mgr = EngineManager({"kokoro": eng}, _Registry(info))
    released = []
    monkeypatch.setattr(mgr, "_release", lambda: released.append(1))
    assert mgr.warm("kokoro:k") == "ready" and len(server.configs) == 1 and released == []
