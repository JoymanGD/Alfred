"""Client for the locally hosted Vane answer engine."""

import json
import re
import time
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class VaneAnswer:
    text: str
    sources: tuple[str, ...]
    excerpts: tuple[str, ...]


class VaneSearchClient:
    def __init__(
        self, base_url: str, chat_model: str, embedding_model: str = "",
        timeout: float = 30.0,
    ):
        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Vane URL must point to a local HTTP service")
        self.base_url = base_url.rstrip("/")
        self.chat_model = chat_model
        self.embedding_model = embedding_model
        self.timeout = timeout
        self._models: tuple[dict, dict] | None = None
        self._retry_after = 0.0

    def _request(self, path: str, payload: dict | None = None, timeout: float | None = None) -> dict:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        request = Request(
            self.base_url + path, data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST" if body is not None else "GET",
        )
        with urlopen(request, timeout=timeout or self.timeout) as response:
            result = json.load(response)
        if not isinstance(result, dict):
            raise ValueError("Vane returned a non-object response")
        return result

    def _select_models(self) -> tuple[dict, dict]:
        if self._models is not None:
            return self._models
        providers = self._request("/api/providers", timeout=2).get("providers")
        if not isinstance(providers, list):
            raise ValueError("Vane has no configured providers")
        chat = None
        embedding = None
        for provider in providers:
            if not isinstance(provider, dict) or not provider.get("id"):
                continue
            for model in provider.get("chatModels") or []:
                if isinstance(model, dict) and model.get("key") == self.chat_model:
                    chat = {"providerId": provider["id"], "key": model["key"]}
            for model in provider.get("embeddingModels") or []:
                if not isinstance(model, dict) or not model.get("key"):
                    continue
                if self.embedding_model and model["key"] != self.embedding_model:
                    continue
                if embedding is None or model["key"] == self.embedding_model:
                    embedding = {"providerId": provider["id"], "key": model["key"]}
        if chat is None or embedding is None:
            raise ValueError(
                f"Configure Vane's Ollama provider with chat model {self.chat_model!r} "
                "and a local embedding model"
            )
        self._models = chat, embedding
        return self._models

    def search(self, question: str, user_history: list[str], language: str) -> VaneAnswer | None:
        if time.monotonic() < self._retry_after:
            return None
        try:
            chat_model, embedding_model = self._select_models()
            response = self._request("/api/search", {
                "chatModel": chat_model,
                "embeddingModel": embedding_model,
                "optimizationMode": "speed",
                "sources": ["web"],
                "query": question,
                "history": [["human", item] for item in user_history[-5:]],
                "systemInstructions": (
                    "Answer the user's exact question in Russian, concisely and naturally. "
                    "Use only facts supported by the retrieved sources. Say when evidence is insufficient."
                    if language == "ru" else
                    "Answer the user's exact question concisely. Use only facts supported by "
                    "retrieved sources. Say when evidence is insufficient."
                ),
                "stream": False,
            })
            sources = []
            excerpts = []
            for source in response.get("sources") or []:
                if not isinstance(source, dict):
                    continue
                metadata = source.get("metadata") or {}
                url = metadata.get("url") if isinstance(metadata, dict) else None
                if isinstance(url, str) and urlsplit(url).scheme in {"http", "https"}:
                    sources.append(url)
                    content = str(source.get("content") or "").strip()
                    if content:
                        excerpts.append(f"Source: {url}\n{content[:2200]}")
            answer = str(response.get("message") or "").strip()
            answer = re.sub(r"\[\d+(?:,\s*\d+)*\]", "", answer)
            answer = re.sub(r"\s+", " ", answer).strip()
            if not answer or not sources or not excerpts:
                raise ValueError("Vane did not return a sourced answer")
            print(f"[VANE] Answered using {len(sources)} sources: {sources[:3]}")
            return VaneAnswer(answer, tuple(sources), tuple(excerpts))
        except Exception as exc:
            self._retry_after = time.monotonic() + 30
            self._models = None
            print(f"[VANE] Unavailable; using built-in web search: {exc}")
            return None
