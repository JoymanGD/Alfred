import time
from dataclasses import dataclass, field
from typing import Any

from alfred.tooling import ToolContext, ToolRegistry


INTENTS = {
    "weather": "web", "web_search": "web", "fact": "web", "current_events": "web",
    "local_files": "local_files", "screen": "screen", "vision": "vision",
    "browser_open": "system", "system_open": "system", "codex_request": "codex",
    "planning": "plan", "music_play": "music", "music_control": "music",
    "image": "image", "chat": None, "unsupported_physical_action": None,
}

INTENT_CRITERIA = {
    "weather": "Asks Alfred to report weather, temperature, or a forecast directly. Excludes requests to Google or open browser search results.",
    "web_search": "Explicitly asks Alfred to look up a concrete topic online and report the findings in the conversation. The user does not request a browser tab.",
    "fact": "Asks Alfred for factual information about a person, place, organization, event, or thing and would benefit from an online lookup.",
    "current_events": "Asks for news, recent developments, or live updates.",
    "local_files": "Asks to find, list, or describe files or folders on this computer.",
    "screen": "Asks the assistant to inspect the computer screen or a screenshot.",
    "vision": "Requests a fresh camera observation of the user's surroundings. This includes visual questions without an explicit mention of the camera, but excludes statements that do not request a new observation.",
    "browser_open": "Explicitly asks to open a webpage, search results page, or new tab in the user's browser.",
    "system_open": "Asks to open or close an app or window, open a file or folder, or control Alfred or the computer. Closing Codex is an app control request.",
    "codex_request": "Explicitly asks Codex CLI to do a programming or code editing task. Merely mentioning Codex does not qualify.",
    "planning": "Asks to create, show, add to, complete, remove from, or clear a plan.",
    "music_play": "Asks to play a song, artist, playlist, genre, or mood music.",
    "music_control": "Asks to control playback or volume, or diagnose why Alfred is hard to hear.",
    "image": "Asks to generate or draw an image.",
    "unsupported_physical_action": "Directly commands Alfred to change the physical environment without a connected actuator, such as controlling room lights or handling real-world objects. Alfred has no body, robot or smart-home connection. A question about HOW the USER can do the task is chat; a question about Alfred's ability is chat. Excludes advice and available computer or internet operations.",
    "chat": "Ordinary conversation, including questions about Alfred's abilities, hypothetical requests, casual mentions of a topic, and corrections to earlier answers that do not request an action.",
}

ACTION_CRITERIA = {
    "local_files": {
        "list": "List files or folders matching the request.",
        "find": "Find a specific file or folder by name.",
        "describe": "Describe or summarize the contents of a folder.",
    },
    "music_control": {
        "next": "Skip to next track.", "previous": "Go to previous track.",
        "pause": "Pause playback.", "resume": "Resume playback.",
        "volume_up": "Increase volume.", "volume_down": "Decrease volume.",
        "mute": "Mute audio.", "unmute": "Unmute audio.",
        "volume_status": "Report current volume.",
        "volume_diagnose": "Explain why Alfred is hard to hear.",
    },
    "planning": {
        "create": "Create a new plan.", "show": "Show current plan.",
        "add": "Add an item to the plan.", "complete": "Mark a plan item complete.",
        "remove": "Remove an item from the plan.", "clear": "Clear the entire plan.",
    },
    "system_open": {
        "open": "Open an application, file, folder, or website.",
        "close_application": "Close an application or its window.",
        "restart_assistant": "Restart Alfred.", "shutdown_assistant": "Shut down Alfred.",
        "shutdown_computer": "Shut down the computer.",
        "restart_computer": "Restart the computer.",
    },
}


@dataclass
class RouteDecision:
    intent: str = "chat"
    tool_name: str | None = None
    normalized_query: str | None = None
    args: dict[str, Any] = field(default_factory=dict)


def analyze_request(context: ToolContext, text: str, tool_registry: ToolRegistry) -> RouteDecision:
    started_at = time.perf_counter()
    full_history = context.recent_history or []
    history = full_history[-4:]
    if history and history[-1].get("role") == "user" and history[-1].get("content") == text:
        history = history[:-1]
    subject = {"recent_conversation": history, "user_message": text}
    try:
        intent = context.decider.choose(
            subject,
            "What is the user's current intent? Classify the request, not isolated words. "
            "A request to report information found online is web_search, even when the topic is weather. "
            "Use browser_open only when the user wants a browser tab or page opened. "
            "A question about whether Alfred can search is chat, not a search request. "
            "Use the full conversation to resolve what the user means. "
            "Questions about objects physically around the user right now require vision, not web facts.",
            INTENT_CRITERIA,
        )
        if intent not in INTENTS:
            raise ValueError(f"Unknown intent: {intent!r}")
        if intent == "unsupported_physical_action":
            request_kind = context.decider.choose(
                subject,
                "Is the current message asking Alfred to personally execute a physical action now, "
                "or is it asking about an ability, seeking instructions or advice, or discussing "
                "a hypothetical action? Interpret the full meaning, including whether the user "
                "asks a question rather than giving an instruction.",
                {
                    "execute_now": "The user directs Alfred to perform bodily work in the real world now.",
                    "discuss": "The user asks whether Alfred can do it, how to do it, for advice, or hypothetically discusses it without requesting execution now.",
                },
            )
            if request_kind == "discuss":
                intent = "chat"
        if intent in {"web_search", "browser_open", "fact", "current_events"}:
            web_action = context.decider.choose(
                subject,
                "What does the user want Alfred to do right now? Distinguish doing a search from discussing whether searching is possible.",
                {
                    "answer_online": "Find information about a concrete subject online and report the answer in the conversation.",
                    "open_browser": "Open a webpage or search results tab in the user's browser.",
                    "inspect_camera": "Answer by looking at a physical object or scene near the user through the camera.",
                    "discuss": "Answer a question about Alfred's search ability or discuss searching without performing it now.",
                },
            )
            intent = {"answer_online": "web_search", "open_browser": "browser_open", "inspect_camera": "vision", "discuss": "chat"}[web_action]
        if intent == "chat":
            prior_user_messages = [item.get("content", "") for item in full_history if item.get("role") == "user"]
            if prior_user_messages and prior_user_messages[-1] == text:
                prior_user_messages.pop()
            information_subject = {
                "current_user_message": text,
                "prior_user_messages": prior_user_messages[-6:],
                "recent_conversation": history,
            }
            asks_for_information = context.decider.choose(
                information_subject,
                "Is the CURRENT message asking for an informational answer? A challenge or correction to Alfred's "
                "earlier factual answer can implicitly ask Alfred to verify and answer the same question again. "
                "Use conversation only to resolve the request, never as factual evidence.",
                {
                    "ask": "The current message asks for an answer or explanation, or challenges an earlier factual answer and expects it to be checked.",
                    "other": "The current message is a greeting, statement, or action request without asking for an informational answer.",
                },
            )
            if asks_for_information == "ask":
                grounding = context.decider.choose(
                    information_subject,
                    "What source is needed to answer the CURRENT request reliably?",
                    {
                        "web": "The current question asks for facts about an external subject, including a follow-up or challenge to an earlier unverified answer. Treat the local model and its earlier answers as unverified; search when in doubt.",
                        "vision": "The question requests a fresh observation of what is physically visible near the user right now. Inspect the camera rather than searching the web or guessing.",
                        "local": "The current question concerns Alfred's abilities, the user's own statements or corrections, simple reasoning, or a subjective opinion, and can be answered without new external evidence.",
                    },
                )
                if grounding == "web":
                    intent = "fact"
                elif grounding == "vision":
                    intent = "vision"
        args = {}
        if intent in ACTION_CRITERIA:
            action = context.decider.choose(
                subject,
                f"Which {intent} action does the user request?",
                ACTION_CRITERIA[intent],
                sensitive_choices={"shutdown_computer", "restart_computer"} if intent == "system_open" else (),
            )
            if action not in ACTION_CRITERIA[intent]:
                raise ValueError(f"Unknown {intent} action: {action!r}")
            if action != "open":
                args["action"] = action
        decision = RouteDecision(intent=intent, tool_name=INTENTS[intent], args=args)
        print(
            f"[ROUTER] Decider route in {time.perf_counter() - started_at:.3f}s: "
            f"intent={intent}, tool={decision.tool_name}, args={args} for: {text}"
        )
        return decision
    except Exception as exc:
        print(f"[ROUTER] Decider failed: {exc}")
        raise RuntimeError(f"Decider routing failed: {exc}") from exc


def map_intent_to_tool_name(intent: str) -> str | None:
    return INTENTS.get(intent)
