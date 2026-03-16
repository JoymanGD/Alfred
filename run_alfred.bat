@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"
powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command "$code='[DllImport(\"kernel32.dll\")] public static extern IntPtr GetConsoleWindow(); [DllImport(\"user32.dll\")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);'; $type=Add-Type -MemberDefinition $code -Name ConsoleWindow -Namespace Alfred -PassThru; $hwnd=$type::GetConsoleWindow(); if($hwnd -ne [IntPtr]::Zero){$null=$type::ShowWindowAsync($hwnd, 9); $null=$type::ShowWindowAsync($hwnd, 5)}" > nul 2>&1

set "ALFRED_OLLAMA_MODEL=gemma3:12b"
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
"%ALFRED_PYTHON%" alfred-bot.py

echo Alfred stopped.
exit
