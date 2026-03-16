import io
import os
import wave
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import soundfile as sf
from piper import PiperVoice


QUALITY_PRIORITY = {
    "medium": 0,
    "high": 1,
    "low": 2,
    "x_low": 3,
}

LANGUAGE_FALLBACKS = {
    "be": "ru",
    "bs": "sr",
    "hr": "sr",
    "ja": "en",
    "ko": "en",
}


@dataclass(frozen=True)
class TTSConfig:
    piper_voices_dir: str


class TTSBackend(Protocol):
    def synthesize(self, text: str, language: str) -> tuple[object, int]:
        ...

    def supported_languages(self) -> list[str]:
        ...


class PiperTTSBackend:
    def __init__(self, config: TTSConfig):
        self.config = config
        self._voice_paths = self._discover_voice_paths(config.piper_voices_dir)
        self._voices: dict[str, PiperVoice] = {}

        if not self._voice_paths:
            raise RuntimeError(f"No Piper voices were found in: {config.piper_voices_dir}")

    def _discover_voice_paths(self, voices_dir: str) -> dict[str, str]:
        discovered: dict[str, tuple[int, str]] = {}

        for root, _, files in os.walk(voices_dir):
            for file_name in files:
                if not file_name.endswith(".onnx"):
                    continue

                full_path = os.path.join(root, file_name)
                relative_path = os.path.relpath(full_path, voices_dir)
                parts = relative_path.split(os.sep)
                if not parts:
                    continue

                language = parts[0].lower()
                if language.startswith("_"):
                    continue

                quality = parts[-2].lower() if len(parts) >= 2 else ""
                score = QUALITY_PRIORITY.get(quality, 99)
                current = discovered.get(language)
                candidate = (score, full_path)
                if current is None or candidate < current:
                    discovered[language] = candidate

        return {language: path for language, (_, path) in discovered.items()}

    def _resolve_language(self, language: str) -> str:
        normalized = (language or "").strip().lower()
        if not normalized:
            normalized = "en"

        candidates = []
        if normalized:
            candidates.append(normalized)
        if "-" in normalized:
            candidates.append(normalized.split("-", 1)[0])
        if "_" in normalized:
            candidates.append(normalized.split("_", 1)[0])

        base_code = normalized.split("-", 1)[0].split("_", 1)[0]
        fallback = LANGUAGE_FALLBACKS.get(base_code)
        if fallback:
            candidates.append(fallback)
        candidates.append("en")

        seen = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            if candidate in self._voice_paths:
                return candidate

        return "en"

    def _get_voice(self, language: str) -> PiperVoice:
        resolved_language = self._resolve_language(language)
        if resolved_language not in self._voices:
            voice_path = self._voice_paths[resolved_language]
            print(f"[TTS] Loading Piper voice for {resolved_language}: {voice_path}")
            self._voices[resolved_language] = PiperVoice.load(voice_path)
            print(f"[TTS] Piper voice loaded for {resolved_language}.")
        return self._voices[resolved_language]

    def supported_languages(self) -> list[str]:
        return sorted(self._voice_paths.keys())

    def synthesize(self, text: str, language: str):
        tts_voice = self._get_voice(language)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(22050)
            tts_voice.synthesize_wav(text, wav_file)

        buffer.seek(0)
        audio, sample_rate = sf.read(buffer, dtype="float32")
        return audio, sample_rate


def create_tts_backend(config: TTSConfig) -> TTSBackend:
    return PiperTTSBackend(config)
