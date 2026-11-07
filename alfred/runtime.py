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


def make_ollama_chat(model_name: str, think=None, options=None):
    def _ollama_chat(messages):
        kwargs = {"model": model_name, "messages": messages, "keep_alive": -1}
        if think is not None:
            kwargs["think"] = think
        if options is not None:
            kwargs["options"] = options
        return ollama.chat(**kwargs)

    def _structured_chat(messages):
        return ollama.chat(
            model=model_name, messages=messages, keep_alive=-1,
            think=False, format="json", options={"temperature": 0, "seed": 42},
        )

    _ollama_chat.structured = _structured_chat
    return _ollama_chat


def preload_ollama_model(model_name: str, options=None):
    print(f"[LLM] Loading Ollama model: {model_name}...")
    ollama.generate(model=model_name, prompt="", keep_alive=-1, options=options)
    print(f"[LLM] Ollama model ready: {model_name}")


def warmup_ollama_chat(model_name: str, system_prompt: str):
    print(f"[LLM] Warming chat inference: {model_name}...")
    response = ollama.chat(
        model=model_name,
        messages=[
            {
                "role": "system",
                "content": f"{system_prompt}\n{build_reply_language_instruction('en')}",
            },
            {"role": "user", "content": "Reply with OK."},
        ],
        keep_alive=-1,
    )
    load_seconds = (response.get("load_duration") or 0) / 1_000_000_000
    prefill_seconds = (response.get("prompt_eval_duration") or 0) / 1_000_000_000
    decode_seconds = (response.get("eval_duration") or 0) / 1_000_000_000
    print(
        f"[LLM] Chat inference warmed: load={load_seconds:.3f}s, "
        f"prefill={prefill_seconds:.3f}s, decode={decode_seconds:.3f}s, "
        f"output_tokens={response.get('eval_count') or 0}."
    )


def build_reply_language_instruction(language: str) -> str:
    labels = {
        "ar": "Arabic",
        "bg": "Bulgarian",
        "ca": "Catalan",
        "cs": "Czech",
        "cy": "Welsh",
        "da": "Danish",
        "de": "German",
        "el": "Greek",
        "en": "English",
        "es": "Spanish",
        "fa": "Persian",
        "fi": "Finnish",
        "fr": "French",
        "hi": "Hindi",
        "hu": "Hungarian",
        "id": "Indonesian",
        "is": "Icelandic",
        "it": "Italian",
        "ka": "Georgian",
        "kk": "Kazakh",
        "lb": "Luxembourgish",
        "lv": "Latvian",
        "ml": "Malayalam",
        "ne": "Nepali",
        "nl": "Dutch",
        "no": "Norwegian",
        "pl": "Polish",
        "pt": "Portuguese",
        "ro": "Romanian",
        "ru": "Russian",
        "sk": "Slovak",
        "sl": "Slovenian",
        "sr": "Serbian",
        "sv": "Swedish",
        "sw": "Swahili",
        "te": "Telugu",
        "tr": "Turkish",
        "uk": "Ukrainian",
        "vi": "Vietnamese",
        "zh": "Chinese",
    }
    target = labels.get(language, "English")
    return (
        f"Reply in {target}. "
        "Use the language of the user's own request, not the language of quoted text, titles, filenames, URLs, code, or short foreign phrases. "
        "If the user explicitly asks for another language, follow that request."
    )


def detect_language(text: str) -> str:
    sample = (text or "").strip()
    if not sample:
        return "en"

    explicit_language = _detect_explicit_reply_language(sample)
    if explicit_language is not None:
        return explicit_language

    cleaned = _prepare_text_for_language_detection(sample)
    if len(re.findall(r"\w", cleaned, flags=re.UNICODE)) >= 3:
        sample = cleaned

    ascii_sample = re.sub(r"\s+", " ", sample)
    if re.fullmatch(r"[A-Za-z0-9 .,!?()'\":;_/\-]+", ascii_sample):
        words = re.findall(r"[A-Za-z]+", ascii_sample)
        if len(words) <= 8:
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
        if re.search(r"[ѝ]", lowered):
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


def _prepare_text_for_language_detection(text: str) -> str:
    sample = re.sub(r"https?://\S+|www\.\S+", " ", text)
    sample = re.sub(r"\b[A-Za-z]:\\[^\s]+", " ", sample)
    sample = re.sub(r"\b[\w.\-]+@[\w.\-]+\.\w+\b", " ", sample)
    sample = re.sub(r"`[^`]*`", " ", sample)
    sample = re.sub(r"\"[^\"]*\"", " ", sample)
    sample = re.sub(r"“[^”]*”", " ", sample)
    sample = re.sub(r"„[^“]*“", " ", sample)
    sample = re.sub(r"«[^»]*»", " ", sample)
    sample = re.sub(r"\[[^\]]*\]", " ", sample)
    sample = re.sub(r"\([^)]*\)", " ", sample)
    sample = re.sub(r"\s+", " ", sample).strip()
    if len(re.findall(r"\w", sample, flags=re.UNICODE)) < 3:
        return text
    return sample


def _detect_explicit_reply_language(text: str) -> str | None:
    lowered = text.lower()
    patterns = {
        "en": [
            r"\bin english\b",
            r"\banswer in english\b",
            r"\breply in english\b",
            r"\brespond in english\b",
            r"\bпо-английски\b",
            r"\bна английском\b",
            r"\bанглийською\b",
            r"\bанглійською\b",
        ],
        "ru": [
            r"\bin russian\b",
            r"\banswer in russian\b",
            r"\breply in russian\b",
            r"\brespond in russian\b",
            r"\bпо-русски\b",
            r"\bна русском\b",
            r"\bросійською\b",
        ],
        "uk": [
            r"\bin ukrainian\b",
            r"\banswer in ukrainian\b",
            r"\breply in ukrainian\b",
            r"\brespond in ukrainian\b",
            r"\bпо-украински\b",
            r"\bукраїнською\b",
            r"\bукраинским\b",
            r"\bукраинською\b",
        ],
    }
    for language, variants in patterns.items():
        if any(re.search(pattern, lowered) for pattern in variants):
            return language
    return None


def normalize_memory_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def fetch_url(url: str, *, timeout: float | None = None, attempts: int = 3) -> str:
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
    for attempt in range(max(1, attempts)):
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=timeout if timeout is not None else 10 + attempt * 5) as response:
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
