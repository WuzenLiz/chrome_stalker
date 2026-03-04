@echo off
:: stop.bat — Stop both chrome_stalker NSSM services
:: Run with Administrator privileges.

cd /d "%~dp0"
set "BASE=%~dp0"

where nssm >nul 2>&1
if %errorlevel% neq 0 (
    if exist "%BASE%nssm.exe" (
        set "NSSM=%BASE%nssm.exe"
    ) else (
        echo [ERROR] nssm.exe not found. Cannot stop services.
        pause
        exit /b 1
    )
) else (
    set "NSSM=nssm"
)

echo [INFO] Stopping ChromeStalker...
%NSSM% stop ChromeStalker

echo [INFO] Stopping ChromeTBot...
%NSSM% stop ChromeTBot

echo [OK] Services stopped.
