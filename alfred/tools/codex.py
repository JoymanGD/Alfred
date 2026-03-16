import os
import re
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from alfred.tooling import Tool, ToolContext, ToolResult


@dataclass
class CodexToolConfig:
    codex_exe: str
    project_dir: str


class CodexTool(Tool):
    name = "codex"

    def __init__(self, config: CodexToolConfig):
        self.config = config

    def matches(self, context: ToolContext, text: str) -> bool:
        lowered = text.lower().strip()
        trigger_terms = ["codex", "кодекс", "кодексу", "кодекса"]
        action_terms = ["ask", "tell", "request", "попроси", "спроси", "запроси"]
        return any(term in lowered for term in trigger_terms) and any(term in lowered for term in action_terms)

    def handle(self, context: ToolContext, text: str) -> ToolResult:
        query = (
            context.routed_query
            if context.routed_tool_name == self.name and context.routed_query
            else self._extract_query(text)
        )
        language = context.detect_language(text)
        if not query:
            reply_text = (
                "Скажи, что именно передать Codex."
                if language == "ru"
                else "Tell me what I should send to Codex."
            )
            return ToolResult(handled=True, reply_text=reply_text)

        codex_exe = self._resolve_codex_exe()
        if not codex_exe:
            reply_text = (
                "Не нашел Codex CLI."
                if language == "ru"
                else "I could not find Codex CLI."
            )
            return ToolResult(handled=True, reply_text=reply_text)

        output_file = self._create_temp_file(".txt")
        prompt_file = self._create_temp_file(".txt")
        Path(prompt_file).write_text(self._build_codex_prompt(query, language), encoding="utf-8")
        script_file = self._create_runner_script(codex_exe, output_file, prompt_file)

        try:
            process = subprocess.Popen(
                ["cmd", "/c", script_file],
                cwd=self.config.project_dir,
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            self._cleanup_temp_files(output_file, prompt_file, script_file)
            reply_text = (
                f"Не удалось запустить Codex: {exc}"
                if language == "ru"
                else f"I could not start Codex: {exc}"
            )
            return ToolResult(handled=True, reply_text=reply_text)

        self._watch_codex_process(process, output_file, prompt_file, script_file, context.event_out_queue, language)

        reply_text = (
            "Запускаю Codex в отдельном окне."
            if language == "ru"
            else "Starting Codex in a separate window."
        )
        return ToolResult(handled=True, reply_text=reply_text)

    def _watch_codex_process(
        self,
        process,
        output_file: str,
        prompt_file: str,
        script_file: str,
        event_out_queue,
        language: str,
    ):
        def runner():
            try:
                process.wait(timeout=1800)
                last_message = self._read_last_message(output_file)
                if process.returncode == 0:
                    reply_text = self._build_success_reply(language, last_message)
                else:
                    reply_text = self._build_failure_reply(language, last_message)
            except subprocess.TimeoutExpired:
                reply_text = (
                    "Codex не успел закончить вовремя."
                    if language == "ru"
                    else "Codex did not finish in time."
                )
                try:
                    process.kill()
                except Exception:
                    pass
            except Exception as exc:
                reply_text = (
                    f"Ошибка ожидания Codex: {exc}"
                    if language == "ru"
                    else f"Codex wait error: {exc}"
                )
            finally:
                self._cleanup_temp_files(output_file, prompt_file, script_file)

            if event_out_queue is not None and reply_text:
                event_out_queue.put(reply_text)

        threading.Thread(target=runner, daemon=True).start()

    def _resolve_codex_exe(self) -> str | None:
        if not self.config.codex_exe:
            return None
        if os.path.isabs(self.config.codex_exe) and os.path.exists(self.config.codex_exe):
            print(f"[CODEX] Using configured executable: {self.config.codex_exe}")
            return self.config.codex_exe

        from_path = shutil.which(self.config.codex_exe)
        if from_path:
            print(f"[CODEX] Resolved executable from PATH: {from_path}")
            return from_path

        for candidate in self._fallback_codex_candidates():
            if candidate.exists():
                resolved = str(candidate)
                print(f"[CODEX] Resolved executable from fallback path: {resolved}")
                return resolved

        print("[CODEX] Codex executable was not found.")
        return None

    def _fallback_codex_candidates(self) -> list[Path]:
        candidates: list[Path] = []

        user_profile = Path(os.environ.get("USERPROFILE", ""))
        extension_root = user_profile / ".vscode" / "extensions"
        if extension_root.exists():
            for extension_dir in sorted(extension_root.glob("openai.chatgpt-*"), reverse=True):
                candidates.append(extension_dir / "bin" / "windows-x86_64" / "codex.exe")
                candidates.append(extension_dir / "bin" / "windows-arm64" / "codex.exe")

        local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
        candidates.append(local_app_data / "Programs" / "Microsoft VS Code" / "bin" / "codex")
        candidates.append(local_app_data / "Programs" / "Microsoft VS Code" / "bin" / "codex.cmd")

        return candidates

    def _extract_query(self, text: str) -> str:
        patterns = [
            r"^(?:ask|tell|request)\s+(?:codex)\s+to\s+(.+)$",
            r"^(?:попроси|спроси|запроси)\s+(?:у\s+)?(?:кодекса|кодексу|кодекс)\s+(.+)$",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip(" ?!.,")
        return text.strip(" ?!.,")

    def _build_codex_prompt(self, query: str, language: str) -> str:
        if language == "ru":
            return (
                "Выполни задачу в текущем проекте. "
                f"Запрос пользователя: {query}\n"
                "Если нужны изменения в коде, внеси их. "
                "В финальном ответе напиши ровно одну очень короткую фразу по-русски для TTS: "
                "что сделано или почему не удалось."
            )
        return (
            "Execute the task in the current project. "
            f"User request: {query}\n"
            "If code changes are needed, make them. "
            "In the final response, write exactly one very short sentence in English suitable for TTS: "
            "what was done or why it failed."
        )

    def _create_temp_file(self, suffix: str) -> str:
        fd, path = tempfile.mkstemp(prefix="alfred_codex_", suffix=suffix)
        os.close(fd)
        return path

    def _create_runner_script(self, codex_exe: str, output_file: str, prompt_file: str) -> str:
        script_file = self._create_temp_file(".bat")
        script_lines = [
            "@echo off",
            "chcp 65001 > nul",
            f'cd /d "{self.config.project_dir}"',
            f'type "{prompt_file}" | "{codex_exe}" exec --cd "{self.config.project_dir}" --color never --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox --output-last-message "{output_file}" -',
        ]
        Path(script_file).write_text("\n".join(script_lines) + "\n", encoding="utf-8")
        return script_file

    def _read_last_message(self, output_file: str) -> str:
        try:
            return Path(output_file).read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""

    def _cleanup_temp_files(self, *paths: str):
        for path in paths:
            try:
                os.remove(path)
            except OSError:
                pass

    def _build_success_reply(self, language: str, last_message: str) -> str:
        summary = self._normalize_summary(last_message)
        if summary:
            return summary
        return "Codex закончил." if language == "ru" else "Codex finished."

    def _build_failure_reply(self, language: str, details: str) -> str:
        summary = self._normalize_summary(details)
        if summary:
            prefix = "Codex не смог:" if language == "ru" else "Codex could not:"
            return f"{prefix} {summary}"
        return "Codex не смог выполнить задачу." if language == "ru" else "Codex could not complete the task."

    def _normalize_summary(self, text: str) -> str:
        cleaned = text.replace("\r", "\n")
        cleaned = re.sub(r"```.*?```", " ", cleaned, flags=re.DOTALL)
        cleaned = re.sub(r"[`*_#>\-]+", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,!?:;")
        if not cleaned:
            return ""
        if len(cleaned) > 160:
            cleaned = cleaned[:157].rstrip() + "..."
        return cleaned
