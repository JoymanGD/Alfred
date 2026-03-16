import os
import queue
import sys
import threading
import time

import torch

from alfred.agent import AgentConfig, agent_loop, build_tool_registry
from alfred.audio import AudioEngine, speaker_loop
from alfred.config import load_config
from alfred.conversation import ConversationMemory
from alfred.listen import mic_listener
from alfred.runtime import (
    detect_language,
    ensure_ollama_running,
    fetch_json,
    fetch_url,
    make_ollama_chat,
    normalize_memory_text,
)
from alfred.services import ModelServices
from alfred.tts import TTSConfig, create_tts_backend

print("Running Python:", sys.executable)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
config = load_config(BASE_DIR)

event_in = queue.Queue()
event_out = queue.Queue()
event_img = queue.Queue()

tts_interrupt_event = threading.Event()
tts_playing_event = threading.Event()


ollama_chat = make_ollama_chat(config.ollama_model)
services = ModelServices(config)
conversation_memory = ConversationMemory(config.max_history_messages, normalize_memory_text)
tts_backend = create_tts_backend(
    TTSConfig(piper_voices_dir=config.piper_voices_dir)
)
audio_engine = AudioEngine(tts_backend, detect_language)
tts_languages = ", ".join(tts_backend.supported_languages())


agent_config = AgentConfig(
    system_prompt=config.system_prompt,
    ru_weather_words=config.ru_weather_words,
    ru_search_words=config.ru_search_words,
    ru_fact_words=config.ru_fact_words,
)
tool_registry = build_tool_registry(agent_config)


def generate_image_payload(prompt: str):
    return services.generate_image_payload(prompt)



def start():
    ensure_ollama_running(config.ollama_exe)
    if config.image_device.startswith("cuda"):
        torch.backends.cuda.enable_flash_sdp(True)
        torch.backends.cuda.enable_mem_efficient_sdp(True)
        torch.backends.cuda.enable_math_sdp(True)
    print(f"[LLM] Using Ollama model: {config.ollama_model}")
    print(f"[OLLAMA] Executable path: {config.ollama_exe}")
    print(f"[ASR] Whisper config: model={config.whisper_model}, compute_type={config.whisper_compute_type}")
    print(f"[TTS] Backend: Piper (voices dir: {config.piper_voices_dir})")
    print(f"[TTS] Supported languages: {tts_languages}")
    if config.image_device.startswith("cuda"):
        print(
            "[CUDA] SDP backends: "
            f"flash={torch.backends.cuda.flash_sdp_enabled()}, "
            f"mem_efficient={torch.backends.cuda.mem_efficient_sdp_enabled()}, "
            f"math={torch.backends.cuda.math_sdp_enabled()}"
        )
    print(
        f"[IMAGE] Model: {config.image_model_id}, dtype={config.image_torch_dtype}, "
        f"device={config.image_device}, steps={config.image_steps}, cfg={config.image_guidance_scale}, "
        f"size={config.image_width}x{config.image_height}, sampler={config.image_sampler}, "
        f"attention_backend={config.image_attention_backend or 'default'}, compile={config.image_compile}"
    )
    print("[MUSIC] Backend: YouTube Music Web")
    threading.Thread(
        target=agent_loop,
        args=(
            event_in,
            event_out,
            event_img,
            tool_registry,
            conversation_memory,
            agent_config,
            ollama_chat,
            normalize_memory_text,
            detect_language,
            fetch_url,
            fetch_json,
            generate_image_payload,
        ),
        daemon=True,
    ).start()
    threading.Thread(
        target=mic_listener,
        args=(
            event_in,
            services.get_vad_model,
            services.get_asr_model,
            config.min_speech_seconds,
            config.end_silence_seconds,
            tts_playing_event,
            tts_interrupt_event,
            config.wake_words,
            config.asr_hotwords,
            normalize_memory_text,
        ),
        daemon=True,
    ).start()
    threading.Thread(
        target=speaker_loop,
        args=(event_out, event_img, audio_engine, tts_interrupt_event, tts_playing_event, BASE_DIR),
        daemon=True,
    ).start()
    print("[START] Agent, ASR, and TTS threads started.")


if __name__ == "__main__":
    start()
    while True:
        time.sleep(1)
