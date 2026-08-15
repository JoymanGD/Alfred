@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"

set "ALFRED_OLLAMA_MODEL=gemma4:12b"
set "ALFRED_ROUTER_OLLAMA_MODEL=qwen3.5:2b"
set "ALFRED_IMAGE_MODEL_ID=Tongyi-MAI/Z-Image-Turbo"
set "ALFRED_IMAGE_TORCH_DTYPE=bf16"
set "ALFRED_IMAGE_DEVICE=cuda"
set "ALFRED_IMAGE_STEPS=8"
set "ALFRED_IMAGE_WIDTH=512"
set "ALFRED_IMAGE_HEIGHT=512"
set "ALFRED_IMAGE_GUIDANCE_SCALE=0"
set "ALFRED_IMAGE_SAMPLER=euler_a"
set "ALFRED_IMAGE_ATTENTION_BACKEND="
set "ALFRED_IMAGE_COMPILE=1"
set "ALFRED_TTS_GAIN=2.8"
set "ALFRED_TTS_VOICE_INSTRUCT=male, low pitch"
set "ALFRED_LOG=%~dp0alfred_run.log"
set "PYTHONUNBUFFERED=1"

set "ALFRED_PYTHON=%~dp0.venv\Scripts\python.exe"
if exist "%ALFRED_PYTHON%" goto run_alfred

set "ALFRED_PYTHON=%LocalAppData%\Programs\Python\Python311\python.exe"
if exist "%ALFRED_PYTHON%" goto run_alfred

set "ALFRED_PYTHON=%LocalAppData%\Programs\Python\Python312\python.exe"
if exist "%ALFRED_PYTHON%" goto run_alfred

set "ALFRED_PYTHON=%LocalAppData%\Programs\Python\Python310\python.exe"
if exist "%ALFRED_PYTHON%" goto run_alfred

where python > nul 2>&1
if errorlevel 1 (
  echo Python not found.
  echo Run setup_alfred.bat first.
  exit /b 1
)
set "ALFRED_PYTHON=python"

:run_alfred
echo Starting Alfred with model: %ALFRED_OLLAMA_MODEL%
echo Running Python: %ALFRED_PYTHON%
echo Log file: %ALFRED_LOG%
echo [%date% %time%] Starting Alfred> "%ALFRED_LOG%"
echo This is the Alfred core window. Use start_chat.bat or start_voice.bat for input.
"%ALFRED_PYTHON%" -u alfred-bot.py --mode core
set "ALFRED_EXIT=%errorlevel%"

if not "%ALFRED_EXIT%"=="0" (
  echo Alfred exited with error code %ALFRED_EXIT%.
  echo Check the log file: %ALFRED_LOG%
  echo.
  type "%ALFRED_LOG%"
  pause
  exit /b %ALFRED_EXIT%
)

echo Alfred stopped.
exit /b 0
