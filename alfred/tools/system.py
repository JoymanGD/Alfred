import difflib
import os
import re
import subprocess
import threading
import time
import webbrowser
import ctypes
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
        language = context.language or context.detect_language(text)
        routed_action = (context.routed_args or {}).get("action") if context.routed_args else None
        routed_query = (context.routed_query or "").strip().lower()
        if not routed_action and routed_query in {
            "restart_assistant",
            "shutdown_assistant",
            "shutdown_computer",
            "restart_computer",
            "close_application",
        }:
            routed_action = routed_query
        if routed_action == "restart_assistant":
            self._schedule_alfred_restart()
            reply_text = "Перезапускаю Альфреда." if language == "ru" else "Restarting Alfred."
            return ToolResult(handled=True, reply_text=reply_text)
        if routed_action == "shutdown_assistant":
            self._schedule_alfred_shutdown()
            reply_text = "Выключаю Альфреда." if language == "ru" else "Shutting Alfred down."
            return ToolResult(handled=True, reply_text=reply_text)
        if routed_action == "shutdown_computer":
            self._schedule_computer_shutdown()
            reply_text = "Выключаю компьютер." if language == "ru" else "Shutting the computer down."
            return ToolResult(handled=True, reply_text=reply_text)

        if routed_action == "restart_computer":
            self._schedule_computer_restart()
            reply_text = "РџРµСЂРµР·Р°РіСЂСѓР¶Р°СЋ РєРѕРјРїСЊСЋС‚РµСЂ." if language == "ru" else "Restarting the computer."
            return ToolResult(handled=True, reply_text=reply_text)

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
            if routed_action == "close_application":
                closed = self._close_target(target)
                reply_text = self._build_close_reply(language, target, closed)
                return ToolResult(handled=True, reply_text=reply_text, target=closed or target)

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

    def _close_target(self, target: str) -> str | None:
        resolved = self._resolve_target(target)
        candidates = self._build_process_name_candidates(target, resolved)
        for process_name in candidates:
            result = subprocess.run(
                ["taskkill", "/IM", process_name, "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
            if result.returncode == 0:
                return process_name

        visible_name = self._extract_process_basename(resolved) or target.strip()
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "$target = "
                    + repr(target)
                    + "; "
                    + "$name = "
                    + repr(visible_name)
                    + "; "
                    + "Get-Process | Where-Object { "
                    + "$_.MainWindowTitle -like ('*' + $target + '*') -or "
                    + "$_.ProcessName -like ('*' + $name + '*') "
                    + "} | Stop-Process -Force"
                ),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        if result.returncode == 0:
            return visible_name
        return None

    def _build_process_name_candidates(self, target: str, resolved: str) -> list[str]:
        candidates: list[str] = []
        for value in (resolved, target):
            process_name = self._extract_process_basename(value)
            if process_name and process_name not in candidates:
                candidates.append(process_name)
        return candidates

    def _extract_process_basename(self, value: str) -> str | None:
        if not value:
            return None
        cleaned = value.strip().strip("\"'")
        base_name = os.path.basename(cleaned)
        if not base_name:
            return None
        if "." not in base_name:
            base_name = f"{base_name}.exe"
        return base_name

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

    def _build_close_reply(self, language: str, target: str, closed: str | None) -> str:
        if closed:
            if language == "ru":
                return f"Р—Р°РєСЂС‹РІР°СЋ {target}."
            return f"Closing {target}."
        if language == "ru":
            return f"РќРµ РЅР°С€РµР» Р·Р°РїСѓС‰РµРЅРЅРѕРµ РїСЂРёР»РѕР¶РµРЅРёРµ {target}."
        return f"I could not find a running app matching {target}."

    def _schedule_alfred_restart(self):
        root_dir = Path(__file__).resolve().parents[2]
        run_script = root_dir / "run_alfred.bat"
        if not run_script.exists():
            raise FileNotFoundError(f"run_alfred.bat not found at {run_script}")

        def delayed_restart():
            time.sleep(1.0)
            subprocess.Popen(
                ["cmd", "/c", "start", "", str(run_script)],
                cwd=str(root_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self._request_console_close()
            os._exit(0)

        threading.Thread(target=delayed_restart, daemon=True).start()

    def _schedule_alfred_shutdown(self):
        def delayed_shutdown():
            time.sleep(1.0)
            self._request_console_close()
            os._exit(0)

        threading.Thread(target=delayed_shutdown, daemon=True).start()

    def _schedule_computer_shutdown(self):
        def delayed_shutdown():
            time.sleep(1.0)
            subprocess.Popen(
                ["shutdown", "/s", "/t", "0"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

        threading.Thread(target=delayed_shutdown, daemon=True).start()

    def _schedule_computer_restart(self):
        def delayed_restart():
            time.sleep(1.0)
            subprocess.Popen(
                ["shutdown", "/r", "/t", "0"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

        threading.Thread(target=delayed_restart, daemon=True).start()

    def _request_console_close(self):
        try:
            console_window = ctypes.windll.kernel32.GetConsoleWindow()
            if console_window:
                ctypes.windll.user32.PostMessageW(console_window, 0x0010, 0, 0)
        except Exception:
            pass

    def _build_close_reply(self, language: str, target: str, closed: str | None) -> str:
        if closed:
            if language == "ru":
                return f"\u0417\u0430\u043a\u0440\u044b\u0432\u0430\u044e {target}."
            return f"Closing {target}."
        if language == "ru":
            return (
                f"\u041d\u0435 \u043d\u0430\u0448\u0435\u043b "
                f"\u0437\u0430\u043f\u0443\u0449\u0435\u043d\u043d\u043e\u0435 "
                f"\u043f\u0440\u0438\u043b\u043e\u0436\u0435\u043d\u0438\u0435 {target}."
            )
        return f"I could not find a running app matching {target}."

    def _build_browser_search_reply(self, language: str, query: str, opened_target: str) -> str:
        opened_direct_result = "google.com/search?" not in opened_target
        if language == "ru":
            if opened_direct_result:
                return f"Открываю первую найденную страницу и результаты поиска по запросу {query}."
            return f"Открываю результаты поиска в браузере по запросу {query}."
        if opened_direct_result:
            return f"Opening the first result and the search results for {query}."
        return f"Opening browser results for {query}."
