"""Typed intent decisions using decider-4b v2.1 Q4_K_M."""

import threading
from pathlib import Path
from typing import Any


class IntentDecider:
    def __init__(self, model_id: str, gguf_file: str, n_ctx: int = 4096):
        self.model_id = model_id
        self.gguf_file = gguf_file
        self.n_ctx = n_ctx
        self._model = None
        self._lock = threading.Lock()

    def _get_model(self):
        if self._model is None:
            from decider.infer import Decider

            source = self.model_id
            gguf_file = self.gguf_file
            if Path(gguf_file).is_file():
                source, gguf_file = gguf_file, None
            elif not Path(source).is_dir():
                from huggingface_hub.constants import HF_HUB_CACHE

                snapshots = (
                    Path(HF_HUB_CACHE)
                    / f"models--{source.replace('/', '--')}"
                    / "snapshots"
                )
                cached = [
                    folder for folder in snapshots.iterdir()
                    if (folder / gguf_file).is_file() and (folder / "tokenizer.json").is_file()
                ] if snapshots.is_dir() else []
                if cached:
                    source = str(max(cached, key=lambda folder: folder.stat().st_mtime))
                    print(f"[ROUTER] Using cached Decider snapshot: {source}")

            self._model = Decider(
                source,
                gguf_file=gguf_file,
                gguf_options={"n_ctx": self.n_ctx},
            )
        return self._model

    def warmup(self):
        with self._lock:
            self._get_model()
        print(f"[ROUTER] Decider ready: {self.model_id}, {self.gguf_file}")

    def choose(
        self,
        subject: dict[str, Any],
        question: str,
        criteria: dict[str, str],
        sensitive_choices=(),
    ) -> str:
        with self._lock:
            result = self._get_model().system_one(
                subject,
                {"decision": {"type": "choice", "instructions": question, "criteria": criteria}},
                max_state_tokens=max(256, self.n_ctx - 512),
            )
        decision = result["answers"]["decision"]
        answer = decision["choice"]
        if answer not in criteria:
            raise ValueError(f"Decider returned an unknown choice: {answer!r}")
        probability = decision.get("x_p_max", 0)
        if answer in sensitive_choices and probability < 0.8:
            raise ValueError(f"Low confidence for sensitive action {answer!r}: {probability:.3f}")
        return answer
