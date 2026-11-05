"""Music Studio: lyrics, local DSP, packages and operations with a fake audio.cpp runtime. CPU only."""
import base64
import io
import time

import numpy as np
import pytest
import soundfile as sf

from music import dsp, lyrics as lyr
from music.runtime import find_packages, named_outputs
from music.studio import ProjectStore, Studio, StudioError

LYRICS = "[Verse]\nSoft morning light\nis on the window\n\n[Chorus]\nStay with the rhythm\n"


def _wav(seconds=2.0, rate=48000, channels=2, freq=220.0) -> bytes:
    t = np.arange(int(seconds * rate)) / rate
    tone = (0.2 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, np.stack([tone] * channels, axis=1), rate, format="WAV")
    return buf.getvalue()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def test_parse_sections_lines_and_offsets():
    parsed = lyr.parse(LYRICS)
    assert [s.name for s in parsed.sections] == ["Verse", "Chorus"]
    assert [l.text for l in parsed.lines] == ["Soft morning light", "is on the window", "Stay with the rhythm"]
    w = parsed.words[0]
    assert LYRICS[w.char_start:w.char_end] == "Soft"
    assert lyr.sung_text(parsed).splitlines()[0] == "Soft morning light"


def test_alignment_matching_interpolation_and_ranges():
    parsed = lyr.parse(LYRICS)
    aligned = [{"word": "soft", "start": 1.0, "end": 1.3}, {"word": "morning", "start": 1.3, "end": 1.8},
               {"word": "light", "start": 1.8, "end": 2.4}, {"word": "is", "start": 3.0, "end": 3.1},
               # "on the" missing from the aligner output
               {"word": "window", "start": 3.8, "end": 4.4}, {"word": "stay", "start": 10.0, "end": 10.4},
               {"word": "with", "start": 10.4, "end": 10.6}, {"word": "the", "start": 10.6, "end": 10.7},
               {"word": "rhythm", "start": 10.7, "end": 11.5}]
    assert lyr.apply_alignment(parsed, aligned) == 9  # all but the two missing words
    on = next(w for w in parsed.words if w.text == "on")
    assert 3.1 <= on.start < on.end <= 3.8   # interpolated between neighbours
    # highlight "morning light" in the text → its time range
    a = LYRICS.index("morning")
    rng = lyr.time_range_for_chars(parsed, a, a + len("morning light"))
    assert rng == pytest.approx((1.15, 2.55))
    sections = lyr.section_ranges(parsed, 30.0)
    assert [s["name"] for s in sections] == ["Verse", "Chorus"]
    assert sections[0]["start"] == 0.0 and sections[0]["end"] == 10.0 and sections[1]["end"] == 30.0
    assert lyr.lyrics_slice(parsed, 9.5, 12) == "[Chorus]\nStay with the rhythm"


def test_splice_keeps_outside_identical_and_blend_lengths():
    rate = 1000
    base = np.ones((5000, 2), np.float32) * 0.1
    other = np.ones((5000, 2), np.float32) * 0.5
    out = dsp.splice(base, other, rate, 1.0, 3.0, fade=0.1)
    assert np.array_equal(out[:1000], base[:1000]) and np.array_equal(out[3000:], base[3000:])
    assert np.allclose(out[1200:2800], 0.5)
    blended = dsp.blend(base, other, rate, at=2.0, second_from=2.0, fade=0.5)
    assert len(blended) == len(base)  # same timeline: first[:2 s] + other[2 s:]
    assert np.allclose(blended[:1400], 0.1) and np.allclose(blended[-100:], 0.5)


def test_mix_gains_mutes_and_peak_safety():
    stems = {"drums": np.ones((100, 2), np.float32) * 0.6, "bass": np.ones((100, 2), np.float32) * 0.6}
    assert np.allclose(dsp.mix(stems, {}, {"bass"}), 0.6)
    loud = dsp.mix(stems, {"drums": 6.0}, set())
    assert np.max(np.abs(loud)) <= 0.98 + 1e-6
    assert len(dsp.peaks(np.zeros((10000, 2), np.float32), 50)) == 50


def test_find_packages(tmp_path):
    y = tmp_path / "Music" / "Yue2-3B-GGUF"
    (y / "sidecars").mkdir(parents=True)
    for f in ("yue2-3b-q8_0.gguf", "yue2-3b-ios-q4_0.gguf", "yue2-vae-f16.gguf"):
        (y / f).write_bytes(b"x")
    (tmp_path / "Music" / "ACE-Step1.5-GGUF" / "turbo").mkdir(parents=True)
    (tmp_path / "Music" / "ACE-Step1.5-GGUF" / "turbo" / "ace-step-1.5-turbo-q8_0.gguf").write_bytes(b"x")
    (tmp_path / "Music" / "htdemucs-6stems-q8_0.gguf").write_bytes(b"x")
    pk = find_packages([tmp_path])
    assert set(pk) == {"yue2", "ace_step", "stems"}
    assert pk["yue2"].session_options["yue2.model_gguf"] == "yue2-3b-q8_0.gguf"
    assert pk["stems"].family == "htdemucs_6stems"


def test_named_outputs_shapes():
    data = _wav(0.1)
    assert named_outputs({"named_audio_outputs": {"vocals": _b64(data)}}) == {"vocals": data}
    assert named_outputs({"named_audio_outputs": [{"name": "drums", "audio": _b64(data)}]}) == {"drums": data}


class FakeRuntime:
    def __init__(self):
        self.calls = []
        self.roles = {"yue2", "ace_step", "stems", "vocals", "align", "asr", "score"}

    def has(self, role):
        return role in self.roles

    def stop(self):
        pass

    def run_task(self, role, request, include_artifacts=False, timeout=0):
        self.calls.append((role, request))
        if role == "stems":
            return {"named_audio_outputs": {n: _b64(_wav(4.0, 44100)) for n in ("drums", "vocals", "bass")}}
        if role == "vocals":
            return {"named_audio_outputs": {"vocals": _b64(_wav(4.0, 44100)), "instrumental": _b64(_wav(4.0, 44100))}}
        if role == "score":
            return {"artifacts": [{"id": "score", "kind": "midi", "payload": _b64(b"X:1\nK:C\nCDEF|")}]}
        opts = request.get("options", {})
        if opts.get("route") == "complete":
            return {"audio": _b64(_wav(opts["duration_sec"], 48000, freq=330))}
        if opts.get("route") == "repaint":
            return {"audio": _b64(_wav(4.0, 48000, freq=440))}
        return {"audio": _b64(_wav(4.0, 48000)),
                "artifacts": [{"id": "score", "kind": "midi", "payload": _b64(b"X:1\nK:G\nGABc|")}]}

    def align(self, path, text, language="English"):
        self.calls.append(("align", text))
        words = text.split()
        return {"words": [{"word": w, "start": 0.3 * i, "end": 0.3 * i + 0.25} for i, w in enumerate(words)]}

    def transcribe(self, path, language=None):
        return {"text": "hello there", "words": [{"word": "hello", "start_sample": 0, "end_sample": 8000},
                                                  {"word": "there", "start_sample": 8000, "end_sample": 16000}],
                "sample_rate": 16000}


def _wait(studio, job):
    for _ in range(400):
        j = studio.job(job.id)
        if j["status"] in ("done", "failed", "canceled"):
            return j
        time.sleep(0.02)
    raise AssertionError("job did not finish")


@pytest.fixture
def studio(tmp_path):
    rt = FakeRuntime()
    s = Studio(ProjectStore(tmp_path / "projects"), rt, tmp_path / "tmp")
    yield s
    s.shutdown()


def test_generate_repaint_extend_stems_align_and_blend(studio):
    p = studio.store.create("Song", "indie pop", LYRICS)
    gen = _wait(studio, studio.submit(p["id"], "generate", {"engine": "yue2", "seed": 7}))
    assert gen["status"] == "done", gen
    project = studio.store.get(p["id"])
    take = project["takes"][0]
    assert take["abc"].startswith("X:1") and take["seed"] == 7 and take["channels"] == 2
    role, req = studio.runtime.calls[-1]
    assert role == "yue2" and req["options"]["style"] == "indie pop" and req["options"]["cot"] == "full"

    rep = _wait(studio, studio.submit(p["id"], "repaint", {"take": take["id"], "start": 1.0, "end": 2.0, "seed": 3}))
    assert rep["status"] == "done", rep
    original, rate = studio.store.load_take(p["id"], take["id"])
    repainted, _ = studio.store.load_take(p["id"], rep["take"])
    assert len(repainted) == len(original)
    assert np.allclose(repainted[: int(0.9 * rate)], original[: int(0.9 * rate)], atol=1e-4)
    assert not np.allclose(repainted[int(1.2 * rate):int(1.8 * rate)], original[int(1.2 * rate):int(1.8 * rate)], atol=1e-3)
    opts = studio.runtime.calls[-1][1]["options"]
    assert opts["route"] == "repaint" and opts["repainting_start"] == 1.0 and opts["repainting_end"] == 2.0

    ext = _wait(studio, studio.submit(p["id"], "extend", {"take": take["id"], "seconds": 10}))
    assert ext["status"] == "done", ext
    extended = studio.store.find_take(studio.store.get(p["id"]), ext["take"])
    assert extended["duration"] == pytest.approx(take["duration"] + 10, abs=0.1)

    st = _wait(studio, studio.submit(p["id"], "stems", {"take": take["id"]}))
    assert st["status"] == "done" and set(studio.store.find_take(studio.store.get(p["id"]), take["id"])["stems"]) == {
        "drums", "vocals", "bass"}

    al = _wait(studio, studio.submit(p["id"], "align", {"take": take["id"]}))
    assert al["status"] == "done", al
    alignment = studio.store.find_take(studio.store.get(p["id"]), take["id"])["alignment"]
    assert alignment["source"] == "align" and alignment["matched"] == alignment["total"] == 11
    assert alignment["words"][0]["char_start"] == LYRICS.index("Soft")
    assert [s["name"] for s in alignment["sections"]] == ["Verse", "Chorus"]

    mix = _wait(studio, studio.submit(p["id"], "mixdown", {"take": take["id"], "muted": ["vocals"]}))
    assert mix["status"] == "done", mix
    bl = _wait(studio, studio.submit(p["id"], "blend", {"take": take["id"], "into_take": rep["take"], "at": 2.0, "fade": 0.5}))
    assert bl["status"] == "done", bl
    project = studio.store.get(p["id"])
    assert [t["op"] for t in project["takes"]] == ["generate", "repaint", "extend", "mixdown", "blend"]
    assert project["active_take"] == project["takes"][-1]["id"]


def test_cover_with_yue2_uses_the_melody_score(studio):
    p = studio.store.create("Cover", "jazz", LYRICS)
    src = _wait(studio, studio.submit(p["id"], "generate", {"engine": "ace_step", "duration": 4}))
    job = _wait(studio, studio.submit(p["id"], "cover", {"engine": "yue2", "source_take": src["take"]}))
    assert job["status"] == "done", job
    roles = [c[0] for c in studio.runtime.calls]
    assert roles[-2:] == ["score", "yue2"]
    assert studio.runtime.calls[-1][1]["options"]["abc"].startswith("X:1")
    assert studio.runtime.calls[-1][1]["options"]["cot"] == "melody"


def test_align_without_lyrics_transcribes_and_fills_lyrics(studio):
    p = studio.store.create("Found song")
    gen = _wait(studio, studio.submit(p["id"], "generate", {"engine": "ace_step"}))
    studio.runtime.roles.discard("align")
    job = _wait(studio, studio.submit(p["id"], "align", {"take": gen["take"]}))
    assert job["status"] == "done", job
    project = studio.store.get(p["id"])
    assert "hello there" in project["lyrics"]
    assert project["takes"][0]["alignment"]["source"] == "asr"


def test_transcript_without_word_times_is_timed_by_the_aligner(studio):
    p = studio.store.create("Found song")
    gen = _wait(studio, studio.submit(p["id"], "generate", {"engine": "ace_step"}))
    studio.runtime.transcribe = lambda path, language=None: {"text": "First line here. Second line now!", "language": "en"}
    job = _wait(studio, studio.submit(p["id"], "align", {"take": gen["take"], "language": "auto"}))
    assert job["status"] == "done", job
    project = studio.store.get(p["id"])
    assert project["lyrics"] == "[Verse]\nFirst line here.\nSecond line now!"
    alignment = project["takes"][0]["alignment"]
    assert alignment["source"] == "asr+align" and alignment["matched"] == alignment["total"] == 6


def test_bad_requests_fail_cleanly(studio):
    p = studio.store.create("x")
    with pytest.raises(StudioError):
        studio.submit(p["id"], "nonsense", {})
    gen = _wait(studio, studio.submit(p["id"], "generate", {"engine": "ace_step"}))
    j = _wait(studio, studio.submit(p["id"], "repaint", {"take": gen["take"], "start": 1.0, "end": 1.2}))
    assert j["status"] == "failed" and "half a second" in j["error"]
    with pytest.raises(StudioError):
        studio.store.get("../../etc")


def test_long_sources_are_windowed_for_ace_step(studio, monkeypatch):
    """ACE-Step's encoder cannot take >~28 s on Vulkan: repaint uses local windows, cover uses chunks."""
    from music import studio as studio_mod
    p = studio.store.create("Long")
    rate = 48000
    long_audio = np.tile((0.1 * np.sin(np.arange(rate) / 10)).astype(np.float32)[:, None], (60, 2))  # 60 s
    take = studio.store.add_take(p["id"], long_audio, rate, op="import", label="long")
    seen = []

    def fake_ace(st, audio, r, name, text, lyrics_text, seed, opts):
        seen.append((len(audio) / r, dict(opts)))
        assert len(audio) / r <= studio_mod.ACE_MAX_INPUT_S + 8.5   # region 14 s + 2 × 4 s context
        return np.full_like(audio, 0.5)

    monkeypatch.setattr(studio_mod, "_ace_on_audio", fake_ace)
    rep = _wait(studio, studio.submit(p["id"], "repaint", {"take": take["id"], "start": 5.0, "end": 40.0}))
    assert rep["status"] == "done", rep
    assert len(seen) == 3                                        # 14 + 14 + 7 s windows
    assert seen[0][1]["repainting_start"] == 4.0 and seen[0][1]["repainting_end"] == 18.0
    out, _ = studio.store.load_take(p["id"], rep["take"])
    stored, _ = studio.store.load_take(p["id"], take["id"])  # the 24-bit original on disk
    assert np.array_equal(out[: int(4.9 * rate)], stored[: int(4.9 * rate)])
    assert np.array_equal(out[int(40.1 * rate):], stored[int(40.1 * rate):])
    inside = np.r_[int(6 * rate):int(18.8 * rate), int(19.1 * rate):int(32.8 * rate), int(33.1 * rate):int(39 * rate)]
    assert np.allclose(out[inside], 0.5)                          # all new material between the seams
    seam = out[int(18.85 * rate):int(19.05 * rate)]
    assert np.all(seam >= 0.49)                                   # seams blend new into new, never the original

    seen.clear()
    cov = _wait(studio, studio.submit(p["id"], "cover", {"source_take": take["id"], "engine": "ace_step"}))
    assert cov["status"] == "done", cov
    assert len(seen) == 4 and all(d <= 20.0 + 1e-6 for d, _ in seen)
    covered = studio.store.find_take(studio.store.get(p["id"]), cov["take"])
    assert covered["duration"] == pytest.approx(60.0, abs=0.01) and covered["chunks"] == 4


def test_sfx_variations_inpaint_and_overlay(studio):
    studio.runtime.roles.add("sfx")
    original_run = studio.runtime.run_task

    def run_task(role, request, include_artifacts=False, timeout=0):
        if role == "sfx":
            studio.runtime.calls.append((role, request))
            seconds = request["options"]["duration_sec"]
            return {"audio": _b64(_wav(seconds, 44100, freq=880))}
        return original_run(role, request, include_artifacts, timeout)

    studio.runtime.run_task = run_task
    p = studio.store.create("Foley")
    job = _wait(studio, studio.submit(p["id"], "sfx", {"prompt": "door creaks open", "duration": 3, "variations": 3, "seed": 10}))
    assert job["status"] == "done", job
    takes = studio.store.get(p["id"])["takes"]
    assert [t["seed"] for t in takes] == [10, 11, 12] and all(t["sfx"] for t in takes)
    assert takes[0]["duration"] == pytest.approx(3.0, abs=0.01) and takes[0]["label"].startswith("SFX · door creaks")
    assert studio.runtime.calls[-1][1]["options"]["prompt"] == "door creaks open"

    var = _wait(studio, studio.submit(p["id"], "sfx", {"prompt": "heavier door", "mode": "variation", "take": takes[0]["id"], "strength": 0.4}))
    assert var["status"] == "done", var
    opts = studio.runtime.calls[-1][1]["options"]
    assert opts["audio_input_kind"] == "init_audio" and opts["init_noise_level"] == 0.4 and "audio" in studio.runtime.calls[-1][1]

    inp = _wait(studio, studio.submit(p["id"], "sfx", {"prompt": "squeak", "mode": "inpaint", "take": takes[0]["id"], "start": 1.0, "end": 2.0}))
    assert inp["status"] == "done", inp
    src, rate = studio.store.load_take(p["id"], takes[0]["id"])
    out, _ = studio.store.load_take(p["id"], inp["take"])
    assert np.array_equal(out[: int(0.95 * rate)], src[: int(0.95 * rate)])

    song = _wait(studio, studio.submit(p["id"], "generate", {"engine": "ace_step"}))
    mix = _wait(studio, studio.submit(p["id"], "overlay", {"take": song["take"], "layer_take": takes[0]["id"], "at": 3.0, "gain_db": -6}))
    assert mix["status"] == "done", mix
    mixed = studio.store.find_take(studio.store.get(p["id"]), mix["take"])
    assert mixed["duration"] == pytest.approx(6.0, abs=0.02)   # 4 s song extended by the 3 s effect placed at 3 s

    bad = _wait(studio, studio.submit(p["id"], "sfx", {"prompt": " "}))
    assert bad["status"] == "failed" and "Describe" in bad["error"]


def test_stable_audio_prefers_medium_f16(tmp_path):
    d = tmp_path / "Music"
    d.mkdir()
    for f in ("stable-audio-3-small-sfx-q8_0.gguf", "stable-audio-3-medium-q8_0.gguf", "stable-audio-3-medium-f16.gguf"):
        (d / f).write_bytes(b"x")
    assert find_packages([tmp_path])["sfx"].path.name == "stable-audio-3-medium-f16.gguf"


class _FakeServer:
    def __init__(self, name):
        self.name, self.configs, self.posts, self.running, self.stopped = name, [], [], False, 0
        outer = self

        class _Http:
            def post(self, url, json=None, **_kw):
                outer.posts.append((url, json))

                class _R:
                    status_code = 200

                    @staticmethod
                    def json():
                        return {"result": {"server": outer.name}}
                return _R()
        self.http = _Http()

    def ensure(self, exe, config):
        self.configs.append(config)
        self.running = True
        return f"http://{self.name}"

    def stop(self):
        self.running = False
        self.stopped += 1


def _runtime_with_fakes(tmp_path, monkeypatch, backend="auto"):
    import music.runtime as rt
    d = tmp_path / "Models"
    d.mkdir()
    for f in ("universr-speech-f16.gguf", "sidon-q8_0.gguf", "nemotron-3-diarization-q8_0.gguf"):
        (d / f).write_bytes(b"x")
    exe = tmp_path / "v0.9.3-vulkan-portable" / "audiocpp_server.exe"
    monkeypatch.setattr(rt, "find_server_executable", lambda _d="": exe)
    gpu, cpu = _FakeServer("gpu"), _FakeServer("cpu")
    runtime = rt.MusicRuntime(lambda: [tmp_path], lambda: {"music_backend": backend}, server=gpu, cpu_server=cpu)
    return runtime, gpu, cpu


def test_universr_runs_on_a_cpu_server_beside_vulkan(tmp_path, monkeypatch):
    runtime, gpu, cpu = _runtime_with_fakes(tmp_path, monkeypatch)
    assert set(runtime.packages) == {"bwe", "restore"}  # the old diarizer pattern is gone
    assert runtime.backend() == "vulkan" and runtime.backend("bwe") == "cpu"
    assert runtime.run_task("bwe", {"audio": "a.wav"}) == {"server": "cpu"}
    assert runtime.run_task("restore", {"audio": "a.wav"}) == {"server": "gpu"}
    assert cpu.configs[-1]["backend"] == "cpu" and [m["id"] for m in cpu.configs[-1]["models"]] == ["vv-bwe"]
    assert gpu.configs[-1]["backend"] == "vulkan" and [m["id"] for m in gpu.configs[-1]["models"]] == ["vv-restore"]
    assert runtime.status()["running"]
    runtime.stop()
    assert gpu.stopped == 1 and cpu.stopped == 1 and not runtime.status()["running"]


def test_cpu_backend_keeps_everything_on_one_server(tmp_path, monkeypatch):
    runtime, gpu, cpu = _runtime_with_fakes(tmp_path, monkeypatch, backend="cpu")
    assert runtime.run_task("bwe", {"audio": "a.wav"}) == {"server": "gpu"}
    assert not cpu.configs
    assert {m["id"] for m in gpu.configs[-1]["models"]} == {"vv-bwe", "vv-restore"}


def test_bandwidth_runs_in_overlapping_pieces_and_keeps_length(tmp_path):
    from music import cleanup
    rate = 48000
    t = np.arange(int(20.0 * rate)) / rate
    audio = (0.2 * np.sin(2 * np.pi * 200 * t)).astype(np.float32)[:, None]
    seen = []

    def run_tool(role, request):
        data, r = sf.read(request["audio"], dtype="float32", always_2d=True)
        seen.append((role, len(data) / r, request["options"]["input_sample_rate"]))
        return {"audio": _b64(_wav(len(data) / r, 48000, channels=1, freq=200))}

    out, out_rate, _notes = cleanup.clean(audio, rate, ["bandwidth"], run_tool, tmp_path, source_rate=16000)
    assert out_rate == 48000 and len(out) == len(audio)
    assert all(role == "bwe" and band == 16000 for role, _s, band in seen)
    # 20 s -> pieces of 8 s stepping 7.75 s; the 4.5 s tail is long enough to stand alone.
    assert [round(s, 2) for _r, s, _b in seen] == [8.0, 8.0, 4.5]
    assert float(np.max(np.abs(out))) > 0.1
    steps = {s["id"]: s for s in cleanup.available_steps(lambda r: r == "bwe", True)}
    assert steps["bandwidth"]["available"] and "CPU" in steps["bandwidth"]["note"]
    assert steps["restore"]["reason"] == "The Sidon model is not installed."
