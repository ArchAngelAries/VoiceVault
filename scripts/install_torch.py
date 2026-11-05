"""
scripts/install_torch.py — Smart PyTorch installer
Detects GPU vendor and installs the correct PyTorch build.

Supported:
  AMD   Windows  → ROCm 7.2 wheels from repo.radeon.com
  NVIDIA         → PyTorch CUDA from pytorch.org index
  Apple Silicon  → PyTorch CPU/MPS from PyPI (Metal built-in)
  CPU fallback   → PyTorch CPU from PyPI
"""

import os
import subprocess
import sys
import platform

def run(cmd: list) -> int:
    print(f"\n  > {' '.join(cmd)}\n", flush=True)
    result = subprocess.run(cmd)
    return result.returncode

def install_command(*args, env=None) -> list:
    """pip by default; the desktop installer's environment has no pip and sets VOICEVAULT_UV to uv.exe."""
    uv = (os.environ if env is None else env).get("VOICEVAULT_UV", "").strip()
    if uv:
        return [uv, "pip", "install", "--python", sys.executable, *args]
    return [sys.executable, "-m", "pip", "install", "--no-cache-dir", *args]

def pip(*args) -> int:
    return run(install_command(*args))

def detect_gpu() -> tuple[str, str]:
    """
    Returns (vendor, method) where:
      vendor: "amd" | "nvidia" | "apple" | "cpu"
      method: human readable description
    """
    system = platform.system()

    # Apple Silicon — always MPS
    if system == "Darwin" and platform.machine() == "arm64":
        return "apple", "Apple Silicon (Metal/MPS)"

    # Windows: PowerShell/CIM first. wmic is deprecated and missing on current Windows 11 builds.
    if system == "Windows":
        for cmd in (
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"],
            ["wmic", "path", "win32_VideoController", "get", "name"],
        ):
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
                output = result.stdout.upper()
                if "AMD" in output or "RADEON" in output:
                    return "amd", "AMD Radeon (ROCm 7.2)"
                if "NVIDIA" in output or "GEFORCE" in output or "QUADRO" in output or "RTX" in output or "GTX" in output:
                    return "nvidia", "NVIDIA (CUDA)"
            except Exception:
                continue

    # Linux — try lspci
    if system == "Linux":
        try:
            result = subprocess.run(["lspci"], capture_output=True, text=True, timeout=10)
            output = result.stdout.upper()
            if "AMD" in output or "RADEON" in output or "ADVANCED MICRO" in output:
                return "amd", "AMD Radeon (ROCm)"
            if "NVIDIA" in output:
                return "nvidia", "NVIDIA (CUDA)"
        except Exception:
            pass

    return "cpu", "CPU only (no GPU detected)"


def install_amd_windows():
    print("  Installing ROCm 7.2 SDK...")
    rc = pip(
        "https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/rocm_sdk_core-7.2.1-py3-none-win_amd64.whl",
        "https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/rocm_sdk_devel-7.2.1-py3-none-win_amd64.whl",
        "https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/rocm_sdk_libraries_custom-7.2.1-py3-none-win_amd64.whl",
        "https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/rocm-7.2.1.tar.gz",
    )
    if rc != 0:
        return rc

    print("  Installing PyTorch 2.9.1 + ROCm 7.2...")
    return pip(
        "https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/torch-2.9.1+rocm7.2.1-cp312-cp312-win_amd64.whl",
        "https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/torchaudio-2.9.1+rocm7.2.1-cp312-cp312-win_amd64.whl",
        "https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/torchvision-0.24.1+rocm7.2.1-cp312-cp312-win_amd64.whl",
    )


def install_amd_linux():
    print("  Installing PyTorch with ROCm 6.2 (Linux)...")
    return pip(
        "--index-url", "https://download.pytorch.org/whl/rocm6.2",
        "torch", "torchaudio", "torchvision",
    )


def install_nvidia():
    print("  Installing PyTorch with CUDA 12.4...")
    return pip(
        "--index-url", "https://download.pytorch.org/whl/cu124",
        "torch", "torchaudio", "torchvision",
    )


def install_cpu():
    print("  Installing PyTorch CPU build...")
    return pip("torch", "torchaudio", "torchvision")


def verify():
    print("\n  Verifying PyTorch install...")
    result = subprocess.run(
        [sys.executable, "-c",
         "import torch; "
         "print(f'  PyTorch: {torch.__version__}'); "
         "cuda = torch.cuda.is_available(); "
         "mps = hasattr(torch.backends,'mps') and torch.backends.mps.is_available(); "
         "print(f'  CUDA/ROCm: {cuda}'); "
         "print(f'  MPS: {mps}'); "
         "gpu = torch.cuda.get_device_name(0) if cuda else ('MPS' if mps else 'none'); "
         "print(f'  Device: {gpu}')"],
        timeout=30
    )
    return result.returncode


def existing_torch_version() -> str:
    """Version string of the torch already installed in this environment, or '' if none."""
    result = subprocess.run(
        [sys.executable, "-c", "import torch; print(torch.__version__)"],
        capture_output=True, text=True, timeout=120,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def main():
    print("\n  Detecting hardware...")
    vendor, description = detect_gpu()
    print(f"  Detected: {description}")
    system = platform.system()

    # Never replace a working GPU build. A failed GPU probe must not turn into a CPU reinstall.
    current = existing_torch_version()
    if "+rocm" in current or "+cu" in current:
        print(f"\n  Keeping the installed GPU build: torch {current}")
        if vendor == "cpu":
            print("  (GPU probe found no AMD or NVIDIA adapter; the existing build was left untouched.)")
        verify()
        print("\n  PyTorch install complete (existing build kept)!")
        return

    if vendor == "amd" and system == "Windows":
        rc = install_amd_windows()
    elif vendor == "amd" and system == "Linux":
        rc = install_amd_linux()
    elif vendor == "nvidia":
        rc = install_nvidia()
    elif vendor == "apple":
        rc = install_cpu()   # PyTorch standard includes Metal/MPS
    else:
        rc = install_cpu()

    if rc != 0:
        print(f"\n  ERROR: PyTorch install failed (exit code {rc})")
        sys.exit(rc)

    verify()
    print("\n  PyTorch install complete!")


if __name__ == "__main__":
    main()
