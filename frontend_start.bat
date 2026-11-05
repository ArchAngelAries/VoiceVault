@echo off
setlocal
set "PATH=%SystemRoot%\System32;%PATH%"

set VVROOT=%~dp0

echo Starting VoiceVault frontend...
cd /d "%VVROOT%frontend"
npm run dev
