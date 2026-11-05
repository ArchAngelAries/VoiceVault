"""engines/gpu_probe.py — per-process memoised PyTorch GPU checks for engines' runtime_problem().

Engines ask "can PyTorch see a GPU?" every time the model registry is (re)discovered.
The first call imports torch (seconds on ROCm) and initialises the HIP/CUDA runtime;
the answer cannot change while the process runs, so it is computed once.
"""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def torch_gpu_available() -> bool:
    """True when PyTorch has a CUDA/ROCm device. False if torch is missing or broken."""
    try:
        import torch
    except Exception:
        return False
    try:
        return bool(torch.cuda.is_available())
    except Exception:
        return False


@lru_cache(maxsize=1)
def torch_bf16_supported() -> bool:
    """True when the PyTorch GPU supports bfloat16."""
    if not torch_gpu_available():
        return False
    import torch
    try:
        return bool(torch.cuda.is_bf16_supported())
    except Exception:
        return False
