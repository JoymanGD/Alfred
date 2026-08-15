@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"

set "ALFRED_PYTHON=%~dp0.venv\Scripts\python.exe"
if exist "%ALFRED_PYTHON%" goto run_voice

set "ALFRED_PYTHON=%LocalAppData%\Programs\Python\Python311\python.exe"
if exist "%ALFRED_PYTHON%" goto run_voice

set "ALFRED_PYTHON=%LocalAppData%\Programs\Python\Python312\python.exe"
if exist "%ALFRED_PYTHON%" goto run_voice

set "ALFRED_PYTHON=%LocalAppData%\Programs\Python\Python310\python.exe"
if exist "%ALFRED_PYTHON%" goto run_voice

where python > nul 2>&1
if errorlevel 1 (
  echo Python not found.
  echo Run setup_alfred.bat first.
  exit /b 1
)
set "ALFRED_PYTHON=python"

:run_voice
echo Starting Alfred voice window.
"%ALFRED_PYTHON%" -u alfred-bot.py --mode voice
set "ALFRED_EXIT=%errorlevel%"
if not "%ALFRED_EXIT%"=="0" (
  echo.
  echo Alfred voice exited with error code %ALFRED_EXIT%.
  echo Make sure the core is running through run_alfred.bat.
  pause
  exit /b %ALFRED_EXIT%
)
exit /b 0
