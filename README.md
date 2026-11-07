# Alfred

Alfred is a local voice assistant for Windows. It combines speech recognition, a local LLM through Ollama, multilingual OmniVoice TTS, browser and desktop actions, web search, music playback through YouTube Music Web, image generation, and Codex CLI support for coding tasks.

## Features

- Wake-word assistant with spoken replies
- Local chat with Ollama
- Camera and screen inspection with a dedicated local vision model
- Web search, weather, and current-information lookups
- Local planning with reusable task lists
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

- LLM: `hf.co/HauhauCS/Gemma4-12B-QAT-Uncensored-HauhauCS-Balanced:Q4_K_M` via Ollama by default
- Camera and screen use the main Gemma 4 model by default. To use a separate vision model, change `ALFRED_VISION_MODEL` in `start_alfred_core.bat` to an installed Ollama model that supports images.
- Intent and action classification: `Mapika/decider-4b-GGUF`, `decider-4b-v2.1-Q4_K_M.gguf` via `decider-ai[gguf]`. Alfred loads it at startup and downloads it if needed (about 2.7 GB). It uses the Decider decision API, not Ollama chat. `setup.bat` builds CUDA support when `nvcc` is available.
- Decider decides whether a message requests computer actions and selects the category for each step. Gemma 4 sees only the actions in that category, chooses a step, and receives its result before deciding again. The next step may use a different category. Wording is interpreted by the models; action selection does not search for command keywords in the user's message.
- The action catalog is [`alfred/action_catalog.json`](alfred/action_catalog.json): 65 executable actions in 15 categories, including files, recoverable trash, documents and OCR, browser interaction, downloads, apps and windows, music and audio devices, network checks, reminders, clipboard, screen/camera, system information and Codex. Each action declares its arguments and confirmation policy. The executor validates paths, process IDs, URLs and operation names. Operations that may lose data or make broad changes require a separate confirmation naming the exact target; a different request cancels the pending action.
- Main STT: `faster-whisper` with Whisper `small`
- Knock listener STT: `faster-whisper` with Whisper `tiny` by default
- VAD: `snakers4/silero-vad`
- TTS: `OmniVoice`
- Image generation: `Tongyi-MAI/Z-Image-Turbo`

The core loads Decider, Gemma 4, OmniVoice, and document OCR with a fixed session voice before it accepts chat or voice input. It also runs a short Gemma 4 chat inference after loading OmniVoice, so the first real reply does not pay for cold inference setup. Z-Image-Turbo loads only on the first image request, so that first image takes longer. The voice client loads Silero VAD and Whisper ASR before opening the microphone. Clients wait for the core's `ready` status; a loading error is shown to the client instead of opening chat or the microphone. Ollama keeps Gemma 4 resident until the core is stopped.

## Local web search

Alfred can use a local Vane answer engine for web questions. Vane searches with SearXNG and uses the same Ollama chat model as Alfred. Alfred checks Vane's answer against the returned source excerpts. If Vane is unavailable or its answer is unsupported, Alfred searches through SearXNG directly and uses its existing grounded answer path. DuckDuckGo and Bing remain the final fallback. No user-message keyword rules select the search provider.

On this Windows installation, the native SearXNG and Vane files are in the ignored `.local_services/` directory. `start_alfred_core.bat` starts SearXNG before core startup. Start Vane with `scripts\start_vane.bat` after SearXNG is ready. Vane listens only on `127.0.0.1:3000`; SearXNG listens only on `127.0.0.1:8080`. Both services need internet access for fresh search results. The native Vane installation needs Node.js and the `nomic-embed-text` Ollama model.

The configured endpoints can be changed with `ALFRED_VANE_URL` and `ALFRED_SEARXNG_URL`. `ALFRED_VANE_EMBED_MODEL` selects a locally configured Vane embedding model. Vane's search timeout is controlled by `ALFRED_VANE_TIMEOUT`.

## Action Catalog

`alfred/action_catalog.json` lists the operations Alfred can actually execute. Each category has a semantic description; each action has a description, argument schema and confirmation policy. Decider chooses a category for each step, so Gemma receives a small action menu. A multi-step request can move between categories after observing a result. To add a new capability, add its catalog entry and executor implementation; new user phrasings of an existing capability need no code change.

The catalog is deliberately finite: Alfred cannot carry out an operation simply because a model invents an action name. It can ask for clarification or explain that an operation is unavailable.

Browser interaction uses an Alfred-managed Edge or Chrome window and profile. It cannot inspect tabs that were opened independently in the user's regular browser. Browser clicks, form filling and downloads ask for confirmation of the inspected element and page. Alfred's recoverable trash, text-edit undo records, browser profile and reminders are stored in `.alfred_state/` under the project. Direct file downloads run in the background; Alfred can report progress and announces completion or failure.

The managed browser uses an installed Edge or Chrome executable; `ALFRED_BROWSER_EXECUTABLE` can point to another compatible browser path. The first browser request launches the controlled window. PDF extraction reads embedded text, while OCR handles text in local images. Reminders are kept on disk and announced while the Alfred core is running; overdue reminders are announced when the core starts again.

## TTS Languages

Alfred uses OmniVoice for speech synthesis only.

It switches languages in real time based on the detected reply language while keeping the same voice for the core session. If a detected language is unavailable in OmniVoice, Alfred falls back to the closest supported language and then to English.

## Language Support

- Text conversation depends on the capabilities of your Ollama model
- Voice input is multilingual through Whisper
- Voice output follows OmniVoice language support
- In active voice mode, Whisper sends each recognized utterance to the core. Decider decides from the utterance and recent conversation whether it is addressed to Alfred; no name or fixed command phrase is required. Ambiguous background speech is ignored.

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
5. Install the Python dependencies.

Example:

```powershell
git clone --recurse-submodules https://github.com/JoymanGD/Alfred.git
cd Alfred
git submodule update --init --recursive
python -m venv .venv
.venv\Scripts\activate
pip install -U pip
pip install ollama sounddevice soundfile numpy torch torchvision torchaudio "diffusers>=0.40,<0.41" transformers accelerate safetensors faster-whisper omnivoice silero av ctranslate2 triton-windows langdetect opencv-python comtypes pillow "decider-ai[gguf]"
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
start_alfred_core.bat
```

Core plus separate input windows:

```powershell
start_alfred_core.bat
start_chat.bat
start_voice.bat
```

`start_alfred_core.bat` starts the Alfred core window with logs and replies.
`start_chat.bat` opens a separate text chat console that connects to the running core.
`start_voice.bat` opens a separate voice-input console that connects to the running core.
The chat prompt returns as soon as Alfred begins speaking. Sending another message interrupts the current audio immediately and starts processing the new request.

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

On startup Alfred prints the detected OmniVoice-supported TTS languages so you can confirm what is available in the current backend.

## Lightweight Knock Listener

The repository includes a separate lightweight background listener based on `faster-whisper` plus Silero VAD.

- It is much lighter than the full Alfred stack because it does not load Ollama, OmniVoice, or image models
- It uses a separate ASR model intended only for launch phrases
- It listens for wake phrases and starts `start_alfred_core.bat`
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

Alfred uses its dedicated vision model to inspect a camera frame or screenshot and answer based on what it sees. Questions about physical objects near you can route to the camera without explicitly saying "camera".

Examples:

- `what do you see`
- `do you see my keyboard`
- `what's on camera right now`
- `что ты видишь`
- `ты видишь кружку на столе`

If no camera is available, Alfred falls back to a spoken message that he cannot see anything.

## Planning Commands

Alfred can keep a simple local plan and update it over time.

Examples:

- `make a plan for my week`
- `show my plan`
- `add buy groceries to my plan`
- `complete step 2`
- `remove step 3`
- `clear my plan`

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
- `ALFRED_DECIDER_MODEL` (default `Mapika/decider-4b-GGUF`)
- `ALFRED_DECIDER_GGUF_FILE` (default `decider-4b-v2.1-Q4_K_M.gguf`; can be a local GGUF path)
- `ALFRED_DECIDER_N_CTX` (default `4096`)
- `ALFRED_OLLAMA_EXE`
- `ALFRED_CODEX_EXE`
- `ALFRED_WHISPER_MODEL`
- `ALFRED_WHISPER_COMPUTE_TYPE`
- `ALFRED_PRE_SPEECH_SECONDS`
- `ALFRED_WAKE_FOLLOWUP_SECONDS`
- `ALFRED_CONVERSATION_CONTEXT_SECONDS` (preferred name for Decider's continuation context)
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
- `ALFRED_CAMERA_WIDTH` and `ALFRED_CAMERA_HEIGHT` (requested capture size; defaults `1280` by `720`, if supported by the camera)
- `ALFRED_VISION_MODEL` (defaults to `ALFRED_OLLAMA_MODEL`; `start_alfred_core.bat` sets them to the same model)
- `ALFRED_VISION_GPU_LAYERS` (default `15`; applies when using a separate vision model)
- `ALFRED_IMAGE_ATTENTION_BACKEND`
- `ALFRED_IMAGE_COMPILE`
- `ALFRED_IMAGE_CPU_OFFLOAD` (default `1`; keeps the image model in RAM between generations to leave GPU memory for fast speech synthesis; image generation becomes slower)
- `ALFRED_KNOCK_WHISPER_MODEL`
- `ALFRED_KNOCK_WHISPER_DEVICE`
- `ALFRED_KNOCK_WHISPER_COMPUTE_TYPE`

`start_alfred_core.bat` contains one example local runtime configuration.

If Alfred clips the first word of your command, try increasing `ALFRED_PRE_SPEECH_SECONDS`, for example to `0.6`.

`ALFRED_CONVERSATION_CONTEXT_SECONDS` controls how long a recent assistant reply is marked as conversation context for Decider. It does not automatically accept speech. The older `ALFRED_WAKE_FOLLOWUP_SECONDS` setting is also accepted.

## Notes

- Music playback uses YouTube Music Web only
- TTS uses OmniVoice
- Image generation speed depends heavily on GPU, scheduler, precision, and runtime backend
- This project is local-first; external access is limited to normal web requests, model downloads, and services you explicitly use
