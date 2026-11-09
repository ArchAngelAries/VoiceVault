"""
scripts/install_torch.py — Smart PyTorch installer
Detects GPU vendor and installs the correct PyTorch build.

Supported:
  AMD   Windows  → ROCm 7.2 wheels from repo.radeon.com
  NVIDIA         → PyTorch CUDA 12.8 (12.6 for drivers older than 570) from the pytorch.org index
  Apple Silicon  → PyTorch CPU/MPS from PyPI (Metal built-in)
  CPU fallback   → PyTorch CPU from PyPI

Every build is the same PyTorch version (TORCH_VERSION). backend/requirements-lock.txt is resolved
against it, so a different version can make setup unsatisfiable (GitHub issue #1: torch 2.6 from the
CUDA 12.4 index needs sympy 1.13.1 while the lock pins 1.14.0).
"""

import os
import re
import subprocess
import sys
import platform

TORCH_VERSION = "2.9.1"
TORCHAUDIO_VERSION = "2.9.1"
TORCHVISION_VERSION = "0.24.1"
CUDA_INDEX = "https://download.pytorch.org/whl/{tag}"
CUDA_12_8_MIN_DRIVER = 570   # CUDA 12.8 (also needed for RTX 50-series) wants driver 570 or newer

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

REINSTALL: list[str] = []   # set by main() when a different torch build must be replaced


def pip(*args) -> int:
    return run(install_command(*REINSTALL, *args))

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
                # NVIDIA first: a laptop with an AMD processor (Radeon integrated graphics) and an NVIDIA
                # card must get CUDA. A PC with only an AMD card (no NVIDIA adapter) still gets ROCm.
                if "NVIDIA" in output or "GEFORCE" in output or "QUADRO" in output or "RTX" in output or "GTX" in output:
                    return "nvidia", "NVIDIA (CUDA)"
                if "AMD" in output or "RADEON" in output:
                    return "amd", "AMD Radeon (ROCm 7.2)"
            except Exception:
                continue

    # Linux — try lspci
    if system == "Linux":
        try:
            result = subprocess.run(["lspci"], capture_output=True, text=True, timeout=10)
            # Only display adapters: AMD processors also list "Advanced Micro Devices" host bridges.
            output = "\n".join(line for line in result.stdout.upper().splitlines() if "VGA" in line or "3D" in line or "DISPLAY" in line)
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


def nvidia_driver_major() -> int | None:
    """The NVIDIA driver's major version (for example 572), or None when nvidia-smi can't tell."""
    try:
        result = subprocess.run(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
                                capture_output=True, text=True, timeout=20)
        match = re.match(r"\s*(\d+)\.", result.stdout)
        return int(match.group(1)) if result.returncode == 0 and match else None
    except Exception:
        return None


def cuda_tag(driver_major: int | None) -> str:
    """cu128 for current drivers (and when the driver can't be read); cu126 for drivers older than 570."""
    return "cu126" if driver_major is not None and driver_major < CUDA_12_8_MIN_DRIVER else "cu128"


def install_nvidia():
    tag = cuda_tag(nvidia_driver_major())
    print(f"  Installing PyTorch {TORCH_VERSION} with CUDA {tag[2:4]}.{tag[4:]}...")
    if tag == "cu126":
        print("  (Your NVIDIA driver is older than 570; update it for CUDA 12.8 and RTX 50-series support.)")
    return pip(
        "--index-url", CUDA_INDEX.format(tag=tag),
        f"torch=={TORCH_VERSION}", f"torchaudio=={TORCHAUDIO_VERSION}", f"torchvision=={TORCHVISION_VERSION}",
    )


def install_cpu():
    print(f"  Installing PyTorch {TORCH_VERSION} CPU build...")
    return pip(f"torch=={TORCH_VERSION}", f"torchaudio=={TORCHAUDIO_VERSION}", f"torchvision=={TORCHVISION_VERSION}")


def keep_existing(current: str) -> bool:
    """Keep an installed GPU build only when it is the version VoiceVault is built for."""
    if not ("+rocm" in current or "+cu" in current):
        return False
    return current.split("+", 1)[0] == TORCH_VERSION


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

    # Never replace a working GPU build of the right version. A failed GPU probe must not turn into a
    # CPU reinstall. A GPU build of another version (for example torch 2.6 from an earlier setup) is
    # replaced, because the rest of VoiceVault's packages are resolved against TORCH_VERSION.
    current = existing_torch_version()
    if current and not keep_existing(current):
        print(f"\n  Replacing torch {current} with {TORCH_VERSION}, the build VoiceVault's packages need.")
        # The installed build says which card it was made for, more reliably than the adapter probe.
        if "+cu" in current:
            vendor = "nvidia"
        elif "+rocm" in current:
            vendor = "amd"
        # Without this, pip and uv see "2.9.1+cpu" as already satisfying torch==2.9.1 and do nothing.
        REINSTALL[:] = ["--force-reinstall", "--no-deps"]
    if keep_existing(current):
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
