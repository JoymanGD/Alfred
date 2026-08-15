import json
import re
from dataclasses import dataclass, field
from typing import Any

from alfred.tooling import ToolContext, ToolRegistry


@dataclass
class RouteDecision:
    intent: str = "chat"
    tool_name: str | None = None
    normalized_query: str | None = None
    args: dict[str, Any] = field(default_factory=dict)


def analyze_request(context: ToolContext, text: str, tool_registry: ToolRegistry) -> RouteDecision:
    labels = [
        "weather",
        "web_search",
        "fact",
        "current_events",
        "local_files",
        "screen",
        "vision",
        "browser_open",
        "system_open",
        "codex_request",
        "planning",
        "music_play",
        "music_control",
        "image",
        "chat",
    ]
    history_lines = []
    for item in context.recent_history:
        role = item.get("role", "user")
        content = item.get("content", "")
        if not content:
            continue
        history_lines.append(f"{role}: {content}")
    history_block = "\n".join(history_lines) if history_lines else "(no recent history)"

    prompt = (
        "Analyze the user's request and return JSON only.\n"
        "Choose exactly one intent from this list:\n"
        f"{', '.join(labels)}.\n"
        "Return JSON with this schema only:\n"
        '{"intent":"...", "normalized_query":"...", "args":{"location":"","time":"","site":"","action":"","delta_percent":"","factor":"","qualifier":"","item":"","step":""}}\n'
        "Rules:\n"
        "- weather: asking for weather or forecast.\n"
        "- web_search: asking to search online or find information.\n"
        "- fact: factual lookup about a person, company, place, event, or thing.\n"
        "- current_events: news, recent developments, live situations, updates.\n"
        "- local_files: search, list, locate, or describe files and folders on the user's computer.\n"
        "- screen: answer by capturing the user's screen and analyzing what is visible there.\n"
        "- vision: answer by looking through the camera and analyzing what is visible.\n"
        "- browser_open: open something in a browser, especially search or a page.\n"
        "- system_open: open an app, file, folder, or website directly, or control the assistant itself.\n"
        "- codex_request: send a development task to Codex CLI for the current project.\n"
        "- planning: create, show, add, complete, remove, or clear a plan.\n"
        "- music_play: play a song, artist, playlist, genre, or mood music.\n"
        "- music_control: next, pause, resume, louder, quieter, mute, previous, unmute, full volume, volume status, or explain why Alfred is hard to hear.\n"
        "- image: generate an image.\n"
        "- chat: ordinary conversation.\n"
        "- Use the user's actual intent, not just isolated keywords. If the user casually mentions a topic without asking to look it up or act on it, choose chat.\n"
        "- Pay close attention to the recent conversation context. If the current message is clearly a conversational reply to the assistant's previous message, choose chat unless the user explicitly asks to search, check, open, play, generate, or control something.\n"
        "- Example: if the assistant asks how the user is doing and the user replies that the weather is not great, that is still chat, not weather.\n"
        "- For music_control, set args.action to one of: next, previous, pause, resume, volume_up, volume_down, mute, unmute, volume_status, volume_diagnose.\n"
        "- For planning add, put the item in args.item.\n"
        "- For planning complete or remove, put the step number in args.step when possible.\n"
        "- For system_open control actions, use args.action like restart_assistant, shutdown_assistant, shutdown_computer, restart_computer when clearly requested.\n"
        "- Use normalized_query to rewrite the request into the best input for the chosen tool.\n"
        "- If no rewritten query is needed, normalized_query can be an empty string.\n\n"
        f"Recent conversation:\n{history_block}\n\n"
        f"User message: {text}"
    )
    try:
        response = context.router_ollama_chat(
            [
                {
                    "role": "system",
                    "content": "You are a strict request router. Output valid JSON only.",
                },
                {"role": "user", "content": prompt},
            ]
        )
        raw = context.normalize_memory_text(response["message"]["content"])
        data = _parse_router_json(raw)
        decision = RouteDecision(
            intent=data.get("intent", "chat"),
            tool_name=map_intent_to_tool_name(data.get("intent", "chat")),
            normalized_query=(data.get("normalized_query") or "").strip() or None,
            args=data.get("args") if isinstance(data.get("args"), dict) else {},
        )
        print(
            f"[ROUTER] LLM route decision: intent={decision.intent}, tool={decision.tool_name}, "
            f"query={decision.normalized_query!r}, args={decision.args} for: {text}"
        )
        if decision.intent in labels:
            return decision
    except Exception as e:
        print(f"[ROUTER] LLM router failed: {e}")

    return RouteDecision()


def _parse_router_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def map_intent_to_tool_name(intent: str) -> str | None:
    mapping = {
        "weather": "web",
        "web_search": "web",
        "fact": "web",
        "current_events": "web",
        "local_files": "local_files",
        "screen": "screen",
        "vision": "vision",
        "browser_open": "system",
        "system_open": "system",
        "codex_request": "codex",
        "planning": "plan",
        "music_play": "music",
        "music_control": "music",
        "image": "image",
        "chat": None,
    }
    return mapping.get(intent)
