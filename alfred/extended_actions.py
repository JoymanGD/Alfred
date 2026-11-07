"""Reusable desktop capabilities kept separate from language interpretation."""

import ctypes
import hashlib
import json
import os
import shutil
import socket
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


class AlfredTrash:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def move(self, source: Path) -> dict:
        with self._lock:
            item_id = uuid.uuid4().hex
            folder = self.root / item_id
            folder.mkdir()
            stored = folder / source.name
            metadata = {"id": item_id, "original": str(source), "stored": str(stored),
                        "deleted_utc": datetime.now(timezone.utc).isoformat()}
            try:
                shutil.move(str(source), str(stored))
                (folder / "item.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
            except Exception:
                if stored.exists() and not source.exists():
                    shutil.move(str(stored), str(source))
                shutil.rmtree(folder, ignore_errors=True)
                raise
            return metadata

    def list_items(self) -> list[dict]:
        items = []
        for record in self.root.glob("*/item.json"):
            try:
                data = json.loads(record.read_text(encoding="utf-8"))
                if Path(data["stored"]).exists():
                    items.append(data)
            except (OSError, ValueError, KeyError):
                continue
        return sorted(items, key=lambda item: item["deleted_utc"], reverse=True)

    def get(self, item_id: str) -> dict:
        if len(item_id) != 32 or any(c not in "0123456789abcdef" for c in item_id):
            raise ValueError("Invalid trash item ID")
        record = self.root / item_id / "item.json"
        data = json.loads(record.read_text(encoding="utf-8"))
        if data.get("id") != item_id or Path(data["stored"]).parent != record.parent:
            raise ValueError("Trash record is invalid")
        return data

    def restore(self, item_id: str) -> str:
        with self._lock:
            data = self.get(item_id)
            original = Path(data["original"])
            stored = Path(data["stored"])
            if original.exists() or not original.parent.exists() or not stored.exists():
                raise ValueError("Original path is occupied or unavailable")
            shutil.move(str(stored), str(original))
            shutil.rmtree(self.root / item_id)
            return str(original)

    def empty(self) -> int:
        with self._lock:
            items = self.list_items()
            for item in items:
                shutil.rmtree(self.root / item["id"])
            return len(items)


class TextUndoStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def begin(self, path: Path) -> str:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 5_000_000:
            raise ValueError("Text undo supports regular files up to 5 MB")
        item_id = uuid.uuid4().hex
        folder = self.root / item_id
        folder.mkdir()
        shutil.copy2(path, folder / "before")
        (folder / "edit.json").write_text(json.dumps({"id": item_id, "path": str(path),
                                                        "created_utc": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
        return item_id

    def finish(self, item_id: str, path: Path):
        record = self.root / item_id / "edit.json"
        data = json.loads(record.read_text(encoding="utf-8"))
        data["after_sha256"] = self._digest(path)
        record.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def latest(self) -> dict | None:
        records = sorted(self.root.glob("*/edit.json"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
        for record in records:
            try:
                data = json.loads(record.read_text(encoding="utf-8"))
                if data.get("after_sha256") and (record.parent / "before").exists():
                    return data
            except (OSError, ValueError):
                continue
        return None

    def undo_latest(self) -> str:
        data = self.latest()
        if data is None:
            raise ValueError("No text edit to undo")
        target = Path(data["path"])
        if not target.is_file() or self._digest(target) != data["after_sha256"]:
            raise ValueError("Edited file changed since Alfred modified it")
        folder = self.root / data["id"]
        shutil.copy2(folder / "before", target)
        shutil.rmtree(folder)
        return str(target)


def extract_pdf_text(path: Path, max_pages: int = 10, max_chars: int = 12000, start_page: int = 1) -> dict:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = []
    remaining = max_chars
    for index in range(start_page - 1, min(len(reader.pages), start_page - 1 + max_pages)):
        page = reader.pages[index]
        content = page.extract_text() or ""
        pages.append({"page": index + 1, "text": content[:min(3000, remaining)]})
        remaining -= min(len(content), 3000, remaining)
        if remaining <= 0:
            break
    next_page = pages[-1]["page"] + 1 if pages and pages[-1]["page"] < len(reader.pages) else None
    return {"path": str(path), "page_count": len(reader.pages), "pages": pages,
            "next_page": next_page, "truncated": next_page is not None or remaining <= 0}


class OcrEngine:
    def __init__(self):
        self._engine = None

    def warmup(self):
        from rapidocr import RapidOCR

        self._engine = RapidOCR()

    def read_image(self, path: Path) -> dict:
        if self._engine is None:
            raise RuntimeError("OCR is not loaded")
        result = self._engine(str(path))
        texts = getattr(result, "txts", None) or []
        return {"path": str(path), "text": "\n".join(str(value) for value in texts)[:15000],
                "lines": len(texts)}


def _window_handle(pid: int) -> int:
    user32 = ctypes.windll.user32
    matches = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def callback(hwnd, _):
        process_id = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if process_id.value == pid and user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd):
            matches.append(hwnd)
        return True

    user32.EnumWindows(callback_type(callback), 0)
    if not matches:
        raise ValueError("Selected process has no visible window")
    return matches[0]


def control_window(pid: int, operation: str) -> str:
    user32 = ctypes.windll.user32
    hwnd = _window_handle(pid)
    if operation == "focus":
        user32.ShowWindowAsync(hwnd, 9)
        if not user32.SetForegroundWindow(hwnd):
            raise RuntimeError("Windows refused to focus the window")
    elif operation == "minimize":
        user32.ShowWindowAsync(hwnd, 6)
    elif operation == "maximize":
        user32.ShowWindowAsync(hwnd, 3)
    elif operation == "restore":
        user32.ShowWindowAsync(hwnd, 9)
    elif operation == "close":
        user32.PostMessageW(hwnd, 0x0010, 0, 0)
    else:
        raise ValueError("Unknown window operation")
    return f"Requested {operation} for window of PID {pid}"


def network_diagnostics(host: str, port: int = 443) -> dict:
    if not host or "/" in host or "\\" in host:
        raise ValueError("Host must be a domain or IP address")
    addresses = [item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]
    unique = list(dict.fromkeys(addresses))
    try:
        with socket.create_connection((host, port), timeout=4):
            reachable = True
    except OSError:
        reachable = False
    return {"host": host, "port": port, "addresses": unique[:8], "tcp_reachable": reachable}


def local_network_info() -> list[dict]:
    import psutil

    result = []
    for name, addresses in psutil.net_if_addrs().items():
        ips = [address.address for address in addresses if address.family in {socket.AF_INET, socket.AF_INET6}]
        if ips:
            result.append({"interface": name, "addresses": ips})
    return result


def list_audio_outputs() -> list[dict]:
    import comtypes
    from pycaw.constants import DEVICE_STATE, EDataFlow
    from pycaw.pycaw import AudioUtilities

    comtypes.CoInitialize()
    try:
        current = AudioUtilities.GetSpeakers().id
        devices = AudioUtilities.GetAllDevices(EDataFlow.eRender.value, DEVICE_STATE.ACTIVE.value)
        return [{"id": device.id, "name": device.FriendlyName, "default": device.id == current}
                for device in devices]
    finally:
        comtypes.CoUninitialize()


def select_audio_output(device_id: str) -> str:
    import comtypes
    from pycaw.constants import DEVICE_STATE, EDataFlow, ERole
    from pycaw.pycaw import AudioUtilities

    comtypes.CoInitialize()
    try:
        devices = AudioUtilities.GetAllDevices(EDataFlow.eRender.value, DEVICE_STATE.ACTIVE.value)
        target = next((device for device in devices if device.id == device_id), None)
        if target is None:
            raise ValueError("Audio output is no longer available")
        AudioUtilities.SetDefaultDevice(device_id, roles=[ERole.eConsole, ERole.eMultimedia])
        return target.FriendlyName
    finally:
        comtypes.CoUninitialize()
