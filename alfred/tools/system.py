import difflib
import os
import re
import subprocess
import threading
import time
import webbrowser
import ctypes
import winsound
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus, unquote

from alfred.tooling import Tool, ToolContext, ToolResult
from alfred.running_windows import list_running_windows


class SystemTool(Tool):
    name = "system"
    AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".ogg"}
    def matches(self, context: ToolContext, text: str) -> bool:
        return context.routed_tool_name == self.name

    def handle(self, context: ToolContext, text: str) -> ToolResult:
        language = context.language or context.detect_language(text)
        routed_action = (context.routed_args or {}).get("action") if context.routed_args else None
        routed_query = (context.routed_query or "").strip().lower()
        location_hint = ((context.routed_args or {}).get("location_hint") or "").strip().lower()
        target_kind = ((context.routed_args or {}).get("target_kind") or "").strip().lower()
        if not routed_action and routed_query in {
            "restart_assistant",
            "shutdown_assistant",
            "shutdown_computer",
            "restart_computer",
            "close_application",
        }:
            routed_action = routed_query
        if routed_action == "restart_assistant":
            self._request_assistant_control(context, "restart_assistant")
            reply_text = "Перезапускаю Альфреда." if language == "ru" else "Restarting Alfred."
            return ToolResult(handled=True, reply_text=reply_text)
        if routed_action == "shutdown_assistant":
            self._request_assistant_control(context, "shutdown_assistant")
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

        browser_query = (context.routed_query or "").strip() if context.routed_intent == "browser_open" else ""
        if context.routed_intent == "browser_open" and not browser_query:
            reply_text = "Что открыть в браузере?" if language == "ru" else "What should I open in the browser?"
            return ToolResult(handled=True, reply_text=reply_text)
        if browser_query:
            opened_target = self._open_browser_search_target(context, browser_query)
            reply_text = self._build_browser_search_reply(language, browser_query, opened_target)
            return ToolResult(handled=True, reply_text=reply_text, target=opened_target)

        target = (context.routed_query or "").strip()
        if routed_action == "close_application" and not (context.routed_args or {}).get("process_id"):
            reply_text = "Уточни, какое открытое приложение закрыть." if language == "ru" else "Which open app should I close?"
            return ToolResult(handled=True, reply_text=reply_text)
        if not target:
            reply_text = (
                "Я не понял, что именно нужно открыть."
                if language == "ru"
                else "I could not tell what you want me to open."
            )
            return ToolResult(handled=True, reply_text=reply_text)

        if routed_action != "close_application" and self._should_treat_as_file(target, target_kind):
            file_match = self._find_file_target_details(target, location_hint=location_hint)
            if file_match["resolved"]:
                opened = self._open_target(str(file_match["resolved"]), location_hint=location_hint)
                reply_text = self._build_reply(language, target, opened)
                return ToolResult(handled=True, reply_text=reply_text)
            reply_text = self._build_file_not_found_reply(
                language,
                target,
                file_match["searched_roots"],
                file_match["suggestions"],
            )
            return ToolResult(handled=True, reply_text=reply_text)

        try:
            if routed_action == "close_application":
                closed = self._close_target(
                    target,
                    int((context.routed_args or {})["process_id"]),
                    str((context.routed_args or {})["process_name"]),
                )
                reply_text = self._build_close_reply(language, target, closed)
                return ToolResult(handled=True, reply_text=reply_text, target=closed)

            opened = self._open_target(target, location_hint=location_hint)
            reply_text = self._build_reply(language, target, opened)
        except Exception as e:
            reply_text = (
                f"Не удалось открыть {target}: {e}"
                if language == "ru"
                else f"I could not open {target}: {e}"
            )
        return ToolResult(handled=True, reply_text=reply_text)

    def _request_assistant_control(self, context: ToolContext, action: str):
        if context.assistant_control is not None:
            context.assistant_control(action)
            return
        if action == "restart_assistant":
            self._schedule_alfred_restart()
            return
        if action == "shutdown_assistant":
            self._schedule_alfred_shutdown()

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

    def _resolve_target(self, target: str, location_hint: str = "") -> str:
        file_target = self._find_file_target(target, location_hint=location_hint)
        if file_target:
            return file_target

        steam_target = self._find_steam_game_target(target)
        if steam_target:
            return steam_target

        discovered_target = self._find_installed_app_target(target)
        if discovered_target:
            return discovered_target

        return target

    def _open_target(self, target: str, location_hint: str = "") -> str:
        resolved = self._resolve_target(target, location_hint=location_hint)
        if re.match(r"^https?://", resolved, flags=re.IGNORECASE):
            webbrowser.open(resolved)
            return resolved
        if resolved.startswith("steam://"):
            os.startfile(resolved)
            return resolved
        if os.path.exists(resolved):
            if self._is_audio_file(resolved):
                self._play_audio_file(resolved)
                return resolved
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

    def _play_audio_file(self, path: str):
        suffix = Path(path).suffix.lower()
        if suffix == ".wav":
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            print(f"[SYSTEM] Playing wav file through winsound: {path}")
            return

        escaped = path.replace("'", "''")
        command = (
            "Add-Type -AssemblyName presentationCore; "
            "$player = New-Object System.Windows.Media.MediaPlayer; "
            f"$player.Open([Uri]'{escaped}'); "
            "$player.Volume = 1.0; "
            "$player.Play(); "
            "Start-Sleep -Seconds 2"
        )
        try:
            subprocess.Popen(
                ["powershell", "-NoProfile", "-STA", "-WindowStyle", "Hidden", "-Command", command],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            print(f"[SYSTEM] Playing audio file: {path}")
        except Exception:
            os.startfile(path)

    def _is_audio_file(self, path: str) -> bool:
        return Path(path).suffix.lower() in self.AUDIO_EXTENSIONS

    def _close_target(self, target: str, pid: int, process_name: str) -> str | None:
        current = next(
            (window for window in list_running_windows() if window["pid"] == pid and window["name"] == process_name),
            None,
        )
        if current is None:
            print(f"[SYSTEM] Selected app is no longer open: {target!r}, pid={pid}")
            return None
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        if result.returncode == 0:
            print(f"[SYSTEM] Closed selected app: {target!r}, pid={pid}")
            return process_name
        print(f"[SYSTEM] Failed to close selected app: {target!r}, pid={pid}, exit={result.returncode}")
        return None

    def _find_file_target(self, target: str, location_hint: str = "") -> str | None:
        details = self._find_file_target_details(target, location_hint=location_hint)
        resolved = details["resolved"]
        return str(resolved) if resolved else None

    def _find_file_target_details(self, target: str, location_hint: str = "") -> dict:
        cleaned = target.strip().strip("\"'")
        result = {
            "resolved": None,
            "searched_roots": [],
            "suggestions": [],
        }
        if not cleaned:
            return result

        expanded = Path(os.path.expandvars(cleaned)).expanduser()
        if expanded.exists():
            result["resolved"] = expanded
            return result

        if os.path.exists(cleaned):
            result["resolved"] = Path(os.path.abspath(cleaned))
            return result

        if not self._looks_like_file_target(cleaned):
            return result

        file_name = Path(cleaned).name
        search_roots = self._candidate_search_roots(location_hint)
        result["searched_roots"] = [str(root) for root in search_roots if root.exists()]
        seen: set[str] = set()
        suggestions: list[str] = []

        for root in search_roots:
            normalized_root = str(root).lower()
            if normalized_root in seen or not root.exists():
                continue
            seen.add(normalized_root)

            direct_candidate = root / file_name
            if direct_candidate.exists():
                print(f"[SYSTEM] Found file directly: {direct_candidate}")
                result["resolved"] = direct_candidate
                return result

            try:
                candidate_names: list[str] = []
                for candidate in root.rglob("*"):
                    if not candidate.is_file():
                        continue
                    if candidate.name.lower() == file_name.lower():
                        print(f"[SYSTEM] Found file by search: {candidate}")
                        result["resolved"] = candidate
                        return result
                    candidate_names.append(candidate.name)
                if not suggestions:
                    close = difflib.get_close_matches(file_name, candidate_names, n=3, cutoff=0.68)
                    suggestions.extend(close)
            except OSError:
                continue

        result["suggestions"] = suggestions[:3]
        return result

    def _candidate_search_roots(self, location_hint: str) -> list[Path]:
        user_profile = Path(os.environ.get("USERPROFILE", ""))
        project_root = Path(__file__).resolve().parents[2]
        one_drive = Path(os.environ.get("OneDrive", ""))
        known_roots = {
            "desktop": user_profile / "Desktop",
            "desktop_onedrive": one_drive / "Desktop" if str(one_drive) else None,
            "downloads": user_profile / "Downloads",
            "documents": user_profile / "Documents",
            "documents_onedrive": one_drive / "Documents" if str(one_drive) else None,
            "music": user_profile / "Music",
            "pictures": user_profile / "Pictures",
            "videos": user_profile / "Videos",
            "home": user_profile,
            "project": project_root,
        }

        ordered_roots: list[Path] = []
        preferred_keys = {
            "desktop": ("desktop", "desktop_onedrive"),
            "documents": ("documents", "documents_onedrive"),
        }
        if location_hint in preferred_keys:
            for key in preferred_keys[location_hint]:
                root = known_roots.get(key)
                if isinstance(root, Path):
                    ordered_roots.append(root)
        elif location_hint in known_roots and isinstance(known_roots[location_hint], Path):
            ordered_roots.append(known_roots[location_hint])

        for key in ("desktop", "desktop_onedrive", "downloads", "documents", "documents_onedrive", "music", "project", "home"):
            root = known_roots[key]
            if isinstance(root, Path) and root not in ordered_roots:
                ordered_roots.append(root)
        return ordered_roots

    def _should_treat_as_file(self, target: str, target_kind: str) -> bool:
        if target_kind == "file":
            return True
        return self._looks_like_file_target(target)

    def _build_file_not_found_reply(
        self,
        language: str,
        target: str,
        searched_roots: list[str],
        suggestions: list[str],
    ) -> str:
        searched_preview = ", ".join(searched_roots[:3]) if searched_roots else "the usual folders"
        if language == "ru":
            reply = f"Не нашел файл {target}. Искал в: {searched_preview}."
            if suggestions:
                reply += f" Похожие файлы: {', '.join(suggestions)}."
            return reply
        reply = f"I could not find the file {target}. I looked in: {searched_preview}."
        if suggestions:
            reply += f" Close matches: {', '.join(suggestions)}."
        return reply

    def _looks_like_file_target(self, target: str) -> bool:
        lowered = target.lower().strip()
        if any(sep in target for sep in ("\\", "/")):
            return True
        if re.search(r"\.[a-z0-9]{2,5}$", lowered):
            return True
        media_extensions = (
            ".wav",
            ".mp3",
            ".flac",
            ".m4a",
            ".ogg",
            ".txt",
            ".pdf",
            ".png",
            ".jpg",
            ".jpeg",
            ".mp4",
            ".mkv",
        )
        return lowered.endswith(media_extensions)

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

        partial = []
        for candidate in candidates:
            name = candidate["normalized_name"]
            if len(name.replace(" ", "")) < 3:
                continue
            if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", normalized_target) or (
                len(normalized_target) >= 3
                and re.search(rf"(?<!\w){re.escape(normalized_target)}(?!\w)", name)
            ):
                partial.append(candidate)
        if partial:
            best_match = max(partial, key=lambda candidate: len(candidate["normalized_name"]))
            print(f"[SYSTEM] Matched installed app partially: {best_match['name']}")
            return best_match["path"]

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
        run_script = root_dir / "start_alfred_core.bat"
        if not run_script.exists():
            run_script = root_dir / "run_alfred.bat"
        if not run_script.exists():
            raise FileNotFoundError(f"Alfred launcher not found in {root_dir}")

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
