"""install_torch: every GPU gets the PyTorch version the package lock was resolved against (GitHub issue #1)."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("install_torch", Path(__file__).with_name("install_torch.py"))
it = importlib.util.module_from_spec(spec)
spec.loader.exec_module(it)


def test_cuda_build_follows_the_driver():
    assert it.cuda_tag(572) == "cu128"
    assert it.cuda_tag(570) == "cu128"
    assert it.cuda_tag(566) == "cu126"
    assert it.cuda_tag(None) == "cu128"          # unknown driver: current default


def test_nvidia_and_cpu_install_the_pinned_version(monkeypatch):
    calls = []
    monkeypatch.setattr(it, "pip", lambda *args: calls.append(args) or 0)
    monkeypatch.setattr(it, "nvidia_driver_major", lambda: 572)
    it.install_nvidia()
    it.install_cpu()
    nvidia, cpu = calls
    assert nvidia[:2] == ("--index-url", "https://download.pytorch.org/whl/cu128")
    assert f"torch=={it.TORCH_VERSION}" in nvidia and f"torchvision=={it.TORCHVISION_VERSION}" in nvidia
    assert cpu == (f"torch=={it.TORCH_VERSION}", f"torchaudio=={it.TORCHAUDIO_VERSION}", f"torchvision=={it.TORCHVISION_VERSION}")


def test_only_the_right_gpu_build_is_kept():
    assert it.keep_existing(f"{it.TORCH_VERSION}+rocm7.2.1")
    assert it.keep_existing(f"{it.TORCH_VERSION}+cu128")
    assert not it.keep_existing("2.6.0+cu124")       # issue #1: replaced, not kept
    assert not it.keep_existing(it.TORCH_VERSION)    # a CPU build is never "kept" as a GPU build
    assert not it.keep_existing("")


def test_amd_wheels_match_the_pinned_version():
    source = Path(it.__file__).read_text(encoding="utf-8")
    assert f"torch-{it.TORCH_VERSION}+rocm" in source and f"torchvision-{it.TORCHVISION_VERSION}+rocm" in source


def test_a_wrong_cuda_build_is_reinstalled_for_nvidia(monkeypatch):
    calls = []
    monkeypatch.setattr(it, "detect_gpu", lambda: ("nvidia", "NVIDIA (CUDA)"))
    monkeypatch.setattr(it, "existing_torch_version", lambda: "2.6.0+cu124")
    monkeypatch.setattr(it, "nvidia_driver_major", lambda: 560)
    monkeypatch.setattr(it, "pip", lambda *args: calls.append(args) or 0)
    monkeypatch.setattr(it, "verify", lambda: 0)
    it.main()
    assert calls and calls[0][:2] == ("--index-url", "https://download.pytorch.org/whl/cu126")


def test_the_owners_amd_rocm_build_is_left_alone(monkeypatch):
    """The AMD path must not change: an installed torch 2.9.1+rocm7.2.1 is kept, nothing is reinstalled."""
    calls = []
    monkeypatch.setattr(it, "detect_gpu", lambda: ("amd", "AMD Radeon (ROCm 7.2)"))
    monkeypatch.setattr(it, "existing_torch_version", lambda: "2.9.1+rocm7.2.1")
    monkeypatch.setattr(it, "pip", lambda *args: calls.append(args) or 0)
    monkeypatch.setattr(it, "install_amd_windows", lambda: calls.append("amd") or 0)
    monkeypatch.setattr(it, "verify", lambda: 0)
    it.main()
    assert calls == []


def test_a_fresh_amd_windows_install_still_uses_the_rocm_wheels(monkeypatch):
    calls = []
    monkeypatch.setattr(it, "detect_gpu", lambda: ("amd", "AMD Radeon (ROCm 7.2)"))
    monkeypatch.setattr(it.platform, "system", lambda: "Windows")
    monkeypatch.setattr(it, "existing_torch_version", lambda: "")
    monkeypatch.setattr(it, "install_amd_windows", lambda: calls.append("amd") or 0)
    monkeypatch.setattr(it, "install_nvidia", lambda: calls.append("nvidia") or 0)
    monkeypatch.setattr(it, "verify", lambda: 0)
    it.main()
    assert calls == ["amd"]


def test_laptop_with_amd_processor_and_nvidia_card_gets_cuda(monkeypatch):
    import types
    monkeypatch.setattr(it.platform, "system", lambda: "Windows")
    monkeypatch.setattr(it.subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        stdout="AMD Radeon(TM) Graphics\nNVIDIA GeForce RTX 4070 Laptop GPU\n", returncode=0))
    assert it.detect_gpu()[0] == "nvidia"
    monkeypatch.setattr(it.subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        stdout="AMD Radeon RX 7900 XT\n", returncode=0))
    assert it.detect_gpu()[0] == "amd"                          # the owner's PC: still ROCm


def test_the_installed_build_decides_the_vendor_and_forces_a_reinstall(monkeypatch):
    calls = []
    monkeypatch.setattr(it, "REINSTALL", [])
    monkeypatch.setattr(it, "detect_gpu", lambda: ("amd", "AMD Radeon (ROCm 7.2)"))   # e.g. a Radeon iGPU probe
    monkeypatch.setattr(it, "existing_torch_version", lambda: "2.6.0+cu124")
    monkeypatch.setattr(it, "nvidia_driver_major", lambda: 572)
    monkeypatch.setattr(it, "install_amd_windows", lambda: calls.append("amd") or 0)
    monkeypatch.setattr(it, "install_nvidia", lambda: calls.append("nvidia") or 0)
    monkeypatch.setattr(it, "verify", lambda: 0)
    it.main()
    assert calls == ["nvidia"] and it.REINSTALL == ["--force-reinstall", "--no-deps"]


def test_a_cpu_build_of_the_right_version_is_upgraded_on_an_nvidia_pc(monkeypatch):
    calls = []
    monkeypatch.setattr(it, "REINSTALL", [])
    monkeypatch.setattr(it, "detect_gpu", lambda: ("nvidia", "NVIDIA (CUDA)"))
    monkeypatch.setattr(it, "existing_torch_version", lambda: "2.9.1+cpu")
    monkeypatch.setattr(it, "install_nvidia", lambda: calls.append("nvidia") or 0)
    monkeypatch.setattr(it, "verify", lambda: 0)
    it.main()
    assert calls == ["nvidia"] and "--force-reinstall" in it.REINSTALL
    assert it.install_command("torch==2.9.1", env={})[-1] == "torch==2.9.1"
