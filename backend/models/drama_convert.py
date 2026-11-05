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


def disk_needed(also_max: bool) -> int:
    return CHECKPOINT_BYTES + INTERMEDIATE_BYTES + Q4_BYTES + (Q6_BYTES if also_max else 0) + 5_000_000_000


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
    cmd = [str(tool), "--input", str(intermediate), "--output", str(out), "--type", "q8_0", "--family", "fish_drama3",
           "--model-spec", str(spec), "--root", str(checkpoint),
           "--fold-weight-norm", "codec_weights*", "--keep-type", "codec_weights*=f16",
           "--keep-type", "model_weights/audio_decoder*=orig",
           "--keep-type", "model_weights/model.language_model.embed_tokens.weight=f16",
           "--keep-type", "model_weights/lm_head.weight*=orig"]
    for i in range(40):
        cmd += ["--keep-type", f"model_weights/model.language_model.layers.{i}.mlp.experts.*=q6_k",
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
        say: Callable[[str], None]) -> list[str]:
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
    need = disk_needed(also_max) - sum(f.stat().st_size for f in work.rglob("*") if f.is_file()) if work.exists() else disk_needed(also_max)
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
    if also_max:
        q6 = out_dir / Q6_NAME
        if not q6.is_file():
            say("Step 4/5: building the Max quality (Q6) package…")
            r = subprocess.run(q6_command(tool, intermediate, q6, checkpoint, spec),
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode != 0 or not q6.is_file():
                raise RuntimeError(f"Building the Q6 package stopped (exit {r.returncode}). Start again to retry.")
        files.append(str(q6))
    say("Step 5/5: writing licence notices and cleaning up temporary files…")
    if (checkpoint / "LICENSE").is_file():
        shutil.copyfile(checkpoint / "LICENSE", out_dir / "LICENSE")
        files.append(str(out_dir / "LICENSE"))
    (out_dir / "NOTICE").write_text(NOTICE.format(rev=CHECKPOINT_REVISION, commit=AUDIOCPP_COMMIT,
                                                  experts="Q4_K" + (" and Q6_K" if also_max else "")), encoding="utf-8")
    files.append(str(out_dir / "NOTICE"))
    intermediate.unlink(missing_ok=True)
    if not keep_checkpoint:
        shutil.rmtree(work, ignore_errors=True)
    return files
