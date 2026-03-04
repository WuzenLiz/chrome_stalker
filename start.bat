@echo off
:: start.bat — Start both chrome_stalker NSSM services
:: Requires services to be installed first via nssm_install.bat
:: Run with Administrator privileges.

cd /d "%~dp0"
set "BASE=%~dp0"

where nssm >nul 2>&1
if %errorlevel% neq 0 (
    if exist "%BASE%nssm.exe" (
        set "NSSM=%BASE%nssm.exe"
    ) else (
        echo [ERROR] nssm.exe not found.
        echo         Download from https://nssm.cc or run nssm_install.bat first.
        pause
        exit /b 1
    )
) else (
    set "NSSM=nssm"
)

if not exist logs mkdir logs

echo [INFO] Starting ChromeStalker...
%NSSM% start ChromeStalker

echo [INFO] Starting ChromeTBot...
%NSSM% start ChromeTBot

echo [OK] Services started.
