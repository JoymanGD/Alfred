"""Persistent local reminders delivered through Alfred's normal output queue."""

import json
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path


def parse_due_time(*, at: str | None = None, seconds_from_now: float | None = None) -> datetime:
    if at:
        due = datetime.fromisoformat(at.replace("Z", "+00:00"))
        if due.tzinfo is None:
            due = due.astimezone()
    elif seconds_from_now is not None:
        if isinstance(seconds_from_now, bool) or not isinstance(seconds_from_now, (int, float)):
            raise ValueError("seconds_from_now must be a number")
        due = datetime.now().astimezone() + timedelta(seconds=seconds_from_now)
    else:
        raise ValueError("Specify at or seconds_from_now")
    if due <= datetime.now().astimezone():
        raise ValueError("Reminder time must be in the future")
    return due


class ReminderService:
    def __init__(self, path: Path, output_queue):
        self.path = path
        self.output_queue = output_queue
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._items = self._load()
        threading.Thread(target=self._run, daemon=True, name="alfred-reminders").start()

    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self._items, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def add(self, text: str, due: datetime) -> dict:
        item = {"id": uuid.uuid4().hex[:12], "text": text, "due_at": due.isoformat()}
        with self._lock:
            self._items.append(item)
            self._save()
        self._wake.set()
        return item

    def list_items(self) -> list[dict]:
        with self._lock:
            return sorted((dict(item) for item in self._items), key=lambda item: item["due_at"])

    def cancel(self, item_id: str) -> bool:
        with self._lock:
            before = len(self._items)
            self._items = [item for item in self._items if item["id"] != item_id]
            changed = len(self._items) != before
            if changed:
                self._save()
        self._wake.set()
        return changed

    def _run(self):
        while True:
            due = []
            now = datetime.now().astimezone()
            with self._lock:
                remaining = []
                for item in self._items:
                    try:
                        if datetime.fromisoformat(item["due_at"]) <= now:
                            due.append(item)
                        else:
                            remaining.append(item)
                    except (KeyError, ValueError):
                        continue
                if due or len(remaining) != len(self._items):
                    self._items = remaining
                    self._save()
            for item in due:
                if self.output_queue is not None:
                    self.output_queue.put(f"Напоминание: {item['text']}")
            self._wake.wait(2)
            self._wake.clear()
