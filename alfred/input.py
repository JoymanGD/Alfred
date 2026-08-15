import json
import queue
import socket
import threading
from abc import ABC, abstractmethod


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 45771


class InputSink(ABC):
    @abstractmethod
    def submit_text(self, text: str):
        raise NotImplementedError

    @abstractmethod
    def trigger_wake(self):
        raise NotImplementedError

    @abstractmethod
    def interrupt_tts(self):
        raise NotImplementedError


class QueueInputSink(InputSink):
    def __init__(self, event_in, tts_interrupt_event):
        self.event_in = event_in
        self.tts_interrupt_event = tts_interrupt_event

    def submit_text(self, text: str):
        self.event_in.put(text)

    def trigger_wake(self):
        self.event_in.put("")

    def interrupt_tts(self):
        self.tts_interrupt_event.set()


class LocalInputServer:
    def __init__(
        self,
        event_in,
        tts_interrupt_event,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        tts_warmup_callback=None,
    ):
        self._host = host
        self._port = port
        self._sink = QueueInputSink(event_in, tts_interrupt_event)
        self._tts_warmup_callback = tts_warmup_callback
        self._server_socket: socket.socket | None = None
        self._chat_clients: set[socket.socket] = set()
        self._voice_clients: set[socket.socket] = set()
        self._client_lock = threading.Lock()

    @property
    def address(self) -> tuple[str, int]:
        return self._host, self._port

    def start(self):
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind((self._host, self._port))
        server_socket.listen()
        self._server_socket = server_socket
        threading.Thread(target=self._accept_loop, daemon=True).start()
        print(f"[INPUT] Listening for remote chat and voice clients on {self._host}:{self._port}")

    def notify_tts_started(self):
        self._broadcast({"type": "tts_started"})

    def notify_tts_finished(self):
        self._broadcast({"type": "tts_finished"})

    def notify_assistant_replied(self):
        self._broadcast({"type": "assistant_replied"})

    def notify_assistant_message(self, text: str):
        self._broadcast({"type": "assistant_message", "text": text})

    def has_chat_clients(self) -> bool:
        with self._client_lock:
            return bool(self._chat_clients)

    def _accept_loop(self):
        assert self._server_socket is not None
        while True:
            client_socket, address = self._server_socket.accept()
            print(f"[INPUT] Client connected from {address[0]}:{address[1]}")
            threading.Thread(
                target=self._client_loop,
                args=(client_socket, address),
                daemon=True,
            ).start()

    def _client_loop(self, client_socket: socket.socket, address: tuple[str, int]):
        client_file = client_socket.makefile("r", encoding="utf-8")
        registered_chat = False
        registered_voice = False
        try:
            for line in client_file:
                raw_line = line.strip()
                if not raw_line:
                    continue
                try:
                    payload = json.loads(raw_line)
                except json.JSONDecodeError:
                    print(f"[INPUT] Invalid payload from {address[0]}:{address[1]}")
                    continue

                message_type = payload.get("type")
                if message_type == "register":
                    role = str(payload.get("role", "")).strip().lower()
                    if role == "chat" and not registered_chat:
                        with self._client_lock:
                            self._chat_clients.add(client_socket)
                        registered_chat = True
                    if role == "voice" and not registered_voice:
                        with self._client_lock:
                            self._voice_clients.add(client_socket)
                        registered_voice = True
                    self._send(client_socket, {"type": "registered", "role": role or "unknown"})
                    continue

                if message_type == "input":
                    text = str(payload.get("text", "")).strip()
                    if text:
                        self._sink.submit_text(text)
                    continue

                if message_type == "wake":
                    self._sink.trigger_wake()
                    continue

                if message_type == "interrupt_tts":
                    self._sink.interrupt_tts()
                    continue

                if message_type == "warmup_tts":
                    if self._tts_warmup_callback is not None:
                        threading.Thread(target=self._tts_warmup_callback, daemon=True).start()
                    continue
        except OSError as e:
            print(f"[INPUT] Client connection error from {address[0]}:{address[1]}: {e}")
        finally:
            if registered_chat:
                with self._client_lock:
                    self._chat_clients.discard(client_socket)
            if registered_voice:
                with self._client_lock:
                    self._voice_clients.discard(client_socket)
            try:
                client_file.close()
            except OSError:
                pass
            try:
                client_socket.close()
            except OSError:
                pass
            print(f"[INPUT] Client disconnected from {address[0]}:{address[1]}")

    def _broadcast(self, payload: dict):
        with self._client_lock:
            clients = list(self._chat_clients | self._voice_clients)

        for client_socket in clients:
            try:
                self._send(client_socket, payload)
            except OSError:
                with self._client_lock:
                    self._chat_clients.discard(client_socket)
                    self._voice_clients.discard(client_socket)
                try:
                    client_socket.close()
                except OSError:
                    pass

    @staticmethod
    def _send(client_socket: socket.socket, payload: dict):
        message = json.dumps(payload, ensure_ascii=True) + "\n"
        client_socket.sendall(message.encode("utf-8"))


class RemoteInputClient(InputSink):
    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        role: str = "chat",
        tts_playing_event=None,
        followup_signal_queue: queue.Queue | None = None,
    ):
        self._host = host
        self._port = port
        self._role = role
        self._tts_playing_event = tts_playing_event
        self._followup_signal_queue = followup_signal_queue
        self._socket: socket.socket | None = None
        self._socket_file = None
        self._socket_lock = threading.Lock()
        self._reconnect_lock = threading.Lock()
        self._connection_ready = threading.Event()
        self._reconnect_notice_shown = False
        self._connect(initial=True)
        threading.Thread(target=self._listen_loop, daemon=True).start()

    def submit_text(self, text: str):
        self._send({"type": "input", "text": text})

    def trigger_wake(self):
        self._send({"type": "wake"})

    def interrupt_tts(self):
        self._send({"type": "interrupt_tts"})

    def warmup_tts(self):
        self._send({"type": "warmup_tts"})

    def _listen_loop(self):
        while True:
            self._connection_ready.wait()
            socket_file = self._socket_file
            if socket_file is None:
                continue
            try:
                for line in socket_file:
                    raw_line = line.strip()
                    if not raw_line:
                        continue
                    try:
                        payload = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue
                    self._handle_server_message(payload)
                raise OSError("connection closed")
            except OSError as e:
                self._handle_disconnect(e)

    def _handle_server_message(self, payload: dict):
        message_type = payload.get("type")
        if message_type == "registered":
            if self._role != "chat":
                print(f"[INPUT] Connected to Alfred core as {payload.get('role', self._role)}.")
            return
        if message_type == "assistant_message" and self._role == "chat":
            text = str(payload.get("text", "")).strip()
            if text:
                print(f"Alfred> {text}\n")
            return
        if message_type == "tts_started" and self._tts_playing_event is not None:
            self._tts_playing_event.set()
            return
        if message_type == "tts_finished" and self._tts_playing_event is not None:
            self._tts_playing_event.clear()
            return
        if message_type == "assistant_replied" and self._followup_signal_queue is not None:
            self._followup_signal_queue.put("assistant_replied")

    def _send(self, payload: dict):
        message = json.dumps(payload, ensure_ascii=True) + "\n"
        while True:
            self._connection_ready.wait()
            send_error = None
            with self._socket_lock:
                sock = self._socket
                if sock is None:
                    continue
                try:
                    sock.sendall(message.encode("utf-8"))
                    return
                except OSError as e:
                    send_error = e
            if send_error is not None:
                self._handle_disconnect(send_error)

    def _connect(self, initial: bool = False):
        while True:
            try:
                sock = socket.create_connection((self._host, self._port), timeout=3.0)
                sock.settimeout(None)
                socket_file = sock.makefile("r", encoding="utf-8")
                with self._socket_lock:
                    self._socket = sock
                    self._socket_file = socket_file
                self._connection_ready.set()
                self._reconnect_notice_shown = False
                self._send_raw({"type": "register", "role": self._role})
                if self._role in {"chat", "voice"}:
                    self._send_raw({"type": "warmup_tts"})
                return
            except OSError as e:
                if initial:
                    print(f"[INPUT] Failed to connect to Alfred core: {e}")
                    raise
                if not self._reconnect_notice_shown:
                    print("[INPUT] Waiting for Alfred core to come back...")
                    self._reconnect_notice_shown = True
                self._close_connection()
                self._connection_ready.clear()
                threading.Event().wait(1.0)

    def _handle_disconnect(self, error: OSError):
        with self._reconnect_lock:
            if self._socket is None and not self._connection_ready.is_set():
                return
            self._close_connection()
            self._connection_ready.clear()
            print(f"[INPUT] Lost connection to Alfred core: {error}")
            self._connect(initial=False)

    def _close_connection(self):
        with self._socket_lock:
            socket_file = self._socket_file
            sock = self._socket
            self._socket_file = None
            self._socket = None
        if socket_file is not None:
            try:
                socket_file.close()
            except OSError:
                pass
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _send_raw(self, payload: dict):
        message = json.dumps(payload, ensure_ascii=True) + "\n"
        with self._socket_lock:
            if self._socket is None:
                raise OSError("not connected")
            self._socket.sendall(message.encode("utf-8"))
