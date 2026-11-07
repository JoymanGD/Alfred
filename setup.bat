@echo off
setlocal
chcp 65001 > nul

cd /d "%~dp0"

echo [SETUP] Alfred initial setup started.

where py > nul 2>&1
if %errorlevel%==0 (
    set "PYTHON_CMD=py -3.11"
) else (
    where python > nul 2>&1
    if %errorlevel%==0 (
        set "PYTHON_CMD=python"
    ) else (
        echo [ERROR] Python was not found. Install Python 3.11 and run this script again.
        pause
        exit /b 1
    )
)

echo [SETUP] Using Python command: %PYTHON_CMD%

if not exist ".gitmodules" (
    echo [SETUP] No .gitmodules file found, skipping submodule init.
) else (
    where git > nul 2>&1
    if not %errorlevel%==0 (
        echo [ERROR] Git was not found. Install Git and run this script again.
        pause
        exit /b 1
    )
    echo [SETUP] Initializing submodules...
    git submodule update --init --recursive
    if not %errorlevel%==0 (
        echo [ERROR] Failed to initialize git submodules.
        pause
        exit /b 1
    )
)

if not exist ".venv\Scripts\python.exe" (
    echo [SETUP] Creating virtual environment...
    call %PYTHON_CMD% -m venv .venv
    if not %errorlevel%==0 (
        echo [ERROR] Failed to create the virtual environment.
        pause
        exit /b 1
    )
)

set "VENV_PYTHON=.venv\Scripts\python.exe"
if not exist "%VENV_PYTHON%" (
    echo [ERROR] Virtual environment Python was not found at %VENV_PYTHON%.
    pause
    exit /b 1
)

echo [SETUP] Upgrading pip...
"%VENV_PYTHON%" -m pip install -U pip
if not %errorlevel%==0 (
    echo [ERROR] Failed to upgrade pip.
    pause
    exit /b 1
)

echo [SETUP] Installing Alfred dependencies...
"%VENV_PYTHON%" -m pip install ollama sounddevice soundfile "numpy==1.26.4" torch torchvision torchaudio "diffusers>=0.40,<0.41" transformers accelerate safetensors faster-whisper omnivoice silero av ctranslate2 triton-windows langdetect "opencv-python==4.10.0.84" comtypes pillow "decider-ai[gguf]" "playwright==1.63.0" "pypdf==6.19.0" "rapidocr==3.9.2" onnxruntime "pycaw==20260927"
if not %errorlevel%==0 (
    echo [ERROR] Failed to install one or more Python packages.
    pause
    exit /b 1
)

where nvcc > nul 2>&1
if %errorlevel%==0 (
    echo [SETUP] CUDA toolkit detected. Building GPU acceleration for Decider...
    set "CMAKE_ARGS=-DGGML_CUDA=on"
    set "CMAKE_BUILD_PARALLEL_LEVEL=8"
    set "FORCE_CMAKE=1"
    "%VENV_PYTHON%" -m pip install --force-reinstall --no-deps --no-cache-dir llama-cpp-python==0.3.35
    if errorlevel 1 (
        echo [ERROR] Failed to build CUDA support for Decider.
        pause
        exit /b 1
    )
)

echo [SETUP] Alfred setup completed successfully.
echo [SETUP] Installing knock listener autostart...
call "knock_listener\install_startup.bat"
if not %errorlevel%==0 (
    echo [ERROR] Failed to install knock listener autostart.
    pause
    exit /b 1
)

echo [SETUP] Next steps:
echo [SETUP] 1. Install and start Ollama.
echo [SETUP] 2. Pull the main model: ollama pull hf.co/HauhauCS/Gemma4-12B-QAT-Uncensored-HauhauCS-Balanced:Q4_K_M
echo [SETUP] 3. The main Gemma 4 model also handles camera and screen input.
echo [SETUP] 4. Decider downloads decider-4b v2.1 Q4_K_M on first start.
echo [SETUP] 5. Run Alfred with start_alfred_core.bat
echo [SETUP] 6. Run Alfred input with start_chat.bat or start_voice.bat

pause
