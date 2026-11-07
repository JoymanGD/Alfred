from dataclasses import dataclass
from typing import Protocol

import numpy as np
import torch
from omnivoice import OmniVoice
from omnivoice.utils.lang_map import LANG_IDS


LANGUAGE_FALLBACKS = {
    "ar": "arb",
    "be": "ru",
    "bs": "sr",
    "ca": "es",
    "cs": "en",
    "cy": "en",
    "fa-ir": "fa",
    "hr": "sr",
    "hu": "en",
    "id": "en",
    "is": "en",
    "ja": "en",
    "ko": "en",
    "lb": "de",
    "ml-in": "ml",
    "ne": "en",
    "nl-be": "nl",
    "pt-br": "pt",
    "pt-pt": "pt",
    "sl": "sr",
    "sw": "en",
    "te-in": "te",
    "uk-ua": "uk",
    "vi-vn": "vi",
    "zh-cn": "zh",
    "zh-tw": "zh",
}


@dataclass(frozen=True)
class TTSConfig:
    device: str
    reference_audio_path: str | None = None
    reference_text: str | None = None
    voice_instruct: str | None = None


class TTSBackend(Protocol):
    def synthesize(self, text: str, language: str) -> tuple[object, int]:
        ...

    def supported_languages(self) -> list[str]:
        ...

    def warmup(self) -> None:
        ...


class OmniVoiceTTSBackend:
    def __init__(self, config: TTSConfig):
        self.config = config
        self._device = self._resolve_device(config.device)
        self._reference_audio_path = self._resolve_reference_audio_path(config.reference_audio_path)
        self._reference_text = (config.reference_text or "").strip() or None
        self._voice_instruct = (config.voice_instruct or "").strip() or None
        self._model: OmniVoice | None = None
        self._voice_clone_prompt = None
        self._session_reference_audio = None
        self._session_reference_text = "Hello, I am Alfred, your personal assistant."
        self._supported_languages = set(LANG_IDS)

    def supported_languages(self) -> list[str]:
        return sorted(self._supported_languages)

    def warmup(self) -> None:
        try:
            self._prepare_voice()
        except RuntimeError as exc:
            if self._device.startswith("cuda") and "cuda" in str(exc).lower():
                print(f"[TTS] OmniVoice CUDA warmup failed: {exc}. Falling back to CPU.")
                self._device = "cpu"
                self._model = None
                self._voice_clone_prompt = None
                self._prepare_voice()
            else:
                raise

    def _prepare_voice(self) -> None:
        if self._voice_clone_prompt is not None:
            return
        model = self._get_model()
        if self._reference_audio_path:
            self._voice_clone_prompt = model.create_voice_clone_prompt(
                ref_audio=self._reference_audio_path,
                ref_text=self._reference_text,
            )
        else:
            if self._session_reference_audio is None:
                reference_audio = model.generate(
                    text=self._session_reference_text,
                    language="en",
                    instruct=self._voice_instruct,
                    normalize_text=False,
                    num_step=16,
                )[0]
                self._session_reference_audio = self._to_numpy_audio(reference_audio)
            self._voice_clone_prompt = model.create_voice_clone_prompt(
                ref_audio=(torch.from_numpy(self._session_reference_audio), model.sampling_rate),
                ref_text=self._session_reference_text,
            )
        print("[TTS] Session voice locked.")

    def synthesize(self, text: str, language: str):
        try:
            return self._synthesize_once(text, language)
        except RuntimeError as exc:
            if self._device.startswith("cuda") and "cuda" in str(exc).lower():
                print(f"[TTS] OmniVoice CUDA generation failed: {exc}. Falling back to CPU.")
                self._device = "cpu"
                self._model = None
                self._voice_clone_prompt = None
                return self._synthesize_once(text, language)
            raise

    def _synthesize_once(self, text: str, language: str):
        self._prepare_voice()
        model = self._get_model()
        resolved_language = self._resolve_language(language)
        result = model.generate(
            text=text,
            language=resolved_language,
            voice_clone_prompt=self._voice_clone_prompt,
            speed=1.0,
            normalize_text=False,
            num_step=16,
        )
        audio = self._to_numpy_audio(result[0])
        return audio, model.sampling_rate

    def _get_model(self) -> OmniVoice:
        if self._model is None:
            dtype = torch.float16 if self._device.startswith("cuda") else torch.float32
            print(f"[TTS] Loading OmniVoice on {self._device}...")
            self._model = OmniVoice.from_pretrained(
                "k2-fsa/OmniVoice",
                device_map=self._device,
                dtype=dtype,
            )
            print("[TTS] OmniVoice loaded.")
        return self._model

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
        fallback = LANGUAGE_FALLBACKS.get(normalized) or LANGUAGE_FALLBACKS.get(base_code)
        if fallback:
            candidates.append(fallback)
        candidates.append("en")

        seen = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            if candidate in self._supported_languages:
                return candidate

        return "en"

    @staticmethod
    def _resolve_device(configured_device: str) -> str:
        normalized = (configured_device or "").strip().lower()
        if normalized in {"cpu", "mps"}:
            return normalized
        if normalized in {"cuda", "auto", ""} and torch.cuda.is_available():
            return "cuda:0"
        return "cpu"

    @staticmethod
    def _resolve_reference_audio_path(reference_audio_path: str | None) -> str | None:
        candidate = (reference_audio_path or "").strip()
        if candidate:
            try:
                import os

                if os.path.exists(candidate):
                    return candidate
                print(f"[TTS] Reference audio was not found: {candidate}. Using OmniVoice default voice.")
            except Exception:
                pass
        return None

    @staticmethod
    def _to_numpy_audio(wav) -> np.ndarray:
        if isinstance(wav, np.ndarray):
            audio = wav
        elif torch.is_tensor(wav):
            audio = wav.detach().cpu().numpy()
        else:
            audio = np.asarray(wav)
        return np.asarray(audio, dtype=np.float32).reshape(-1)


def create_tts_backend(config: TTSConfig) -> TTSBackend:
    return OmniVoiceTTSBackend(config)
