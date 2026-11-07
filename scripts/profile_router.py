import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alfred.agent import AgentConfig, build_tool_registry
from alfred.config import load_config
from alfred.decider import IntentDecider
from alfred.router import analyze_request
from alfred.tooling import ToolContext
from alfred.vision import VisionConfig


def normalize_text(text: str) -> str:
    return " ".join((text or "").split())


def detect_language(_: str) -> str:
    return "en"


def build_context(router):
    config = AgentConfig(
        base_dir=str(ROOT),
        codex_exe="codex",
        system_prompt="You are Alfred.",
        plan_storage_path=str(ROOT / "alfred_plan.json"),
        vision_config=VisionConfig(enabled=False, camera_indexes=[]),
    )
    tool_registry = build_tool_registry(config)
    context = ToolContext(
        user_text="",
        language="en",
        system_prompt=config.system_prompt,
        recent_history=[],
        ollama_chat=lambda messages: {"message": {"content": ""}},
        decider=router,
        normalize_memory_text=normalize_text,
        detect_language=detect_language,
        fetch_url=lambda url: "",
        fetch_json=lambda *args, **kwargs: {},
    )
    return context, tool_registry


def main():
    requests = [
        ("thanks", [{"role": "assistant", "content": "Opening browser search results."}]),
        ("open discord", []),
        ("play jazz", []),
        ("pause music", []),
        ("show my plan", []),
        ("find files in my downloads", []),
        ("look at my screen", []),
        ("ask codex to optimize the router", []),
        ("generate an image of a fox astronaut", []),
        ("what's the weather in Kyiv", []),
    ]

    config = load_config(str(ROOT))
    router = IntentDecider(config.decider_model, config.decider_gguf_file, config.decider_n_ctx)
    context, tool_registry = build_context(router)

    started_at = time.perf_counter()
    results = []
    for text, history in requests:
        context.user_text = text
        context.recent_history = history
        decision = analyze_request(context, text, tool_registry)
        results.append((text, decision.intent, decision.tool_name))
    elapsed = time.perf_counter() - started_at

    print(f"Requests tested: {len(requests)}")
    print(f"Decider model: {router.model_id}, file={router.gguf_file}")
    print(f"Elapsed time: {elapsed:.2f}s")
    print("Decisions:")
    for text, intent, tool_name in results:
        print(f"- {text!r} -> intent={intent}, tool={tool_name}")


if __name__ == "__main__":
    main()
