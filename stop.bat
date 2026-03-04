@echo off
:: stop.bat — Stop both chrome_stalker NSSM services
:: Run with Administrator privileges.

cd /d "%~dp0"

echo [INFO] Stopping ChromeStalker...
net stop ChromeStalker

echo [INFO] Stopping ChromeTBot...
net stop ChromeTBot

echo [OK] Services stopped.
