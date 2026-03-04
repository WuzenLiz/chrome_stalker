@echo off
:: nssm_uninstall.bat — Stop and remove chrome_stalker NSSM services
:: Run with Administrator privileges.

setlocal EnableDelayedExpansion
cd /d "%~dp0"
set "BASE=%~dp0"

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Please run this script as Administrator.
    pause
    exit /b 1
)

where nssm >nul 2>&1
if %errorlevel% neq 0 (
    if exist "%BASE%nssm.exe" (
        set "NSSM=%BASE%nssm.exe"
    ) else (
        echo [ERROR] nssm.exe not found.
        pause
        exit /b 1
    )
) else (
    set "NSSM=nssm"
)

echo [INFO] Stopping and removing ChromeStalker...
%NSSM% stop   ChromeStalker
%NSSM% remove ChromeStalker confirm

echo [INFO] Stopping and removing ChromeTBot...
%NSSM% stop   ChromeTBot
%NSSM% remove ChromeTBot confirm

echo.
echo [OK] Services removed.
pause
