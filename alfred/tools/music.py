import ctypes
import re
import subprocess
import time
import webbrowser
from dataclasses import dataclass
from urllib.parse import quote_plus, urlencode

from comtypes import CLSCTX_ALL, COMMETHOD, GUID, HRESULT, CoCreateInstance, IUnknown
from ctypes import POINTER
from alfred.tooling import Tool, ToolContext, ToolResult


@dataclass
class MusicToolConfig:
    pass


@dataclass
class VolumeInstruction:
    target_percent: float
    summary_key: str
    summary_value: float | str | None = None


@dataclass
class VolumeContext:
    action: str
    summary_key: str
    summary_value: float | str | None
    timestamp: float


class IMMDeviceEnumerator(IUnknown):
    _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    pass


class IMMDevice(IUnknown):
    _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
    pass


class IAudioEndpointVolume(IUnknown):
    _iid_ = GUID("{5CDF2C82-841E-4546-9722-0CF74078229A}")
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
              (["out"], POINTER(POINTER(IAudioEndpointVolume)), "ppInterface")),
    COMMETHOD([], HRESULT, "OpenPropertyStore"),
    COMMETHOD([], HRESULT, "GetId"),
    COMMETHOD([], HRESULT, "GetState"),
)


IAudioEndpointVolume._methods_ = (
    COMMETHOD([], HRESULT, "RegisterControlChangeNotify"),
    COMMETHOD([], HRESULT, "UnregisterControlChangeNotify"),
    COMMETHOD([], HRESULT, "GetChannelCount"),
    COMMETHOD([], HRESULT, "SetMasterVolumeLevel",
              (["in"], ctypes.c_float, "fLevelDB"),
              (["in"], ctypes.c_void_p, "pguidEventContext")),
    COMMETHOD([], HRESULT, "SetMasterVolumeLevelScalar",
              (["in"], ctypes.c_float, "fLevel"),
              (["in"], ctypes.c_void_p, "pguidEventContext")),
    COMMETHOD([], HRESULT, "GetMasterVolumeLevel",
              (["out"], POINTER(ctypes.c_float), "pfLevelDB")),
    COMMETHOD([], HRESULT, "GetMasterVolumeLevelScalar",
              (["out"], POINTER(ctypes.c_float), "pfLevel")),
    COMMETHOD([], HRESULT, "SetChannelVolumeLevel"),
    COMMETHOD([], HRESULT, "SetChannelVolumeLevelScalar"),
    COMMETHOD([], HRESULT, "GetChannelVolumeLevel"),
    COMMETHOD([], HRESULT, "GetChannelVolumeLevelScalar"),
    COMMETHOD([], HRESULT, "SetMute",
              (["in"], ctypes.c_bool, "bMute"),
              (["in"], ctypes.c_void_p, "pguidEventContext")),
    COMMETHOD([], HRESULT, "GetMute",
              (["out"], POINTER(ctypes.c_bool), "pbMute")),
    COMMETHOD([], HRESULT, "GetVolumeStepInfo"),
    COMMETHOD([], HRESULT, "VolumeStepUp",
              (["in"], ctypes.c_void_p, "pguidEventContext")),
    COMMETHOD([], HRESULT, "VolumeStepDown",
              (["in"], ctypes.c_void_p, "pguidEventContext")),
    COMMETHOD([], HRESULT, "QueryHardwareSupport"),
    COMMETHOD([], HRESULT, "GetVolumeRange"),
)


class MusicTool(Tool):
    name = "music"

    VK_MEDIA_NEXT_TRACK = 0xB0
    VK_MEDIA_PREV_TRACK = 0xB1
    VK_MEDIA_STOP = 0xB2
    VK_MEDIA_PLAY_PAUSE = 0xB3
    VK_VOLUME_MUTE = 0xAD
    VK_VOLUME_DOWN = 0xAE
    VK_VOLUME_UP = 0xAF
    KEYEVENTF_KEYUP = 0x0002
    WAVE_MAPPER = 0xFFFFFFFF

    def __init__(self, config: MusicToolConfig):
        self.config = config
        self.last_volume_context: VolumeContext | None = None
        self.last_nonzero_volume_percent = 30.0
        self._endpoint_volume = None

    def matches(self, context: ToolContext, text: str) -> bool:
        return context.routed_tool_name == self.name

    def handle(self, context: ToolContext, text: str) -> ToolResult:
        language = context.language or context.detect_language(text)
        control_action = self._get_routed_control_action(context)
        if control_action is not None:
            try:
                if control_action == "volume_diagnose":
                    return ToolResult(handled=True, reply_text=self.build_volume_diagnosis_reply(language))
                if control_action == "volume_status":
                    return ToolResult(handled=True, reply_text=self.build_volume_status_reply(language))
                volume_instruction = self.resolve_volume_instruction(context, control_action)
                self.perform_control_action(control_action, volume_instruction)
                self._remember_volume_context(control_action, volume_instruction)
                return ToolResult(
                    handled=True,
                    reply_text=self.build_control_reply(language, control_action, volume_instruction),
                )
            except Exception as e:
                reply_text = (
                    f"Не удалось управлять воспроизведением: {e}"
                    if language == "ru"
                    else f"I could not control playback: {e}"
                )
                return ToolResult(handled=True, reply_text=reply_text)

        if context.routed_intent == "music_control":
            reply_text = (
                "Не удалось распознать команду управления музыкой."
                if language == "ru"
                else "I could not recognize the music control command."
            )
            return ToolResult(handled=True, reply_text=reply_text)

        try:
            query = (
                context.routed_query
                if context.routed_tool_name == "music" and context.routed_query
                else self.extract_music_query(text)
            )
            target = self.open_music(context, query)
            reply_text = self.build_music_reply(language, query, target)
        except Exception as e:
            reply_text = (
                f"Не удалось открыть YouTube Music в браузере: {e}"
                if language == "ru"
                else f"I could not open YouTube Music in the browser: {e}"
            )
        return ToolResult(handled=True, reply_text=reply_text)

    def extract_music_query(self, text: str) -> str:
        patterns = [
            r"^(?:play me|play|put on|turn on|start)\s*(.*)",
            r"^(?:включи|поставь)\s*(.*)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                query = match.group(1).strip(" ?!.,")
                query = re.sub(
                    r"^(?:song|track|music|radio|playlist|something|anything|some)\s+",
                    "",
                    query,
                    flags=re.IGNORECASE,
                )
                query = re.sub(
                    r"^(?:песню|трек|музыку|что-нибудь|что то|что-то)\s+",
                    "",
                    query,
                    flags=re.IGNORECASE,
                )
                return query.strip(" ?!.,")
        return text.strip(" ?!.")

    def normalize_music_query(self, context: ToolContext, query: str) -> str:
        prompt = (
            "Convert the user's music request into a clean YouTube Music search query. "
            "Fix likely ASR mistakes in artist names and song titles, but keep the intent. "
            "If the request is about a mood, genre, or activity, keep it as a mood or genre query instead of forcing a specific song. "
            "Return only the corrected query with no explanation.\n\n"
            f"User request: {query}"
        )
        try:
            response = context.ollama_chat([
                {"role": "system", "content": context.system_prompt},
                {"role": "user", "content": prompt},
            ])
            fixed_query = context.normalize_memory_text(response["message"]["content"])
            return fixed_query or query
        except Exception:
            return query

    def get_first_youtube_video_id(self, context: ToolContext, query: str):
        html = context.fetch_url(f"https://www.youtube.com/results?search_query={quote_plus(query)}")
        matches = re.findall(r'"videoId":"([A-Za-z0-9_-]{11})"', html)
        seen = set()
        for match in matches:
            if match not in seen:
                seen.add(match)
                return match
        return None

    def build_watch_url(self, video_id: str, query: str = "") -> str:
        params = {"v": video_id, "autoplay": "1"}
        if query:
            params["q"] = query
        return f"https://music.youtube.com/watch?{urlencode(params)}"

    def _get_routed_control_action(self, context: ToolContext) -> str | None:
        if context.routed_intent != "music_control":
            return None
        args = context.routed_args or {}
        action = args.get("action")
        return action if isinstance(action, str) and action else None

    def press_vk(self, vk_code: int, repeats: int = 1):
        for _ in range(repeats):
            ctypes.windll.user32.keybd_event(vk_code, 0, 0, 0)
            ctypes.windll.user32.keybd_event(vk_code, 0, self.KEYEVENTF_KEYUP, 0)

    def perform_control_action(self, action: str, volume_instruction: VolumeInstruction | None = None):
        if action == "next":
            self.press_vk(self.VK_MEDIA_NEXT_TRACK)
            return
        if action == "previous":
            self.press_vk(self.VK_MEDIA_PREV_TRACK)
            return
        if action in {"pause", "resume"}:
            self.press_vk(self.VK_MEDIA_PLAY_PAUSE)
            return
        if action == "volume_up":
            if volume_instruction is not None:
                self.set_system_volume_percent(volume_instruction.target_percent)
            else:
                self.press_vk(self.VK_VOLUME_UP, repeats=5)
            return
        if action == "volume_down":
            if volume_instruction is not None:
                self.set_system_volume_percent(volume_instruction.target_percent)
            else:
                self.press_vk(self.VK_VOLUME_DOWN, repeats=5)
            return
        if action == "mute":
            current = self.get_system_volume_percent()
            if current > 0:
                self.last_nonzero_volume_percent = current
            self.set_system_mute(True)
            return
        if action == "unmute":
            self.set_system_mute(False)
            if self.get_system_volume_percent() <= 0:
                restore = max(5.0, self.last_nonzero_volume_percent)
                self.set_system_volume_percent(restore)
            return
        if action in {"volume_status", "volume_diagnose"}:
            return
        raise ValueError(f"Unsupported action: {action}")

    def get_system_volume_percent(self) -> float:
        endpoint = self._get_endpoint_volume()
        if endpoint is not None:
            scalar = endpoint.GetMasterVolumeLevelScalar()
            return float(scalar) * 100.0

        volume = ctypes.c_uint()
        result = ctypes.windll.winmm.waveOutGetVolume(self.WAVE_MAPPER, ctypes.byref(volume))
        if result != 0:
            raise OSError(f"waveOutGetVolume failed with code {result}")
        left = volume.value & 0xFFFF
        right = (volume.value >> 16) & 0xFFFF
        average = (left + right) / 2
        return average * 100.0 / 65535.0

    def set_system_volume_percent(self, percent: float):
        clamped = max(0.0, min(100.0, percent))
        if clamped > 0:
            self.last_nonzero_volume_percent = clamped

        endpoint = self._get_endpoint_volume()
        if endpoint is not None:
            endpoint.SetMute(False, None)
            endpoint.SetMasterVolumeLevelScalar(ctypes.c_float(clamped / 100.0), None)
            return

        raw = int(round(clamped * 65535.0 / 100.0))
        packed = raw | (raw << 16)
        result = ctypes.windll.winmm.waveOutSetVolume(self.WAVE_MAPPER, packed)
        if result != 0:
            raise OSError(f"waveOutSetVolume failed with code {result}")

    def set_system_mute(self, muted: bool):
        endpoint = self._get_endpoint_volume()
        if endpoint is not None:
            endpoint.SetMute(bool(muted), None)
            return
        if muted:
            self.press_vk(self.VK_VOLUME_MUTE)
            return
        current = self.get_system_volume_percent()
        if current <= 0:
            self.set_system_volume_percent(max(5.0, self.last_nonzero_volume_percent))

    def is_system_muted(self) -> bool:
        endpoint = self._get_endpoint_volume()
        if endpoint is not None:
            return bool(endpoint.GetMute())
        return self.get_system_volume_percent() <= 0.0

    def _get_endpoint_volume(self) -> IAudioEndpointVolume | None:
        if self._endpoint_volume is not None:
            return self._endpoint_volume
        try:
            enumerator = CoCreateInstance(
                GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}"),
                IMMDeviceEnumerator,
                CLSCTX_ALL,
            )
            device = enumerator.GetDefaultAudioEndpoint(0, 1)
            self._endpoint_volume = device.Activate(
                IAudioEndpointVolume._iid_,
                CLSCTX_ALL,
                None,
            )
        except Exception as e:
            print(f"[MUSIC] Core Audio endpoint unavailable, using winmm fallback: {e}")
            self._endpoint_volume = None
        return self._endpoint_volume

    def resolve_volume_instruction(self, context: ToolContext, action: str) -> VolumeInstruction | None:
        if action not in {"volume_up", "volume_down"}:
            return None
        current = self.get_system_volume_percent()
        args = context.routed_args or {}
        target_percent = self._parse_float_arg(args.get("target_percent"))
        delta_percent = self._parse_float_arg(args.get("delta_percent"))
        factor = self._parse_float_arg(args.get("factor"))
        qualifier = args.get("qualifier") if isinstance(args.get("qualifier"), str) else None

        if target_percent is not None:
            return VolumeInstruction(
                target_percent=target_percent,
                summary_key="target_percent",
                summary_value=target_percent,
            )
        if delta_percent is not None:
            target = current + delta_percent if action == "volume_up" else current - delta_percent
            return VolumeInstruction(
                target_percent=target,
                summary_key="delta_percent",
                summary_value=delta_percent,
            )
        if factor is not None and factor > 0:
            return VolumeInstruction(
                target_percent=current * factor,
                summary_key="factor",
                summary_value=factor,
            )

        qualifier_map = {"slight": 5.0, "medium": 12.0, "strong": 25.0, "max": 100.0}
        if qualifier in qualifier_map:
            if qualifier == "max":
                target = 100.0 if action == "volume_up" else 0.0
            else:
                delta = qualifier_map[qualifier]
                target = current + delta if action == "volume_up" else current - delta
            return VolumeInstruction(
                target_percent=target,
                summary_key="qualifier",
                summary_value=qualifier,
            )

        target = current + 10.0 if action == "volume_up" else current - 10.0
        return VolumeInstruction(target_percent=target, summary_key="default")

    def _parse_float_arg(self, value) -> float | None:
        if value is None or value == "":
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.replace(",", "."))
            except ValueError:
                return None
        return None

    def _remember_volume_context(self, action: str, volume_instruction: VolumeInstruction | None):
        if action not in {"volume_up", "volume_down"} or volume_instruction is None:
            return
        self.last_volume_context = VolumeContext(
            action=action,
            summary_key=volume_instruction.summary_key,
            summary_value=volume_instruction.summary_value,
            timestamp=time.monotonic(),
        )

    def stop_existing_music_playback(self):
        try:
            self.press_vk(self.VK_MEDIA_STOP)
            time.sleep(0.15)
        except Exception as e:
            print(f"[MUSIC] Failed to stop existing playback before opening new music: {e}")

    def _open_music_target(self, target: str):
        if self._reuse_existing_music_window(target):
            return
        webbrowser.open(target)

    def _reuse_existing_music_window(self, target: str) -> bool:
        script = r"""
$target = $env:ALFRED_YTM_TARGET
$shell = New-Object -ComObject WScript.Shell
if (-not ($shell.AppActivate('YouTube Music') -or $shell.AppActivate('music.youtube.com'))) {
    exit 1
}
Start-Sleep -Milliseconds 150
Set-Clipboard -Value $target
$shell.SendKeys('^l')
Start-Sleep -Milliseconds 100
$shell.SendKeys('^v')
Start-Sleep -Milliseconds 50
$shell.SendKeys('{ENTER}')
"""
        env = dict(subprocess.os.environ)
        env["ALFRED_YTM_TARGET"] = target
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=5,
            )
            if result.returncode == 0:
                print(f"[YTM] Reused existing YouTube Music window: {target}")
                return True
        except Exception as e:
            print(f"[YTM] Could not reuse existing YouTube Music window: {e}")
        return False

    def open_music(self, context: ToolContext, query: str) -> str:
        self.stop_existing_music_playback()
        if not query:
            target = "https://music.youtube.com/"
            print(f"[YTM] Opening generic web target: {target}")
            self._open_music_target(target)
            return target

        fixed_query = self.normalize_music_query(context, query)
        print(f"[YTM] Raw music query: {query}")
        print(f"[YTM] Normalized music query: {fixed_query}")

        video_id = self.get_first_youtube_video_id(context, fixed_query)
        print(f"[YTM] Resolved video id: {video_id}")

        if video_id:
            target = self.build_watch_url(video_id, fixed_query)
            print(f"[YTM] Opening track with autoplay in web YouTube Music: {target}")
            self._open_music_target(target)
            return target

        target = f"https://music.youtube.com/search?q={quote_plus(fixed_query)}"
        print(f"[YTM] Falling back to search URL: {target}")
        self._open_music_target(target)
        return target

    def build_music_reply(self, language: str, query: str, target: str) -> str:
        if "watch?" in target:
            if language == "ru":
                return f"Открываю {query} в YouTube Music."
            return f"Opening {query} in YouTube Music."
        if language == "ru":
            return f"Открываю YouTube Music по запросу {query}."
        return f"Opening YouTube Music for {query}."

    def build_volume_status_reply(self, language: str) -> str:
        current = round(self.get_system_volume_percent())
        if language == "ru":
            if self.is_system_muted():
                return f"Сейчас звук выключен. Текущий уровень громкости около {current} процентов."
            return f"Сейчас громкость примерно {current} процентов."
        if self.is_system_muted():
            return f"The sound is muted right now. The current volume level is about {current} percent."
        return f"The current volume is about {current} percent."

    def build_volume_diagnosis_reply(self, language: str) -> str:
        current = round(self.get_system_volume_percent())
        muted = self.is_system_muted()
        if language == "ru":
            if muted:
                return f"Скорее всего, Alfred плохо слышно, потому что звук выключен. Текущая громкость около {current} процентов."
            if current <= 15:
                return f"Скорее всего, Alfred плохо слышно из-за очень низкой громкости. Сейчас около {current} процентов."
            if current <= 35:
                return f"Alfred может быть плохо слышно из-за низкой громкости. Сейчас около {current} процентов."
            return f"Системная громкость сейчас около {current} процентов, так что причина, вероятно, не в общей громкости."
        if muted:
            return f"Alfred is probably hard to hear because the sound is muted. The current volume is about {current} percent."
        if current <= 15:
            return f"Alfred is probably hard to hear because the volume is very low. It is about {current} percent right now."
        if current <= 35:
            return f"Alfred may be hard to hear because the volume is low. It is about {current} percent right now."
        return f"The system volume is about {current} percent right now, so the cause is probably not the main volume level."

    def build_control_reply(self, language: str, action: str, volume_instruction: VolumeInstruction | None = None) -> str:
        if language == "ru":
            replies = {
                "next": "Переключаю на следующий трек.",
                "previous": "Возвращаю предыдущий трек.",
                "pause": "Ставлю на паузу.",
                "resume": "Продолжаю воспроизведение.",
                "mute": "Выключаю звук.",
                "unmute": "Включаю звук.",
            }
            if action in {"volume_up", "volume_down"}:
                return self._build_ru_volume_reply(action, volume_instruction)
            return replies[action]

        replies = {
            "next": "Switching to the next track.",
            "previous": "Switching to the previous track.",
            "pause": "Pausing playback.",
            "resume": "Resuming playback.",
            "mute": "Muting the sound.",
            "unmute": "Turning the sound back on.",
        }
        if action in {"volume_up", "volume_down"}:
            return self._build_en_volume_reply(action, volume_instruction)
        return replies[action]

    def _build_ru_volume_reply(self, action: str, volume_instruction: VolumeInstruction | None) -> str:
        if volume_instruction is None:
            return "Делаю громче." if action == "volume_up" else "Делаю тише."
        direction = "громче" if action == "volume_up" else "тише"
        if volume_instruction.summary_key == "delta_percent" and volume_instruction.summary_value is not None:
            value = round(float(volume_instruction.summary_value))
            return f"Делаю {direction} примерно на {value} процентов."
        if volume_instruction.summary_key == "factor" and volume_instruction.summary_value is not None:
            factor = float(volume_instruction.summary_value)
            if abs(factor - 0.5) < 0.01:
                return "Делаю в два раза тише."
            if abs(factor - 2.0) < 0.01:
                return "Делаю в два раза громче."
        if volume_instruction.summary_key == "qualifier" and volume_instruction.summary_value == "slight":
            return "Делаю чуть громче." if action == "volume_up" else "Делаю чуть тише."
        if volume_instruction.summary_key == "qualifier" and volume_instruction.summary_value == "strong":
            return "Делаю заметно громче." if action == "volume_up" else "Делаю заметно тише."
        if volume_instruction.summary_key == "qualifier" and volume_instruction.summary_value == "max":
            return "Ставлю громкость на максимум." if action == "volume_up" else "Свожу громкость к нулю."
        if volume_instruction.summary_key == "target_percent" and volume_instruction.summary_value is not None:
            value = round(float(volume_instruction.summary_value))
            return f"Ставлю громкость примерно на {value} процентов."
        return "Делаю громче." if action == "volume_up" else "Делаю тише."

    def _build_en_volume_reply(self, action: str, volume_instruction: VolumeInstruction | None) -> str:
        if volume_instruction is None:
            return "Turning the volume up." if action == "volume_up" else "Turning the volume down."
        direction = "up" if action == "volume_up" else "down"
        if volume_instruction.summary_key == "delta_percent" and volume_instruction.summary_value is not None:
            value = round(float(volume_instruction.summary_value))
            return f"Turning the volume {direction} by about {value} percent."
        if volume_instruction.summary_key == "factor" and volume_instruction.summary_value is not None:
            factor = float(volume_instruction.summary_value)
            if abs(factor - 0.5) < 0.01:
                return "Making it about half as loud."
            if abs(factor - 2.0) < 0.01:
                return "Making it about twice as loud."
        if volume_instruction.summary_key == "qualifier" and volume_instruction.summary_value == "slight":
            return "Turning the volume down a little." if action == "volume_down" else "Turning the volume up a little."
        if volume_instruction.summary_key == "qualifier" and volume_instruction.summary_value == "strong":
            return "Turning the volume down a lot." if action == "volume_down" else "Turning the volume up a lot."
        if volume_instruction.summary_key == "qualifier" and volume_instruction.summary_value == "max":
            return "Setting the volume to maximum." if action == "volume_up" else "Turning the volume all the way down."
        if volume_instruction.summary_key == "target_percent" and volume_instruction.summary_value is not None:
            value = round(float(volume_instruction.summary_value))
            return f"Setting the volume to about {value} percent."
        return "Turning the volume up." if action == "volume_up" else "Turning the volume down."
