# Third-party software in the VoiceVault installer

VoiceVault's own code is under the licence in `LICENSE`. The Windows installer also contains the programs below, each under its own licence. Licence texts ship next to each program.

| Component | Where in the installed app | Licence | Source |
|---|---|---|---|
| Electron (includes Chromium and Node.js) | the app folder (`LICENSE.electron.txt`, `LICENSES.chromium.html`) | MIT; Chromium's bundled licences | <https://github.com/electron/electron> |
| audio.cpp v0.9.3 Vulkan portable build, with the Microsoft Visual C++ runtime DLLs it ships with | `resources/voicevault/runtime/audiocpp/v0.9.3-vulkan-portable/` (`LICENSE`) | Apache-2.0 (runtime DLLs: Microsoft Visual C++ Redistributable terms) | <https://github.com/0xShug0/audio.cpp> |
| uv | `resources/voicevault/uv/` (`LICENSE-MIT.txt`, `LICENSE-APACHE.txt`, `uv-source.json`) | MIT OR Apache-2.0 | <https://github.com/astral-sh/uv> |
| Vendored Breeze runtime code | `resources/voicevault/backend/engines/breeze_runtime/` | Apache-2.0 | see the `LICENSE` file there |
| Vendored VibeVoice code | `resources/voicevault/backend/engines/vibevoice/` | MIT | see the licence files there |
| Noto Sans JP (PDF export font) | inside the app archive (`dist/fonts`) | SIL Open Font License 1.1 (`OFL-NotoSansJP.txt`) | <https://fonts.google.com/noto> |

## Downloaded on first launch, not shipped

On first launch VoiceVault uses uv to download these into `%LOCALAPPDATA%\VoiceVault\python`. They come from their official sources under their own licences:

- **Python 3.12** (python-build-standalone builds, PSF licence);
- **PyTorch**: ROCm 7.2.1 wheels from repo.radeon.com on AMD graphics cards, CUDA wheels from download.pytorch.org on NVIDIA, otherwise the CPU build from PyPI;
- **the Python packages** listed in `backend/requirements.txt` and `backend/requirements-tts.txt`, from PyPI;
- **Prisma's query engine** (Apache-2.0), downloaded by the Prisma client when it is generated. On a PC without Node.js, the Prisma client also downloads a private copy of Node.js for this step.

## Installed by you, never shipped

- **Model weights**: downloaded in Settings → Models after you accept each licence. See `docs/MODEL_LICENSES.md`.
- **eSpeak NG** (GPL-3.0): needed only for Kokoro voices; Settings → Models installs it from its official release on request.
- **FFmpeg**: needed for audio import and conversion. Install it yourself and put it on your PATH, for example with `winget install Gyan.FFmpeg`.
