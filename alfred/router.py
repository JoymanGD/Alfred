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
        "screen",
        "vision",
        "browser_open",
        "system_open",
        "codex_request",
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
        "- screen: answer by capturing the user's screen and analyzing what is visible there.\n"
        "- vision: answer by looking through the camera and analyzing what is visible.\n"
        "- browser_open: open something in a browser, especially search or a page.\n"
        "- system_open: open an app, file, folder, or website directly, or control the assistant itself.\n"
        "- codex_request: send a development task to Codex CLI for the current project.\n"
        "- music_play: play a song, artist, playlist, genre, or mood music.\n"
        "- music_control: next, pause, resume, louder, quieter, mute, previous, unmute, full volume, volume status, or explain why Alfred is hard to hear.\n"
        "- Use screen when the user is asking about what is on the monitor, screen, desktop, window, app, browser page, or any visible on-screen content.\n"
        "- Use vision when the user is asking about something Alfred must see to answer, such as an object, text, packaging, what is in the user's hand, what is on the desk, colors, appearance, or anything visually present.\n"
        "- If the user asks to play or turn on music, a song, an artist, a playlist, a genre, or mood music, use music_play even if they also mention the browser.\n"
        "- If the user asks to open news, search results, articles, or information in the browser, use browser_open.\n"
        "- Use browser_open only when the main goal is to open a page or search results in the browser.\n"
        "- image: generate an image.\n"
        "- chat: ordinary conversation.\n"
        "- For music_control, set args.action to one of: next, previous, pause, resume, volume_up, volume_down, mute, unmute, volume_status, volume_diagnose.\n"
        "- For music_control volume requests, also fill at most one of these optional fields when applicable:\n"
        "  delta_percent: numeric percent change such as 10 or 50.\n"
        "  factor: numeric multiplier such as 0.5 for twice quieter or 2.0 for twice louder.\n"
        "  qualifier: one of slight, medium, strong, max.\n"
        "- For music_control, do not leave args.action empty.\n"
        "Examples for music_control:\n"
        '- "mute" -> {"intent":"music_control","normalized_query":"","args":{"action":"mute"}}\n'
        '- "unmute" -> {"intent":"music_control","normalized_query":"","args":{"action":"unmute"}}\n'
        '- "turn the volume up by 10 percent" -> {"intent":"music_control","normalized_query":"","args":{"action":"volume_up","delta_percent":"10"}}\n'
        '- "make it twice quieter" -> {"intent":"music_control","normalized_query":"","args":{"action":"volume_down","factor":"0.5"}}\n'
        '- "сделай тише в два раза" -> {"intent":"music_control","normalized_query":"","args":{"action":"volume_down","factor":"0.5"}}\n'
        '- "сделай громче в два раза" -> {"intent":"music_control","normalized_query":"","args":{"action":"volume_up","factor":"2.0"}}\n'
        '- "включи звук" -> {"intent":"music_control","normalized_query":"","args":{"action":"unmute"}}\n'
        '- "убери звук" -> {"intent":"music_control","normalized_query":"","args":{"action":"mute"}}\n'
        '- "на полную громкость" -> {"intent":"music_control","normalized_query":"","args":{"action":"volume_up","qualifier":"max"}}\n'
        '- "какая сейчас громкость" -> {"intent":"music_control","normalized_query":"","args":{"action":"volume_status"}}\n'
        '- "what is the current volume" -> {"intent":"music_control","normalized_query":"","args":{"action":"volume_status"}}\n'
        '- "why is Alfred so hard to hear" -> {"intent":"music_control","normalized_query":"","args":{"action":"volume_diagnose"}}\n'
        '- "restart Alfred" -> {"intent":"system_open","normalized_query":"","args":{"action":"restart_assistant"}}\n'
        '- "перезапусти Альфреда" -> {"intent":"system_open","normalized_query":"","args":{"action":"restart_assistant"}}\n'
        '- "turn off Alfred" -> {"intent":"system_open","normalized_query":"","args":{"action":"shutdown_assistant"}}\n'
        '- "выключи Альфреда" -> {"intent":"system_open","normalized_query":"","args":{"action":"shutdown_assistant"}}\n'
        '- "shutdown the computer" -> {"intent":"system_open","normalized_query":"","args":{"action":"shutdown_computer"}}\n'
        '- "turn off my PC" -> {"intent":"system_open","normalized_query":"","args":{"action":"shutdown_computer"}}\n'
        '- "restart the computer" -> {"intent":"system_open","normalized_query":"","args":{"action":"restart_computer"}}\n'
        '- "reboot my PC" -> {"intent":"system_open","normalized_query":"","args":{"action":"restart_computer"}}\n'
        '- "выключи компьютер" -> {"intent":"system_open","normalized_query":"","args":{"action":"shutdown_computer"}}\n'
        '- "ask codex to add a shutdown computer command" -> {"intent":"codex_request","normalized_query":"add a shutdown computer command","args":{}}\n'
        '- "ask codex to fix the router" -> {"intent":"codex_request","normalized_query":"fix the router","args":{}}\n'
        "Return JSON with this schema:\n"
        '{"intent":"...", "normalized_query":"...", "args":{"location":"","time":"","site":"","action":"","delta_percent":"","factor":"","qualifier":""}}\n'
        "Use normalized_query to rewrite the request into the best input for the chosen tool.\n"
        "For screen, normalized_query should usually stay close to the user's original request.\n"
        "For vision, normalized_query should usually stay close to the user's original request.\n"
        "For browser_open, normalized_query should be the clean browser search phrase.\n"
        "For web_search, fact, and current_events, normalized_query should be the clean web search phrase.\n"
        "For music_play, normalized_query should be the clean music search phrase.\n"
        "For music_control, normalized_query should usually be empty.\n"
        "For system_open, normalized_query should be the app or target to open.\n"
        "For codex_request, normalized_query should be the clean software task for Codex.\n"
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
            f"query={decision.normalized_query!r}, args={decision.args} for: {text}"
        )
        if decision.intent in labels:
            return decision
    except Exception as e:
        print(f"[ROUTER] LLM router failed: {e}")

    return RouteDecision()


def _postprocess_route_decision(text: str, decision: RouteDecision) -> RouteDecision:
    lowered = text.lower()
    assistant_restart_patterns = [
        r"\brestart\s+alfred\b",
        r"\breboot\s+alfred\b",
        r"\brestart\s+yourself\b",
        r"\breboot\s+yourself\b",
        r"\bперезапусти\b",
        r"\bперезагрузись\b",
        r"\bперезапустись\b",
    ]
    assistant_shutdown_patterns = [
        r"\bturn\s+off\s+alfred\b",
        r"\bshutdown\s+alfred\b",
        r"\bshut\s+yourself\s+down\b",
        r"\bвыключи\s+альфреда\b",
        r"\bотключись\b",
    ]
    computer_terms = [
        "computer",
        "pc",
        "laptop",
        "\u043a\u043e\u043c\u043f\u044c\u044e\u0442\u0435\u0440",
        "\u043f\u043a",
        "\u043d\u043e\u0443\u0442\u0431\u0443\u043a",
    ]

    if any(re.search(pattern, lowered) for pattern in assistant_restart_patterns):
        if not any(term in lowered for term in computer_terms):
            return RouteDecision(
                intent="system_open",
                tool_name="system",
                normalized_query=None,
                args={"action": "restart_assistant"},
            )

    if any(re.search(pattern, lowered) for pattern in assistant_shutdown_patterns):
        if not any(term in lowered for term in computer_terms):
            return RouteDecision(
                intent="system_open",
                tool_name="system",
                normalized_query=None,
                args={"action": "shutdown_assistant"},
            )

    hearing_issue_patterns = [
        r"\bwhy\s+is\s+alfred\s+so\s+hard\s+to\s+hear\b",
        r"\balfred\b.*\b(?:hard|quiet|low)\b.*\b(?:hear|hearing)\b",
        r"\b(?:can(?:not|'t)\s+hear|hard\s+to\s+hear)\b.*\balfred\b",
    ]
    if any(re.search(pattern, lowered) for pattern in hearing_issue_patterns):
        return RouteDecision(
            intent="music_control",
            tool_name="music",
            normalized_query=None,
            args={"action": "volume_diagnose"},
        )

    shutdown_computer_patterns = [
        r"\bshutdown\b.*\b(?:computer|pc|laptop)\b",
        r"\bturn\s+off\b.*\b(?:computer|pc|laptop)\b",
        r"\bpower\s+off\b.*\b(?:computer|pc|laptop)\b",
        r"\bвыключи\b.*\b(?:компьютер|пк|ноутбук)\b",
    ]
    if any(re.search(pattern, lowered) for pattern in shutdown_computer_patterns):
        return RouteDecision(
            intent="system_open",
            tool_name="system",
            normalized_query=None,
            args={"action": "shutdown_computer"},
        )

    restart_computer_patterns = [
        r"\brestart\b.*\b(?:computer|pc|laptop)\b",
        r"\breboot\b.*\b(?:computer|pc|laptop)\b",
        r"\brestart\s+my\s+(?:computer|pc|laptop)\b",
        r"\breboot\s+my\s+(?:computer|pc|laptop)\b",
        r"\bРїРµСЂРµР·Р°РіСЂСѓР·Рё\b.*\b(?:РєРѕРјРїСЊСЋС‚РµСЂ|РїРє|РЅРѕСѓС‚Р±СѓРє)\b",
    ]
    if any(re.search(pattern, lowered) for pattern in restart_computer_patterns):
        return RouteDecision(
            intent="system_open",
            tool_name="system",
            normalized_query=None,
            args={"action": "restart_computer"},
        )

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
        "музык",
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

    codex_terms = ["codex", "кодекс", "кодекса", "кодексу"]
    codex_request_patterns = [
        r"\bask\s+codex\b",
        r"\btell\s+codex\b",
        r"\brequest\s+codex\b",
        r"\bпопроси\b",
        r"\bспроси\b",
        r"\bзапроси\b",
    ]
    if any(term in lowered for term in codex_terms) and any(re.search(pattern, lowered) for pattern in codex_request_patterns):
        normalized_query = decision.normalized_query or _extract_codex_query(text)
        return RouteDecision(
            intent="codex_request",
            tool_name="codex",
            normalized_query=normalized_query,
            args=decision.args,
        )

    return decision


def _extract_codex_query(text: str) -> str | None:
    patterns = [
        r"^(?:ask|tell|request)\s+(?:codex)\s+to\s+(.+)$",
        r"^(?:попроси|спроси|запроси)\s+(?:у\s+)?(?:кодекса|кодексу|кодекс)\s+(.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip(" ?!.,")
    return None


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
        "screen": "screen",
        "vision": "vision",
        "browser_open": "system",
        "system_open": "system",
        "codex_request": "codex",
        "music_play": "music",
        "music_control": "music",
        "image": "image",
        "chat": None,
    }
    return mapping.get(intent)
