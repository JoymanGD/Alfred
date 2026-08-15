import argparse
import os
import queue
import subprocess
import sys
import threading
import time
import ctypes
from pathlib import Path

import torch

from alfred.agent import AgentConfig, agent_loop, build_tool_registry
from alfred.audio import AudioEngine, speaker_loop
from alfred.config import load_config
from alfred.conversation import ConversationMemory
from alfred.input import DEFAULT_HOST, DEFAULT_PORT, LocalInputServer, RemoteInputClient
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
from alfred.screen import analyze_screen_request
from alfred.tts import TTSConfig, create_tts_backend
from alfred.vision import VisionConfig, analyze_vision_request


class StreamTee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for stream in self.streams:
            stream.write(data)
        return len(data)

    def flush(self):
        for stream in self.streams:
            stream.flush()

    def isatty(self):
        return any(getattr(stream, "isatty", lambda: False)() for stream in self.streams)


def configure_console_logging():
    log_path = os.getenv("ALFRED_LOG", "").strip()
    if not log_path:
        return

    log_file = open(log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = StreamTee(sys.stdout, log_file)
    sys.stderr = StreamTee(sys.stderr, log_file)


configure_console_logging()
print("Running Python:", sys.executable)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
config = load_config(BASE_DIR)

event_in = queue.Queue()
event_out = queue.Queue()
event_img = queue.Queue()
followup_signal_queue = queue.Queue()
restart_listener_queue = queue.Queue()
assistant_control_queue = queue.Queue()

tts_interrupt_event = threading.Event()
tts_playing_event = threading.Event()


ollama_chat = make_ollama_chat(config.ollama_model)
router_ollama_chat = make_ollama_chat(config.router_ollama_model, think=False)
services = ModelServices(config)
conversation_memory = ConversationMemory(config.max_history_messages, normalize_memory_text)
tts_backend = create_tts_backend(
    TTSConfig(
        device=config.tts_device,
        reference_audio_path=config.tts_reference_audio_path,
        reference_text=config.tts_reference_text,
        voice_instruct=config.tts_voice_instruct,
    )
)
audio_engine = AudioEngine(tts_backend, detect_language, config.tts_gain)
tts_languages = ", ".join(tts_backend.supported_languages())


agent_config = AgentConfig(
    base_dir=config.base_dir,
    codex_exe=config.codex_exe,
    system_prompt=config.system_prompt,
    ru_weather_words=config.ru_weather_words,
    ru_search_words=config.ru_search_words,
    ru_fact_words=config.ru_fact_words,
    plan_storage_path=config.plan_storage_path,
    vision_config=VisionConfig(
        enabled=config.camera_enabled,
        camera_indexes=config.camera_indexes,
        warmup_frames=config.camera_warmup_frames,
    ),
)
tool_registry = build_tool_registry(agent_config)


def generate_image_payload(prompt: str):
    return services.generate_image_payload(prompt)


def analyze_vision_payload(prompt: str):
    return analyze_vision_request(
        text=prompt,
        config=agent_config.vision_config,
        ollama_chat=ollama_chat,
        normalize_text=normalize_memory_text,
        system_prompt=config.system_prompt,
        language=detect_language(prompt),
    )


def analyze_screen_payload(prompt: str):
    return analyze_screen_request(
        text=prompt,
        ollama_chat=ollama_chat,
        normalize_text=normalize_memory_text,
        system_prompt=config.system_prompt,
        language=detect_language(prompt),
    )


def request_listener_restart():
    restart_listener_queue.put("restart")


def request_assistant_control(action: str, **payload):
    assistant_control_queue.put({"action": action, **payload})


setattr(agent_config, "analyze_vision", analyze_vision_payload)
setattr(agent_config, "analyze_screen", analyze_screen_payload)
setattr(agent_config, "restart_listener", request_listener_restart)
setattr(agent_config, "assistant_control", request_assistant_control)


def stop_alfred(exit_code: int = 0):
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(exit_code)


def close_console_window():
    try:
        console_window = ctypes.windll.kernel32.GetConsoleWindow()
        if console_window:
            ctypes.windll.user32.PostMessageW(console_window, 0x0010, 0, 0)
    except Exception:
        pass


def resolve_core_launcher() -> Path:
    root_dir = Path(BASE_DIR)
    for launcher_name in ("start_alfred_core.bat", "run_alfred.bat"):
        launcher_path = root_dir / launcher_name
        if launcher_path.exists():
            return launcher_path
    raise FileNotFoundError(f"Alfred launcher not found in {root_dir}")


def launch_replacement_core():
    launcher_path = resolve_core_launcher()
    launcher_text = str(launcher_path).replace("'", "''")
    base_dir_text = BASE_DIR.replace("'", "''")
    helper_command = (
        f"$parentId = {os.getpid()}; "
        "while (Get-Process -Id $parentId -ErrorAction SilentlyContinue) { "
        "Start-Sleep -Milliseconds 200 "
        "}; "
        f"Start-Process -FilePath '{launcher_text}' -WorkingDirectory '{base_dir_text}'"
    )
    subprocess.Popen(
        ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", helper_command],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def wait_for_tts_completion(tts_event: threading.Event, timeout_seconds: float = 15.0):
    deadline = time.time() + timeout_seconds
    saw_tts = False
    while time.time() < deadline:
        if tts_event.is_set():
            saw_tts = True
            break
        time.sleep(0.05)
    if saw_tts:
        while tts_event.is_set():
            time.sleep(0.05)
    else:
        time.sleep(0.5)


def assistant_control_loop(control_queue: queue.Queue, tts_event: threading.Event):
    while True:
        command = control_queue.get()
        action = command.get("action")
        if action == "restart_assistant":
            if command.get("wait_for_tts"):
                wait_for_tts_completion(tts_event)
            print("[CONTROL] Restarting Alfred core.")
            launch_replacement_core()
            close_console_window()
            stop_alfred(0)
        if action == "shutdown_assistant":
            if command.get("wait_for_tts"):
                wait_for_tts_completion(tts_event)
            print("[CONTROL] Shutting Alfred down.")
            close_console_window()
            stop_alfred(0)


def install_console_close_handler():
    if os.name != "nt":
        return

    handler = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)
    close_events = {0, 1, 2, 5, 6}

    def _handle_console_event(event_type):
        if event_type in close_events:
            print(f"\n[CONSOLE] Received console close event {event_type}. Stopping Alfred.")
            stop_alfred(0)
        return False

    console_handler = handler(_handle_console_event)
    ctypes.windll.kernel32.SetConsoleCtrlHandler(console_handler, True)
    setattr(install_console_close_handler, "_handler_ref", console_handler)


def console_chat_loop(input_sink, followup_queue=None):
    if not sys.stdin or not sys.stdin.isatty():
        print("[CHAT] Interactive input is unavailable in this launch mode.")
        return

    print("[CHAT] Type a message for Alfred and press Enter.")
    print("[CHAT] Commands: /wake, /quit")
    print()
    while True:
        try:
            user_text = input("You> ").strip()
        except EOFError:
            print("\n[CHAT] Input stream closed.")
            return
        except KeyboardInterrupt:
            print("\n[CHAT] Stopping chat window.")
            return

        if not user_text:
            continue
        if user_text.lower() == "/quit":
            print("[CHAT] Stopping chat window.")
            return
        if user_text.lower() == "/wake":
            input_sink.trigger_wake()
            continue

        input_sink.submit_text(normalize_memory_text(user_text))
        print()
        if followup_queue is not None:
            while True:
                signal = followup_queue.get()
                if signal == "assistant_replied":
                    break


def configure_cuda():
    if services.get_image_device().startswith("cuda"):
        torch.backends.cuda.enable_flash_sdp(True)
        torch.backends.cuda.enable_mem_efficient_sdp(True)
        torch.backends.cuda.enable_math_sdp(True)


def print_core_banner():
    image_device = services.get_image_device()
    print(f"[LLM] Using Ollama model: {config.ollama_model}")
    print(f"[ROUTER] Using Ollama model: {config.router_ollama_model}")
    print(f"[OLLAMA] Executable path: {config.ollama_exe}")
    print(f"[ASR] Whisper config: model={config.whisper_model}, compute_type={config.whisper_compute_type}")
    print(
        f"[TTS] Backend: OmniVoice "
        f"(device={config.tts_device}, reference_audio={config.tts_reference_audio_path or 'default'}, "
        f"voice_instruct={config.tts_voice_instruct})"
    )
    print(f"[TTS] Supported languages: {tts_languages}")
    print(f"[TTS] Gain: {config.tts_gain:.2f}x")
    if image_device.startswith("cuda"):
        print(
            "[CUDA] SDP backends: "
            f"flash={torch.backends.cuda.flash_sdp_enabled()}, "
            f"mem_efficient={torch.backends.cuda.mem_efficient_sdp_enabled()}, "
            f"math={torch.backends.cuda.math_sdp_enabled()}"
        )
    print(
        f"[IMAGE] Model: {config.image_model_id}, dtype={config.image_torch_dtype}, "
        f"device={image_device}, steps={config.image_steps}, cfg={config.image_guidance_scale}, "
        f"size={config.image_width}x{config.image_height}, sampler={config.image_sampler}, "
        f"attention_backend={config.image_attention_backend or 'default'}, compile={config.image_compile}"
    )
    print(
        f"[VISION] Camera enabled={config.camera_enabled}, "
        f"indexes={config.camera_indexes}, warmup_frames={config.camera_warmup_frames}"
    )
    print("[MUSIC] Backend: YouTube Music Web")


def start_core(host: str, port: int):
    install_console_close_handler()
    ensure_ollama_running(config.ollama_exe)
    configure_cuda()
    print_core_banner()

    input_server = LocalInputServer(
        event_in=event_in,
        tts_interrupt_event=tts_interrupt_event,
        host=host,
        port=port,
        tts_warmup_callback=audio_engine.warmup_tts,
    )
    input_server.start()
    threading.Thread(
        target=assistant_control_loop,
        args=(assistant_control_queue, tts_playing_event),
        daemon=True,
    ).start()
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
            router_ollama_chat,
            normalize_memory_text,
            detect_language,
            fetch_url,
            fetch_json,
            generate_image_payload,
        ),
        daemon=True,
    ).start()
    threading.Thread(
        target=speaker_loop,
        args=(
            event_out,
            event_img,
            audio_engine,
            tts_interrupt_event,
            tts_playing_event,
            BASE_DIR,
            followup_signal_queue,
            input_server,
        ),
        daemon=True,
    ).start()
    print("[START] Core agent, TTS, and remote input server started.")


def start_chat_client(host: str, port: int):
    install_console_close_handler()
    print(f"[CHAT] Connecting to Alfred core at {host}:{port}")
    try:
        client = RemoteInputClient(
            host=host,
            port=port,
            role="chat",
            followup_signal_queue=followup_signal_queue,
        )
    except OSError as e:
        print(f"[CHAT] Failed to connect to Alfred core: {e}")
        raise SystemExit(1) from e
    console_chat_loop(client, followup_signal_queue)


def start_voice_client(host: str, port: int):
    install_console_close_handler()
    print(f"[VOICE] Connecting to Alfred core at {host}:{port}")
    try:
        client = RemoteInputClient(
            host=host,
            port=port,
            role="voice",
            tts_playing_event=tts_playing_event,
            followup_signal_queue=followup_signal_queue,
        )
    except OSError as e:
        print(f"[VOICE] Failed to connect to Alfred core: {e}")
        raise SystemExit(1) from e

    print(
        f"[VOICE] Whisper config: model={config.whisper_model}, compute_type={config.whisper_compute_type}, "
        f"wake_followup={config.wake_followup_seconds:.2f}s"
    )
    mic_listener(
        client,
        services.get_vad_model,
        services.get_asr_model,
        config.min_speech_seconds,
        config.end_silence_seconds,
        config.pre_speech_seconds,
        config.wake_followup_seconds,
        tts_playing_event,
        followup_signal_queue,
        restart_listener_queue,
        config.wake_words,
        config.asr_hotwords,
        normalize_memory_text,
    )


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("core", "chat", "voice"),
        default=os.getenv("ALFRED_MODE", "core").strip().lower() or "core",
    )
    parser.add_argument("--input-host", default=os.getenv("ALFRED_INPUT_HOST", DEFAULT_HOST))
    parser.add_argument(
        "--input-port",
        type=int,
        default=int(os.getenv("ALFRED_INPUT_PORT", str(DEFAULT_PORT))),
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.mode == "core":
        start_core(args.input_host, args.input_port)
    elif args.mode == "chat":
        start_chat_client(args.input_host, args.input_port)
        return
    elif args.mode == "voice":
        start_voice_client(args.input_host, args.input_port)
        return

    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
