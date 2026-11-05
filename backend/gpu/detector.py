"""
gpu/detector.py — Hardware detection and acceleration layer
Detects ROCm, CUDA, Metal, or CPU and returns unified hardware info.
ROCm exposes itself as CUDA to PyTorch — torch.cuda.is_available() returns
True on ROCm. We check for ROCm specifically via torch version string.
"""

import asyncio
import subprocess
import shutil
from functools import lru_cache
from typing import Optional

async def detect_hardware() -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _detect_sync)

def _detect_sync() -> dict:
    torch_version, acceleration, gpus, device = _detect_torch()
    ffmpeg_available, ffmpeg_encoder = _detect_ffmpeg(acceleration)

    return {
        "acceleration":      acceleration,
        "torch_version":     torch_version,
        "gpus":              gpus,
        "recommended_device": device,
        "ffmpeg_available":  ffmpeg_available,
        "ffmpeg_hw_encoder": ffmpeg_encoder,
        "whisper_device":    device,
        "tts_device":        device,
    }

def _detect_torch() -> tuple[str, str, list, str]:
    try:
        import torch
        version = torch.__version__

        # ROCm check — torch version string contains "rocm" on ROCm builds
        if torch.cuda.is_available():
            acceleration = "rocm" if "rocm" in version.lower() else "cuda"
            device = "cuda"
            gpus = []
            for i in range(torch.cuda.device_count()):
                props = torch.cuda.get_device_properties(i)
                free, total = torch.cuda.mem_get_info(i)
                gpus.append({
                    "index":        i,
                    "name":         props.name,
                    "vram_total_mb": round(total / 1024 / 1024, 1),
                    "vram_free_mb":  round(free / 1024 / 1024, 1),
                })
            return version, acceleration, gpus, device

        # Apple Metal (MPS)
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return version, "metal", [], "mps"

        # CPU fallback
        return version, "cpu", [], "cpu"

    except ImportError:
        return "not_installed", "cpu", [], "cpu"

@lru_cache(maxsize=None)
def _detect_ffmpeg(acceleration: str) -> tuple[bool, str]:
    # Cached once per process (per acceleration type): spawning `ffmpeg -encoders`
    # on every hardware poll cost ~0.12 s. The per-poll work is only the VRAM query.
    if not shutil.which("ffmpeg"):
        return False, "none"

    # Map acceleration type to hardware encoder
    encoder_map = {
        "rocm":  "h264_amf",
        "cuda":  "h264_nvenc",
        "metal": "h264_videotoolbox",
        "cpu":   "libx264",
    }
    encoder = encoder_map.get(acceleration, "libx264")

    # Verify the encoder is actually available in this ffmpeg build
    try:
        result = subprocess.run(
            ["ffmpeg", "-encoders"],
            capture_output=True, text=True, timeout=10
        )
        encoder_name = encoder.replace("h264_", "")
        if encoder not in result.stdout:
            encoder = "libx264"   # graceful fallback
    except Exception:
        encoder = "libx264"

    return True, encoder
