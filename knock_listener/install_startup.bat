@echo off
setlocal
chcp 65001 > nul
cd /d "%~dp0"

set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "STARTUP_FILE=%STARTUP_DIR%\Alfred Knock Listener.cmd"

if not exist "%STARTUP_DIR%" (
  echo [ERROR] Windows Startup folder was not found: %STARTUP_DIR%
  exit /b 1
)

(
  echo @echo off
  echo cd /d "%~dp0"
  echo call "%~dp0start_knock_listener.bat"
) > "%STARTUP_FILE%"

echo [KNOCK] Startup entry installed:
echo [KNOCK] %STARTUP_FILE%
exit /b 0
