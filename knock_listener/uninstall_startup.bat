@echo off
setlocal
chcp 65001 > nul

set "STARTUP_FILE=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Alfred Knock Listener.cmd"

if exist "%STARTUP_FILE%" (
  del /f /q "%STARTUP_FILE%"
  echo [KNOCK] Startup entry removed:
  echo [KNOCK] %STARTUP_FILE%
) else (
  echo [KNOCK] Startup entry is not installed.
)

exit /b 0
