import os
from dataclasses import dataclass


def _parse_csv_env(value: str | None, default: list[str]) -> list[str]:
    if not value:
        return default
    items = [item.strip() for item in value.split(",")]
    return [item for item in items if item] or default


def _parse_int_csv_env(value: str | None, default: list[int]) -> list[int]:
    if not value:
        return default
    items: list[int] = []
    for item in value.split(","):
        stripped = item.strip()
        if not stripped:
            continue
        try:
            items.append(int(stripped))
        except ValueError:
            continue
    return items or default


def _get_env(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _get_env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AppConfig:
    base_dir: str
    codex_exe: str
    ollama_model: str
    router_ollama_model: str
    ollama_exe: str
    whisper_model: str
    whisper_compute_type: str
    max_history_messages: int
    min_speech_seconds: float
    end_silence_seconds: float
    pre_speech_seconds: float
    wake_followup_seconds: float
    tts_gain: float
    tts_device: str
    tts_reference_audio_path: str
    tts_reference_text: str
    tts_voice_instruct: str
    image_model_id: str
    image_torch_dtype: str
    image_device: str
    image_steps: int
    image_width: int
    image_height: int
    image_guidance_scale: float
    image_sampler: str
    image_attention_backend: str
    image_compile: bool
    camera_enabled: bool
    camera_indexes: list[int]
    camera_warmup_frames: int
    system_prompt: str
    ru_weather_words: list[str]
    ru_search_words: list[str]
    ru_fact_words: list[str]
    plan_storage_path: str
    wake_words: list[str]
    asr_hotwords: list[str]


def load_config(base_dir: str) -> AppConfig:
    default_hotwords = [
        "Alfred",
        "alfred",
        "\u0410\u043b\u044c\u0444\u0440\u0435\u0434",
        "\u0430\u043b\u044c\u0444\u0440\u0435\u0434",
        "\u0410\u043b\u0444\u0440\u0435\u0434",
        "\u0430\u043b\u0444\u0440\u0435\u0434",
    ]
    ollama_model = _get_env("ALFRED_OLLAMA_MODEL", "deepseek-r1:14b")
    router_ollama_model = _get_env("ALFRED_ROUTER_OLLAMA_MODEL", ollama_model)
    return AppConfig(
        base_dir=base_dir,
        codex_exe=os.getenv("ALFRED_CODEX_EXE", "codex"),
        ollama_model=ollama_model,
        router_ollama_model=router_ollama_model,
        ollama_exe=os.getenv(
            "ALFRED_OLLAMA_EXE",
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe"),
        ),
        whisper_model=_get_env("ALFRED_WHISPER_MODEL", "small"),
        whisper_compute_type=_get_env("ALFRED_WHISPER_COMPUTE_TYPE", "float16"),
        max_history_messages=int(os.getenv("ALFRED_MAX_HISTORY_MESSAGES", "12")),
        min_speech_seconds=float(os.getenv("ALFRED_MIN_SPEECH_SECONDS", "0.7")),
        end_silence_seconds=float(os.getenv("ALFRED_END_SILENCE_SECONDS", "1.35")),
        pre_speech_seconds=float(os.getenv("ALFRED_PRE_SPEECH_SECONDS", "0.45")),
        wake_followup_seconds=float(os.getenv("ALFRED_WAKE_FOLLOWUP_SECONDS", "8.0")),
        tts_gain=float(os.getenv("ALFRED_TTS_GAIN", "2.4")),
        tts_device=_get_env("ALFRED_TTS_DEVICE", "auto"),
        tts_reference_audio_path=os.getenv("ALFRED_TTS_REFERENCE_AUDIO", ""),
        tts_reference_text=os.getenv("ALFRED_TTS_REFERENCE_TEXT", ""),
        tts_voice_instruct=_get_env(
            "ALFRED_TTS_VOICE_INSTRUCT",
            "male, low pitch",
        ),
        image_model_id=_get_env("ALFRED_IMAGE_MODEL_ID", "Tongyi-MAI/Z-Image-Turbo"),
        image_torch_dtype=_get_env("ALFRED_IMAGE_TORCH_DTYPE", "bfloat16"),
        image_device=_get_env("ALFRED_IMAGE_DEVICE", "cpu"),
        image_steps=int(os.getenv("ALFRED_IMAGE_STEPS", "8")),
        image_width=int(os.getenv("ALFRED_IMAGE_WIDTH", "768")),
        image_height=int(os.getenv("ALFRED_IMAGE_HEIGHT", "768")),
        image_guidance_scale=float(os.getenv("ALFRED_IMAGE_GUIDANCE_SCALE", "0.0")),
        image_sampler=_get_env("ALFRED_IMAGE_SAMPLER", "default"),
        image_attention_backend=_get_env("ALFRED_IMAGE_ATTENTION_BACKEND", ""),
        image_compile=_get_env_bool("ALFRED_IMAGE_COMPILE", False),
        camera_enabled=_get_env_bool("ALFRED_CAMERA_ENABLED", True),
        camera_indexes=_parse_int_csv_env(os.getenv("ALFRED_CAMERA_INDEXES"), [0, 1, 2]),
        camera_warmup_frames=int(os.getenv("ALFRED_CAMERA_WARMUP_FRAMES", "5")),
        plan_storage_path=os.getenv("ALFRED_PLAN_STORAGE_PATH", os.path.join(base_dir, "alfred_plan.json")),
        system_prompt=f"""You are Alfred, a local voice assistant running on the user's computer.
Your current language model backend is {ollama_model} through Ollama.
Speak naturally, like a helpful real assistant, not like a generic chatbot.
Keep awareness of the ongoing conversation and use prior messages as context.
Always reply in the same language the user is currently using unless the user asks you to switch languages.
If the user asks who you are or what model you use, answer directly and truthfully.
If the user tells you facts about themselves, remember and use them later in the same conversation.
Prefer concise spoken answers unless the user clearly wants depth.
When the user explicitly asks you to find, look up, search online, check weather, or use the internet, rely on the provided web results and say when you are using them.
Your output will be delivered through TTS. Please answer in a speech-friendly format and ensure no awkward punctuation marks, such as "*" or other, are included.
Avoid emojis, markdown, bullet lists, and decorative symbols in spoken replies.
""",
        ru_weather_words=[
            "\u043f\u043e\u0433\u043e\u0434\u0430",
            "\u0442\u0435\u043c\u043f\u0435\u0440\u0430\u0442\u0443\u0440\u0430",
            "\u043f\u0440\u043e\u0433\u043d\u043e\u0437",
        ],
        ru_search_words=[
            "\u043d\u0430\u0439\u0434\u0438",
            "\u043f\u043e\u0438\u0449\u0438",
            "\u0443\u0437\u043d\u0430\u0439",
            "\u043f\u043e\u0441\u043c\u043e\u0442\u0440\u0438 \u0432 \u0438\u043d\u0442\u0435\u0440\u043d\u0435\u0442\u0435",
            "\u043f\u043e\u0433\u0443\u0433\u043b\u0438",
        ],
        ru_fact_words=[
            "\u0434\u0430\u0442\u0430 \u0440\u043e\u0436\u0434\u0435\u043d\u0438\u044f",
            "\u043a\u043e\u0433\u0434\u0430 \u0440\u043e\u0434\u0438\u043b",
            "\u043a\u043e\u0433\u0434\u0430 \u0440\u043e\u0434\u0438\u043b\u0441\u044f",
            "\u043a\u043e\u0433\u0434\u0430 \u0440\u043e\u0434\u0438\u043b\u0430\u0441\u044c",
            "\u0441\u043a\u043e\u043b\u044c\u043a\u043e \u043b\u0435\u0442",
        ],
        wake_words=["alfred", "\u0430\u043b\u044c\u0444\u0440\u0435\u0434"],
        asr_hotwords=_parse_csv_env(os.getenv("ALFRED_ASR_HOTWORDS"), default_hotwords),
    )
