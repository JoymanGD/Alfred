import time
from collections import deque

import numpy as np
import sounddevice as sd
import torch


def transcribe_audio(local_asr_model, audio: np.ndarray, hotwords: list[str] | None = None) -> str:
    transcribe_kwargs = {"suppress_tokens": [-1]}
    if hotwords:
        transcribe_kwargs["hotwords"] = ",".join(hotwords)
    segments, _ = local_asr_model.transcribe(audio, **transcribe_kwargs)
    return "".join(seg.text for seg in segments).strip()


def mic_listener(
    input_sink,
    get_vad_model,
    get_asr_model,
    min_speech_seconds: float,
    end_silence_seconds: float,
    pre_speech_seconds: float,
    conversation_context_seconds: float,
    tts_playing_event,
    followup_signal_queue,
    restart_signal_queue,
    hotwords: list[str] | None,
    normalize_text,
):
    while True:
        if hasattr(input_sink, "wait_until_ready"):
            input_sink.wait_until_ready()
        try:
            local_vad_model = get_vad_model()
            local_asr_model = get_asr_model()

            sample_rate = 16000
            block_size = 512
            min_speech_samples = int(sample_rate * min_speech_seconds)
            silence_blocks_to_end = max(1, int(end_silence_seconds * sample_rate / block_size))
            pre_speech_blocks = max(1, int(pre_speech_seconds * sample_rate / block_size))

            print(
                f"[ASR] Microphone listener started. pre_speech={pre_speech_seconds:.2f}s, "
                f"conversation_context={conversation_context_seconds:.2f}s"
            )

            with sd.InputStream(
                channels=1,
                samplerate=sample_rate,
                dtype="float32",
                blocksize=block_size,
            ) as stream:
                speech_chunks = []
                silence_block_count = 0
                utterance_active = False
                pre_speech_buffer = deque(maxlen=pre_speech_blocks)
                conversation_until = 0.0
                assistant_audio_tail_until = 0.0
                speech_started_during_tts = False
                speech_started_after_tts = False

                while True:
                    if hasattr(input_sink, "is_ready") and not input_sink.is_ready:
                        print("[ASR] Core is loading; pausing microphone input.")
                        break
                    while not followup_signal_queue.empty():
                        followup_signal_queue.get_nowait()
                        conversation_until = time.monotonic() + conversation_context_seconds
                        assistant_audio_tail_until = time.monotonic() + 1.2
                        print("[VOICE] Conversation continuation window refreshed.")

                    if not restart_signal_queue.empty():
                        restart_signal_queue.get_nowait()
                        print("[ASR] Restart requested, recreating microphone stream.")
                        break

                    block, _ = stream.read(block_size)
                    pcm = block[:, 0].astype(np.float32)
                    pcm_tensor = torch.from_numpy(pcm)
                    speech_prob = local_vad_model(pcm_tensor, sample_rate).item()
                    speaking_now = speech_prob > 0.35
                    pre_speech_buffer.append(pcm.copy())

                    if speaking_now:
                        if not utterance_active:
                            speech_chunks = list(pre_speech_buffer)
                            speech_started_during_tts = tts_playing_event.is_set()
                            speech_started_after_tts = time.monotonic() < assistant_audio_tail_until
                        speech_chunks.append(pcm)
                        silence_block_count = 0
                        utterance_active = True
                        continue

                    if utterance_active:
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

                        text = transcribe_audio(local_asr_model, audio, hotwords=hotwords)
                        if text:
                            print(f"[ASR HEARD]: {text}")
                            input_sink.submit_voice_candidate(
                                normalize_text(text),
                                followup_active=time.monotonic() < conversation_until,
                                during_tts=speech_started_during_tts,
                                recent_tts=speech_started_after_tts,
                            )
        except Exception as e:
            print(f"[ASR ERROR]: microphone listener crashed, restarting: {e}")
            time.sleep(0.5)
