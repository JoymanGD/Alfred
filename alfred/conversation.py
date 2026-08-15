import threading


class ConversationMemory:
    def __init__(self, max_messages: int, normalize_text):
        self.max_messages = max_messages
        self.normalize_text = normalize_text
        self._history = []
        self._lock = threading.Lock()

    def append(self, role: str, content: str):
        content = self.normalize_text(content)
        if not content:
            return

        with self._lock:
            self._history.append({"role": role, "content": content})
            if len(self._history) > self.max_messages:
                del self._history[:-self.max_messages]

    def build_messages(self, system_prompt: str, user_text: str):
        with self._lock:
            history = list(self._history)

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_text})
        return messages

    def recent_history(self, limit: int = 4):
        with self._lock:
            history = list(self._history)
        if limit <= 0:
            return []
        return history[-limit:]
