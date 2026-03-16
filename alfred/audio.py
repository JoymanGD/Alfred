import os
import re

import sounddevice as sd


def clean_text_for_tts(text: str) -> str:
    text = text.replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[*_`#~|]+", " ", text)
    text = re.sub(r'["“”\'‘’«»„]+', " ", text)
    text = re.sub(r"[ \t]+([,.!?;:])", r"\1", text)
    text = re.sub(r"([,.!?;:]){2,}", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


class AudioEngine:
    def __init__(self, tts_backend, detect_language):
        self.tts_backend = tts_backend
        self.detect_language = detect_language

    def synthesize_audio(self, text: str):
        language = self.detect_language(text)
        spoken_text = clean_text_for_tts(text)
        if not spoken_text:
            spoken_text = "I have no spoken reply."
        return self.tts_backend.synthesize(spoken_text, language)

    def play_audio_interruptible(self, audio, sample_rate: int, interrupt_event, playing_event):
        if audio.ndim == 1:
            audio = audio.reshape(-1, 1)

        block_size = 2048
        interrupt_event.clear()
        playing_event.set()
        sd.stop()

        try:
            with sd.OutputStream(
                samplerate=sample_rate,
                channels=audio.shape[1],
                dtype="float32",
            ) as stream:
                index = 0
                total = len(audio)
                while index < total:
                    if interrupt_event.is_set():
                        break
                    end = min(index + block_size, total)
                    stream.write(audio[index:end])
                    index = end
        finally:
            sd.stop()
            playing_event.clear()
            interrupt_event.clear()


def speaker_loop(event_out, event_img, audio_engine: AudioEngine, interrupt_event, playing_event, output_dir: str):
    while True:
        reply_text = event_out.get()
        print("AI:", reply_text)

        try:
            audio, sample_rate = audio_engine.synthesize_audio(reply_text)
            audio_engine.play_audio_interruptible(audio, sample_rate, interrupt_event, playing_event)
        except Exception as e:
            print(f"[TTS ERROR]: {e}")

        while not event_img.empty():
            img = event_img.get()
            output_path = os.path.join(output_dir, "generated.png")
            with open(output_path, "wb") as f:
                f.write(img.read())
            print("Saved generated image as generated.png")
            try:
                os.startfile(output_path)
                print(f"[IMAGE] Opened generated image: {output_path}")
            except Exception as e:
                print(f"[IMAGE] Failed to open generated image: {e}")
