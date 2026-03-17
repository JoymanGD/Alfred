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
USE_HOTWORDS = os.getenv("ALFRED_KNOCK_USE_HOTWORDS", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
FUZZY_MATCH_THRESHOLD = max(
    0.0,
    min(1.0, float(os.getenv("ALFRED_KNOCK_MATCH_THRESHOLD", "0.72").strip() or "0.72")),
)
REQUIRED_MATCHING_CANDIDATES = max(
    1,
    int(os.getenv("ALFRED_KNOCK_REQUIRED_MATCHES", "1").strip() or "1"),
)
WAKE_PREFIX_THRESHOLD = max(
    0.0,
    min(1.0, float(os.getenv("ALFRED_KNOCK_PREFIX_THRESHOLD", "0.68").strip() or "0.68")),
)
NAME_THRESHOLD = max(
    0.0,
    min(1.0, float(os.getenv("ALFRED_KNOCK_NAME_THRESHOLD", "0.66").strip() or "0.66")),
)

KNOCK_PHRASES = [
    "knock knock alfred",
    "\u0442\u0443\u043a \u0442\u0443\u043a \u0430\u043b\u044c\u0444\u0440\u0435\u0434",
]
KNOCK_HOTWORDS = [
    "knock knock alfred",
    "knock knock",
    "alfred",
    "\u0442\u0443\u043a \u0442\u0443\u043a \u0430\u043b\u044c\u0444\u0440\u0435\u0434",
    "\u0442\u0443\u043a-\u0442\u0443\u043a \u0430\u043b\u044c\u0444\u0440\u0435\u0434",
    "\u0442\u0443\u043a \u0442\u0443\u043a",
    "\u0430\u043b\u044c\u0444\u0440\u0435\u0434",
    "\u0430\u043b\u0444\u0440\u0435\u0434",
]
HOTWORDS_TEXT = ",".join(dict.fromkeys(KNOCK_HOTWORDS))
CANONICAL_CHAR_MAP = str.maketrans(
    {
        "a": "a",
        "b": "b",
        "c": "c",
        "d": "d",
        "e": "e",
        "f": "f",
        "g": "g",
        "h": "h",
        "i": "i",
        "k": "k",
        "l": "l",
        "m": "m",
        "n": "n",
        "o": "o",
        "p": "p",
        "r": "r",
        "s": "s",
        "t": "t",
        "u": "u",
        "v": "v",
        "x": "x",
        "y": "y",
        "\u0430": "a",
        "\u0431": "b",
        "\u0432": "v",
        "\u0433": "g",
        "\u0434": "d",
        "\u0435": "e",
        "\u0451": "e",
        "\u0436": "zh",
        "\u0437": "z",
        "\u0438": "i",
        "\u0439": "i",
        "\u043a": "k",
        "\u043b": "l",
        "\u043c": "m",
        "\u043d": "n",
        "\u043e": "o",
        "\u043f": "p",
        "\u0440": "r",
        "\u0441": "s",
        "\u0442": "t",
        "\u0443": "u",
        "\u0444": "f",
        "\u0445": "h",
        "\u0446": "c",
        "\u0447": "ch",
        "\u0448": "sh",
        "\u0449": "sh",
        "\u044b": "y",
        "\u044d": "e",
        "\u044e": "u",
        "\u044f": "ya",
        "\u044c": "",
        "\u044a": "",
        "\u0456": "i",
        "\u0457": "i",
        "\u0491": "g",
    }
)


def normalize_phrase(text: str) -> str:
    lowered = text.lower().translate(CANONICAL_CHAR_MAP)
    tokens = re.findall(r"[a-z0-9]+", lowered)
    return " ".join(tokens)


KNOCK_NORMALIZED = {normalize_phrase(phrase) for phrase in KNOCK_PHRASES}
WAKE_PREFIXES = {
    "tuk tuk",
    "tuktuk",
    "duk tuk",
    "duktuk",
    "dok tuk",
    "doktuk",
    "knock knock",
    "knockknock",
}
NAME_VARIANTS = {
    "alfred",
    "alfret",
    "alfres",
    "alfreto",
}


def candidate_match_score(text: str) -> float:
    normalized = sanitize_recognized_text(text)
    if not normalized:
        return 0.0
    return max(
        difflib.SequenceMatcher(a=normalized, b=phrase).ratio()
        for phrase in KNOCK_NORMALIZED
    )


def _best_similarity(text: str, variants: set[str]) -> float:
    if not text:
        return 0.0
    return max(difflib.SequenceMatcher(a=text, b=variant).ratio() for variant in variants)


def wake_match_details(text: str) -> tuple[bool, float, float, float]:
    normalized = sanitize_recognized_text(text)
    if not normalized:
        return False, 0.0, 0.0, 0.0

    overall_score = candidate_match_score(normalized)
    words = normalized.split()
    joined = "".join(words)

    prefix_candidates = {
        " ".join(words[:2]),
        "".join(words[:2]),
        words[0],
        joined[: min(len(joined), 8)],
    }
    name_candidates = {
        words[-1],
        joined[-min(len(joined), 8) :],
    }
    if len(words) >= 3:
        prefix_candidates.add(" ".join(words[:3]))
        prefix_candidates.add("".join(words[:3]))
        name_candidates.add(" ".join(words[1:]))

    prefix_score = max(_best_similarity(candidate, WAKE_PREFIXES) for candidate in prefix_candidates if candidate)
    name_score = max(_best_similarity(candidate, NAME_VARIANTS) for candidate in name_candidates if candidate)

    exact_match = normalized in KNOCK_NORMALIZED
    structured_match = prefix_score >= WAKE_PREFIX_THRESHOLD and name_score >= NAME_THRESHOLD
    fuzzy_match = overall_score >= FUZZY_MATCH_THRESHOLD
    return exact_match or (structured_match and fuzzy_match), overall_score, prefix_score, name_score


def _dedupe_repeated_words(words: list[str]) -> list[str]:
    deduped: list[str] = []
    repeatable_limits = {
        "tuk": 2,
        "knock": 2,
        "alfred": 1,
        "alfret": 1,
        "alfres": 1,
        "alfreto": 1,
    }
    for word in words:
        limit = repeatable_limits.get(word)
        if limit is None:
            deduped.append(word)
            continue
        trailing_same = 0
        for existing in reversed(deduped):
            if existing != word:
                break
            trailing_same += 1
        if trailing_same >= limit:
            continue
        deduped.append(word)
    return deduped


def _extract_best_knock_window(words: list[str]) -> list[str]:
    best_window: list[str] = words
    best_score = -1.0
    max_window = min(len(words), 6)
    for window_size in range(1, max_window + 1):
        for start in range(0, len(words) - window_size + 1):
            window = words[start : start + window_size]
            normalized = " ".join(window)
            score = max(
                difflib.SequenceMatcher(a=normalized, b=phrase).ratio()
                for phrase in KNOCK_NORMALIZED
            )
            if score > best_score:
                best_score = score
                best_window = window
    return best_window


def sanitize_recognized_text(text: str) -> str:
    words = normalize_phrase(text).split()
    if not words:
        return ""
    words = _dedupe_repeated_words(words)
    if len(words) > 6:
        words = _extract_best_knock_window(words)
    return " ".join(words)


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
    matched, _, _, _ = wake_match_details(text)
    return matched


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
    }
    if USE_HOTWORDS:
        common_kwargs["hotwords"] = HOTWORDS_TEXT

    candidates: list[str] = []
    for language in (None, "en", "ru"):
        segments, _ = asr_model.transcribe(audio, language=language, **common_kwargs)
        text = "".join(segment.text for segment in segments).strip()
        cleaned = sanitize_recognized_text(text)
        if cleaned:
            candidates.append(cleaned)
    if not candidates:
        return "", []

    matching_candidates = [text for text in candidates if is_knock_phrase(text)]
    if len(matching_candidates) >= REQUIRED_MATCHING_CANDIDATES:
        exact = next((text for text in matching_candidates if sanitize_recognized_text(text) in KNOCK_NORMALIZED), None)
        if exact is not None:
            return exact, candidates
        best_match = max(matching_candidates, key=candidate_match_score)
        return best_match, candidates

    best = max(
        candidates,
        key=candidate_match_score,
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
            match_count = sum(1 for candidate in candidates if is_knock_phrase(candidate))
            matched, score, prefix_score, name_score = wake_match_details(text)
            write_log(
                f"Recognized: '{text}' normalized='{normalized}' score={score:.2f} "
                f"prefix_score={prefix_score:.2f} name_score={name_score:.2f} "
                f"matching_candidates={match_count}/{len(candidates)} candidates={candidates}"
            )
            if matched:
                if match_count < REQUIRED_MATCHING_CANDIDATES:
                    write_log(
                        "Wake phrase rejected: insufficient matching candidates "
                        f"({match_count} < {REQUIRED_MATCHING_CANDIDATES})."
                    )
                    continue
                start_alfred_if_needed()


def main() -> None:
    if not RUN_SCRIPT.exists():
        raise FileNotFoundError(f"run_alfred.bat was not found at {RUN_SCRIPT}")

    LOG_FILE.write_text("", encoding="utf-8")
    write_log("Listener starting.")
    write_log(f"Whisper hotwords enabled: {USE_HOTWORDS}")
    write_log(f"Fuzzy match threshold: {FUZZY_MATCH_THRESHOLD:.2f}")
    write_log(f"Required matching candidates: {REQUIRED_MATCHING_CANDIDATES}")
    write_log(f"Wake prefix threshold: {WAKE_PREFIX_THRESHOLD:.2f}")
    write_log(f"Name threshold: {NAME_THRESHOLD:.2f}")
    write_log(f"Accepted phrases: {', '.join(KNOCK_PHRASES)}")
    write_log(f"Hotwords: {HOTWORDS_TEXT}")

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
