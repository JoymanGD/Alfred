import difflib
import os
import re
import subprocess
import webbrowser
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus, unquote

from alfred.tooling import Tool, ToolContext, ToolResult


@dataclass
class SystemToolConfig:
    app_aliases: dict[str, str]


class SystemTool(Tool):
    name = "system"

    def __init__(self, config: SystemToolConfig):
        self.config = config

    def matches(self, context: ToolContext, text: str) -> bool:
        lowered = text.lower().strip()
        patterns = [
            r"^(?:open|launch|start|run)\s+.+",
            r"^(?:открой|запусти)\s+.+",
        ]
        return any(re.search(pattern, lowered) for pattern in patterns)

    def handle(self, context: ToolContext, text: str) -> ToolResult:
        language = context.detect_language(text)
        browser_query = (
            context.routed_query
            if context.routed_intent == "browser_open" and context.routed_query
            else self._extract_browser_search_query(text)
        )
        if browser_query:
            opened_target = self._open_browser_search_target(context, browser_query)
            reply_text = self._build_browser_search_reply(language, browser_query, opened_target)
            return ToolResult(handled=True, reply_text=reply_text, target=opened_target)

        target = (
            context.routed_query
            if context.routed_intent == "system_open" and context.routed_query
            else self._extract_target(text)
        )
        if not target:
            reply_text = (
                "Я не понял, что именно нужно открыть."
                if language == "ru"
                else "I could not tell what you want me to open."
            )
            return ToolResult(handled=True, reply_text=reply_text)

        try:
            opened = self._open_target(target)
            reply_text = self._build_reply(language, target, opened)
        except Exception as e:
            reply_text = (
                f"Не удалось открыть {target}: {e}"
                if language == "ru"
                else f"I could not open {target}: {e}"
            )
        return ToolResult(handled=True, reply_text=reply_text)

    def _extract_browser_search_query(self, text: str) -> str | None:
        patterns = [
            r"^(?:open|launch|start)\s+(?:in\s+browser|in\s+the\s+browser)\s+(.+)",
            r"^(?:open|launch|start)\s+(.+?)\s+(?:in\s+browser|in\s+the\s+browser)$",
            r"^(?:открой|запусти)\s+(?:в\s+браузере)\s+(.+)",
            r"^(?:открой|запусти)\s+(.+?)\s+в\s+браузере$",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return self._normalize_browser_search_query(match.group(1))
        return None

    def _normalize_browser_search_query(self, query: str) -> str:
        normalized = query.strip(" ?!.,")
        prefix_patterns = [
            r"^(?:page|website|site)\s+(?:with|about|for)\s+",
            r"^(?:page|website|site)\s+",
            r"^(?:страницу|сайт)\s+(?:с|о|про)\s+",
            r"^(?:страницу|сайт)\s+",
        ]
        for pattern in prefix_patterns:
            normalized = re.sub(pattern, "", normalized, flags=re.IGNORECASE).strip(" ?!.,")

        filler_patterns = [
            r"\b(?:please|me)\b",
            r"\b(?:мне|пожалуйста)\b",
        ]
        for pattern in filler_patterns:
            normalized = re.sub(pattern, " ", normalized, flags=re.IGNORECASE)

        normalized = re.sub(r"\s+", " ", normalized).strip(" ?!.,")
        return normalized

    def _build_browser_search_url(self, query: str) -> str:
        return f"https://www.google.com/search?q={quote_plus(query)}"

    def _open_browser_search_target(self, context: ToolContext, query: str) -> str:
        first_result_url = self._get_first_search_result_url(context, query)
        search_url = self._build_browser_search_url(query)
        target = first_result_url or search_url
        if first_result_url:
            print(f"[SYSTEM] Opening first browser search result: {target}")
            print(f"[SYSTEM] Opening browser search results page: {search_url}")
            webbrowser.open(search_url)
        else:
            print(f"[SYSTEM] Falling back to browser search results page: {target}")
        webbrowser.open(target)
        return target

    def _get_first_search_result_url(self, context: ToolContext, query: str) -> str | None:
        html = context.fetch_url(f"https://html.duckduckgo.com/html/?q={quote_plus(query)}")
        match = re.search(
            r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return None

        href = match.group(1)
        url_match = re.search(r"uddg=([^&]+)", href)
        resolved_url = unquote(url_match.group(1)) if url_match else href
        if re.match(r"^https?://", resolved_url, flags=re.IGNORECASE):
            return resolved_url
        return None

    def _extract_target(self, text: str) -> str:
        patterns = [
            r"^(?:open|launch|start|run)\s+(.+)",
            r"^(?:открой|запусти)\s+(.+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip(" ?!.,")
        return text.strip(" ?!.,")

    def _resolve_target(self, target: str) -> str:
        lowered = target.lower().strip()
        if lowered in self.config.app_aliases:
            return self.config.app_aliases[lowered]

        steam_target = self._find_steam_game_target(target)
        if steam_target:
            return steam_target

        discovered_target = self._find_installed_app_target(target)
        if discovered_target:
            return discovered_target

        return target

    def _open_target(self, target: str) -> str:
        resolved = self._resolve_target(target)
        if re.match(r"^https?://", resolved, flags=re.IGNORECASE):
            webbrowser.open(resolved)
            return resolved
        if resolved.startswith("steam://"):
            os.startfile(resolved)
            return resolved
        if os.path.exists(resolved):
            os.startfile(resolved)
            return resolved
        try:
            os.startfile(resolved)
            return resolved
        except OSError:
            subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", f"Start-Process -FilePath '{resolved}'"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return resolved

    def _find_steam_game_target(self, target: str) -> str | None:
        normalized_target = self._normalize_match_key(target)
        if not normalized_target:
            return None

        games = self._get_installed_steam_games()
        exact = [game for game in games if game["normalized_name"] == normalized_target]
        if exact:
            game = exact[0]
            print(f"[SYSTEM] Matched Steam game exactly: {game['name']} ({game['appid']})")
            return f"steam://rungameid/{game['appid']}"

        partial = [game for game in games if normalized_target in game["normalized_name"] or game["normalized_name"] in normalized_target]
        if partial:
            game = partial[0]
            print(f"[SYSTEM] Matched Steam game partially: {game['name']} ({game['appid']})")
            return f"steam://rungameid/{game['appid']}"

        names = [game["normalized_name"] for game in games]
        close = difflib.get_close_matches(normalized_target, names, n=1, cutoff=0.72)
        if close:
            match_name = close[0]
            game = next((game for game in games if game["normalized_name"] == match_name), None)
            if game:
                print(f"[SYSTEM] Matched Steam game fuzzily: {game['name']} ({game['appid']})")
                return f"steam://rungameid/{game['appid']}"
        return None

    def _find_installed_app_target(self, target: str) -> str | None:
        normalized_target = self._normalize_match_key(target)
        if not normalized_target:
            return None

        candidates = self._get_app_candidates()
        exact = [candidate for candidate in candidates if candidate["normalized_name"] == normalized_target]
        if exact:
            print(f"[SYSTEM] Matched installed app exactly: {exact[0]['name']}")
            return exact[0]["path"]

        partial = [
            candidate
            for candidate in candidates
            if normalized_target in candidate["normalized_name"] or candidate["normalized_name"] in normalized_target
        ]
        if partial:
            print(f"[SYSTEM] Matched installed app partially: {partial[0]['name']}")
            return partial[0]["path"]

        names = [candidate["normalized_name"] for candidate in candidates]
        close = difflib.get_close_matches(normalized_target, names, n=1, cutoff=0.74)
        if close:
            match_name = close[0]
            candidate = next((item for item in candidates if item["normalized_name"] == match_name), None)
            if candidate:
                print(f"[SYSTEM] Matched installed app fuzzily: {candidate['name']}")
                return candidate["path"]
        return None

    @lru_cache(maxsize=1)
    def _get_installed_steam_games(self) -> tuple[dict[str, str], ...]:
        library_file = self._find_steam_libraryfolders_file()
        if not library_file or not os.path.exists(library_file):
            return tuple()

        try:
            content = Path(library_file).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return tuple()

        library_paths = re.findall(r'"path"\s+"([^"]+)"', content)
        if not library_paths:
            library_paths = [str(Path(library_file).parent.parent)]

        games: list[dict[str, str]] = []
        for library_path in library_paths:
            steamapps_path = Path(library_path) / "steamapps"
            if not steamapps_path.exists():
                continue
            for manifest_path in steamapps_path.glob("appmanifest_*.acf"):
                try:
                    manifest = manifest_path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                appid_match = re.search(r'"appid"\s+"(\d+)"', manifest)
                name_match = re.search(r'"name"\s+"([^"]+)"', manifest)
                if not appid_match or not name_match:
                    continue
                name = name_match.group(1)
                games.append(
                    {
                        "appid": appid_match.group(1),
                        "name": name,
                        "normalized_name": self._normalize_match_key(name),
                    }
                )
        return tuple(games)

    def _find_steam_libraryfolders_file(self) -> str | None:
        candidates = [
            os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Steam", "steamapps", "libraryfolders.vdf"),
            os.path.join(os.environ.get("ProgramFiles", ""), "Steam", "steamapps", "libraryfolders.vdf"),
        ]

        steam_shortcut = Path(r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Steam\Steam.lnk")
        if steam_shortcut.exists():
            candidates.append(os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Steam", "steamapps", "libraryfolders.vdf"))

        for candidate in candidates:
            if candidate and os.path.exists(candidate):
                return candidate
        return None

    @lru_cache(maxsize=1)
    def _get_app_candidates(self) -> tuple[dict[str, str], ...]:
        candidates: list[dict[str, str]] = []
        seen_paths: set[str] = set()

        shortcut_paths = [
            Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
            Path(os.environ.get("ProgramData", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        ]
        for root in shortcut_paths:
            if not root.exists():
                continue
            for path in root.rglob("*.lnk"):
                if path.name.lower().startswith(("uninstall", "деинсталлировать")):
                    continue
                self._append_candidate(candidates, seen_paths, path.stem, str(path))

        common_execs = [
            ("steam", os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Steam", "Steam.exe")),
            ("discord", os.path.join(os.environ.get("LOCALAPPDATA", ""), "Discord", "Update.exe")),
            ("telegram", os.path.join(os.environ.get("APPDATA", ""), "Telegram Desktop", "Telegram.exe")),
            ("chrome", os.path.join(os.environ.get("ProgramFiles", ""), "Google", "Chrome", "Application", "chrome.exe")),
            ("vscode", os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Microsoft VS Code", "Code.exe")),
            ("obsidian", os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Obsidian", "Obsidian.exe")),
        ]
        for name, path in common_execs:
            if path and os.path.exists(path):
                self._append_candidate(candidates, seen_paths, name, path)

        return tuple(candidates)

    def _append_candidate(self, candidates: list[dict[str, str]], seen_paths: set[str], name: str, path: str):
        if not path or path in seen_paths:
            return
        seen_paths.add(path)
        candidates.append(
            {
                "name": name,
                "path": path,
                "normalized_name": self._normalize_match_key(name),
            }
        )

    def _normalize_match_key(self, text: str) -> str:
        text = text.lower()
        text = text.replace("™", " ")
        text = re.sub(r"[^a-zа-я0-9]+", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _build_browser_search_reply(self, language: str, query: str, opened_target: str) -> str:
        opened_direct_result = "google.com/search?" not in opened_target
        if language == "ru":
            if opened_direct_result:
                return f"Открываю и первую найденную страницу, и результаты поиска по запросу {query}."
            return f"Открываю в браузере результаты по запросу {query}."
        if opened_direct_result:
            return f"Opening the first result and the search results for {query}."
        return f"Opening browser results for {query}."

    def _build_reply(self, language: str, target: str, opened: str) -> str:
        if opened.startswith("steam://rungameid/"):
            if language == "ru":
                return f"Запускаю {target} в Steam."
            return f"Launching {target} in Steam."
        if language == "ru":
            return f"Открываю {target}."
        return f"Opening {target}."
