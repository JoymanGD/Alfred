import difflib
import re
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


def extract_wake_command(text: str, wake_words: list[str], normalize_text):
    cleaned = normalize_text(text)
    lowered = cleaned.lower()

    for wake_word in wake_words:
        if lowered == wake_word:
            return ""
        match = re.search(rf"\b{re.escape(wake_word)}\b", lowered)
        if not match:
            continue

        before = cleaned[:match.start()].strip(" ,.!?:;")
        after = cleaned[match.end():].strip(" ,.!?:;")

        if after:
            return after
        if before:
            return before
        return ""

    fuzzy_match = _find_fuzzy_wake_match(cleaned, lowered, wake_words)
    if fuzzy_match is not None:
        start, end = fuzzy_match
        before = cleaned[:start].strip(" ,.!?:;")
        after = cleaned[end:].strip(" ,.!?:;")

        if after:
            print(f"[WAKE] Fuzzy wake word match: {cleaned[start:end]}")
            return after
        if before:
            print(f"[WAKE] Fuzzy wake word match: {cleaned[start:end]}")
            return before
        print(f"[WAKE] Ignored fuzzy wake-only match: {cleaned[start:end]}")
        return None

    return None


def _find_fuzzy_wake_match(cleaned: str, lowered: str, wake_words: list[str]) -> tuple[int, int] | None:
    token_matches = list(re.finditer(r"[a-zA-Z\u0400-\u04FF]+", lowered))
    candidates: list[tuple[str, int, int]] = []

    for index, match in enumerate(token_matches):
        candidates.append((match.group(0), match.start(), match.end()))
        if index + 1 < len(token_matches):
            next_match = token_matches[index + 1]
            combined = match.group(0) + next_match.group(0)
            candidates.append((combined, match.start(), next_match.end()))

    best_match: tuple[int, int] | None = None
    best_score = 0.0
    for candidate, start, end in candidates:
        for wake_word in wake_words:
            score = _wake_similarity(candidate, wake_word)
            if score > best_score:
                best_score = score
                best_match = (start, end)

    if best_score >= 0.72:
        return best_match
    return None


def _wake_similarity(candidate: str, wake_word: str) -> float:
    if not candidate or not wake_word:
        return 0.0
    if candidate[0] != wake_word[0]:
        return 0.0
    if abs(len(candidate) - len(wake_word)) > 3:
        return 0.0
    return difflib.SequenceMatcher(a=candidate, b=wake_word).ratio()


def mic_listener(
    event_in,
    get_vad_model,
    get_asr_model,
    min_speech_seconds: float,
    end_silence_seconds: float,
    pre_speech_seconds: float,
    wake_followup_seconds: float,
    tts_playing_event,
    tts_interrupt_event,
    followup_signal_queue,
    restart_signal_queue,
    wake_words: list[str],
    hotwords: list[str] | None,
    normalize_text,
):
    while True:
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
                f"wake_followup={wake_followup_seconds:.2f}s"
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
                wake_followup_until = 0.0
                self_echo_guard_until = 0.0

                while True:
                    while not followup_signal_queue.empty():
                        followup_signal_queue.get_nowait()
                        wake_followup_until = time.monotonic() + wake_followup_seconds
                        self_echo_guard_until = time.monotonic() + 1.2
                        print("[WAKE] Refreshed follow-up window after assistant reply.")

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

                    if speaking_now and tts_playing_event.is_set():
                        print("[TTS] Interrupted by user speech.")
                        tts_interrupt_event.set()

                    if speaking_now:
                        if not utterance_active:
                            speech_chunks = list(pre_speech_buffer)
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
                            wake_command = extract_wake_command(text, wake_words, normalize_text)
                            now = time.monotonic()
                            if now < self_echo_guard_until:
                                if wake_command == "" or wake_command is None:
                                    print("[WAKE] Ignored likely self-echo right after assistant reply.")
                                    continue
                            if wake_command is None:
                                if now < wake_followup_until:
                                    print("[WAKE] Accepted follow-up command without wake word.")
                                    wake_followup_until = now + wake_followup_seconds
                                    event_in.put(normalize_text(text))
                                    continue
                                print("[WAKE] Ignored speech without wake word.")
                                continue
                            wake_followup_until = time.monotonic() + wake_followup_seconds
                            event_in.put(wake_command)
        except Exception as e:
            print(f"[ASR ERROR]: microphone listener crashed, restarting: {e}")
            time.sleep(0.5)
