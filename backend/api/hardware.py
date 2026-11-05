"""
api/hardware.py — Hardware detection endpoint
Detects GPU, VRAM, acceleration type (ROCm/CUDA/Metal/CPU)
"""

from fastapi import APIRouter
from pydantic import BaseModel
from gpu.detector import detect_hardware

router = APIRouter()

class GPUInfo(BaseModel):
    index: int
    name: str
    vram_total_mb: float
    vram_free_mb: float

class HardwareInfo(BaseModel):
    acceleration: str          # "rocm" | "cuda" | "metal" | "cpu"
    torch_version: str
    gpus: list[GPUInfo]
    recommended_device: str    # "cuda" | "cpu"
    ffmpeg_available: bool
    ffmpeg_hw_encoder: str     # "h264_amf" | "h264_nvenc" | "h264_videotoolbox" | "libx264"
    whisper_device: str
    tts_device: str

@router.get("/", response_model=HardwareInfo)
async def get_hardware():
    return await detect_hardware()
