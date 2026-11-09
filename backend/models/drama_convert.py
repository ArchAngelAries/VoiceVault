"""
models/drama_convert.py — One-click Drama 3: download Fish Audio's official final checkpoint and convert it
locally into the audio.cpp GGUF packages VoiceVault uses. Runs inside the downloader process (models/fetch.py).

Nothing here is redistributed by VoiceVault:
  - the checkpoint comes from Fish Audio on Hugging Face (fishaudio/drama-3, pinned revision);
  - the converter is audio.cpp's own convert_fish_drama3.py, fetched from GitHub at a pinned commit and
    checked against its SHA-256 (Apache-2.0);
  - the gguf Python package comes from PyPI (wheel checked against PyPI's SHA-256), unpacked into a side
    folder so the shared venv is unchanged;
  - audiocpp_gguf comes with the audio.cpp runtime the user already has.
Packages (same recipes as the verified 2026-11-04 local builds):
  Q4  routed experts Q4_K, backbone Q8_0, text embedding F16              (~25 GB, default)
  Q6  routed experts Q6_K, routers/gates/speech head F16, backbone Q8_0   (~34 GB, "max quality", optional)
Peak disk use: checkpoint 41.5 GB + F16 intermediate ~68 GB + packages. Temporary files are removed at the end.
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

CHECKPOINT_REPO = "fishaudio/drama-3"
CHECKPOINT_REVISION = "78e5ab79e49aa9a25abe8799eff2c4d73a1c3216"
AUDIOCPP_REPO = "0xShug0/audio.cpp"
AUDIOCPP_COMMIT = "22cad6af5043a2d7c7f1d9a0e6c9d7996a80dfc8"
CONVERTER_PATH = "tools/community_models/convert_fish_drama3.py"
CONVERTER_SHA256 = "90c481f61775242ce50981e24884cf0d53dad41fc4588a6a6ff3060f2811af9b"
GGUF_WHEEL_URL = "https://files.pythonhosted.org/packages/py3/g/gguf/gguf-0.19.0-py3-none-any.whl"
GGUF_WHEEL_SHA256 = "70bcd10edfe697fb2dad6e40af2234b9d8ece9a41a99761405121ebda1c3c1cd"
Q4_NAME = "drama-3-final-experts-Q4_K-backbone-Q8_0-embed-F16.gguf"
Q6_NAME = "drama-3-final-experts-Q6_K-backbone-Q8_0-embed-F16-routers-head-F16.gguf"
CHECKPOINT_BYTES = 41_500_000_000
INTERMEDIATE_BYTES = 73_000_000_000
Q4_BYTES = 25_100_000_000
Q6_BYTES = 33_900_000_000
SIDE_FILES = ("config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "LICENSE",
              "chat_template.jinja")


# Sizes the user can build. All use the same proven conversion method (routers, gates and speech head kept
# at full precision, only the routed experts change). Q4 and Q6 have been built and listened to on the
# project's test PC; the others haven't, and the app says so. Smaller levels can lose quality.
LEVELS: dict[str, dict] = {
    "q2_k": {"label": "Q2", "bytes": 17_100_000_000, "tested": False, "warning": True,
             "note": "Smallest. Quality loss is likely: voices can sound flat, less like the reference, or garbled. Same proven method as Q4 and Q6; not yet tested on our test PC."},
    "q3_k": {"label": "Q3", "bytes": 20_600_000_000, "tested": False, "warning": True,
             "note": "Smaller, for 8–12 GB graphics cards. Some quality loss is likely. Same proven method as Q4 and Q6; not yet tested on our test PC."},
    "q4_k": {"label": "Q4", "bytes": Q4_BYTES, "tested": True, "warning": False,
             "note": "Recommended. Built and listened to on our test PC: close to the original."},
    "q5_k": {"label": "Q5", "bytes": 29_400_000_000, "tested": False, "warning": False,
             "note": "Between Q4 and Q6. Same proven method as Q4 and Q6; not yet tested on our test PC."},
    "q6_k": {"label": "Q6", "bytes": Q6_BYTES, "tested": True, "warning": False,
             "note": "Max quality; built and listened to on our test PC. Slower voice."},
    "q8_0": {"label": "Q8", "bytes": 42_200_000_000, "tested": False, "warning": False,
             "note": "Closest to the original; largest and slowest. Same proven method as Q4 and Q6; not yet tested on our test PC."},
}
DEFAULT_LEVELS = ["q4_k"]


def level_name(level: str) -> str:
    if level == "q4_k":
        return Q4_NAME
    if level == "q6_k":
        return Q6_NAME
    tag = {"q2_k": "Q2_K", "q3_k": "Q3_K", "q5_k": "Q5_K", "q8_0": "Q8_0"}[level]
    return f"drama-3-final-experts-{tag}-backbone-Q8_0-embed-F16-routers-head-F16.gguf"


def clean_levels(levels, also_max: bool = False) -> list[str]:
    """Q4 is always built (it's the default package); add the chosen extra sizes, in a fixed order."""
    chosen = set(DEFAULT_LEVELS) | {lv for lv in (levels or []) if lv in LEVELS}
    if also_max:
        chosen.add("q6_k")
    return [lv for lv in LEVELS if lv in chosen]


def disk_needed(also_max: bool = False, levels=None, have_checkpoint: bool = False) -> int:
    chosen = clean_levels(levels, also_max)
    return ((0 if have_checkpoint else CHECKPOINT_BYTES) + INTERMEDIATE_BYTES
            + sum(LEVELS[lv]["bytes"] for lv in chosen) + 5_000_000_000)


def _fetch(url: str, sha256: str) -> bytes:
    data = urllib.request.urlopen(url, timeout=120).read()
    got = hashlib.sha256(data).hexdigest()
    if got != sha256:
        raise RuntimeError(f"Checksum mismatch for {url}: expected {sha256}, got {got}.")
    return data


def prepare_tools(work: Path, say: Callable[[str], None]) -> tuple[Path, Path]:
    """(converter script, pylib folder with gguf)."""
    tools = work / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    conv = tools / "convert_fish_drama3.py"
    if not conv.is_file() or hashlib.sha256(conv.read_bytes()).hexdigest() != CONVERTER_SHA256:
        say("Fetching audio.cpp's converter (GitHub, pinned commit)…")
        conv.write_bytes(_fetch(f"https://raw.githubusercontent.com/{AUDIOCPP_REPO}/{AUDIOCPP_COMMIT}/{CONVERTER_PATH}",
                                CONVERTER_SHA256))
    pylib = tools / "pylib"
    if not (pylib / "gguf" / "__init__.py").is_file():
        say("Fetching the gguf package (PyPI)…")
        with zipfile.ZipFile(io.BytesIO(_fetch(GGUF_WHEEL_URL, GGUF_WHEEL_SHA256))) as z:
            z.extractall(pylib)
    return conv, pylib


def download_checkpoint(work: Path, token, say: Callable[[str], None]) -> Path:
    from huggingface_hub import snapshot_download

    say("Downloading Fish Audio's official Drama 3 checkpoint (41.5 GB; resumes if interrupted)…")
    root = Path(snapshot_download(CHECKPOINT_REPO, revision=CHECKPOINT_REVISION, local_dir=str(work / "drama-3-final"),
                                  token=token))
    missing = [f"model-{i:05d}-of-00015.safetensors" for i in range(1, 16) if not (root / f"model-{i:05d}-of-00015.safetensors").is_file()]
    if missing or not (root / "codec.pth").is_file():
        raise RuntimeError("The checkpoint download is incomplete; start again to resume it.")
    return root


def q6_command(tool: Path, intermediate: Path, out: Path, checkpoint: Path, spec: Path) -> list[str]:
    return level_command(tool, intermediate, out, checkpoint, spec, "q6_k")


def level_command(tool: Path, intermediate: Path, out: Path, checkpoint: Path, spec: Path, level: str) -> list[str]:
    """Routed experts at `level`; routers, gates and the speech head stay at full precision (the verified Q6 recipe)."""
    cmd = [str(tool), "--input", str(intermediate), "--output", str(out), "--type", "q8_0", "--family", "fish_drama3",
           "--model-spec", str(spec), "--root", str(checkpoint),
           "--fold-weight-norm", "codec_weights*", "--keep-type", "codec_weights*=f16",
           "--keep-type", "model_weights/audio_decoder*=orig",
           "--keep-type", "model_weights/model.language_model.embed_tokens.weight=f16",
           "--keep-type", "model_weights/lm_head.weight*=orig"]
    for i in range(40):
        cmd += ["--keep-type", f"model_weights/model.language_model.layers.{i}.mlp.experts.*={level}",
                "--keep-type", f"model_weights/model.language_model.layers.{i}.mlp.gate.weight*=orig",
                "--keep-type", f"model_weights/model.language_model.layers.{i}.mlp.shared_expert_gate.weight*=orig"]
    for name in SIDE_FILES:
        if (checkpoint / name).is_file():
            cmd += ["--sidecar", f"{checkpoint / name}={name}"]
    return cmd


NOTICE = """These Fish Audio Materials are licensed under the Fish Audio Community License, Version 1.0.
Copyright (c) 39 AI, INC. All Rights Reserved.
Built with Fish Audio.

Converted on this computer by VoiceVault (not distributed) from fishaudio/drama-3 revision {rev},
with audio.cpp's convert_fish_drama3.py (commit {commit}, Apache-2.0) and audiocpp_gguf:
FP8 routed experts dequantised with their scales and requantised ({experts}); dense backbone Q8_0;
text embedding F16; codec weight norm folded (F16).
"""


def run(models_dir: Path, dest: str, token, also_max: bool, keep_checkpoint: bool,
        say: Callable[[str], None], levels=None) -> list[str]:
    levels = clean_levels(levels, also_max)
    if all((models_dir / dest / level_name(lv)).is_file() for lv in levels):
        say("Every chosen size is built already; nothing to do.")
        return [str(models_dir / dest / level_name(lv)) for lv in levels]
    from engines.audiocpp_server import find_server_executable

    exe = find_server_executable("")
    tool = exe.parent / ("audiocpp_gguf.exe" if os.name == "nt" else "audiocpp_gguf") if exe else None
    spec = exe.parent / "model_specs" / "fish_drama3.json" if exe else None
    if tool is None or not tool.is_file() or spec is None or not spec.is_file():
        raise RuntimeError("The audio.cpp runtime (with audiocpp_gguf) is needed to convert Drama 3. Install it first.")
    out_dir = models_dir / dest
    out_dir.mkdir(parents=True, exist_ok=True)
    work = models_dir / ".drama3-work"
    free = shutil.disk_usage(models_dir).free
    need = disk_needed(levels=levels) - sum(f.stat().st_size for f in work.rglob("*") if f.is_file()) if work.exists() else disk_needed(levels=levels)
    need -= sum((out_dir / level_name(lv)).stat().st_size for lv in levels if (out_dir / level_name(lv)).is_file())
    if free < need:
        raise RuntimeError(f"Not enough disk space: converting needs about {need / 1e9:.0f} GB free, {free / 1e9:.0f} GB available.")
    conv, pylib = prepare_tools(work, say)
    checkpoint = download_checkpoint(work, token, say)
    intermediate = work / "drama3-final-f16.gguf"
    q4 = out_dir / Q4_NAME
    files = []
    if not q4.is_file():
        say("Step 3/5: converting to the Q4 package (CPU; this takes a while)…")
        args = [sys.executable, "-u", str(conv), "--checkpoint", str(checkpoint), "--quantized-output", str(q4),
                "--gguf-tool", str(tool), "--expert-type", "q4_k", "--backbone-type", "q8_0", "--embedding-type", "f16",
                "--threads", str(max(2, os.cpu_count() or 4))]
        args += ["--input-gguf", str(intermediate)] if intermediate.is_file() else ["--output", str(intermediate)]
        env = {**os.environ, "PYTHONPATH": str(pylib) + os.pathsep + os.environ.get("PYTHONPATH", "")}
        r = subprocess.run(args, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0 or not q4.is_file():
            raise RuntimeError(f"The Drama 3 converter stopped (exit {r.returncode}). Start again to retry; finished steps are kept.")
    files.append(str(q4))
    extra = [lv for lv in levels if lv != "q4_k" and not (out_dir / level_name(lv)).is_file()]
    if extra and not intermediate.is_file():
        # Q4 already exists, so make the full-precision intermediate again (Q4 output goes to a scratch file).
        say("Preparing the full-precision intermediate for the other sizes (CPU; this takes a while)…")
        scratch = work / "q4-scratch.gguf"
        args = [sys.executable, "-u", str(conv), "--checkpoint", str(checkpoint), "--quantized-output", str(scratch),
                "--gguf-tool", str(tool), "--expert-type", "q4_k", "--backbone-type", "q8_0", "--embedding-type", "f16",
                "--threads", str(max(2, os.cpu_count() or 4)), "--output", str(intermediate)]
        env = {**os.environ, "PYTHONPATH": str(pylib) + os.pathsep + os.environ.get("PYTHONPATH", "")}
        r = subprocess.run(args, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        scratch.unlink(missing_ok=True)
        if r.returncode != 0 or not intermediate.is_file():
            raise RuntimeError(f"The Drama 3 converter stopped (exit {r.returncode}). Start again to retry; finished steps are kept.")
    for lv in [lv for lv in levels if lv != "q4_k"]:
        out = out_dir / level_name(lv)
        if not out.is_file():
            say(f"Step 4/5: building the {LEVELS[lv]['label']} package…")
            r = subprocess.run(level_command(tool, intermediate, out, checkpoint, spec, lv),
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode != 0 or not out.is_file():
                raise RuntimeError(f"Building the {LEVELS[lv]['label']} package stopped (exit {r.returncode}). Start again to retry.")
        files.append(str(out))
    say("Step 5/5: writing licence notices and cleaning up temporary files…")
    if (checkpoint / "LICENSE").is_file():
        shutil.copyfile(checkpoint / "LICENSE", out_dir / "LICENSE")
        files.append(str(out_dir / "LICENSE"))
    (out_dir / "NOTICE").write_text(NOTICE.format(rev=CHECKPOINT_REVISION, commit=AUDIOCPP_COMMIT,
                                                  experts=", ".join(LEVELS[lv]["label"] + "_K" if lv != "q8_0" else "Q8_0" for lv in levels)), encoding="utf-8")
    files.append(str(out_dir / "NOTICE"))
    intermediate.unlink(missing_ok=True)
    if not keep_checkpoint:
        shutil.rmtree(work, ignore_errors=True)
    return files
