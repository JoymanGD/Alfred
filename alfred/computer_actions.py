"""Model planned computer actions with an executor owned confirmation boundary."""

import json
import hashlib
import os
import platform
import re
import shutil
import subprocess
import urllib.error
import urllib.request
import webbrowser
import zipfile
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus, urlparse

from alfred.action_catalog import action_help, category_criteria, confirmation_policy, load_catalog
from alfred.browser_automation import ManagedBrowser
from alfred.downloads import DownloadManager
from alfred.extended_actions import (AlfredTrash, TextUndoStore, control_window, extract_pdf_text,
                                     list_audio_outputs, local_network_info, network_diagnostics,
                                     select_audio_output)
from alfred.reminders import ReminderService, parse_due_time
from alfred.running_windows import list_running_windows
from alfred.tools.web import strip_html

EXECUTABLE_EXTENSIONS = {".exe", ".com", ".bat", ".cmd", ".ps1", ".msi", ".vbs", ".js"}


@dataclass
class PendingAction:
    action: str
    args: dict
    description: str
    fingerprint: tuple | None = None
    steps: list[dict] = field(default_factory=list)
    original_request: str = ""
    requires_confirmation: bool = False


def _json_object(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise ValueError("The planner did not return JSON")
        data = json.loads(match.group())
    if not isinstance(data, dict):
        raise ValueError("The planner response must be an object")
    return data


def _required(args: dict, key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing {key}")
    return value.strip()


def _text(args: dict, key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str):
        raise ValueError(f"Missing {key}")
    return value


def _path(value: str) -> Path:
    path = Path(os.path.expandvars(value)).expanduser()
    if not path.is_absolute():
        raise ValueError("Use an exact absolute path; search for the file first")
    return Path(os.path.abspath(path))


def _fingerprint(path: Path) -> tuple:
    stat = path.lstat()
    return (str(path), stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size, path.is_dir(), path.is_symlink())


class ComputerActionRunner:
    def __init__(self, registry, base_dir: str, event_out_queue=None, ocr_engine=None):
        load_catalog()
        self.registry = registry
        self.base_dir = base_dir
        self.pending: PendingAction | None = None
        state_dir = Path(base_dir) / ".alfred_state"
        self.trash = AlfredTrash(state_dir / "trash")
        self.undo = TextUndoStore(state_dir / "text_undo")
        self.browser = ManagedBrowser(state_dir / "browser_profile")
        self.downloads = DownloadManager(event_out_queue)
        self.reminders = ReminderService(state_dir / "reminders.json", event_out_queue)
        self.ocr_engine = ocr_engine

    def wants_actions(self, context) -> bool:
        choice = context.decider.choose(
            {"recent_conversation": context.recent_history[-6:], "current_message": context.user_text},
            "Does the current message ask Alfred to DO an operation using available computer capabilities, possibly with multiple steps? Classify meaning, not wording. Alfred has no body or robot: a request to move or handle physical objects is other. A question asking only for facts or ordinary conversation is other. An explicit request to search online is computer_actions.",
            {
                "computer_actions": "Perform one or more operations with files, documents, browser, internet, windows, media, devices, reminders, clipboard, system, vision or Codex.",
                "other": "Answer or discuss without performing an operation; includes ordinary factual questions, image generation and plan management.",
            },
        )
        return choice == "computer_actions"

    def resolve_confirmation(self, context) -> str:
        pending = self.pending
        if pending is None:
            raise RuntimeError("No pending action")
        choice = context.decider.choose(
            {"requested_action": pending.description, "user_reply": context.user_text},
            "Does the user approve this exact pending action, reject it, or start a different request? Only clear, affirmative consent is approve. Uncertainty is reject.",
            {
                "approve": "Clearly and specifically authorizes the shown action now.",
                "reject": "Declines, expresses uncertainty, or does not clearly authorize it.",
                "new_request": "Asks Alfred to do something else instead of answering the confirmation.",
            },
            sensitive_choices={"approve"},
        )
        self.pending = None
        if choice == "new_request":
            return "new_request"
        if choice != "approve":
            return "Отменено." if context.language == "ru" else "Cancelled."
        try:
            self._revalidate(pending)
            result = self._execute(context, pending.action, pending.args)
        except Exception as exc:
            return f"Действие не выполнено: {exc}" if context.language == "ru" else f"Action not completed: {exc}"
        pending.steps.append({"action": pending.action, "args": pending.args, "result": result})
        return self.run(context, original_request=pending.original_request, steps=pending.steps)

    def run(self, context, *, original_request: str | None = None, steps: list[dict] | None = None) -> str:
        request = original_request or context.user_text
        observations = list(steps or [])
        selected_category = None
        for _ in range(max(1, 14 - len(observations))):
            try:
                category = selected_category or self._select_category(context, request, observations)
            except Exception as exc:
                return (f"Не удалось выбрать действие: {exc}" if context.language == "ru"
                        else f"Could not select an action: {exc}")
            selected_category = None
            try:
                decision = self._plan(context, request, observations, category)
                action = decision.get("action")
                if action == "reply":
                    return _required(decision, "text")
                if action == "select_category":
                    requested_category = _required(decision, "category")
                    if requested_category not in category_criteria():
                        raise ValueError(f"Unknown category: {requested_category}")
                    selected_category = requested_category
                    continue
                allowed = load_catalog()["categories"][category]["actions"]
                if action not in allowed:
                    raise ValueError(f"Action {action!r} is not available in category {category!r}; select the correct category")
                if len(observations) >= 8:
                    raise ValueError("Action limit reached")
                args = decision.get("args")
                if not isinstance(args, dict):
                    raise ValueError("Action arguments must be an object")
                if any(item.get("action") == action and item.get("args") == args for item in observations):
                    raise ValueError("This exact action was already completed")
                prepared = self._prepare(action, args)
                policy = confirmation_policy(action)
                if policy == "always" or (policy == "conditional" and prepared.requires_confirmation):
                    prepared.steps = observations
                    prepared.original_request = request
                    self.pending = prepared
                    return (f"Подтвердите действие: {prepared.description}. Выполнить?" if context.language == "ru"
                            else f"Confirm this action: {prepared.description}. Proceed?")
                result = self._execute(context, action, prepared.args)
                observations.append({"action": action, "args": prepared.args, "result": result})
            except Exception as exc:
                observations.append({"error": str(exc), "step": len(observations) + 1})
                if len(observations) >= 8:
                    break
        last_issue = observations[-1] if observations and "error" in observations[-1] else "action limit reached"
        return ("Не удалось завершить задачу. Причина: " if context.language == "ru" else "I could not complete the task. Reason: ") + str(last_issue)

    def _select_category(self, context, request: str, observations: list[dict]) -> str:
        recent_steps = [{"action": item.get("action"), "args": item.get("args"),
                         "result": str(item.get("result", item.get("error", "")))[:600]}
                        for item in observations[-4:]]
        category = context.decider.choose(
            {"request": request, "recent_conversation": context.recent_history[-6:], "completed_steps": recent_steps},
            "Which capability category contains the NEXT step needed to fulfill this request? Use the results of completed steps. Choose by meaning, never by matching words. If ready to answer, any category is acceptable.",
            category_criteria(),
        )
        if category not in category_criteria():
            raise ValueError(f"Unknown category: {category}")
        return category

    def _plan(self, context, request: str, observations: list[dict], category: str) -> dict:
        prompt = (
            "You are Alfred's computer action planner. Return exactly one JSON object per turn: "
            '{"action":"name","args":{...}} or {"action":"reply","text":"..."} '
            'or {"action":"select_category","category":"category_id"}. '
            "Choose the next necessary action from the available capabilities. Interpret the user's meaning in any language. "
            "Use results from prior steps, never invent paths, process IDs, URLs, or action results. "
            "Use search_files/list_windows before acting on an unclear target. "
            "Prefer recoverable trash for ordinary deletion and graceful window close for ordinary closing. "
            "Use permanent deletion or force close only when clearly requested or necessary. "
            "Inspect browser controls before using an element ID. "
            "Never repeat password field contents in the final reply. "
            "If multiple matching targets remain, ask which one in a reply. "
            "If a search result says truncated, never claim the whole location was searched. "
            "For factual questions about external subjects, use web_answer if you cannot reliably answer. "
            "If the next action is in another category, select_category. "
            "Stop with a concise, truthful reply when the task is complete. No shell commands.\n"
            f"Current category: {category}\nCapabilities:\n{action_help(category)}\nOther categories:\n"
            + "\n".join(f"{name}: {description}" for name, description in category_criteria().items() if name != category)
        )
        compact_steps = []
        remaining = 24000
        for item in reversed(observations[-8:]):
            view = dict(item)
            result = str(view.get("result", ""))
            if result:
                view["result"] = result[:min(8000, remaining)]
                if len(result) > len(view["result"]):
                    view["result_truncated"] = True
                remaining -= len(view["result"])
            compact_steps.append(view)
        payload = {"request": request, "conversation": context.recent_history[-8:],
                   "observations": list(reversed(compact_steps))}
        response = context.ollama_chat([
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
        ])
        return _json_object(response["message"]["content"])

    def _prepare(self, action: str, args: dict) -> PendingAction:
        clean = dict(args)
        fingerprint = None
        requires_confirmation = False
        existing_path_actions = {
            "read_text_file", "delete_path", "rename_path", "copy_path", "create_zip", "extract_zip", "file_hash",
            "file_info", "list_directory", "append_text_file", "replace_text_file", "move_to_trash",
            "backup_file", "replace_text_in_file", "extract_pdf_text", "ocr_image",
        }
        if action in existing_path_actions:
            key = "source" if action in {"rename_path", "copy_path", "create_zip", "extract_zip"} else "path"
            source = _path(_required(args, key))
            if not source.exists():
                raise FileNotFoundError(str(source))
            clean[key] = str(source)
            fingerprint = _fingerprint(source)
            if action in {"read_text_file", "append_text_file", "replace_text_file", "replace_text_in_file",
                          "file_hash", "extract_zip", "backup_file", "extract_pdf_text", "ocr_image"} and not source.is_file():
                raise ValueError("Expected a file")
            if action == "extract_pdf_text" and source.suffix.lower() != ".pdf":
                raise ValueError("Expected a PDF file")
            if action == "extract_pdf_text":
                start_page = args.get("start_page", 1)
                max_pages = args.get("max_pages", 10)
                if (not isinstance(start_page, int) or isinstance(start_page, bool) or start_page < 1 or
                    not isinstance(max_pages, int) or isinstance(max_pages, bool) or not 1 <= max_pages <= 10):
                    raise ValueError("PDF page range is invalid")
                clean.update({"start_page": start_page, "max_pages": max_pages})
            if action == "ocr_image" and source.suffix.lower() not in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}:
                raise ValueError("Expected an image file")
            if action == "extract_zip" and not zipfile.is_zipfile(source):
                raise ValueError("Source is not a ZIP archive")
            if action == "list_directory" and not source.is_dir():
                raise ValueError("Expected a directory")
            if action in {"append_text_file", "replace_text_file"}:
                if source.stat().st_size > 5_000_000:
                    raise ValueError("Text edit supports files up to 5 MB")
                clean["text"] = _text(args, "text")
            if action == "replace_text_in_file":
                old_text = _text(args, "old")
                if not old_text:
                    raise ValueError("Source text cannot be empty")
                if source.stat().st_size > 5_000_000:
                    raise ValueError("Text edit supports files up to 5 MB")
                clean.update({"old": old_text, "new": _text(args, "new"), "all": args.get("all") is True})
                if clean["old"] not in source.read_text(encoding="utf-8"):
                    raise ValueError("Exact source text was not found in the file")
            if action in {"move_to_trash", "delete_path"} and (source == self.trash.root or self.trash.root.is_relative_to(source)):
                raise ValueError("Alfred's state directory cannot be removed")
            if action in {"rename_path", "copy_path", "create_zip", "extract_zip"}:
                destination = _path(_required(args, "destination"))
                if destination.exists() or not destination.parent.exists():
                    raise ValueError("Destination exists or its parent folder does not exist")
                if source.is_dir() and destination.is_relative_to(source):
                    raise ValueError("Cannot move a directory into itself")
                if action == "create_zip" and destination.suffix.lower() != ".zip":
                    raise ValueError("Destination must be a .zip file")
                clean["destination"] = str(destination)
        if action == "search_file_contents":
            root = _path(_required(args, "root"))
            if not root.is_dir():
                raise ValueError("Search root must be an existing directory")
            clean = {"root": str(root), "query": _required(args, "query"), "recursive": args.get("recursive") is True}
        if action == "search_documents":
            root = _path(_required(args, "root"))
            if not root.is_dir():
                raise ValueError("Search root must be an existing directory")
            clean = {"root": str(root), "query": _required(args, "query"),
                     "name_hint": str(args.get("name_hint") or "")}
        if action in {"create_text_file", "create_directory"}:
            target = _path(_required(args, "path"))
            if target.exists() or not target.parent.is_dir():
                raise ValueError("Target exists or its parent folder does not exist")
            clean = {"path": str(target)}
            if action == "create_text_file":
                clean["text"] = _text(args, "text")
        if action == "download_file":
            url = _required(args, "url")
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("A direct HTTP(S) URL is required")
            destination = _path(_required(args, "destination"))
            if destination.exists() or not destination.parent.exists():
                raise ValueError("Destination exists or its parent folder does not exist")
            checksum = str(args.get("sha256") or "").lower().strip()
            if checksum and (len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum)):
                raise ValueError("Expected SHA-256 must be 64 hex characters")
            clean = {"url": url, "destination": str(destination), "sha256": checksum}
        if action in {"read_web_page", "check_url_status"}:
            url = _required(args, "url")
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("An HTTP(S) URL is required")
            clean = {"url": url}
        if action == "set_volume":
            value = args.get("percent")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
                raise ValueError("Volume percent must be a number from 0 to 100")
            clean = {"percent": float(value)}
        if action == "write_clipboard":
            clean = {"text": _text(args, "text")}
        if action in {"inspect_screen", "inspect_camera"}:
            clean = {"question": _required(args, "question")}
        if action in {"browser_open_page"}:
            url = _required(args, "url")
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("An HTTP(S) URL is required")
            clean = {"url": url}
        if action == "browser_select_tab":
            tab_id = args.get("tab_id")
            if not isinstance(tab_id, int) or isinstance(tab_id, bool) or tab_id <= 0:
                raise ValueError("Use an exact tab ID")
            clean = {"tab_id": tab_id}
        if action == "browser_find_elements":
            clean = {"query": str(args.get("query") or "")}
        if action in {"browser_click_element", "browser_fill_element", "browser_download_element"}:
            element_id = _required(args, "element_id")
            element = self.browser.describe_element(element_id)
            clean = {"element_id": element_id, "element": element}
            fingerprint = (element["url"], element["id"], element["tag"], element.get("type", ""), element["label"], element["href"])
            if action == "browser_fill_element":
                clean["text"] = _text(args, "text")
            if action == "browser_download_element":
                destination = _path(_required(args, "destination"))
                if destination.exists() or not destination.parent.is_dir():
                    raise ValueError("Download destination exists or its parent is missing")
                clean["destination"] = str(destination)
        if action == "restore_trash":
            item_id = _required(args, "item_id")
            item = self.trash.get(item_id)
            original = Path(item["original"])
            if original.exists() or not original.parent.is_dir():
                raise ValueError("Original path is occupied or unavailable")
            clean = {"item_id": item_id, "original": str(original), "stored": item["stored"]}
            fingerprint = _fingerprint(Path(item["stored"]))
        if action == "empty_trash":
            trash_items = self.trash.list_items()
            clean = {"item_count": len(trash_items)}
            fingerprint = tuple(item["id"] for item in trash_items)
        if action == "undo_last_text_edit":
            item = self.undo.latest()
            if item is None:
                raise ValueError("No text edit to undo")
            clean = {"path": item["path"], "id": item["id"]}
            fingerprint = _fingerprint(Path(item["path"]))
        if action == "download_status":
            clean = {"job_id": str(args.get("job_id") or "")}
        if action == "select_audio_output":
            device_id = _required(args, "device_id")
            item = next((item for item in list_audio_outputs() if item["id"] == device_id), None)
            if item is None:
                raise ValueError("Audio output not found; list outputs first")
            clean = {"device_id": device_id, "device_name": item["name"]}
        if action == "network_diagnostics":
            host = _required(args, "host")
            port = args.get("port", 443)
            if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
                raise ValueError("Port must be an integer from 1 to 65535")
            clean = {"host": host, "port": port}
        if action == "create_reminder":
            due = parse_due_time(at=args.get("at"), seconds_from_now=args.get("seconds_from_now"))
            clean = {"text": _required(args, "text"), "due_at": due.isoformat()}
        if action == "cancel_reminder":
            item_id = _required(args, "id")
            item = next((item for item in self.reminders.list_items() if item["id"] == item_id), None)
            if item is None:
                raise ValueError("Reminder ID not found")
            clean = {"id": item_id, "text": item["text"], "due_at": item["due_at"]}
            fingerprint = (item_id, item["text"], item["due_at"])
        if action == "open_target":
            target = _required(args, "target")
            target_path = Path(os.path.expandvars(target)).expanduser()
            if target_path.is_absolute() and target_path.exists() and target_path.suffix.lower() in EXECUTABLE_EXTENSIONS:
                clean["target"] = str(target_path)
                fingerprint = _fingerprint(target_path)
                requires_confirmation = True
        if action in {"close_window", "window_control"}:
            pid = args.get("pid")
            name = _required(args, "name")
            if not isinstance(pid, int) or pid <= 0:
                raise ValueError("Exact process ID required")
            current = next((w for w in list_running_windows() if w["pid"] == pid and w["name"] == name), None)
            if current is None:
                raise ValueError("Selected window is no longer open")
            clean = {"pid": pid, "name": name, "title": current["title"], "path": current["path"]}
            fingerprint = (pid, name, current["title"], current["path"])
            if action == "window_control":
                operation = _required(args, "operation")
                if operation not in {"focus", "minimize", "maximize", "restore", "close"}:
                    raise ValueError("Unknown window operation")
                clean["operation"] = operation
                requires_confirmation = operation == "close"
        if action == "system_control" and args.get("operation") not in {
            "restart_assistant", "shutdown_assistant", "restart_computer", "shutdown_computer"
        }:
            raise ValueError("Unknown system operation")
        if action == "run_codex":
            clean = {"task": _required(args, "task")}
        description = {
            "delete_path": lambda: f"безвозвратно удалить {clean['path']}",
            "rename_path": lambda: f"переместить/переименовать {clean['source']} → {clean['destination']}",
            "copy_path": lambda: f"скопировать {clean['source']} → {clean['destination']}",
            "create_zip": lambda: f"упаковать {clean['source']} в архив {clean['destination']}",
            "extract_zip": lambda: f"извлечь архив {clean['source']} в новую папку {clean['destination']}",
            "append_text_file": lambda: f"добавить в {clean['path']} текст ({len(clean['text'])} символов): {clean['text'][:200]}",
            "replace_text_file": lambda: f"полностью заменить содержимое {clean['path']} текстом ({len(clean['text'])} символов): {clean['text'][:200]}",
            "write_clipboard": lambda: f"заменить буфер обмена текстом ({len(clean['text'])} символов): {clean['text'][:200]}",
            "download_file": lambda: f"скачать {clean['url']} в {clean['destination']}",
            "close_window": lambda: f"принудительно закрыть {clean['title']} (PID {clean['pid']}, {clean['name']}); несохранённые данные могут пропасть",
            "system_control": lambda: f"выполнить {clean['operation']}",
            "run_codex": lambda: f"передать Codex задачу на изменение проекта: {clean['task']}",
            "open_target": lambda: f"запустить исполняемый файл {clean['target']}",
            "move_to_trash": lambda: f"переместить {clean['path']} в корзину Альфреда",
            "restore_trash": lambda: f"восстановить {clean['original']} из корзины Альфреда (ID {clean['item_id']})",
            "empty_trash": lambda: f"безвозвратно удалить {clean['item_count']} элементов из корзины Альфреда",
            "replace_text_in_file": lambda: f"заменить в {clean['path']} текст {clean['old'][:120]!r} на {clean['new'][:120]!r}",
            "undo_last_text_edit": lambda: f"отменить последнее текстовое изменение файла {clean['path']}",
            "window_control": lambda: f"{clean['operation']} окно {clean['title']} (PID {clean['pid']})",
            "browser_click_element": lambda: f"нажать {clean['element']['tag']} {clean['element']['label']!r} на {clean['element']['url']}",
            "browser_fill_element": lambda: f"заполнить поле {clean['element']['label']!r} на {clean['element']['url']} "
                                             + (f"секретом длиной {len(clean['text'])} символов" if clean['element'].get('type') == 'password'
                                                else f"текстом {clean['text'][:200]!r}"),
            "browser_download_element": lambda: f"скачать через элемент {clean['element']['label']!r} на {clean['element']['url']} в {clean['destination']}",
            "select_audio_output": lambda: f"переключить вывод звука на {clean['device_name']} ({clean['device_id']})",
            "cancel_reminder": lambda: f"отменить напоминание {clean['text']!r} на {clean['due_at']}",
        }.get(action, lambda: action)()
        return PendingAction(action, clean, description, fingerprint, requires_confirmation=requires_confirmation)

    def _revalidate(self, pending: PendingAction):
        if pending.action in {"delete_path", "rename_path", "copy_path", "create_zip", "extract_zip", "append_text_file", "replace_text_file", "move_to_trash", "replace_text_in_file", "undo_last_text_edit"}:
            key = "source" if pending.action in {"rename_path", "copy_path", "create_zip", "extract_zip"} else "path"
            if _fingerprint(Path(pending.args[key])) != pending.fingerprint:
                raise ValueError("Target changed since confirmation was requested")
        if pending.action == "open_target" and pending.fingerprint is not None:
            if _fingerprint(Path(pending.args["target"])) != pending.fingerprint:
                raise ValueError("Executable changed since confirmation was requested")
        if pending.action in {"rename_path", "copy_path", "create_zip", "extract_zip", "download_file", "browser_download_element"} and Path(pending.args["destination"]).exists():
            raise ValueError("Destination now exists")
        if pending.action in {"close_window", "window_control"}:
            current = next((w for w in list_running_windows() if w["pid"] == pending.args["pid"]), None)
            if current is None or (current["pid"], current["name"], current["title"], current["path"]) != pending.fingerprint:
                raise ValueError("Selected window changed")
        if pending.action == "restore_trash":
            if Path(pending.args["original"]).exists() or _fingerprint(Path(pending.args["stored"])) != pending.fingerprint:
                raise ValueError("Trash item or original path changed")
        if pending.action == "empty_trash":
            if tuple(item["id"] for item in self.trash.list_items()) != pending.fingerprint:
                raise ValueError("Trash contents changed since confirmation was requested")
        if pending.action == "undo_last_text_edit":
            latest = self.undo.latest()
            if latest is None or latest["id"] != pending.args["id"]:
                raise ValueError("The latest text edit changed")
        if pending.action in {"browser_click_element", "browser_fill_element", "browser_download_element"}:
            element = self.browser.describe_element(pending.args["element_id"])
            current = (element["url"], element["id"], element["tag"], element.get("type", ""), element["label"], element["href"])
            if current != pending.fingerprint:
                raise ValueError("Browser page or element changed")
        if pending.action == "select_audio_output":
            if pending.args["device_id"] not in {item["id"] for item in list_audio_outputs()}:
                raise ValueError("Selected audio output disappeared")
        if pending.action == "cancel_reminder":
            item = next((item for item in self.reminders.list_items() if item["id"] == pending.args["id"]), None)
            if item is None or (item["id"], item["text"], item["due_at"]) != pending.fingerprint:
                raise ValueError("Selected reminder changed")

    def _execute(self, context, action: str, args: dict) -> str:
        if action == "move_to_trash":
            return json.dumps(self.trash.move(Path(args["path"])), ensure_ascii=False)
        if action == "list_trash":
            return json.dumps(self.trash.list_items()[:50], ensure_ascii=False)
        if action == "restore_trash":
            return f"Restored {self.trash.restore(args['item_id'])}"
        if action == "empty_trash":
            return f"Permanently deleted {self.trash.empty()} trash items"
        if action == "backup_file":
            source = Path(args["path"])
            suffix = datetime.now().strftime("%Y%m%d-%H%M%S")
            destination = source.with_name(source.name + f".alfred-backup-{suffix}")
            with source.open("rb") as original, destination.open("xb") as copy:
                shutil.copyfileobj(original, copy)
            shutil.copystat(source, destination)
            return f"Created backup {destination}"
        if action == "undo_last_text_edit":
            return f"Restored {self.undo.undo_latest()}"
        if action == "replace_text_in_file":
            path = Path(args["path"])
            original = path.read_text(encoding="utf-8")
            count = original.count(args["old"])
            if count == 0:
                raise ValueError("Source text no longer exists")
            edit_id = self.undo.begin(path)
            updated = original.replace(args["old"], args["new"], -1 if args["all"] else 1)
            path.write_text(updated, encoding="utf-8")
            self.undo.finish(edit_id, path)
            replaced = count if args["all"] else 1
            return f"Replaced {replaced} occurrence(s) in {path}; undo is available"
        if action == "extract_pdf_text":
            return json.dumps(extract_pdf_text(Path(args["path"]), max_pages=args["max_pages"],
                                               start_page=args["start_page"]), ensure_ascii=False)
        if action == "ocr_image":
            if self.ocr_engine is None:
                raise RuntimeError("OCR is unavailable; reinstall Alfred dependencies")
            return json.dumps(self.ocr_engine.read_image(Path(args["path"])), ensure_ascii=False)
        if action == "search_documents":
            root = Path(args["root"])
            needle = args["query"].casefold()
            name_hint = args["name_hint"].casefold()
            matches = []
            inspected = 0
            image_count = 0
            for path in root.rglob("*"):
                if not path.is_file() or path.is_symlink():
                    continue
                if name_hint and name_hint not in path.name.casefold():
                    continue
                if path.suffix.lower() not in {".txt", ".md", ".pdf", ".png", ".jpg", ".jpeg"}:
                    continue
                inspected += 1
                if inspected > 200:
                    break
                try:
                    if path.suffix.lower() == ".pdf":
                        pages = extract_pdf_text(path, max_pages=10)["pages"]
                        content = "\n".join(page["text"] for page in pages)
                    elif path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
                        image_count += 1
                        if self.ocr_engine is None or image_count > 10:
                            continue
                        content = self.ocr_engine.read_image(path)["text"]
                    else:
                        content = path.read_text(encoding="utf-8")[:100000]
                except Exception:
                    continue
                if needle in content.casefold():
                    matches.append(str(path))
            return json.dumps({"matches": matches[:30], "inspected_files": inspected,
                               "truncated": inspected > 200 or image_count > 10 or len(matches) > 30}, ensure_ascii=False)
        if action == "browser_open_page":
            return json.dumps(self.browser.open_page(args["url"]), ensure_ascii=False)
        if action == "browser_list_tabs":
            return json.dumps(self.browser.list_tabs(), ensure_ascii=False)
        if action == "browser_select_tab":
            return json.dumps(self.browser.select_tab(args["tab_id"]), ensure_ascii=False)
        if action == "browser_read_tab":
            return json.dumps(self.browser.read_page(), ensure_ascii=False)
        if action == "browser_find_elements":
            return json.dumps(self.browser.find_elements(args["query"]), ensure_ascii=False)
        if action == "browser_click_element":
            return json.dumps(self.browser.click(args["element_id"]), ensure_ascii=False)
        if action == "browser_fill_element":
            return json.dumps(self.browser.fill(args["element_id"], args["text"]), ensure_ascii=False)
        if action == "browser_download_element":
            return json.dumps(self.browser.download(args["element_id"], Path(args["destination"])), ensure_ascii=False)
        if action == "browser_back":
            return json.dumps(self.browser.back(), ensure_ascii=False)
        if action == "window_control":
            return control_window(args["pid"], args["operation"])
        if action == "download_status":
            return json.dumps(self.downloads.status(args["job_id"] or None), ensure_ascii=False)
        if action == "list_audio_outputs":
            return json.dumps(list_audio_outputs(), ensure_ascii=False)
        if action == "select_audio_output":
            return f"Selected audio output: {select_audio_output(args['device_id'])}"
        if action == "network_diagnostics":
            return json.dumps(network_diagnostics(args["host"], args["port"]), ensure_ascii=False)
        if action == "local_network_info":
            return json.dumps(local_network_info(), ensure_ascii=False)
        if action == "create_reminder":
            due = datetime.fromisoformat(args["due_at"])
            return json.dumps(self.reminders.add(args["text"], due), ensure_ascii=False)
        if action == "list_reminders":
            return json.dumps(self.reminders.list_items(), ensure_ascii=False)
        if action == "cancel_reminder":
            return "Cancelled reminder" if self.reminders.cancel(args["id"]) else "Reminder was not found"
        if action == "list_directory":
            root = Path(args["path"])
            entries = []
            for item in sorted(root.iterdir(), key=lambda path: path.name.casefold())[:100]:
                entries.append({"name": item.name, "path": str(item), "type": "directory" if item.is_dir() else "file"})
            return json.dumps({"directory": str(root), "entries": entries}, ensure_ascii=False)
        if action == "file_info":
            path = Path(args["path"])
            info = path.stat()
            return json.dumps({
                "path": str(path), "type": "directory" if path.is_dir() else "file",
                "size_bytes": info.st_size,
                "modified_utc": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
            }, ensure_ascii=False)
        if action == "file_hash":
            digest = hashlib.sha256()
            path = Path(args["path"])
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            return json.dumps({"path": str(path), "sha256": digest.hexdigest()})
        if action == "search_file_contents":
            root = Path(args["root"])
            matches = []
            files = root.rglob("*") if args["recursive"] else root.iterdir()
            inspected = 0
            for path in files:
                if not path.is_file() or path.is_symlink() or path.stat().st_size > 1_000_000:
                    continue
                inspected += 1
                if inspected > 500:
                    break
                try:
                    with path.open("r", encoding="utf-8") as source:
                        for number, line in enumerate(source, 1):
                            if args["query"].casefold() in line.casefold():
                                matches.append({"path": str(path), "line": number, "preview": line.strip()[:200]})
                                if len(matches) >= 50:
                                    break
                except (OSError, UnicodeError):
                    continue
                if len(matches) >= 50:
                    break
            return json.dumps({"matches": matches, "inspected_files": inspected, "truncated": inspected > 500 or len(matches) >= 50}, ensure_ascii=False)
        if action == "search_files":
            root_text = _required(args, "root")
            if root_text in {"desktop", "downloads", "documents", "music", "pictures", "videos", "project", "home"}:
                profile = Path.home()
                root = Path(self.base_dir) if root_text == "project" else profile if root_text == "home" else profile / root_text.capitalize()
            else:
                root = _path(root_text)
            if not root.is_dir():
                raise ValueError(f"Directory not found: {root}")
            query = str(args.get("query") or "").casefold()
            recursive = args.get("recursive") is True
            matches = []
            for item in (root.rglob("*") if recursive else root.iterdir()):
                if query in item.name.casefold():
                    matches.append(str(item))
                    if len(matches) >= 30:
                        break
            return json.dumps({"root": str(root), "matches": matches}, ensure_ascii=False)
        if action == "read_text_file":
            path = Path(args["path"])
            if path.stat().st_size > 100_000:
                raise ValueError("File is too large to read in chat")
            return path.read_text(encoding="utf-8")[:12000]
        if action == "create_text_file":
            path = Path(args["path"])
            with path.open("x", encoding="utf-8") as output:
                output.write(args["text"])
            return f"Created {path}"
        if action == "append_text_file":
            path = Path(args["path"])
            edit_id = self.undo.begin(path)
            with path.open("a", encoding="utf-8") as output:
                output.write(args["text"])
            self.undo.finish(edit_id, path)
            return f"Appended {len(args['text'])} characters to {path}"
        if action == "replace_text_file":
            path = Path(args["path"])
            edit_id = self.undo.begin(path)
            path.write_text(args["text"], encoding="utf-8")
            self.undo.finish(edit_id, path)
            return f"Replaced contents of {path}"
        if action == "create_directory":
            path = Path(args["path"])
            path.mkdir()
            return f"Created directory {path}"
        if action == "copy_path":
            source, destination = Path(args["source"]), Path(args["destination"])
            if source.is_dir() and not source.is_symlink():
                shutil.copytree(source, destination)
            else:
                with source.open("rb") as original, destination.open("xb") as copy:
                    shutil.copyfileobj(original, copy)
            return f"Copied {source} to {destination}"
        if action == "create_zip":
            source, destination = Path(args["source"]), Path(args["destination"])
            with zipfile.ZipFile(destination, mode="x", compression=zipfile.ZIP_DEFLATED) as archive:
                if source.is_file():
                    archive.write(source, arcname=source.name)
                else:
                    for item in source.rglob("*"):
                        if item.is_file() and not item.is_symlink():
                            archive.write(item, arcname=str(Path(source.name) / item.relative_to(source)))
            return f"Created archive {destination}"
        if action == "extract_zip":
            source, destination = Path(args["source"]), Path(args["destination"])
            with zipfile.ZipFile(source) as archive:
                members = archive.infolist()
                if len(members) > 10000 or sum(item.file_size for item in members) > 2_000_000_000:
                    raise ValueError("Archive is too large to extract safely")
                safe_members = []
                for member in members:
                    name = member.filename.replace("\\", "/")
                    relative = Path(name)
                    if relative.is_absolute() or any(part in {"..", ""} for part in relative.parts):
                        raise ValueError("Archive contains an unsafe path")
                    if (member.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ValueError("Archive contains a symbolic link")
                    target = destination / relative
                    if not target.resolve(strict=False).is_relative_to(destination.resolve(strict=False)):
                        raise ValueError("Archive contains a path outside the destination")
                    safe_members.append((member, target))
                destination.mkdir()
                try:
                    for member, target in safe_members:
                        if member.is_dir():
                            target.mkdir(parents=True, exist_ok=True)
                        else:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            with archive.open(member) as original, target.open("xb") as output:
                                shutil.copyfileobj(original, output)
                except Exception:
                    shutil.rmtree(destination)
                    raise
            return f"Extracted archive to {destination}"
        if action == "open_target":
            target = _required(args, "target")
            resolved = self.registry.get_tool("system")._resolve_target(target)
            parsed = urlparse(resolved)
            if parsed.scheme in {"http", "https"}:
                webbrowser.open(resolved)
            elif resolved.startswith("steam://") or Path(resolved).exists():
                os.startfile(resolved)
            elif parsed.scheme:
                raise ValueError("Unsupported target protocol")
            else:
                executable = shutil.which(resolved)
                if not executable:
                    raise ValueError(f"Application not found: {target}")
                subprocess.Popen([executable], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return f"Opened {resolved}"
        if action == "web_answer":
            answer = self.registry.get_tool("web").search_and_answer(context, _required(args, "question"))
            return answer or "No verified answer found"
        if action == "search_web_links":
            results = self.registry.get_tool("web")._search_web(context, _required(args, "query"))
            return json.dumps(results[:8], ensure_ascii=False)
        if action == "read_web_page":
            url = args["url"]
            html = context.fetch_url(url, timeout=10, attempts=1)
            return json.dumps({"url": url, "text": strip_html(html[:500_000], context.normalize_memory_text)[:12000]}, ensure_ascii=False)
        if action == "check_url_status":
            request = urllib.request.Request(args["url"], method="HEAD", headers={"User-Agent": "Alfred/1.0"})
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    return json.dumps({"url": response.url, "status": response.status, "content_type": response.headers.get_content_type()})
            except urllib.error.HTTPError as exc:
                return json.dumps({"url": args["url"], "status": exc.code, "content_type": exc.headers.get_content_type()})
        if action == "open_browser":
            query = _required(args, "query")
            url = f"https://www.google.com/search?q={quote_plus(query)}"
            webbrowser.open(url)
            return f"Opened {url}"
        if action == "play_music":
            tool = self.registry.get_tool("music")
            return f"Opened {tool.open_music(context, _required(args, 'query'))}"
        if action == "music_control":
            operation = _required(args, "operation")
            if operation not in {"next", "previous", "pause", "resume", "mute", "unmute", "volume_up", "volume_down", "volume_status"}:
                raise ValueError("Unknown music operation")
            tool = self.registry.get_tool("music")
            if operation == "volume_status":
                return tool.build_volume_status_reply(context.language)
            tool.perform_control_action(operation)
            return f"Completed {operation}"
        if action == "set_volume":
            self.registry.get_tool("music").set_system_volume_percent(args["percent"])
            return f"Set volume to {args['percent']:g}%"
        if action == "system_info":
            usage = shutil.disk_usage(self.base_dir)
            return json.dumps({
                "operating_system": platform.platform(),
                "computer": platform.node(),
                "disk_total_bytes": usage.total,
                "disk_free_bytes": usage.free,
            }, ensure_ascii=False)
        if action == "current_time":
            return datetime.now().astimezone().isoformat()
        if action == "read_clipboard":
            command = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; Get-Clipboard -Raw"
            result = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True,
                                    text=True, encoding="utf-8", errors="replace", timeout=10,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or "Clipboard read failed")
            return result.stdout[:12000]
        if action == "write_clipboard":
            command = "Set-Clipboard -Value ([Console]::In.ReadToEnd())"
            result = subprocess.run(["powershell", "-NoProfile", "-Command", command], input=args["text"],
                                    capture_output=True, text=True, encoding="utf-8", timeout=10,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or "Clipboard write failed")
            return f"Copied {len(args['text'])} characters to clipboard"
        if action == "inspect_screen":
            if context.analyze_screen is None:
                raise RuntimeError("Screen inspection is unavailable")
            result = context.analyze_screen(args["question"])
            if context.restart_listener is not None:
                context.restart_listener()
            return result
        if action == "inspect_camera":
            if context.analyze_vision is None:
                raise RuntimeError("Camera inspection is unavailable")
            return context.analyze_vision(args["question"])
        if action == "list_windows":
            return json.dumps(list_running_windows()[:60], ensure_ascii=False)
        if action == "list_processes":
            script = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Process | Select-Object -First 150 Id,ProcessName,CPU | ConvertTo-Json -Compress"
            result = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True,
                                    text=True, encoding="utf-8", errors="replace", timeout=15,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or "Could not list processes")
            return result.stdout[:16000]
        if action == "list_installed_apps":
            apps = self.registry.get_tool("system")._get_app_candidates()
            return json.dumps([{"name": item["name"], "path": item["path"]} for item in apps[:150]], ensure_ascii=False)
        if action == "close_window":
            result = self.registry.get_tool("system")._close_target(args["title"], args["pid"], args["name"])
            if not result:
                raise RuntimeError("Could not close selected window")
            return f"Closed {args['title']}"
        if action == "rename_path":
            Path(args["source"]).rename(args["destination"])
            return f"Renamed to {args['destination']}"
        if action == "delete_path":
            path = Path(args["path"])
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
            return f"Deleted {path}"
        if action == "download_file":
            destination = Path(args["destination"])
            return json.dumps(self.downloads.start(args["url"], destination, args.get("sha256", "")), ensure_ascii=False)
        if action == "system_control":
            tool = self.registry.get_tool("system")
            operation = args["operation"]
            if operation in {"restart_assistant", "shutdown_assistant"}:
                tool._request_assistant_control(context, operation)
            elif operation == "restart_computer":
                tool._schedule_computer_restart()
            else:
                tool._schedule_computer_shutdown()
            return f"Started {operation}"
        if action == "run_codex":
            tool = self.registry.get_tool("codex")
            routed = replace(context, routed_intent="codex_request", routed_tool_name="codex", routed_query=args["task"])
            result = tool.handle(routed, args["task"])
            return result.reply_text or "Codex task started"
        raise ValueError(f"Unsupported action: {action}")
