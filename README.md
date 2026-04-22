# Alfred

Alfred is a local voice assistant for Windows. It combines speech recognition, a local LLM through Ollama, multilingual Piper TTS, browser and desktop actions, web search, music playback through YouTube Music Web, image generation, and Codex CLI support for coding tasks.

## Features

- Wake-word assistant with spoken replies
- Local chat with Ollama
- Camera-based vision requests for multimodal Ollama models such as Gemma 3
- Web search, weather, and current-information lookups
- Open apps, sites, browser searches, and search results
- Send coding tasks to Codex CLI and hear a short spoken completion summary
- Play and control music in YouTube Music Web
- Control Alfred itself: restart or shut it down by voice
- Shut down or restart the whole computer by voice
- Generate images with Z-Image-Turbo
- Automatic TTS language switching based on the detected reply language
- One-step Windows setup with `setup_alfred.bat`
- Lightweight background `knock listener` that can start Alfred on demand

## Models and AI Components

- LLM: `gemma3:12b` via Ollama by default
- Main STT: `faster-whisper` with Whisper `small`
- Knock listener STT: `faster-whisper` with Whisper `tiny` by default
- VAD: `snakers4/silero-vad`
- TTS: `Piper`
- Image generation: `Tongyi-MAI/Z-Image-Turbo`

## TTS Languages

Alfred uses Piper for speech synthesis only.

It automatically discovers installed Piper voices from `piper-voices/` and switches between them in real time based on the language of the current assistant reply.

Supported Piper language folders in the current repository layout:

`ar`, `bg`, `ca`, `cs`, `cy`, `da`, `de`, `el`, `en`, `es`, `fa`, `fi`, `fr`, `hi`, `hu`, `id`, `is`, `it`, `ka`, `kk`, `lb`, `lv`, `ml`, `ne`, `nl`, `no`, `pl`, `pt`, `ro`, `ru`, `sk`, `sl`, `sr`, `sv`, `sw`, `te`, `tr`, `uk`, `vi`, `zh`

If a detected language is unavailable, Alfred falls back to the closest installed Piper voice and then to English.

## Language Support

- Text conversation depends on the capabilities of your Ollama model
- Voice input is multilingual through Whisper
- Voice output follows the installed Piper voices
- Default wake words include `Alfred` and `Альфред`

## Requirements

- Windows
- Python 3.11
- Ollama installed locally
- NVIDIA GPU recommended for image generation

## Installation

Fastest Windows setup:

```powershell
setup_alfred.bat
```

This installs Python dependencies, initializes submodules, and also installs Windows autostart for the lightweight knock listener.

Manual setup:

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
pip install ollama sounddevice soundfile numpy torch torchvision torchaudio diffusers accelerate safetensors faster-whisper piper-tts silero av ctranslate2 triton-windows langdetect opencv-python comtypes pillow
```

If you already cloned the repository without submodules, run:

```powershell
git submodule update --init --recursive
```

## Running Alfred

Main assistant:

```powershell
python alfred-bot.py
```

Or:

```powershell
run_alfred.bat
```

Lightweight knock listener:

```powershell
knock_listener\start_knock_listener.bat
```

Stop the lightweight listener:

```powershell
knock_listener\stop_knock_listener.bat
```

Install Windows autostart for the knock listener:

```powershell
knock_listener\install_startup.bat
```

Remove Windows autostart for the knock listener:

```powershell
knock_listener\uninstall_startup.bat
```

On startup Alfred prints the detected Piper TTS languages so you can confirm what is available in your local `piper-voices/` directory.

## Lightweight Knock Listener

The repository includes a separate lightweight background listener based on `faster-whisper` plus Silero VAD.

- It is much lighter than the full Alfred stack because it does not load Ollama, Piper, or image models
- It uses a separate ASR model intended only for launch phrases
- It listens for wake phrases and starts `run_alfred.bat`
- It does nothing if Alfred is already running
- It writes diagnostics to `knock_listener/alfred_knock_listener.log`
- Windows autostart is installed through the user's Startup folder

Default wake phrases:

- `knock knock Alfred`
- `тук тук Альфред`

Default knock-listener environment:

- `ALFRED_KNOCK_WHISPER_MODEL=tiny`
- `ALFRED_KNOCK_WHISPER_DEVICE=cpu`
- `ALFRED_KNOCK_WHISPER_COMPUTE_TYPE=int8`

## Music Commands

Alfred can open music in YouTube Music Web and control playback with voice commands.

Examples in English:

- `play daft punk`
- `play relaxing music`
- `play jazz`
- `next song`
- `previous song`
- `pause music`
- `resume music`
- `mute`
- `unmute`
- `volume up`
- `volume down`
- `make it 50 percent quieter`
- `make it twice quieter`
- `make it twice louder`
- `a little louder`
- `full volume`
- `what is the current volume`
- `why is Alfred so hard to hear`

Examples in Russian:

- `включи daft punk`
- `включи расслабляющую музыку`
- `следующий трек`
- `предыдущий трек`
- `пауза`
- `продолжи`
- `выключи звук`
- `включи звук`
- `сделай тише`
- `сделай громче`
- `сделай на 50 процентов тише`
- `в два раза тише`
- `в два раза громче`
- `немного погромче`
- `ещё тише`
- `на полную громкость`
- `какая сейчас громкость`

You can also wake Alfred first and then speak the command during the follow-up window:

- `Alfred`
- `Yes?`
- `Play music`

## Vision Commands

If your Ollama model supports images, Alfred can take a picture from an available camera and answer based on what it sees.

Examples:

- `what do you see`
- `do you see my keyboard`
- `what's on camera right now`
- `что ты видишь`
- `ты видишь кружку на столе`

If no camera is available, Alfred falls back to a spoken message that he cannot see anything.

## Codex Support

Alfred can send development tasks to Codex CLI in the current project directory without opening VS Code.

Examples:

- `ask codex to fix the router`
- `ask codex to add a shutdown computer command`
- `tell codex to update the README`
- `попроси кодекс исправить роутер`
- `попроси кодекс добавить команду выключения компьютера`

How it works:

- Alfred resolves `codex` from `ALFRED_CODEX_EXE`, `PATH`, or common VS Code installation paths
- The task is executed inside the current project directory
- Alfred waits for completion and then speaks a very short final result sentence

## System Commands

Assistant control:

- `restart Alfred`
- `turn off Alfred`
- `перезапусти Альфреда`
- `выключи Альфреда`

Computer power commands:

- `shutdown the computer`
- `turn off my PC`
- `restart the computer`
- `reboot my PC`
- `выключи компьютер`

## Configuration

Useful environment variables:

- `ALFRED_OLLAMA_MODEL`
- `ALFRED_OLLAMA_EXE`
- `ALFRED_CODEX_EXE`
- `ALFRED_WHISPER_MODEL`
- `ALFRED_WHISPER_COMPUTE_TYPE`
- `ALFRED_PRE_SPEECH_SECONDS`
- `ALFRED_WAKE_FOLLOWUP_SECONDS`
- `ALFRED_PIPER_VOICES_DIR`
- `ALFRED_ASR_HOTWORDS`
- `ALFRED_TTS_GAIN`
- `ALFRED_IMAGE_MODEL_ID`
- `ALFRED_IMAGE_TORCH_DTYPE`
- `ALFRED_IMAGE_DEVICE`
- `ALFRED_IMAGE_STEPS`
- `ALFRED_IMAGE_WIDTH`
- `ALFRED_IMAGE_HEIGHT`
- `ALFRED_IMAGE_GUIDANCE_SCALE`
- `ALFRED_IMAGE_SAMPLER`
- `ALFRED_CAMERA_ENABLED`
- `ALFRED_CAMERA_INDEXES`
- `ALFRED_CAMERA_WARMUP_FRAMES`
- `ALFRED_IMAGE_ATTENTION_BACKEND`
- `ALFRED_IMAGE_COMPILE`
- `ALFRED_KNOCK_WHISPER_MODEL`
- `ALFRED_KNOCK_WHISPER_DEVICE`
- `ALFRED_KNOCK_WHISPER_COMPUTE_TYPE`

`run_alfred.bat` contains one example local runtime configuration.

If Alfred clips the first word of your command, try increasing `ALFRED_PRE_SPEECH_SECONDS`, for example to `0.6`.

If you want a longer wake-word follow-up window, increase `ALFRED_WAKE_FOLLOWUP_SECONDS`.

## Notes

- Music playback uses YouTube Music Web only
- TTS uses Piper only
- Image generation speed depends heavily on GPU, scheduler, precision, and runtime backend
- This project is local-first; external access is limited to normal web requests, model downloads, and services you explicitly use
