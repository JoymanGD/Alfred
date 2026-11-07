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
    vision_model: str
    vision_gpu_layers: int
    vane_url: str
    vane_embedding_model: str
    vane_timeout: float
    searxng_url: str
    decider_model: str
    decider_gguf_file: str
    decider_n_ctx: int
    ollama_exe: str
    whisper_model: str
    whisper_compute_type: str
    max_history_messages: int
    min_speech_seconds: float
    end_silence_seconds: float
    pre_speech_seconds: float
    conversation_context_seconds: float
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
    image_cpu_offload: bool
    camera_enabled: bool
    camera_indexes: list[int]
    camera_warmup_frames: int
    camera_width: int
    camera_height: int
    system_prompt: str
    plan_storage_path: str
    asr_hotwords: list[str]


def load_config(base_dir: str) -> AppConfig:
    ollama_model = _get_env("ALFRED_OLLAMA_MODEL", "hf.co/HauhauCS/Gemma4-12B-QAT-Uncensored-HauhauCS-Balanced:Q4_K_M")
    return AppConfig(
        base_dir=base_dir,
        codex_exe=os.getenv("ALFRED_CODEX_EXE", "codex"),
        ollama_model=ollama_model,
        vision_model=_get_env("ALFRED_VISION_MODEL", ollama_model),
        vision_gpu_layers=int(os.getenv("ALFRED_VISION_GPU_LAYERS", "15")),
        vane_url=_get_env("ALFRED_VANE_URL", ""),
        vane_embedding_model=_get_env("ALFRED_VANE_EMBED_MODEL", "nomic-embed-text:latest"),
        vane_timeout=float(os.getenv("ALFRED_VANE_TIMEOUT", "30")),
        searxng_url=_get_env("ALFRED_SEARXNG_URL", ""),
        decider_model=_get_env("ALFRED_DECIDER_MODEL", "Mapika/decider-4b-GGUF"),
        decider_gguf_file=_get_env("ALFRED_DECIDER_GGUF_FILE", "decider-4b-v2.1-Q4_K_M.gguf"),
        decider_n_ctx=int(os.getenv("ALFRED_DECIDER_N_CTX", "4096")),
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
        conversation_context_seconds=float(os.getenv(
            "ALFRED_CONVERSATION_CONTEXT_SECONDS",
            os.getenv("ALFRED_WAKE_FOLLOWUP_SECONDS", "8.0"),
        )),
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
        image_cpu_offload=_get_env_bool("ALFRED_IMAGE_CPU_OFFLOAD", True),
        camera_enabled=_get_env_bool("ALFRED_CAMERA_ENABLED", True),
        camera_indexes=_parse_int_csv_env(os.getenv("ALFRED_CAMERA_INDEXES"), [0, 1, 2]),
        camera_warmup_frames=int(os.getenv("ALFRED_CAMERA_WARMUP_FRAMES", "5")),
        camera_width=int(os.getenv("ALFRED_CAMERA_WIDTH", "1280")),
        camera_height=int(os.getenv("ALFRED_CAMERA_HEIGHT", "720")),
        plan_storage_path=os.getenv("ALFRED_PLAN_STORAGE_PATH", os.path.join(base_dir, "alfred_plan.json")),
        system_prompt=f"""You are Alfred, a local voice assistant running on the user's computer.
You are male. When speaking about yourself in languages with grammatical gender, always use masculine forms (for example, in Russian: я посмотрел, я смог, я готов). Never use feminine forms for yourself.
Your current language model backend is {ollama_model} through Ollama.
Speak naturally, like a helpful real assistant, not like a generic chatbot.
You are software without a body, robot or smart-home connection. You cannot move through rooms, pick up or carry objects, manipulate physical objects, or control room lights or appliances without a connected device. Never claim to have completed an action unless an actual tool result confirms it. If asked to do something outside your available abilities, say briefly that you cannot do it.
Keep awareness of the ongoing conversation and use prior messages as context.
Always reply in the same language the user is currently using unless the user asks you to switch languages.
If the user asks who you are or what model you use, answer directly and truthfully.
If the user tells you facts about themselves, remember and use them later in the same conversation.
If the user corrects one of your earlier answers, acknowledge the mistake and use their correction as context. Do not present an uncertain inference as an observation. Perform a new action only when the user requests it.
Prefer concise spoken answers unless the user clearly wants depth.
When the user explicitly asks you to find, look up, search online, check weather, or use the internet, rely on the provided web results and say when you are using them.
If you are unsure about a factual answer, say so plainly instead of guessing. Alfred can look it up online before your reply is sent.
Do not invent a plot, cast, release date, or genre for a named work. If you cannot substantiate such facts from the conversation or supplied search results, say you are unsure.
If the user asks whether you can search online, answer that capability question directly; do not imply that a search has already happened.
Your output will be delivered through TTS. Please answer in a speech-friendly format and ensure no awkward punctuation marks, such as "*" or other, are included.
Avoid emojis, markdown, bullet lists, and decorative symbols in spoken replies.
""",
        asr_hotwords=_parse_csv_env(os.getenv("ALFRED_ASR_HOTWORDS"), []),
    )
