import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from alfred.tooling import Tool, ToolContext, ToolResult


@dataclass
class LocalFilesToolConfig:
    base_dir: str


class LocalFilesTool(Tool):
    name = "local_files"
    def __init__(self, config: LocalFilesToolConfig):
        self.config = config

    def matches(self, context: ToolContext, text: str) -> bool:
        return context.routed_tool_name == self.name

    def handle(self, context: ToolContext, text: str) -> ToolResult:
        language = context.language or context.detect_language(text)
        try:
            request = self._build_request(context, text)
        except Exception as exc:
            print(f"[FILES] Request parsing failed: {exc}")
            return ToolResult(
                handled=True,
                reply_text=(
                    "Не удалось разобрать запрос о файлах. Попробуй переформулировать."
                    if language == "ru" else "I could not parse the file request. Please rephrase it."
                ),
            )
        roots = self._candidate_search_roots(request["location_hint"])
        if not roots:
            reply_text = "I could not determine where to search." if language != "ru" else "Не понял, где искать."
            return ToolResult(handled=True, reply_text=reply_text)

        root = roots[0]
        if request["action"] == "describe":
            reply_text = self._describe_folder(language, root, request["recursive"])
            return ToolResult(handled=True, reply_text=reply_text)

        matches = self._search_files(root, request["recursive"], request["extensions"], request["name_query"])
        reply_text = self._format_results(language, root, matches, request)
        return ToolResult(handled=True, reply_text=reply_text)

    def _build_request(self, context: ToolContext, text: str) -> dict:
        action = (context.routed_args or {}).get("action")
        if action not in {"list", "find", "describe"}:
            raise ValueError(f"Missing Decider file action: {action!r}")
        response = context.ollama_chat(
            [
                {"role": "system", "content": "You extract local file search parameters. Return valid JSON only."},
                {"role": "user", "content": self._build_prompt(text)},
            ]
        )
        raw = context.normalize_memory_text(response["message"]["content"])
        data = self._parse_json(raw)
        return self._merge_request(
            {"action": action, "location_hint": "home", "extensions": [], "name_query": "", "recursive": False},
            data,
        )

    def _build_prompt(self, text: str) -> str:
        return (
            "Convert this local file request into JSON only.\n"
            'Schema: {"location_hint":"","extensions":[".txt"],"name_query":"","recursive":true}\n'
            "Rules:\n"
            "- This is only for local files and folders on the PC.\n"
            "- location_hint should be one of: desktop, downloads, documents, music, pictures, videos, project, home.\n"
            "- Put file extensions in lowercase with a leading dot.\n"
            "- name_query should be the important filename fragment if the user names one.\n"
            "- recursive should be true when the request implies searching broadly.\n"
            "- Return JSON only.\n\n"
            f"User request: {text}"
        )

    def _merge_request(self, defaults: dict, parsed: dict) -> dict:
        result = dict(defaults)
        location_hint = str(parsed.get("location_hint") or "").strip().lower()
        if location_hint:
            result["location_hint"] = location_hint
        name_query = str(parsed.get("name_query") or "").strip()
        if name_query:
            result["name_query"] = name_query
        extensions = parsed.get("extensions")
        if isinstance(extensions, list):
            normalized = []
            for item in extensions:
                ext = str(item).strip().lower()
                if not ext:
                    continue
                if not ext.startswith("."):
                    ext = f".{ext}"
                normalized.append(ext)
            if normalized:
                result["extensions"] = sorted(set(normalized))
        if isinstance(parsed.get("recursive"), bool):
            result["recursive"] = parsed["recursive"]
        return result

    def _candidate_search_roots(self, location_hint: str) -> list[Path]:
        user_profile = Path(os.environ.get("USERPROFILE", ""))
        one_drive = Path(os.environ.get("OneDrive", ""))
        project_root = Path(self.config.base_dir)
        mapping = {
            "desktop": [user_profile / "Desktop", one_drive / "Desktop" if str(one_drive) else None],
            "downloads": [user_profile / "Downloads"],
            "documents": [user_profile / "Documents", one_drive / "Documents" if str(one_drive) else None],
            "music": [user_profile / "Music"],
            "pictures": [user_profile / "Pictures"],
            "videos": [user_profile / "Videos"],
            "project": [project_root],
            "home": [user_profile],
        }
        roots = mapping.get(location_hint, mapping["home"])
        return [root for root in roots if isinstance(root, Path) and root.exists()]

    def _search_files(self, root: Path, recursive: bool, extensions: list[str], name_query: str) -> list[Path]:
        matches: list[Path] = []
        iterator = root.rglob("*") if recursive else root.iterdir()
        lowered_query = name_query.lower()
        try:
            for candidate in iterator:
                if not candidate.is_file():
                    continue
                if extensions and candidate.suffix.lower() not in extensions:
                    continue
                if lowered_query and lowered_query not in candidate.name.lower():
                    continue
                matches.append(candidate)
        except OSError as exc:
            print(f"[FILES] Search failed in {root}: {exc}")
        matches.sort(key=lambda item: item.name.lower())
        return matches[:100]

    def _describe_folder(self, language: str, root: Path, recursive: bool) -> str:
        if not root.exists():
            return f"I could not find {root}." if language != "ru" else f"Не нашел {root}."
        file_count = 0
        folder_count = 0
        extension_counts: dict[str, int] = {}
        iterator = root.rglob("*") if recursive else root.iterdir()
        try:
            for candidate in iterator:
                if candidate.is_dir():
                    folder_count += 1
                    continue
                if candidate.is_file():
                    file_count += 1
                    extension = candidate.suffix.lower() or "[no extension]"
                    extension_counts[extension] = extension_counts.get(extension, 0) + 1
        except OSError as exc:
            return f"I could not inspect {root}: {exc}" if language != "ru" else f"Не удалось просмотреть {root}: {exc}"
        top_types = sorted(extension_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
        type_preview = ", ".join(f"{ext} {count}" for ext, count in top_types) if top_types else "no files"
        if language == "ru":
            return f"В {root} нашел {file_count} файлов и {folder_count} папок. Основные типы: {type_preview}."
        return f"In {root} I found {file_count} files and {folder_count} folders. Main types: {type_preview}."

    def _format_results(self, language: str, root: Path, matches: list[Path], request: dict) -> str:
        description = self._request_description(request)
        if not matches:
            if language == "ru":
                return f"Не нашел {description} в {root}."
            return f"I could not find {description} in {root}."
        preview = ", ".join(path.name for path in matches[:12])
        if len(matches) > 12:
            preview += f", and {len(matches) - 12} more"
        if language == "ru":
            return f"Нашел {len(matches)} {description} в {root}: {preview}."
        return f"I found {len(matches)} {description} in {root}: {preview}."

    def _request_description(self, request: dict) -> str:
        if request["extensions"]:
            if len(request["extensions"]) == 1:
                return f"{request['extensions'][0]} files"
            return "matching files"
        if request["name_query"]:
            return f'files matching {request["name_query"]}'
        return "files"

    def _parse_json(self, raw: str) -> dict:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
            if match:
                return json.loads(match.group(0))
            raise
