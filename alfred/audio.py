import os
import re
import ctypes
import numpy as np

import sounddevice as sd
from comtypes import CLSCTX_ALL, COMMETHOD, GUID, HRESULT, CoCreateInstance, IUnknown
from ctypes import POINTER


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
    def __init__(self, tts_backend, detect_language, tts_gain: float = 1.0):
        self.tts_backend = tts_backend
        self.detect_language = detect_language
        self.tts_gain = max(0.1, float(tts_gain))
        self._session_volume_boost_logged = False
        self._warmup_done = False

    def warmup_tts(self):
        if self._warmup_done:
            return
        warmup = getattr(self.tts_backend, "warmup", None)
        if callable(warmup):
            warmup()
        self._warmup_done = True

    def synthesize_audio(self, text: str):
        spoken_text = clean_text_for_tts(text)
        language = self.detect_language(text or spoken_text)
        if not spoken_text:
            spoken_text = (
                "Извини, я не смог сформировать ответ."
                if language == "ru"
                else "Sorry, I couldn't form a reply."
            )
        if re.search(r"[\u0400-\u04FF]", spoken_text) and language not in {"ru", "uk"}:
            language = "ru"
        audio, sample_rate = self.tts_backend.synthesize(spoken_text, language)
        boosted_audio = self._apply_tts_gain(audio)
        return boosted_audio, sample_rate

    def play_audio_interruptible(self, audio, sample_rate: int, interrupt_event, playing_event):
        if audio.ndim == 1:
            audio = audio.reshape(-1, 1)

        block_size = 2048
        interrupt_event.clear()
        playing_event.set()

        try:
            with sd.OutputStream(
                samplerate=sample_rate,
                channels=audio.shape[1],
                dtype="float32",
            ) as stream:
                self._boost_current_process_session_volume()
                index = 0
                total = len(audio)
                while index < total:
                    if interrupt_event.is_set():
                        break
                    end = min(index + block_size, total)
                    stream.write(audio[index:end])
                    index = end
        finally:
            playing_event.clear()
            interrupt_event.clear()

    def _boost_current_process_session_volume(self):
        try:
            boosted = boost_current_process_session_volume()
            if boosted and not self._session_volume_boost_logged:
                print("[AUDIO] Raised Alfred session volume to 100%.")
                self._session_volume_boost_logged = True
        except Exception as e:
            if not self._session_volume_boost_logged:
                print(f"[AUDIO] Failed to raise Alfred session volume: {e}")
                self._session_volume_boost_logged = True

    def _apply_tts_gain(self, audio):
        boosted = np.asarray(audio, dtype=np.float32)
        if boosted.size == 0:
            return boosted

        boosted = boosted * self.tts_gain
        peak = float(np.max(np.abs(boosted)))
        if peak > 0.98:
            boosted = boosted * (0.98 / peak)
        return boosted


class IMMDeviceEnumerator(IUnknown):
    _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    pass


class IMMDevice(IUnknown):
    _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
    pass


class IAudioSessionManager2(IUnknown):
    _iid_ = GUID("{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}")
    pass


class IAudioSessionEnumerator(IUnknown):
    _iid_ = GUID("{E2F5BB11-0570-40CA-ACDD-3AA01277DEE8}")
    pass


class IAudioSessionControl(IUnknown):
    _iid_ = GUID("{F4B1A599-7266-4319-A8CA-E70ACB11E8CD}")
    pass


class IAudioSessionControl2(IUnknown):
    _iid_ = GUID("{bfb7ff88-7239-4fc9-8fa2-07c950be9c6d}")
    pass


class ISimpleAudioVolume(IUnknown):
    _iid_ = GUID("{87CE5498-68D6-44E5-9215-6DA47EF883D8}")
    pass


IMMDeviceEnumerator._methods_ = (
    COMMETHOD([], HRESULT, "EnumAudioEndpoints"),
    COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint",
              (["in"], ctypes.c_int, "dataFlow"),
              (["in"], ctypes.c_int, "role"),
              (["out"], POINTER(POINTER(IMMDevice)), "ppDevice")),
    COMMETHOD([], HRESULT, "GetDevice"),
    COMMETHOD([], HRESULT, "RegisterEndpointNotificationCallback"),
    COMMETHOD([], HRESULT, "UnregisterEndpointNotificationCallback"),
)


IMMDevice._methods_ = (
    COMMETHOD([], HRESULT, "Activate",
              (["in"], POINTER(GUID), "iid"),
              (["in"], ctypes.c_ulong, "dwClsCtx"),
              (["in"], ctypes.c_void_p, "pActivationParams"),
              (["out"], POINTER(ctypes.c_void_p), "ppInterface")),
    COMMETHOD([], HRESULT, "OpenPropertyStore"),
    COMMETHOD([], HRESULT, "GetId"),
    COMMETHOD([], HRESULT, "GetState"),
)


IAudioSessionManager2._methods_ = (
    COMMETHOD([], HRESULT, "GetAudioSessionControl"),
    COMMETHOD([], HRESULT, "GetSimpleAudioVolume"),
    COMMETHOD([], HRESULT, "GetSessionEnumerator",
              (["out"], POINTER(POINTER(IAudioSessionEnumerator)), "SessionEnum")),
    COMMETHOD([], HRESULT, "RegisterSessionNotification"),
    COMMETHOD([], HRESULT, "UnregisterSessionNotification"),
    COMMETHOD([], HRESULT, "RegisterDuckNotification"),
    COMMETHOD([], HRESULT, "UnregisterDuckNotification"),
)


IAudioSessionEnumerator._methods_ = (
    COMMETHOD([], HRESULT, "GetCount",
              (["out"], POINTER(ctypes.c_int), "SessionCount")),
    COMMETHOD([], HRESULT, "GetSession",
              (["in"], ctypes.c_int, "SessionCount"),
              (["out"], POINTER(POINTER(IAudioSessionControl)), "Session")),
)


IAudioSessionControl._methods_ = (
    COMMETHOD([], HRESULT, "GetState"),
    COMMETHOD([], HRESULT, "GetDisplayName"),
    COMMETHOD([], HRESULT, "SetDisplayName"),
    COMMETHOD([], HRESULT, "GetIconPath"),
    COMMETHOD([], HRESULT, "SetIconPath"),
    COMMETHOD([], HRESULT, "GetGroupingParam"),
    COMMETHOD([], HRESULT, "SetGroupingParam"),
    COMMETHOD([], HRESULT, "RegisterAudioSessionNotification"),
    COMMETHOD([], HRESULT, "UnregisterAudioSessionNotification"),
)


IAudioSessionControl2._methods_ = (
    COMMETHOD([], HRESULT, "GetState"),
    COMMETHOD([], HRESULT, "GetDisplayName"),
    COMMETHOD([], HRESULT, "SetDisplayName"),
    COMMETHOD([], HRESULT, "GetIconPath"),
    COMMETHOD([], HRESULT, "SetIconPath"),
    COMMETHOD([], HRESULT, "GetGroupingParam"),
    COMMETHOD([], HRESULT, "SetGroupingParam"),
    COMMETHOD([], HRESULT, "RegisterAudioSessionNotification"),
    COMMETHOD([], HRESULT, "UnregisterAudioSessionNotification"),
    COMMETHOD([], HRESULT, "GetSessionIdentifier"),
    COMMETHOD([], HRESULT, "GetSessionInstanceIdentifier"),
    COMMETHOD([], HRESULT, "GetProcessId",
              (["out"], POINTER(ctypes.c_ulong), "pRetVal")),
    COMMETHOD([], HRESULT, "IsSystemSoundsSession"),
    COMMETHOD([], HRESULT, "SetDuckingPreference"),
)


ISimpleAudioVolume._methods_ = (
    COMMETHOD([], HRESULT, "SetMasterVolume",
              (["in"], ctypes.c_float, "fLevel"),
              (["in"], ctypes.c_void_p, "EventContext")),
    COMMETHOD([], HRESULT, "GetMasterVolume",
              (["out"], POINTER(ctypes.c_float), "pfLevel")),
    COMMETHOD([], HRESULT, "SetMute",
              (["in"], ctypes.c_bool, "bMute"),
              (["in"], ctypes.c_void_p, "EventContext")),
    COMMETHOD([], HRESULT, "GetMute",
              (["out"], POINTER(ctypes.c_bool), "pbMute")),
)


def boost_current_process_session_volume() -> bool:
    current_pid = os.getpid()
    ole32 = ctypes.windll.ole32
    coinitialize_result = ole32.CoInitialize(None)
    try:
        enumerator = CoCreateInstance(
            GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}"),
            IMMDeviceEnumerator,
            CLSCTX_ALL,
        )
        device = enumerator.GetDefaultAudioEndpoint(0, 1)
        manager = device.Activate(
            IAudioSessionManager2._iid_,
            CLSCTX_ALL,
            None,
        )
        session_manager = ctypes.cast(manager, POINTER(IAudioSessionManager2))
        session_enum = session_manager.GetSessionEnumerator()
        count = session_enum.GetCount()
        boosted = False
        for index in range(count):
            session_control = session_enum.GetSession(index)
            session_control2 = session_control.QueryInterface(IAudioSessionControl2)
            process_id = session_control2.GetProcessId()
            if process_id != current_pid:
                continue
            simple_volume = session_control.QueryInterface(ISimpleAudioVolume)
            simple_volume.SetMute(False, None)
            simple_volume.SetMasterVolume(1.0, None)
            boosted = True
        return boosted
    finally:
        if coinitialize_result in (0, 1):
            ole32.CoUninitialize()


def speaker_loop(
    event_out,
    event_img,
    audio_engine: AudioEngine,
    interrupt_event,
    playing_event,
    output_dir: str,
    followup_signal_queue,
    input_server=None,
):
    while True:
        reply_text = event_out.get()
        if input_server is not None:
            input_server.notify_assistant_message(reply_text)
        if input_server is None or not input_server.has_chat_clients():
            print("AI:", reply_text)

        try:
            if input_server is not None:
                input_server.notify_tts_started()
            audio, sample_rate = audio_engine.synthesize_audio(reply_text)
            audio_engine.play_audio_interruptible(audio, sample_rate, interrupt_event, playing_event)
        except Exception as e:
            print(f"[TTS ERROR]: {e}")
        finally:
            if input_server is not None:
                input_server.notify_tts_finished()
                input_server.notify_assistant_replied()
            followup_signal_queue.put("assistant_replied")

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
