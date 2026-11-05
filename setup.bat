@echo off
setlocal enabledelayedexpansion
set "VVROOT=%~dp0"

echo ============================================================
echo  VoiceVault Setup
echo ============================================================

REM Find Python 3.12; an existing project environment is reused and preserved.
set "VVPYTHON=%VOICEVAULT_PYTHON%"
if not defined VVPYTHON (
    where py >nul 2>&1
    if not errorlevel 1 (
        for /f "delims=" %%P in ('py -3.12 -c "import sys; print(sys.executable)" 2^>nul') do set "VVPYTHON=%%P"
    )
)
if not defined VVPYTHON (
    for /f "delims=" %%P in ('where python 2^>nul') do (
        if not defined VVPYTHON (
            "%%P" -c "import sys; raise SystemExit(0 if sys.version_info[:2]==(3,12) else 1)" >nul 2>&1
            if not errorlevel 1 set "VVPYTHON=%%P"
        )
    )
)
if exist "%VVROOT%venv\Scripts\python.exe" set "VVPYTHON=%VVROOT%venv\Scripts\python.exe"
if not defined VVPYTHON (
    echo ERROR: Python 3.12 was not found. Install it or set VOICEVAULT_PYTHON.
    goto :failed
)
"!VVPYTHON!" -c "import sys; raise SystemExit(0 if sys.version_info[:2]==(3,12) else 1)" >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python 3.12 is required. The existing environment has been preserved.
    goto :failed
)
REM Check/dry-run modes do not create or alter the environment.
if /i "%~1"=="--check" goto :runhelper
if /i "%~1"=="--dry-run" goto :runhelper

echo Preparing the project Python environment...
if not exist "%VVROOT%venv\Scripts\python.exe" (
    "!VVPYTHON!" -m venv "%VVROOT%venv"
    if errorlevel 1 goto :failed
)
set "VVPYTHON=%VVROOT%venv\Scripts\python.exe"

:runhelper
"!VVPYTHON!" "%VVROOT%scripts\setup_project.py" %*
if errorlevel 1 goto :failed
if /i "%~1"=="--check" exit /b 0
if /i "%~1"=="--dry-run" exit /b 0
pause
exit /b 0

:failed
echo ERROR: Setup failed. See the messages above.
if /i "%~1"=="--check" exit /b 1
if /i "%~1"=="--dry-run" exit /b 1
pause
exit /b 1
