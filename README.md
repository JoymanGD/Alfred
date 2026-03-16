# Alfred

Alfred is a local voice assistant for Windows. It runs speech recognition, a local LLM through Ollama, multilingual Piper TTS, browser and desktop actions, web search, music playback through YouTube Music Web, and image generation.

## Features

- Wake-word assistant with spoken replies
- Local chat with Ollama
- Web search, weather, and current-information lookups
- Open apps, sites, and browser search results
- Play and control music in YouTube Music Web
- Generate images with Z-Image-Turbo
- Automatic TTS language switching based on the detected language of the reply

## Models and AI Components

- LLM: `gemma3:12b` via Ollama by default
- STT: `faster-whisper` with Whisper `small`
- VAD: `snakers4/silero-vad`
- TTS: `Piper`
- Image generation: `Tongyi-MAI/Z-Image-Turbo`

## TTS Languages

Alfred now uses Piper only for speech synthesis.

It automatically discovers installed Piper voices from `piper-voices/` and switches between them in real time based on the language of the current assistant reply.

The current repository layout supports these Piper language folders:

`ar`, `bg`, `ca`, `cs`, `cy`, `da`, `de`, `el`, `en`, `es`, `fa`, `fi`, `fr`, `hi`, `hu`, `id`, `is`, `it`, `ka`, `kk`, `lb`, `lv`, `ml`, `ne`, `nl`, `no`, `pl`, `pt`, `ro`, `ru`, `sk`, `sl`, `sr`, `sv`, `sw`, `te`, `tr`, `uk`, `vi`, `zh`

If a detected language is unavailable, Alfred falls back to the closest installed Piper voice and then to English.

## Language Support

- Text conversation depends on the capabilities of your Ollama model
- Voice input is multilingual through Whisper
- Voice output follows the installed Piper voices
- Wake words included by default: `Alfred`, `Альфред`

## Requirements

- Windows
- Python 3.11
- Ollama installed locally
- NVIDIA GPU recommended for image generation

## Installation

1. Clone the repository with submodules.
2. Create and activate a Python environment.
3. Install the dependencies.
4. Install and set up Ollama.
5. Initialize or update the `piper-voices` submodule if needed.

Example:

```powershell
git clone --recurse-submodules https://github.com/JoymanGD/Alfred.git
cd Alfred
git submodule update --init --recursive
python -m venv .venv
.venv\Scripts\activate
pip install -U pip
pip install ollama sounddevice soundfile numpy torch torchvision torchaudio diffusers accelerate safetensors faster-whisper piper-tts silero av ctranslate2 triton-windows langdetect
```

If you already cloned the repository without submodules, run:

```powershell
git submodule update --init --recursive
```

## Running Alfred

```powershell
python alfred-bot.py
```

Or:

```powershell
run_alfred.bat
```

On startup Alfred prints the detected Piper TTS languages so you can confirm what is available in your local `piper-voices/` directory.

## Configuration

Useful environment variables:

- `ALFRED_OLLAMA_MODEL`
- `ALFRED_OLLAMA_EXE`
- `ALFRED_WHISPER_MODEL`
- `ALFRED_WHISPER_COMPUTE_TYPE`
- `ALFRED_PIPER_VOICES_DIR`
- `ALFRED_ASR_HOTWORDS`
- `ALFRED_IMAGE_MODEL_ID`
- `ALFRED_IMAGE_TORCH_DTYPE`
- `ALFRED_IMAGE_DEVICE`
- `ALFRED_IMAGE_STEPS`
- `ALFRED_IMAGE_WIDTH`
- `ALFRED_IMAGE_HEIGHT`
- `ALFRED_IMAGE_GUIDANCE_SCALE`
- `ALFRED_IMAGE_SAMPLER`
- `ALFRED_IMAGE_ATTENTION_BACKEND`
- `ALFRED_IMAGE_COMPILE`

`run_alfred.bat` contains one example local configuration.

## Notes

- Music playback uses YouTube Music Web only.
- TTS is Piper only.
- Image generation speed depends heavily on your GPU, scheduler, precision, and runtime backend.
- This project is local-first. External access is limited to normal web requests, model downloads, and sites or services you explicitly use.
