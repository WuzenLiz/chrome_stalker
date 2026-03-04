@echo off
:: start.bat — Start both chrome_stalker NSSM services
:: Requires services to be installed first via nssm_install.bat
:: Run with Administrator privileges.

cd /d "%~dp0"

if not exist logs mkdir logs

echo [INFO] Starting ChromeStalker...
net start ChromeStalker

echo [INFO] Starting ChromeTBot...
net start ChromeTBot

echo [OK] Services started.
