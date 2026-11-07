import json
import re
import time
from dataclasses import replace

from alfred.router import RouteDecision
from alfred.running_windows import list_running_windows
from alfred.tooling import ToolContext


PLANNABLE_INTENTS = {
    "system_open",
    "browser_open",
    "codex_request",
    "planning",
}


def refine_route_with_planning(context: ToolContext, text: str, decision: RouteDecision) -> RouteDecision:
    if decision.intent not in PLANNABLE_INTENTS:
        return decision

    if decision.intent == "system_open" and decision.args.get("action") == "close_application":
        return _plan_close_application(context, text, decision)

    if _can_skip_llm_planning(decision):
        print(
            f"[PLAN] Skipping LLM planning for intent={decision.intent}, "
            f"query={decision.normalized_query!r}, args={decision.args}"
        )
        return decision

    try:
        started_at = time.perf_counter()
        response = context.ollama_chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You are a hidden execution planner for an assistant. "
                        "Return valid JSON only."
                    ),
                },
                {
                    "role": "user",
                    "content": _build_planning_prompt(text, decision),
                },
            ]
        )
        raw = context.normalize_memory_text(response["message"]["content"])
        data = _parse_json(raw)
        elapsed = time.perf_counter() - started_at
        print(f"[PLAN] LLM planning completed in {elapsed:.3f}s for intent={decision.intent}")
    except Exception as exc:
        print(f"[PLAN] Execution planning failed: {exc}")
        return decision

    normalized_query = str(data.get("normalized_query") or "").strip() or decision.normalized_query
    planned_args = data.get("args") if isinstance(data.get("args"), dict) else {}
    merged_args = dict(decision.args or {})
    for key, value in planned_args.items():
        if value in (None, "", []):
            continue
        merged_args[key] = value

    plan_steps = data.get("plan_steps")
    if isinstance(plan_steps, list):
        compact_steps = [str(step).strip() for step in plan_steps if str(step).strip()]
        if compact_steps:
            print(f"[PLAN] {decision.intent}: {' | '.join(compact_steps[:4])}")

    return replace(
        decision,
        normalized_query=normalized_query,
        args=merged_args,
    )


def _plan_close_application(context: ToolContext, text: str, decision: RouteDecision) -> RouteDecision:
    windows = list_running_windows()
    if not windows:
        return replace(decision, normalized_query=None)
    criteria = {"none": "No listed window clearly matches the application the user wants to close."}
    for window in windows:
        criteria[str(window["pid"])] = (
            f"The user wants to close the application with window title {window['title']!r}, "
            f"process name {window['name']!r}, and executable path {window['path']!r}."
        )
    try:
        started_at = time.perf_counter()
        choice = context.decider.choose(
            {"user_message": text, "recent_conversation": context.recent_history or []},
            "Which listed open window does the user want to close? Use the meaning of the full request. "
            "Select none if there is no clear match. Do not match incidental substrings in the request.",
            criteria,
        )
        chosen = next((window for window in windows if str(window["pid"]) == choice), None)
        print(f"[PLAN] Decider selected close-app window in {time.perf_counter() - started_at:.3f}s: {choice}")
    except Exception as exc:
        print(f"[PLAN] Close-app selection failed: {exc}")
        return replace(decision, normalized_query=None)
    if chosen is None:
        return replace(decision, normalized_query=None)
    args = dict(decision.args)
    args["process_id"] = chosen["pid"]
    args["process_name"] = chosen["name"]
    return replace(decision, normalized_query=chosen["title"], args=args)


def _build_planning_prompt(text: str, decision: RouteDecision) -> str:
    return (
        "Refine this request for tool execution and preserve important constraints.\n"
        "Return JSON with this schema only:\n"
        '{"normalized_query":"","args":{"location_hint":"","target_kind":"","search_terms":"","item":"","step":""},"plan_steps":[""]}\n'
        "Rules:\n"
        "- Keep the same intent; do not change the tool category.\n"
        "- Preserve location details such as desktop, downloads, documents, music, pictures, videos, project folder, or home folder.\n"
        "- Preserve filenames, extensions, URLs, app names, and quoted strings.\n"
        "- For system_open, set args.target_kind to one of: file, folder, app, url.\n"
        "- For system_open with action close_application, normalized_query must name only the app or window to close.\n"
        "- For system_open, use args.location_hint when the user implies a likely folder.\n"
        "- For local_files, preserve the requested folder, filename fragments, and file extensions or file types.\n"
        "- For browser_open, normalized_query should be the best search phrase or page target.\n"
        "- For codex_request, normalized_query should keep all requested behavior and constraints.\n"
        "- For planning create, normalized_query is the goal of the plan.\n"
        "- For planning add, put the new item in args.item. For complete or remove, put the step number in args.step when available.\n"
        "- plan_steps are short hidden execution steps, 2 to 5 items.\n"
        "- No explanation outside JSON.\n\n"
        f"Original request: {text}\n"
        f"Current intent: {decision.intent}\n"
        f"Current normalized_query: {decision.normalized_query or ''}\n"
        f"Current args: {json.dumps(decision.args or {}, ensure_ascii=False)}"
    )


def _can_skip_llm_planning(decision: RouteDecision) -> bool:
    if decision.intent == "planning":
        return decision.args.get("action") in {"show", "clear"}
    if decision.intent == "system_open":
        return decision.args.get("action") in {
            "restart_assistant", "shutdown_assistant", "shutdown_computer", "restart_computer"
        }
    return False


def _parse_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise
