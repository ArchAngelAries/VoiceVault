"""Fish Audio Drama 3 adapters. CPU only: fake audio.cpp server, fake Fish API, tiny fake GGUF headers."""
import io
import json
import struct
from pathlib import Path

import httpx
import msgpack
import numpy as np
import pytest
import soundfile as sf

from engines.audiocpp_server import AudioCppServer, find_server_executable
from engines.base import EngineError, ModelInfo, ModelState, SpeakerReference, SynthesisRequest, SynthesisResult
from engines.fish_drama import (
    FishDramaCloudEngine,
    FishDramaEngine,
    _read_gguf_architecture,
    validate_speakers,
)
from engines.manager import EngineManager
from engines.registry import ModelRegistry


def _gguf(path: Path, arch: str = "audiocpp") -> Path:
    key, val = b"general.architecture", arch.encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"GGUF" + struct.pack("<IQQ", 3, 0, 1) + struct.pack("<Q", len(key)) + key
                     + struct.pack("<IQ", 8, len(val)) + val + b"\0" * 32)
    return path


def _wav_bytes(seconds=0.25, rate=44100) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, (0.1 * np.sin(np.arange(int(seconds * rate)) / 20)).astype(np.float32), rate, format="WAV")
    return buf.getvalue()


def _runtime(tmp_path: Path) -> Path:
    exe_dir = tmp_path / "runtime" / "v0.9.3-vulkan"
    (exe_dir / "model_specs").mkdir(parents=True, exist_ok=True)
    (exe_dir / "model_specs" / "fish_drama3.json").write_text("{}")
    (exe_dir / "audiocpp_server.exe").write_bytes(b"")
    return exe_dir


def _clip(tmp_path: Path, name: str) -> Path:
    p = tmp_path / f"{name}.wav"
    p.write_bytes(_wav_bytes(0.1, 24000))
    return p


class FakeServer:
    """Stands in for AudioCppServer; records configs and serves /v1/audio/speech through MockTransport."""

    def __init__(self, handler):
        self.http = httpx.Client(transport=httpx.MockTransport(handler))
        self.base_url = None
        self.configs = []
        self.stops = 0

    @property
    def running(self):
        return self.base_url is not None

    def ensure(self, exe, config):
        self.configs.append(config)
        self.base_url = "http://127.0.0.1:9"
        return self.base_url

    def stop(self):
        self.stops += 1
        self.base_url = None


def test_gguf_header_reader_accepts_audiocpp_and_rejects_other_files(tmp_path):
    assert _read_gguf_architecture(_gguf(tmp_path / "a.gguf")) == "audiocpp"
    assert _read_gguf_architecture(_gguf(tmp_path / "b.gguf", "llama")) == "llama"
    (tmp_path / "c.gguf").write_bytes(b"not a gguf")
    assert _read_gguf_architecture(tmp_path / "c.gguf") is None


def test_registry_finds_drama_ggufs_and_cloud_model(tmp_path):
    root = tmp_path / "Models"
    _gguf(root / "TTS" / "Drama-3-GGUF" / "drama-3-backbone-8bit-experts-Q2_K.gguf")
    _gguf(root / "LLM" / "drama-3-roleplay-llama.gguf", "llama")   # a text model with a similar name
    _gguf(root / "TTS" / "other.gguf")                              # no 'drama' in the name
    settings = {"audiocpp_dir": str(_runtime(tmp_path)), "fish_audio_api_key": ""}
    local = FishDramaEngine(settings_loader=lambda: settings, server=FakeServer(lambda r: None))
    cloud = FishDramaCloudEngine(settings_loader=lambda: settings)

    models = {m.id: m for m in ModelRegistry([local, cloud], [root]).discover()}

    gguf_id = "fish_drama3:TTS/Drama-3-GGUF/drama-3-backbone-8bit-experts-Q2_K.gguf"
    assert set(models) == {gguf_id, "fish_drama3_cloud:fish-audio-api"}
    assert models[gguf_id].state == ModelState.INTEGRATED
    assert "Q2_K experts" in models[gguf_id].architecture
    assert models["fish_drama3_cloud:fish-audio-api"].state == ModelState.BLOCKED
    settings["fish_audio_api_key"] = "k"
    models = {m.id: m for m in ModelRegistry([local, cloud], [root]).discover()}
    assert models["fish_drama3_cloud:fish-audio-api"].state == ModelState.INTEGRATED


def test_missing_runtime_blocks_the_local_model(tmp_path):
    engine = FishDramaEngine(settings_loader=lambda: {"audiocpp_dir": str(tmp_path / "nowhere")})
    assert "audio.cpp runtime was not found" in engine.runtime_problem()


def test_find_server_executable_prefers_vulkan_build(tmp_path):
    for name in ("v0.9.3-cpu", "v0.9.3-vulkan"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "audiocpp_server.exe").write_bytes(b"")
    assert find_server_executable("", tmp_path).parent.name == "v0.9.3-vulkan"
    (tmp_path / "v0.9.3-vulkan-portable").mkdir()
    (tmp_path / "v0.9.3-vulkan-portable" / "audiocpp_server.exe").write_bytes(b"")
    assert find_server_executable("", tmp_path).parent.name == "v0.9.3-vulkan-portable"


def test_server_config_offloads_experts_on_vulkan_and_not_on_cpu(tmp_path):
    settings = {"audiocpp_dir": str(_runtime(tmp_path)), "drama3_cpu_expert_layers": 8, "drama3_threads": 6}
    engine = FishDramaEngine(settings_loader=lambda: settings, server=FakeServer(lambda r: None))
    info = ModelInfo("fish_drama3:x", "fish_drama3", "f", "a", str(tmp_path / "m.gguf"), 1, ModelState.INTEGRATED)

    gpu = engine.server_config(info, "cuda")
    cpu = engine.server_config(info, "cpu")

    assert gpu["backend"] == "vulkan" and gpu["threads"] == 6
    opts = gpu["models"][0]["session_options"]
    assert opts["fish_drama3.cpu_expert_layers"] == 8 and opts["fish_drama3.codec_encoder_f32_accumulation"] is True
    assert cpu["backend"] == "cpu" and "fish_drama3.cpu_expert_layers" not in cpu["models"][0]["session_options"]
    assert gpu["models"][0]["family"] == "fish_drama3" and gpu["ui"] is False


def test_local_payloads_for_single_and_multi_speaker(tmp_path):
    engine = FishDramaEngine(settings_loader=lambda: {})
    ref = _clip(tmp_path, "a")
    single = engine.build_payload(SynthesisRequest(text="[sighs] Fine.", reference_audio=ref,
                                                   reference_text=" We asked. ", seed=7, max_new_tokens=4000))
    assert single["voice_ref"] == {"type": "path", "path": ref.resolve().as_posix()}
    assert single["reference_text"] == "We asked."
    assert single["options"]["max_tokens"] == 1024 and single["options"]["seed"] == 7

    b = _clip(tmp_path, "b")
    multi = engine.build_payload(SynthesisRequest(
        text="<|speaker:0|>Hi.<|speaker:1|>Hello.",
        speakers=[SpeakerReference(ref, "First."), SpeakerReference(b, "Second.")]))
    refs = json.loads(multi["options"]["references"])
    assert [r["text"] for r in refs] == ["<|speaker:0|> First.", "<|speaker:1|> Second."]
    assert "voice_ref" not in multi

    plain = engine.build_payload(SynthesisRequest(text="No reference."))
    assert "voice_ref" not in plain and "references" not in plain["options"]


def test_speaker_validation_messages(tmp_path):
    a = _clip(tmp_path, "a")
    with pytest.raises(EngineError, match="only 1 voice"):
        validate_speakers(SynthesisRequest(text="<|speaker:1|>Hey", speakers=[SpeakerReference(a, "x")]))
    with pytest.raises(EngineError, match="transcript"):
        validate_speakers(SynthesisRequest(text="Hey", speakers=[SpeakerReference(a, "")]))
    with pytest.raises(EngineError, match="speaker:15"):
        validate_speakers(SynthesisRequest(text="<|speaker:16|>Hey"))
    with pytest.raises(EngineError, match="exact transcript"):
        FishDramaEngine(settings_loader=lambda: {}).build_payload(SynthesisRequest(text="x", reference_audio=a))


def test_local_synthesis_round_trip_and_unload_stops_server(tmp_path):
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=_wav_bytes(), headers={"content-type": "audio/wav"})

    server = FakeServer(handler)
    settings = {"audiocpp_dir": str(_runtime(tmp_path))}
    model = _gguf(tmp_path / "drama-3-q2.gguf")
    engine = FishDramaEngine(settings_loader=lambda: settings, server=server)
    info = ModelInfo("fish_drama3:m", "fish_drama3", "f", "a", str(model), 1, ModelState.INTEGRATED)

    engine.load(info, device="cuda")
    result = engine.synthesize(SynthesisRequest(text="Hello there."))
    engine.unload()

    assert seen["path"] == "/v1/audio/speech" and seen["body"]["input"] == "Hello there."
    assert result.sample_rate == 44100 and result.samples.dtype == np.float32 and result.samples.size > 0
    assert result.meta["backend"] == "vulkan" and result.meta["includes_model_load"] is True
    assert server.stops == 1


def test_local_server_errors_are_reported(tmp_path):
    server = FakeServer(lambda r: httpx.Response(400, json={"error": {"message": "unknown option top_q"}}))
    engine = FishDramaEngine(settings_loader=lambda: {"audiocpp_dir": str(_runtime(tmp_path))}, server=server)
    engine.load(ModelInfo("i", "fish_drama3", "f", "a", str(_gguf(tmp_path / "drama3.gguf")), 1,
                          ModelState.INTEGRATED))
    with pytest.raises(EngineError, match="unknown option top_q"):
        engine.synthesize(SynthesisRequest(text="Hi"))


def test_cloud_sends_msgpack_references_and_maps_errors(tmp_path):
    captured = {}

    def handler(request):
        captured["headers"] = dict(request.headers)
        captured["body"] = msgpack.unpackb(request.content, raw=False)
        return httpx.Response(200, content=_wav_bytes())

    engine = FishDramaCloudEngine(settings_loader=lambda: {"fish_audio_api_key": "secret"},
                                  http=httpx.Client(transport=httpx.MockTransport(handler)))
    a, b = _clip(tmp_path, "a"), _clip(tmp_path, "b")
    result = engine.synthesize(SynthesisRequest(
        text="<|speaker:0|>Hi<|speaker:1|>Yo", temperature=1.5,
        speakers=[SpeakerReference(a, "A."), SpeakerReference(b, "B.")]))

    assert captured["headers"]["model"] == "drama-3"
    assert captured["headers"]["authorization"] == "Bearer secret"
    assert captured["headers"]["content-type"] == "application/msgpack"
    body = captured["body"]
    assert body["temperature"] == 1.0 and body["format"] == "wav"
    assert [r[0]["text"] for r in body["references"]] == ["A.", "B."] and len(body["reference_id"]) == 2
    assert isinstance(body["references"][0][0]["audio"], bytes)
    assert result.sample_rate == 44100

    for status, text in ((401, "rejected the API key"), (402, "no remaining credit"), (503, "high load")):
        bad = FishDramaCloudEngine(settings_loader=lambda: {"fish_audio_api_key": "k"},
                                   http=httpx.Client(transport=httpx.MockTransport(
                                       lambda r, s=status: httpx.Response(s, json={"message": "x"}))))
        with pytest.raises(EngineError, match=text):
            bad.synthesize(SynthesisRequest(text="Hi"))


def test_cloud_without_reference_uses_json():
    captured = {}

    def handler(request):
        captured["ct"] = request.headers["content-type"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, content=_wav_bytes())

    engine = FishDramaCloudEngine(settings_loader=lambda: {"fish_audio_api_key": "k"},
                                  http=httpx.Client(transport=httpx.MockTransport(handler)))
    engine.synthesize(SynthesisRequest(text="Plain."))
    assert captured["ct"] == "application/json" and "references" not in captured["body"]


def test_remote_engine_keeps_the_resident_local_model(tmp_path):
    class Local:
        engine_id = "local"
        display_name = "Local"
        from engines.base import EngineCapabilities
        capabilities = EngineCapabilities(languages=["en"])
        unloads = 0

        def identify(self, d):
            return None

        def runtime_problem(self):
            return None

        def load(self, m, device="cuda", dtype="bfloat16"):
            pass

        def unload(self):
            Local.unloads += 1

        def synthesize(self, r):
            return SynthesisResult(np.zeros(10, np.float32), 24000, {})

    cloud = FishDramaCloudEngine(settings_loader=lambda: {"fish_audio_api_key": "k"},
                                 http=httpx.Client(transport=httpx.MockTransport(
                                     lambda r: httpx.Response(200, content=_wav_bytes()))))
    root = tmp_path / "m"
    (root / "local").mkdir(parents=True)
    registry = ModelRegistry([cloud], [root])
    registry.discover()
    local_info = ModelInfo("local:x", "local", "f", "a", str(root), 1, ModelState.INTEGRATED)
    registry._models[local_info.id] = local_info
    manager = EngineManager({"local": Local(), cloud.engine_id: cloud}, registry)

    manager.synthesize("local:x", SynthesisRequest(text="a"))
    manager.synthesize("fish_drama3_cloud:fish-audio-api", SynthesisRequest(text="b"))

    assert manager.loaded_model_id == "local:x" and Local.unloads == 0


def test_audiocpp_server_writes_config_waits_for_health_and_stops(tmp_path):
    started = []

    class Proc:
        pid = 4242

        def __init__(self, args, **kw):
            started.append((args, kw))
            self.alive = True

        def poll(self):
            return None if self.alive else 0

        def wait(self, timeout=None):
            self.alive = False

        def terminate(self):
            self.alive = False

        def kill(self):
            self.alive = False

    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"status": "ok"})))
    server = AudioCppServer(popen=Proc, http=http, work_dir=tmp_path / "w")
    exe = tmp_path / "audiocpp_server.exe"
    exe.write_bytes(b"")
    import engines.audiocpp_server as mod
    mod.subprocess_run_backup = mod.subprocess.run
    mod.subprocess.run = lambda *a, **k: None  # no taskkill in the test
    try:
        url = server.ensure(exe, {"backend": "cpu", "models": []})
        again = server.ensure(exe, {"backend": "cpu", "models": []})
        cfg = json.loads((tmp_path / "w" / "server.json").read_text())
        server.stop()
    finally:
        mod.subprocess.run = mod.subprocess_run_backup
    assert url == again and len(started) == 1
    assert cfg["host"] == "127.0.0.1" and url.endswith(str(cfg["port"]))
    assert started[0][0][1:3] == ["--config", str(tmp_path / "w" / "server.json")]
    assert not server.running


def test_auto_cpu_expert_layers_matches_published_configurations():
    from engines.fish_drama import auto_cpu_expert_layers
    gib = 1024 ** 3
    # audio.cpp's tested 16 GB settings: Q2 → 8, Q4 experts → 24 (ours may be a little more cautious)
    assert 8 <= auto_cpu_expert_layers(int(15.88 * gib), int(15.5e9)) <= 12
    assert 18 <= auto_cpu_expert_layers(int(22.91 * gib), int(15.5e9)) <= 26
    # a 24 GB card needs only a few layers for Q4, none for Q2
    assert auto_cpu_expert_layers(int(22.91 * gib), int(23.5e9)) <= 8
    assert auto_cpu_expert_layers(int(15.88 * gib), int(23.5e9)) == 0
    # unknown VRAM falls back to the 16 GB recommendation; never more than all 40 layers
    assert auto_cpu_expert_layers(int(15.88 * gib), None) == auto_cpu_expert_layers(int(15.88 * gib), int(15.5e9))
    assert auto_cpu_expert_layers(int(60 * gib), int(4e9)) == 40


def test_explicit_cpu_expert_layers_setting_wins(tmp_path, monkeypatch):
    import engines.fish_drama as fd
    monkeypatch.setattr(fd, "free_vram_bytes", lambda: int(15.5e9))
    model = _gguf(tmp_path / "drama-3-q2.gguf")
    info = ModelInfo("fish_drama3:m", "fish_drama3", "f", "a", str(model), 1, ModelState.INTEGRATED)
    settings = {"audiocpp_dir": str(_runtime(tmp_path)), "drama3_cpu_expert_layers": -1}
    engine = FishDramaEngine(settings_loader=lambda: settings, server=FakeServer(lambda r: None))
    auto = engine.server_config(info, "cuda")["models"][0]["session_options"]["fish_drama3.cpu_expert_layers"]
    assert auto == 0  # the tiny fake file fits easily
    settings["drama3_cpu_expert_layers"] = 7
    assert engine.server_config(info, "cuda")["models"][0]["session_options"]["fish_drama3.cpu_expert_layers"] == 7


def test_full_band_reference_copy_is_preferred_and_deleted_with_the_clip(tmp_path):
    from engines import audio_store
    from engines.fish_drama import best_reference
    refs = tmp_path / "refs"
    lo = np.zeros(24000, np.float32)
    hi = np.zeros(44100, np.float32)
    rec = audio_store.save_reference(refs, lo, 24000, "clip.wav", hi, 44100)
    wav = refs / f"{rec['reference_id']}.wav"
    assert best_reference(wav).name.endswith(".hq44.wav") and rec["hq_sample_rate"] == 44100
    assert [r["reference_id"] for r in audio_store.list_references(refs)] == [rec["reference_id"]]  # copy not listed
    payload = FishDramaEngine(settings_loader=lambda: {}).build_payload(
        SynthesisRequest(text="Hi", reference_audio=wav, reference_text="x"))
    assert payload["voice_ref"]["path"].endswith(".hq44.wav")
    audio_store.delete_references(refs, [rec["reference_id"]])
    assert not list(refs.glob("*.wav"))
    old = audio_store.save_reference(refs, lo, 24000, "old.wav")      # older clips have no copy
    assert best_reference(refs / f"{old['reference_id']}.wav").suffix == ".wav"
    assert not best_reference(refs / f"{old['reference_id']}.wav").name.endswith(".hq44.wav")
