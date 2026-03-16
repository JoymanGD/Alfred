import json
import re
from dataclasses import dataclass, field

from alfred.tooling import ToolContext, ToolRegistry


@dataclass
class RouteDecision:
    intent: str = "chat"
    tool_name: str | None = None
    normalized_query: str | None = None
    args: dict[str, str] = field(default_factory=dict)


def analyze_request(context: ToolContext, text: str, tool_registry: ToolRegistry) -> RouteDecision:
    labels = [
        "weather",
        "web_search",
        "fact",
        "current_events",
        "browser_open",
        "system_open",
        "music_play",
        "music_control",
        "image",
        "chat",
    ]
    prompt = (
        "Analyze the user's request and return JSON only.\n"
        "Choose exactly one intent from this list:\n"
        f"{', '.join(labels)}.\n"
        "Rules:\n"
        "- weather: asking for weather or forecast.\n"
        "- web_search: asking to search online or find information.\n"
        "- fact: factual lookup about a person, company, place, event, or thing.\n"
        "- current_events: news, recent developments, live situations, updates.\n"
        "- browser_open: open something in a browser, especially search or a page.\n"
        "- system_open: open an app, file, folder, or website directly.\n"
        "- music_play: play a song, artist, playlist, genre, or mood music.\n"
        "- music_control: next, pause, resume, louder, quieter, mute, previous.\n"
        "- If the user asks to play or turn on music, a song, an artist, a playlist, a genre, or mood music, use music_play even if they also mention the browser.\n"
        "- If the user asks to open news, search results, articles, or information in the browser, use browser_open.\n"
        "- Use browser_open only when the main goal is to open a page or search results in the browser.\n"
        "- image: generate an image.\n"
        "- chat: ordinary conversation.\n"
        "Return JSON with this schema:\n"
        '{"intent":"...", "normalized_query":"...", "args":{"location":"","time":"","site":""}}\n'
        "Use normalized_query to rewrite the request into the best input for the chosen tool.\n"
        "For browser_open, normalized_query should be the clean browser search phrase.\n"
        "For web_search, fact, and current_events, normalized_query should be the clean web search phrase.\n"
        "For music_play, normalized_query should be the clean music search phrase.\n"
        "For system_open, normalized_query should be the app or target to open.\n"
        "If no rewritten query is needed, normalized_query can be an empty string.\n\n"
        f"User message: {text}"
    )
    try:
        response = context.ollama_chat(
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
        decision = _postprocess_route_decision(text, decision)
        print(
            f"[ROUTER] LLM route decision: intent={decision.intent}, tool={decision.tool_name}, "
            f"query={decision.normalized_query!r} for: {text}"
        )
        if decision.intent in labels:
            return decision
    except Exception as e:
        print(f"[ROUTER] LLM router failed: {e}")

    return RouteDecision()


def _postprocess_route_decision(text: str, decision: RouteDecision) -> RouteDecision:
    lowered = text.lower()
    browser_open_patterns = [
        r"\bopen\b",
        r"\blaunch\b",
        r"\bstart\b",
        r"\bоткрой\b",
        r"\bзапусти\b",
    ]
    music_play_patterns = [
        r"\bplay\b",
        r"\bturn on\b",
        r"\bput on\b",
        r"\bstart\b",
        r"\bвключи\b",
        r"\bпоставь\b",
    ]
    music_terms = [
        "music",
        "song",
        "songs",
        "playlist",
        "track",
        "artist",
        "relax",
        "epic",
        "jazz",
        "rock",
        "pop",
        "lofi",
        "музы",
        "песн",
        "плейлист",
        "трек",
        "джаз",
        "рок",
        "эпич",
        "расслаб",
    ]

    if decision.intent == "browser_open":
        asks_to_play = any(re.search(pattern, lowered) for pattern in music_play_patterns)
        mentions_music = any(term in lowered for term in music_terms)
        if asks_to_play and mentions_music:
            return RouteDecision(
                intent="music_play",
                tool_name="music",
                normalized_query=decision.normalized_query,
                args=decision.args,
            )

    if decision.intent in {"weather", "web_search", "fact", "current_events"}:
        asks_to_open = any(re.search(pattern, lowered) for pattern in browser_open_patterns)
        if asks_to_open:
            return RouteDecision(
                intent="browser_open",
                tool_name="system",
                normalized_query=decision.normalized_query,
                args=decision.args,
            )

    return decision


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
        "browser_open": "system",
        "system_open": "system",
        "music_play": "music",
        "music_control": "music",
        "image": "image",
        "chat": None,
    }
    return mapping.get(intent)
