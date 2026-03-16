import json
import os
import re
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import ollama

try:
    from langdetect import DetectorFactory, LangDetectException, detect as langdetect_detect

    DetectorFactory.seed = 0
except Exception:  # pragma: no cover - optional dependency fallback
    LangDetectException = Exception
    langdetect_detect = None


def is_ollama_running() -> bool:
    try:
        request = Request("http://127.0.0.1:11434/api/tags")
        with urlopen(request, timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def ensure_ollama_running(ollama_exe: str):
    if is_ollama_running():
        print("[OLLAMA] Already running.")
        return

    if not os.path.exists(ollama_exe):
        print(f"[OLLAMA] Executable not found: {ollama_exe}")
        return

    print(f"[OLLAMA] Starting Ollama: {ollama_exe}")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(
            [ollama_exe, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except Exception as e:
        print(f"[OLLAMA] Failed to start automatically: {e}")
        return

    for _ in range(20):
        if is_ollama_running():
            print("[OLLAMA] Server is ready.")
            return
        time.sleep(0.5)

    print("[OLLAMA] Startup requested, but the server did not respond in time.")


def make_ollama_chat(model_name: str):
    def _ollama_chat(messages):
        return ollama.chat(model=model_name, messages=messages)

    return _ollama_chat


def detect_language(text: str) -> str:
    sample = (text or "").strip()
    if not sample:
        return "en"

    if re.search(r"[\u4E00-\u9FFF\u3400-\u4DBF]", sample):
        return "zh"
    if re.search(r"[\u10A0-\u10FF]", sample):
        return "ka"
    if re.search(r"[\u0370-\u03FF]", sample):
        return "el"
    if re.search(r"[\u0C00-\u0C7F]", sample):
        return "te"
    if re.search(r"[\u0D00-\u0D7F]", sample):
        return "ml"

    if re.search(r"[\u0600-\u06FF]", sample):
        if re.search(r"[پچژگکیے]", sample):
            return "fa"
        return "ar"

    if re.search(r"[\u0400-\u04FF]", sample):
        lowered = sample.lower()
        if re.search(r"[іїєґ]", lowered):
            return "uk"
        if re.search(r"[ђјљњћџ]", lowered):
            return "sr"
        if re.search(r"[әғқңөүұһі]", lowered):
            return "kk"
        if re.search(r"[ѝъщ]", lowered):
            return "bg"
        return "ru"

    if langdetect_detect is not None:
        try:
            detected = langdetect_detect(sample).lower()
            language_map = {
                "ar": "ar",
                "bg": "bg",
                "ca": "ca",
                "cs": "cs",
                "cy": "cy",
                "da": "da",
                "de": "de",
                "el": "el",
                "en": "en",
                "es": "es",
                "et": "lv",
                "fa": "fa",
                "fi": "fi",
                "fr": "fr",
                "hi": "hi",
                "hu": "hu",
                "id": "id",
                "is": "is",
                "it": "it",
                "ka": "ka",
                "kk": "kk",
                "lb": "lb",
                "lv": "lv",
                "ml": "ml",
                "ne": "ne",
                "nl": "nl",
                "no": "no",
                "pl": "pl",
                "pt": "pt",
                "ro": "ro",
                "ru": "ru",
                "sk": "sk",
                "sl": "sl",
                "sq": "ro",
                "sr": "sr",
                "sv": "sv",
                "sw": "sw",
                "te": "te",
                "tr": "tr",
                "uk": "uk",
                "ur": "ar",
                "vi": "vi",
                "zh-cn": "zh",
                "zh-tw": "zh",
                "zh": "zh",
            }
            return language_map.get(detected, "en")
        except LangDetectException:
            pass

    return "en"


def normalize_memory_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def fetch_url(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/132.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru,en-US;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Connection": "close",
    }
    last_error = None
    for attempt in range(3):
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=10 + attempt * 5) as response:
                return response.read().decode("utf-8", errors="replace")
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
            print(f"[NET] fetch_url attempt {attempt + 1} failed for {url}: {exc}")
            time.sleep(0.6 * (attempt + 1))

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Failed to fetch URL: {url}")


def fetch_json(url: str, method: str = "GET", payload=None, headers=None, timeout: float = 3):
    body = None
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, headers=request_headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        content = response.read().decode("utf-8", errors="replace")
        return json.loads(content) if content else {}
