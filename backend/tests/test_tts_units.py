"""Unit tests for the TTS layer. No GPU and no model weights are needed."""
import json
import wave
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from engines import audio_io
from engines.base import EngineCapabilities, EngineError, ModelInfo, ModelState, SynthesisRequest, SynthesisResult, TTSEngine
from engines.maya1 import (
    DEFAULT_DESCRIPTION,
    MAYA1_VOCAB_SIZE,
    SNAC_END,
    SNAC_START,
    Maya1Engine,
    build_prompt,
    unpack_snac_tokens,
)
from engines.manager import EngineManager
from engines.vibevoice_engine import VibeVoiceEngine
from engines.registry import ModelRegistry


class FakeTokenizer:
    bos_token = "<|begin_of_text|>"
    _names = {128259: "<SOH>", 128260: "<EOH>", 128261: "<SOA>", 128257: "<SOS>", 128009: "<EOT>"}

    def decode(self, ids):
        return "".join(self._names[i] for i in ids)


def test_prompt_matches_the_official_layout():
    prompt = build_prompt(FakeTokenizer(), "Hi <laugh> there.", "Calm voice.")

    assert prompt == '<SOH><|begin_of_text|><description="Calm voice."> Hi <laugh> there.<EOT><EOH><SOA><SOS>'


def test_unpack_maps_each_slot_to_its_codebook_level():
    base = SNAC_START
    # One frame whose slot i holds code (i + 1): L1=[1], L2=[2, 5], L3=[3, 4, 6, 7]
    frame = [base + i for i in range(1, 8)]

    codes, frames = unpack_snac_tokens(frame)

    assert frames == 1
    assert codes == [[1], [2, 5], [3, 4, 6, 7]]


def test_unpack_ignores_text_tokens_and_partial_trailing_frames():
    frame = [SNAC_START + 10 + i for i in range(7)]
    text_tokens = [9906, 1917, 128009]          # ordinary text and control tokens
    partial = [SNAC_START + 1, SNAC_START + 2]  # incomplete frame at the end

    codes, frames = unpack_snac_tokens(text_tokens + frame + text_tokens + partial)

    assert frames == 1
    assert codes[0] == [10]


def test_codes_wrap_within_the_codebook():
    token = SNAC_END  # largest SNAC token
    codes, frames = unpack_snac_tokens([token] * 7)

    assert frames == 1
    assert all(0 <= c < 4096 for level in codes for c in level)


def _maya_tree(root, vocab=MAYA1_VOCAB_SIZE):
    d = root / "maya1-TTS" / "maya1"
    d.mkdir(parents=True)
    (d / "config.json").write_text(json.dumps({"model_type": "llama", "vocab_size": vocab}), encoding="utf-8")
    (d / "model-00001-of-00001.safetensors").write_bytes(b"\0" * 64)
    return d


def test_registry_marks_maya1_integrated_when_runtime_is_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(Maya1Engine, "runtime_problem", lambda self: None)
    _maya_tree(tmp_path)
    reg = ModelRegistry([Maya1Engine()], [tmp_path])

    (info,) = reg.discover()

    assert info.id == "maya1:maya1-TTS/maya1"
    assert info.state == ModelState.INTEGRATED
    assert info.family == "Maya1"


def test_registry_blocks_a_model_when_the_runtime_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(Maya1Engine, "runtime_problem", lambda self: "Missing Python packages: snac")
    _maya_tree(tmp_path)
    reg = ModelRegistry([Maya1Engine()], [tmp_path])

    (info,) = reg.discover()

    assert info.state == ModelState.BLOCKED
    assert "snac" in info.reason


def test_recorded_passing_test_upgrades_to_tested(tmp_path, monkeypatch):
    monkeypatch.setattr(Maya1Engine, "runtime_problem", lambda self: None)
    _maya_tree(tmp_path)
    record = tmp_path / "verification.json"
    record.write_text(json.dumps({"maya1:maya1-TTS/maya1": {"status": "passed"}}), encoding="utf-8")
    reg = ModelRegistry([Maya1Engine()], [tmp_path], verification_file=record)

    (info,) = reg.discover()

    assert info.state == ModelState.TESTED


def test_recorded_verification_accepts_canonical_matching_model_path(tmp_path, monkeypatch):
    monkeypatch.setattr(Maya1Engine, "runtime_problem", lambda self: None)
    model_dir = _maya_tree(tmp_path)
    record = tmp_path / "verification.json"
    record.write_text(json.dumps({"maya1:maya1-TTS/maya1": {
        "status": "passed", "model_path": str(model_dir / "nested" / ".."),
    }}), encoding="utf-8")
    reg = ModelRegistry([Maya1Engine()], [tmp_path], verification_file=record)

    (info,) = reg.discover()

    assert info.path == str(model_dir)
    assert info.state == ModelState.TESTED


def test_recorded_verification_does_not_upgrade_a_different_model_path(tmp_path, monkeypatch):
    monkeypatch.setattr(Maya1Engine, "runtime_problem", lambda self: None)
    model_dir = _maya_tree(tmp_path / "shared-root")
    other_model_dir = _maya_tree(tmp_path / "comfy-root")
    record = tmp_path / "verification.json"
    record.write_text(json.dumps({"maya1:maya1-TTS/maya1": {
        "status": "passed", "model_path": str(other_model_dir),
    }}), encoding="utf-8")
    reg = ModelRegistry([Maya1Engine()], [tmp_path / "shared-root"], verification_file=record)

    (info,) = reg.discover()

    assert info.path == str(model_dir)
    assert info.state == ModelState.INTEGRATED


def test_other_llama_models_are_not_mistaken_for_maya1(tmp_path, monkeypatch):
    monkeypatch.setattr(Maya1Engine, "runtime_problem", lambda self: None)
    _maya_tree(tmp_path, vocab=32000)
    reg = ModelRegistry([Maya1Engine()], [tmp_path])

    assert reg.discover() == []


def _vibevoice_tree(root, model="VibeVoice-Large", quantised=False, tokenizer=True):
    d = root / "vibevoice" / model
    d.mkdir(parents=True)
    cfg = {"model_type": "vibevoice"}
    if quantised:
        cfg["quantization_config"] = {}
    (d / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    (d / "model.safetensors.index.json").write_text("{\"weight_map\": {}}", encoding="utf-8")
    (d / "model-00001-of-00001.safetensors").write_bytes(b"\0")
    if tokenizer:
        tok = root / "vibevoice" / "tokenizer"
        tok.mkdir(parents=True)
        (tok / "tokenizer.json").write_text("{}", encoding="utf-8")
        (tok / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    return d


def test_vibevoice_unknown_quantised_build_is_blocked_with_a_reason(tmp_path):
    _vibevoice_tree(tmp_path, model="VibeVoice-Q8", quantised=True)
    reg = ModelRegistry([VibeVoiceEngine()], [tmp_path])

    (info,) = reg.discover()

    assert info.state == ModelState.BLOCKED
    assert "quantized" in info.reason


def test_vibevoice_supported_q8_config_is_integrated_when_tokenizer_is_present(tmp_path):
    d = _vibevoice_tree(tmp_path, model="VibeVoice-Q8")
    cfg = json.loads((d / "config.json").read_text(encoding="utf-8"))
    cfg["quantization_config"] = {
        "quant_method": "bitsandbytes", "_load_in_8bit": True, "load_in_8bit": True,
        "_load_in_4bit": False, "load_in_4bit": False, "llm_int8_threshold": 3.0,
        "llm_int8_has_fp16_weight": False, "bnb_8bit_compute_dtype": "bfloat16",
        "llm_int8_skip_modules": ["prediction_head", "acoustic_connector", "semantic_connector",
                                  "acoustic_tokenizer", "semantic_tokenizer"],
    }
    (d / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    reg = ModelRegistry([VibeVoiceEngine()], [tmp_path])

    (info,) = reg.discover()

    assert info.state == ModelState.INTEGRATED
    assert "plain PyTorch rowwise dequantization" in info.notes
    assert "unvalidated" in info.notes


def test_vibevoice_full_precision_build_is_integrated_when_tokenizer_is_present(tmp_path):
    _vibevoice_tree(tmp_path)
    reg = ModelRegistry([VibeVoiceEngine()], [tmp_path])

    (info,) = reg.discover()

    assert info.state == ModelState.INTEGRATED
    assert info.reason is None


def test_vibevoice_build_without_tokenizer_is_blocked(tmp_path):
    _vibevoice_tree(tmp_path, tokenizer=False)
    reg = ModelRegistry([VibeVoiceEngine()], [tmp_path])

    (info,) = reg.discover()

    assert info.state == ModelState.BLOCKED
    assert "tokenizer" in info.reason


def test_missing_models_root_is_skipped_not_fatal(tmp_path):
    reg = ModelRegistry([Maya1Engine()], [tmp_path / "does-not-exist"])

    assert reg.discover() == []


def test_wav_round_trips_with_sample_rate_and_length(tmp_path):
    samples = (np.sin(np.linspace(0, 2 * np.pi * 440, 24000)) * 0.5).astype(np.float32)
    path = tmp_path / "tone.wav"

    audio_io.write_wav(path, samples, 24000)

    with wave.open(str(path), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 24000, 24000)
    assert not (tmp_path / "tone.wav.tmp").exists()


def test_save_generation_writes_wav_and_sidecar_and_lists_both(tmp_path):
    samples = np.zeros(2400, dtype=np.float32)
    first = audio_io.save_generation(tmp_path, samples, 24000, {"engine": "maya1", "text": "one"})
    second = audio_io.save_generation(tmp_path, samples, 24000, {"engine": "maya1", "text": "two"})

    history = audio_io.list_generations(tmp_path)

    assert (tmp_path / first["file"]).is_file()
    assert first["duration_s"] == 0.1
    assert {h["id"] for h in history} == {first["id"], second["id"]}


def test_history_skips_entries_whose_wav_is_gone(tmp_path):
    rec = audio_io.save_generation(tmp_path, np.zeros(240, dtype=np.float32), 24000, {"engine": "maya1", "text": "x"})
    (tmp_path / rec["file"]).unlink()

    assert audio_io.list_generations(tmp_path) == []


@pytest.mark.parametrize("name", ["../secret.wav", "a/b.wav", "..\\x.wav", "ok.mp3", "no-ext"])
def test_audio_names_are_restricted(name):
    assert not audio_io.is_valid_audio_name(name)


def test_audio_name_accepts_generated_names():
    assert audio_io.is_valid_audio_name("20261009-101010_maya1_1a2b3c4d.wav")


class _RecordingEngine(TTSEngine):
    engine_id = "fake"
    display_name = "Fake"
    capabilities = EngineCapabilities(languages=["en"])

    def __init__(self):
        self.loads = []
        self.unloads = 0

    def identify(self, model_dir):
        return None

    def runtime_problem(self):
        return None

    def load(self, model, device="cuda", dtype="bfloat16"):
        self.loads.append((model.id, device, dtype))

    def unload(self):
        self.unloads += 1

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        return SynthesisResult(samples=np.zeros(240, np.float32), sample_rate=24000, meta={"fake": True})


def _info(model_id, state=ModelState.INTEGRATED, reason=None):
    return ModelInfo(id=model_id, engine_id="fake", family="Fake", architecture="x", path="/x",
                     size_bytes=1, state=state, reason=reason)


class _FixedRegistry:
    def __init__(self, models):
        self._models = {m.id: m for m in models}

    def get(self, model_id):
        return self._models.get(model_id)


def test_manager_loads_once_per_model_and_reloads_on_change():
    engine = _RecordingEngine()
    reg = _FixedRegistry([_info("fake:a"), _info("fake:b")])
    mgr = EngineManager({"fake": engine}, reg)
    req = SynthesisRequest(text="hi")

    mgr.synthesize("fake:a", req)
    mgr.synthesize("fake:a", req)
    mgr.synthesize("fake:b", req)

    assert [l[0] for l in engine.loads] == ["fake:a", "fake:b"]
    assert engine.unloads == 1
    assert mgr.loaded_model_id == "fake:b"


def test_manager_rejects_unknown_and_blocked_models():
    engine = _RecordingEngine()
    reg = _FixedRegistry([_info("fake:blocked", ModelState.BLOCKED, reason="not yet")])
    mgr = EngineManager({"fake": engine}, reg)

    with pytest.raises(EngineError, match="Unknown model"):
        mgr.synthesize("fake:nope", SynthesisRequest(text="hi"))
    with pytest.raises(EngineError, match="not yet"):
        mgr.synthesize("fake:blocked", SynthesisRequest(text="hi"))
    assert engine.loads == []


def test_manager_unload_releases_the_engine():
    engine = _RecordingEngine()
    mgr = EngineManager({"fake": engine}, _FixedRegistry([_info("fake:a")]))
    mgr.synthesize("fake:a", SynthesisRequest(text="hi"))

    mgr.unload()

    assert engine.unloads == 1
    assert mgr.loaded_model_id is None


def test_manager_serializes_concurrent_synthesis_and_loads_once():
    engine = _RecordingEngine()
    mgr = EngineManager({"fake": engine}, _FixedRegistry([_info("fake:a")]))
    req = SynthesisRequest(text="hi")

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: mgr.synthesize("fake:a", req), range(8)))

    assert len(results) == 8
    assert len(engine.loads) == 1


def test_missing_reference_transcript_is_prepared_before_tts_load(monkeypatch, tmp_path):
    from asr import service

    engine = _RecordingEngine()
    engine.capabilities = EngineCapabilities(languages=["en"], reference_transcript=True)
    mgr = EngineManager({"fake": engine}, _FixedRegistry([_info("fake:a")]))
    events = []

    def prepare(path, language=None):
        events.append(("transcribe", engine.unloads))
        return "reference words"

    monkeypatch.setattr(service, "prepare_reference_transcript", prepare)
    monkeypatch.setattr(engine, "load", lambda model, device="cuda", dtype="bfloat16": events.append(("load", model.id)))
    mgr.synthesize("fake:a", SynthesisRequest(text="warm model"))
    events.clear()
    req = SynthesisRequest(text="hi", reference_audio=tmp_path / "reference.wav")

    mgr.synthesize("fake:a", req)

    assert req.reference_text == "reference words"
    assert events == [("transcribe", 1), ("load", "fake:a")]


@pytest.mark.parametrize("fits", [False, True])
def test_asr_service_releases_resident_tts_only_when_both_do_not_fit(monkeypatch, tmp_path, fits):
    from asr import service
    from core import gpu_scheduler

    monkeypatch.setattr(gpu_scheduler, "vram_fits", lambda need: fits)
    engine = _RecordingEngine()
    mgr = EngineManager({"fake": engine}, _FixedRegistry([_info("fake:a")]))
    mgr.synthesize("fake:a", SynthesisRequest(text="hi"))

    class FakeTranscriber:
        loaded = False

        def load(self):
            self.loaded = True

        def transcribe(self, samples, language=None, timestamps=False):
            assert (mgr.loaded_model_id is None) == (not fits)
            return {"text": "reference words"}

    monkeypatch.setattr(service, "get_transcriber", lambda: FakeTranscriber())
    monkeypatch.setattr(service, "decode_audio", lambda path: object())

    assert service.transcribe_file(tmp_path / "reference.wav") == "reference words"
    assert engine.unloads == (0 if fits else 1)   # live chat keeps its voice loaded when there is room


def test_resident_voice_keeps_whisper_loaded(monkeypatch):
    from asr import service

    engine = _RecordingEngine()
    mgr = EngineManager({"fake": engine}, _FixedRegistry([_info("fake:a")]))
    mgr.synthesize("fake:a", SynthesisRequest(text="hi"))

    class FakeTranscriber:
        loaded = True
        unloads = 0

        def unload(self):
            self.unloads += 1
            self.loaded = False

    transcriber = FakeTranscriber()
    monkeypatch.setattr(service, "get_transcriber", lambda: transcriber)
    mgr.synthesize("fake:a", SynthesisRequest(text="again"))   # same model already loaded
    assert transcriber.unloads == 0


def test_whisper_transcript_is_saved_with_the_voice(tmp_path):
    import json
    from engines.audio_store import remember_transcript

    rid = "a" * 32
    (tmp_path / f"{rid}.json").write_text(json.dumps({"name": "V", "transcript": None}))
    assert remember_transcript(tmp_path / f"{rid}.hq44.wav", "Make it so.")
    assert json.loads((tmp_path / f"{rid}.json").read_text())["transcript"] == "Make it so."
    assert not remember_transcript(tmp_path / f"{rid}.wav", "Other words")      # never overwrites
    assert not remember_transcript(tmp_path / "upload_x.wav", "x")               # only saved voices


def test_tts_run_releases_resident_whisper(monkeypatch):
    from asr import service

    engine = _RecordingEngine()
    mgr = EngineManager({"fake": engine}, _FixedRegistry([_info("fake:a")]))

    class FakeTranscriber:
        loaded = True
        unloads = 0

        def unload(self):
            self.unloads += 1
            self.loaded = False

    transcriber = FakeTranscriber()
    monkeypatch.setattr(service, "get_transcriber", lambda: transcriber)

    mgr.synthesize("fake:a", SynthesisRequest(text="hi"))

    assert transcriber.unloads == 1


def test_default_description_is_the_official_one():
    assert DEFAULT_DESCRIPTION.startswith("Realistic male voice in the 30s")


def test_warm_loads_a_gpu_voice_once():
    engine = _RecordingEngine()
    mgr = EngineManager({"fake": engine}, _FixedRegistry([_info("fake:a")]))
    assert mgr.warm("fake:a") == "ready" and mgr.loaded_model_id == "fake:a"
    loads = getattr(engine, "loads", None)
    assert mgr.warm("fake:a") == "ready"
    assert getattr(engine, "loads", None) == loads   # already resident: not reloaded


def test_whisper_uses_the_small_model_without_a_gpu(monkeypatch):
    from asr import whisper_engine as we

    monkeypatch.setattr(we, "_gpu_available", lambda: False)
    assert we.WhisperTranscriber().effective_model_id() == we.CPU_MODEL_ID
    monkeypatch.setattr(we, "_gpu_available", lambda: True)
    assert we.WhisperTranscriber().effective_model_id() == we.MODEL_ID
    assert we.WhisperTranscriber("custom/model").effective_model_id() == "custom/model"
