@echo off
setlocal
set "PATH=%SystemRoot%\System32;%PATH%"

set VVROOT=%~dp0

set "VVPYTHON=%VOICEVAULT_PYTHON%"
if not defined VVPYTHON set "VVPYTHON=%VVROOT%venv\Scripts\python.exe"
if not exist "%VVPYTHON%" (
    echo ERROR: Python environment not found. Run setup.bat or set VOICEVAULT_PYTHON to a Python executable.
    pause & exit /b 1
)

REM MIOpen FIND_MODE=2: measured faster for OmniVoice cloning on the RX 7900 XT (2026-10-09).
REM Affects only this backend process. Remove the line to return to the defaults.
set MIOPEN_FIND_MODE=2
echo Starting VoiceVault backend on http://127.0.0.1:7763
cd /d "%VVROOT%backend"
"%VVPYTHON%" -m uvicorn main:app --host 127.0.0.1 --port 7763
