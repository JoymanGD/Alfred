import difflib
import os
import re
import subprocess
import time
from collections import deque
from pathlib import Path

import numpy as np
import sounddevice as sd
import torch
from faster_whisper import WhisperModel


KNOCK_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = KNOCK_DIR.parent
LOG_FILE = KNOCK_DIR / "alfred_knock_listener.log"
LOCK_FILE = KNOCK_DIR / "alfred_knock_listener.lock"
RUN_SCRIPT = PROJECT_ROOT / "run_alfred.bat"

SAMPLE_RATE = 16000
BLOCK_SIZE = 512
MIN_SPEECH_SECONDS = 0.35
END_SILENCE_SECONDS = 0.65
PRE_SPEECH_SECONDS = 0.25
VAD_THRESHOLD = 0.35

WHISPER_MODEL = os.getenv("ALFRED_KNOCK_WHISPER_MODEL", "tiny").strip() or "tiny"
WHISPER_DEVICE = os.getenv("ALFRED_KNOCK_WHISPER_DEVICE", "cpu").strip() or "cpu"
WHISPER_COMPUTE_TYPE = os.getenv("ALFRED_KNOCK_WHISPER_COMPUTE_TYPE", "int8").strip() or "int8"

KNOCK_PHRASES = [
    "knock knock alfred",
    "\u0442\u0443\u043a \u0442\u0443\u043a \u0430\u043b\u044c\u0444\u0440\u0435\u0434",
]
CONFUSABLE_MAP = str.maketrans(
    {
        "a": "а",
        "c": "с",
        "e": "е",
        "k": "к",
        "m": "м",
        "o": "о",
        "p": "р",
        "t": "т",
        "x": "х",
        "y": "у",
        "а": "а",
        "с": "с",
        "е": "е",
        "к": "к",
        "м": "м",
        "о": "о",
        "р": "р",
        "т": "т",
        "х": "х",
        "у": "у",
    }
)


def normalize_phrase(text: str) -> str:
    lowered = text.lower().replace("ё", "е").translate(CONFUSABLE_MAP)
    tokens = re.findall(r"[a-z0-9а-я]+", lowered)
    return " ".join(tokens)


KNOCK_NORMALIZED = {normalize_phrase(phrase) for phrase in KNOCK_PHRASES}


def write_log(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(f"[{timestamp}] {message}\n")


def acquire_lock() -> None:
    try:
        fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.close(fd)
    except FileExistsError:
        raise SystemExit("[KNOCK] Listener is already running.")


def release_lock() -> None:
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def is_knock_phrase(text: str) -> bool:
    normalized = normalize_phrase(text)
    if not normalized:
        return False
    if normalized in KNOCK_NORMALIZED:
        return True
    return any(difflib.SequenceMatcher(a=normalized, b=phrase).ratio() >= 0.84 for phrase in KNOCK_NORMALIZED)


def is_alfred_running() -> bool:
    script = (
        "Get-CimInstance Win32_Process | Where-Object { "
        "$_.CommandLine -and "
        "$_.Name -match '^(python|pythonw)\\.exe$' -and "
        "$_.CommandLine -like '*alfred-bot.py*' "
        "} | "
        "Select-Object -First 1 | ForEach-Object { '1' }"
    )
    try:
        output = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", script],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return output == "1"
    except Exception:
        return False


def start_alfred_if_needed() -> None:
    if is_alfred_running():
        write_log("Wake phrase matched, but Alfred is already running.")
        return
    write_log("Wake phrase matched. Starting Alfred.")
    subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f"Start-Process -FilePath 'cmd.exe' -WorkingDirectory '{PROJECT_ROOT}' "
            f"-ArgumentList '/c start \"\" /normal \"{RUN_SCRIPT}\"' -WindowStyle Normal",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def load_models():
    write_log("Loading Silero VAD...")
    vad_model, _ = torch.hub.load(
        repo_or_dir="snakers4/silero-vad",
        model="silero_vad",
        force_reload=False,
    )
    write_log("Silero VAD loaded.")

    write_log(
        f"Loading Whisper model={WHISPER_MODEL} device={WHISPER_DEVICE} compute_type={WHISPER_COMPUTE_TYPE}..."
    )
    asr_model = WhisperModel(
        WHISPER_MODEL,
        device=WHISPER_DEVICE,
        compute_type=WHISPER_COMPUTE_TYPE,
    )
    write_log("Whisper loaded.")
    return vad_model, asr_model


def transcribe_audio(asr_model: WhisperModel, audio: np.ndarray) -> tuple[str, list[str]]:
    common_kwargs = {
        "beam_size": 1,
        "best_of": 1,
        "temperature": 0.0,
        "condition_on_previous_text": False,
        "suppress_tokens": [-1],
        "vad_filter": False,
        "hotwords": "Alfred,alfred,\u0430\u043b\u044c\u0444\u0440\u0435\u0434,\u0442\u0443\u043a \u0442\u0443\u043a,knock knock",
    }
    candidates: list[str] = []
    for language in (None, "en", "ru"):
        segments, _ = asr_model.transcribe(audio, language=language, **common_kwargs)
        text = "".join(segment.text for segment in segments).strip()
        if text:
            candidates.append(text)
    if not candidates:
        return "", []
    exact = next((text for text in candidates if is_knock_phrase(text)), None)
    if exact is not None:
        return exact, candidates
    best = max(
        candidates,
        key=lambda text: max(difflib.SequenceMatcher(a=normalize_phrase(text), b=phrase).ratio() for phrase in KNOCK_NORMALIZED),
    )
    return best, candidates


def listen_loop(vad_model, asr_model) -> None:
    min_speech_samples = int(SAMPLE_RATE * MIN_SPEECH_SECONDS)
    silence_blocks_to_end = max(1, int(END_SILENCE_SECONDS * SAMPLE_RATE / BLOCK_SIZE))
    pre_speech_blocks = max(1, int(PRE_SPEECH_SECONDS * SAMPLE_RATE / BLOCK_SIZE))

    write_log("Listener ready.")

    with sd.InputStream(
        channels=1,
        samplerate=SAMPLE_RATE,
        dtype="float32",
        blocksize=BLOCK_SIZE,
    ) as stream:
        speech_chunks: list[np.ndarray] = []
        silence_block_count = 0
        utterance_active = False
        pre_speech_buffer = deque(maxlen=pre_speech_blocks)

        while True:
            block, _ = stream.read(BLOCK_SIZE)
            pcm = block[:, 0].astype(np.float32)
            pcm_tensor = torch.from_numpy(pcm)
            speech_prob = float(vad_model(pcm_tensor, SAMPLE_RATE).item())
            speaking_now = speech_prob > VAD_THRESHOLD
            pre_speech_buffer.append(pcm.copy())

            if speaking_now:
                if not utterance_active:
                    speech_chunks = list(pre_speech_buffer)
                speech_chunks.append(pcm)
                silence_block_count = 0
                utterance_active = True
                continue

            if not utterance_active:
                continue

            speech_chunks.append(pcm)
            silence_block_count += 1
            if silence_block_count < silence_blocks_to_end:
                continue

            audio = np.concatenate(speech_chunks)
            speech_chunks = []
            silence_block_count = 0
            utterance_active = False
            pre_speech_buffer.clear()

            if len(audio) < min_speech_samples:
                continue

            text, candidates = transcribe_audio(asr_model, audio)
            if not text:
                write_log("Recognized empty text.")
                continue

            normalized = normalize_phrase(text)
            write_log(f"Recognized: '{text}' normalized='{normalized}' candidates={candidates}")
            if is_knock_phrase(text):
                start_alfred_if_needed()


def main() -> None:
    if not RUN_SCRIPT.exists():
        raise FileNotFoundError(f"run_alfred.bat was not found at {RUN_SCRIPT}")

    LOG_FILE.write_text("", encoding="utf-8")
    write_log("Listener starting.")
    write_log(f"Accepted phrases: {', '.join(KNOCK_PHRASES)}")

    acquire_lock()
    try:
        vad_model, asr_model = load_models()
        while True:
            try:
                listen_loop(vad_model, asr_model)
            except Exception as exc:
                write_log(f"Listener error: {exc}. Restarting audio loop.")
                time.sleep(1.0)
    finally:
        release_lock()


if __name__ == "__main__":
    try:
        main()
    except SystemExit as exc:
        print(exc)
        raise
    except Exception as exc:
        write_log(f"Fatal error: {exc}")
        raise
