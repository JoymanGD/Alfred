import ctypes
import re
import webbrowser
from dataclasses import dataclass
from urllib.parse import quote_plus, urlencode

from alfred.tooling import Tool, ToolContext, ToolResult


@dataclass
class MusicToolConfig:
    pass


class MusicTool(Tool):
    name = "music"

    VK_MEDIA_NEXT_TRACK = 0xB0
    VK_MEDIA_PREV_TRACK = 0xB1
    VK_MEDIA_PLAY_PAUSE = 0xB3
    VK_VOLUME_MUTE = 0xAD
    VK_VOLUME_DOWN = 0xAE
    VK_VOLUME_UP = 0xAF
    KEYEVENTF_KEYUP = 0x0002

    def __init__(self, config: MusicToolConfig):
        self.config = config

    def matches(self, context: ToolContext, text: str) -> bool:
        lowered = text.lower().strip()
        if self.get_control_action(lowered) is not None:
            return True

        patterns = [
            r"^(?:play|put on|turn on|start)\b.*",
            r"^(?:play me)\b.*",
            r"^(?:\u0432\u043a\u043b\u044e\u0447\u0438|\u043f\u043e\u0441\u0442\u0430\u0432\u044c)\b.*",
        ]
        if any(re.search(pattern, lowered) for pattern in patterns):
            return True

        mood_terms = [
            "music",
            "song",
            "songs",
            "playlist",
            "relax",
            "relaxing",
            "calm",
            "happy",
            "sad",
            "fun",
            "party",
            "jazz",
            "rock",
            "pop",
            "lofi",
            "lo-fi",
            "focus",
            "sleep",
            "workout",
            "\u043c\u0443\u0437\u044b\u043a\u0430",
            "\u043f\u0435\u0441\u043d\u044f",
            "\u043f\u0435\u0441\u043d\u0438",
            "\u043f\u043b\u0435\u0439\u043b\u0438\u0441\u0442",
            "\u0432\u0435\u0441\u0435\u043b",
            "\u0433\u0440\u0443\u0441\u0442",
            "\u0441\u043f\u043e\u043a\u043e\u0439",
            "\u0440\u0430\u0441\u0441\u043b\u0430\u0431",
            "\u0434\u0436\u0430\u0437",
            "\u0440\u043e\u043a",
            "\u043f\u043e\u043f",
            "\u0434\u043b\u044f \u0441\u043d\u0430",
            "\u0434\u043b\u044f \u0440\u0430\u0431\u043e\u0442\u044b",
            "\u0434\u043b\u044f \u0442\u0440\u0435\u043d\u0438\u0440\u043e\u0432\u043a\u0438",
        ]
        return any(term in lowered for term in mood_terms) and any(
            lowered.startswith(prefix)
            for prefix in ("play", "put on", "turn on", "start", "\u0432\u043a\u043b\u044e\u0447\u0438", "\u043f\u043e\u0441\u0442\u0430\u0432\u044c")
        )

    def handle(self, context: ToolContext, text: str) -> ToolResult:
        language = context.detect_language(text)
        control_action = self.get_control_action((context.routed_query or text).lower())
        if control_action is not None:
            try:
                self.perform_control_action(control_action)
                return ToolResult(handled=True, reply_text=self.build_control_reply(language, control_action))
            except Exception as e:
                reply_text = (
                    f"\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0443\u043f\u0440\u0430\u0432\u043b\u044f\u0442\u044c \u0432\u043e\u0441\u043f\u0440\u043e\u0438\u0437\u0432\u0435\u0434\u0435\u043d\u0438\u0435\u043c: {e}"
                    if language == "ru"
                    else f"I could not control playback: {e}"
                )
                return ToolResult(handled=True, reply_text=reply_text)

        try:
            query = context.routed_query if context.routed_tool_name == "music" and context.routed_query else self.extract_music_query(text)
            target = self.open_music(context, query)
            reply_text = self.build_music_reply(language, query, target)
        except Exception as e:
            reply_text = (
                f"\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u0442\u043a\u0440\u044b\u0442\u044c YouTube Music \u0432 \u0431\u0440\u0430\u0443\u0437\u0435\u0440\u0435: {e}"
                if language == "ru"
                else f"I could not open YouTube Music in the browser: {e}"
            )
        return ToolResult(handled=True, reply_text=reply_text)

    def extract_music_query(self, text: str) -> str:
        patterns = [
            r"^(?:play me|play|put on|turn on|start)\s*(.*)",
            r"^(?:\u0432\u043a\u043b\u044e\u0447\u0438|\u043f\u043e\u0441\u0442\u0430\u0432\u044c)\s*(.*)",
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
                    r"^(?:\u043f\u0435\u0441\u043d\u044e|\u0442\u0440\u0435\u043a|\u043c\u0443\u0437\u044b\u043a\u0443|\u0447\u0442\u043e-\u043d\u0438\u0431\u0443\u0434\u044c|\u0447\u0442\u043e \u0442\u043e|\u0447\u0442\u043e-\u0442\u043e)\s+",
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
            response = context.ollama_chat(
                [
                    {"role": "system", "content": context.system_prompt},
                    {"role": "user", "content": prompt},
                ]
            )
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

    def is_broad_music_query(self, query: str) -> bool:
        lowered = query.lower()
        broad_terms = [
            "music",
            "songs",
            "playlist",
            "relax",
            "relaxing",
            "calm",
            "happy",
            "sad",
            "party",
            "jazz",
            "rock",
            "pop",
            "lofi",
            "focus",
            "sleep",
            "workout",
            "study",
            "chill",
            "\u043c\u0443\u0437\u044b\u043a\u0430",
            "\u043f\u0435\u0441\u043d\u0438",
            "\u043f\u043b\u0435\u0439\u043b\u0438\u0441\u0442",
            "\u0432\u0435\u0441\u0435\u043b",
            "\u0433\u0440\u0443\u0441\u0442",
            "\u0441\u043f\u043e\u043a\u043e\u0439",
            "\u0440\u0430\u0441\u0441\u043b\u0430\u0431",
            "\u0434\u043b\u044f \u0441\u043d\u0430",
            "\u0434\u043b\u044f \u0440\u0430\u0431\u043e\u0442\u044b",
            "\u0434\u043b\u044f \u0442\u0440\u0435\u043d\u0438\u0440\u043e\u0432\u043a\u0438",
            "\u0434\u043b\u044f \u0443\u0447\u0435\u0431\u044b",
        ]
        return any(term in lowered for term in broad_terms)

    def build_watch_url(self, video_id: str, query: str = "") -> str:
        params = {"v": video_id, "autoplay": "1"}
        if query:
            params["q"] = query
        return f"https://music.youtube.com/watch?{urlencode(params)}"

    def get_control_action(self, lowered_text: str) -> str | None:
        text = lowered_text.strip()

        patterns_by_action = {
            "next": [
                r"\bnext\b",
                r"\bskip\b",
                r"\bnext song\b",
                r"\bnext track\b",
                r"\b\u0441\u043b\u0435\u0434\u0443\u044e\u0449",
                r"\b\u043f\u0440\u043e\u043f\u0443\u0441\u0442\u0438\b",
                r"\b\u0441\u043a\u0438\u043f\b",
            ],
            "pause": [
                r"\bpause\b",
                r"\bstop music\b",
                r"\b\u043f\u0430\u0443\u0437\u0430\b",
                r"\b\u043f\u043e\u0441\u0442\u0430\u0432\u044c \u043d\u0430 \u043f\u0430\u0443\u0437\u0443\b",
                r"\b\u043e\u0441\u0442\u0430\u043d\u043e\u0432\u0438 \u043c\u0443\u0437\u044b\u043a\u0443\b",
                r"\b\u0441\u0442\u043e\u043f \u043c\u0443\u0437\u044b\u043a\u0443\b",
            ],
            "resume": [
                r"\bresume\b",
                r"\bcontinue\b",
                r"\bcontinue music\b",
                r"\b\u043f\u0440\u043e\u0434\u043e\u043b\u0436\u0438\b",
                r"\b\u0432\u043e\u0437\u043e\u0431\u043d\u043e\u0432\u0438\b",
            ],
            "volume_up": [
                r"\blouder\b",
                r"\bvolume up\b",
                r"\bturn it up\b",
                r"\bmake it louder\b",
                r"\b\u0433\u0440\u043e\u043c\u0447\u0435\b",
                r"\b\u0441\u0434\u0435\u043b\u0430\u0439 \u0433\u0440\u043e\u043c\u0447\u0435\b",
                r"\b\u0443\u0432\u0435\u043b\u0438\u0447\u044c \u0433\u0440\u043e\u043c\u043a\u043e\u0441\u0442\u044c\b",
            ],
            "volume_down": [
                r"\bquieter\b",
                r"\bsofter\b",
                r"\bvolume down\b",
                r"\bturn it down\b",
                r"\bmake it quieter\b",
                r"\b\u0442\u0438\u0448\u0435\b",
                r"\b\u0441\u0434\u0435\u043b\u0430\u0439 \u0442\u0438\u0448\u0435\b",
                r"\b\u0443\u043c\u0435\u043d\u044c\u0448\u0438 \u0433\u0440\u043e\u043c\u043a\u043e\u0441\u0442\u044c\b",
            ],
            "mute": [
                r"\bmute\b",
                r"\bwithout sound\b",
                r"\b\u0432\u044b\u043a\u043b\u044e\u0447\u0438 \u0437\u0432\u0443\u043a\b",
                r"\b\u0431\u0435\u0437 \u0437\u0432\u0443\u043a\u0430\b",
            ],
        }

        for action, patterns in patterns_by_action.items():
            if any(re.search(pattern, text) for pattern in patterns):
                return action
        return None

    def press_vk(self, vk_code: int, repeats: int = 1):
        for _ in range(repeats):
            ctypes.windll.user32.keybd_event(vk_code, 0, 0, 0)
            ctypes.windll.user32.keybd_event(vk_code, 0, self.KEYEVENTF_KEYUP, 0)

    def perform_control_action(self, action: str):
        if action == "next":
            self.press_vk(self.VK_MEDIA_NEXT_TRACK)
            return
        if action in {"pause", "resume"}:
            self.press_vk(self.VK_MEDIA_PLAY_PAUSE)
            return
        if action == "volume_up":
            self.press_vk(self.VK_VOLUME_UP, repeats=5)
            return
        if action == "volume_down":
            self.press_vk(self.VK_VOLUME_DOWN, repeats=5)
            return
        if action == "mute":
            self.press_vk(self.VK_VOLUME_MUTE)
            return
        raise ValueError(f"Unsupported action: {action}")

    def open_music(self, context: ToolContext, query: str) -> str:
        if not query or query.lower() in {"music", "\u043c\u0443\u0437\u044b\u043a\u0443", "\u043c\u0443\u0437\u044b\u043a\u0430"}:
            target = "https://music.youtube.com/"
            print(f"[YTM] Opening generic web target: {target}")
            webbrowser.open(target)
            return target

        fixed_query = self.normalize_music_query(context, query)
        print(f"[YTM] Raw music query: {query}")
        print(f"[YTM] Normalized music query: {fixed_query}")

        video_id = self.get_first_youtube_video_id(context, fixed_query)
        print(f"[YTM] Resolved video id: {video_id}")

        if self.is_broad_music_query(fixed_query):
            if video_id:
                target = self.build_watch_url(video_id, fixed_query)
                print(f"[YTM] Opening broad-query track with autoplay in web YouTube Music: {target}")
                webbrowser.open(target)
                return target
            target = f"https://music.youtube.com/search?q={quote_plus(fixed_query)}"
            print(f"[YTM] Opening broad music search in web YouTube Music: {target}")
            webbrowser.open(target)
            return target

        if video_id:
            target = self.build_watch_url(video_id, fixed_query)
            print(f"[YTM] Opening track with autoplay in web YouTube Music: {target}")
            webbrowser.open(target)
            return target

        target = f"https://music.youtube.com/search?q={quote_plus(fixed_query)}"
        print(f"[YTM] Falling back to search URL: {target}")
        webbrowser.open(target)
        return target

    def build_music_reply(self, language: str, query: str, target: str) -> str:
        if "watch?" in target:
            if language == "ru":
                return f"\u041e\u0442\u043a\u0440\u044b\u0432\u0430\u044e {query} \u0432 YouTube Music."
            return f"Opening {query} in YouTube Music."
        if language == "ru":
            return f"\u041e\u0442\u043a\u0440\u044b\u0432\u0430\u044e YouTube Music \u043f\u043e \u0437\u0430\u043f\u0440\u043e\u0441\u0443 {query}."
        return f"Opening YouTube Music for {query}."

    def build_control_reply(self, language: str, action: str) -> str:
        if language == "ru":
            replies = {
                "next": "\u041f\u0435\u0440\u0435\u043a\u043b\u044e\u0447\u0430\u044e \u043d\u0430 \u0441\u043b\u0435\u0434\u0443\u044e\u0449\u0438\u0439 \u0442\u0440\u0435\u043a.",
                "pause": "\u0421\u0442\u0430\u0432\u043b\u044e \u043d\u0430 \u043f\u0430\u0443\u0437\u0443.",
                "resume": "\u041f\u0440\u043e\u0434\u043e\u043b\u0436\u0430\u044e \u0432\u043e\u0441\u043f\u0440\u043e\u0438\u0437\u0432\u0435\u0434\u0435\u043d\u0438\u0435.",
                "volume_up": "\u0414\u0435\u043b\u0430\u044e \u0433\u0440\u043e\u043c\u0447\u0435.",
                "volume_down": "\u0414\u0435\u043b\u0430\u044e \u0442\u0438\u0448\u0435.",
                "mute": "\u0412\u044b\u043a\u043b\u044e\u0447\u0430\u044e \u0437\u0432\u0443\u043a.",
            }
            return replies[action]

        replies = {
            "next": "Switching to the next track.",
            "pause": "Pausing playback.",
            "resume": "Resuming playback.",
            "volume_up": "Turning the volume up.",
            "volume_down": "Turning the volume down.",
            "mute": "Muting the sound.",
        }
        return replies[action]
