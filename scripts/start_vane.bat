@echo off
setlocal
cd /d "%~dp0..\.local_services\vane"
if not exist "node_modules\next\dist\bin\next" (
  echo Vane is not installed in .local_services\vane.
  exit /b 1
)
set "SEARXNG_API_URL=http://127.0.0.1:8080"
set "OLLAMA_BASE_URL=http://127.0.0.1:11434"
node "node_modules\next\dist\bin\next" start -p 3000 -H 127.0.0.1
