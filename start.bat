@echo off

cd /d "%~dp0"

set "PY_CMD="
where pythonw >nul 2>nul
if %errorlevel%==0 (
	set "PY_CMD=pythonw"
) else (
	where pyw >nul 2>nul
	if %errorlevel%==0 (
		set "PY_CMD=pyw -3"
	) else (
		where py >nul 2>nul
		if %errorlevel%==0 (
			set "PY_CMD=py -3"
		) else (
			where python >nul 2>nul
			if %errorlevel%==0 (
				set "PY_CMD=python"
			)
		)
	)
)

if "%PY_CMD%"=="" (
	echo Python launcher not found. Install Python or add it to PATH.
	pause
	exit /b 1
)

start "" /B %PY_CMD% stalker.py >nul 2>&1
start "" /B %PY_CMD% tBotAgent.py >nul 2>&1

exit /b 0



