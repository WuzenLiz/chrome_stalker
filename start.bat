@echo off
cd /d "%~dp0"
if not exist logs mkdir logs
start "" /B pythonw -m supervisord -c supervisor.conf
