"""
models/fetch.py — Downloader process for one catalog entry (run by models.manager; reads its spec on stdin).

Uses huggingface_hub, which resumes partial files and checks each file against the Hub's metadata.
After each file, the size on disk is compared with the size the Hub reports. Prints "RESULT {json}" last.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path


def _hf_files(src: dict, models_dir: Path, token) -> list[str]:
    from huggingface_hub import HfApi, hf_hub_download

    dest_root = models_dir / (src.get("dest") or "")
    sizes = {i.path: getattr(i, "size", None) for i in HfApi(token=token).get_paths_info(src["repo"], src["files"],
                                                                                          revision=src.get("revision"))}
    out = []
    for f in src["files"]:
        # Repos that keep packages in folders (audio-cpp/audio.cpp-gguf) already carry the folder name.
        local_dir = models_dir if (not src.get("dest") or f.startswith(src["dest"] + "/")) else dest_root
        print(f"FILE {f}", flush=True)
        path = Path(hf_hub_download(src["repo"], f, revision=src.get("revision"), local_dir=str(local_dir), token=token))
        want = sizes.get(f)
        if want and path.stat().st_size != want:
            raise RuntimeError(f"{f}: size {path.stat().st_size} does not match the Hub's {want}.")
        out.append(str(path))
    return out


def _hf_snapshot(src: dict, models_dir: Path, token, cache: bool) -> list[str]:
    from huggingface_hub import snapshot_download

    kwargs = {"repo_id": src["repo"], "revision": src.get("revision"), "token": token,
              "allow_patterns": src.get("allow") or None}
    if not cache:
        kwargs["local_dir"] = str(models_dir / src["dest"])
    print(f"REPO {src['repo']}", flush=True)
    root = Path(snapshot_download(**kwargs))
    if cache:
        return []   # the shared Hugging Face cache is not "owned" by VoiceVault; removal is left to the user
    return [str(p) for p in root.rglob("*") if p.is_file() and ".cache" not in p.parts]


def _espeak(src: dict, runtime_dir: Path) -> list[str]:
    if os.name != "nt":
        raise RuntimeError("Install eSpeak NG with your system's package manager (for example: apt install espeak-ng).")
    base = runtime_dir / "espeak-ng"
    dl = base / "download"
    dl.mkdir(parents=True, exist_ok=True)
    msi = dl / f"espeak-ng-{src['dest']}.msi"
    if not msi.is_file():
        print("FILE espeak-ng.msi", flush=True)
        urllib.request.urlretrieve(src["url"], msi)
    target = base / src["dest"]
    # An administrative extraction unpacks the files without installing anything system-wide.
    r = subprocess.run(["msiexec", "/a", str(msi), "/qn", f"TARGETDIR={target}"],
                       capture_output=True, timeout=600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    lib = target / "eSpeak NG" / "libespeak-ng.dll"
    if r.returncode != 0 or not lib.is_file():
        raise RuntimeError(f"Unpacking eSpeak NG failed (msiexec {r.returncode}).")
    return [str(p) for p in target.rglob("*") if p.is_file()] + [str(msi)]


def main() -> None:
    spec = json.loads(sys.stdin.read())
    models_dir = Path(spec["models_dir"])
    models_dir.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    try:
        for src in spec["sources"]:
            kind = src["kind"]
            if kind == "hf_files":
                files += _hf_files(src, models_dir, spec.get("token"))
            elif kind == "hf_cache":
                files += _hf_snapshot(src, models_dir, spec.get("token"), cache=True)
            elif kind == "hf_local":
                files += _hf_snapshot(src, models_dir, spec.get("token"), cache=False)
            elif kind == "espeak_msi":
                files += _espeak(src, Path(spec["runtime_dir"]))
            elif kind == "drama_convert":
                from models import drama_convert
                opts = spec.get("options") or {}
                files += drama_convert.run(models_dir, src["dest"], spec.get("token"), bool(opts.get("also_max")),
                                           bool(opts.get("keep_checkpoint")), lambda m: print("STAGE " + m, flush=True),
                                           levels=opts.get("levels"))
            else:
                raise RuntimeError(f"Source kind {kind} can't be downloaded automatically.")
        print("RESULT " + json.dumps({"ok": True, "files": files}), flush=True)
    except Exception as exc:  # report to the manager; the process exit code marks the failure
        msg = f"{exc.__class__.__name__}: {exc}"
        if "401" in msg or "403" in msg or "gated" in msg.lower():
            msg += " — this model may need you to accept its terms on Hugging Face and add a read token in Settings."
        print("RESULT " + json.dumps({"ok": False, "error": msg[:600], "files": files}), flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
