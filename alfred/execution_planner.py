import json
import re
from dataclasses import replace

from alfred.router import RouteDecision
from alfred.tooling import ToolContext


PLANNABLE_INTENTS = {
    "system_open",
    "local_files",
    "browser_open",
    "codex_request",
    "music_play",
    "web_search",
    "fact",
    "current_events",
}


def refine_route_with_planning(context: ToolContext, text: str, decision: RouteDecision) -> RouteDecision:
    if decision.intent not in PLANNABLE_INTENTS:
        return decision

    heuristic_decision = _apply_heuristics(text, decision)
    if _can_skip_llm_planning(text, heuristic_decision):
        print(
            f"[PLAN] Skipping LLM planning for intent={heuristic_decision.intent}, "
            f"query={heuristic_decision.normalized_query!r}, args={heuristic_decision.args}"
        )
        return heuristic_decision

    try:
        response = context.router_ollama_chat(
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
                    "content": _build_planning_prompt(text, heuristic_decision),
                },
            ]
        )
        raw = context.normalize_memory_text(response["message"]["content"])
        data = _parse_json(raw)
    except Exception as exc:
        print(f"[PLAN] Execution planning failed: {exc}")
        return heuristic_decision

    normalized_query = str(data.get("normalized_query") or "").strip() or heuristic_decision.normalized_query
    planned_args = data.get("args") if isinstance(data.get("args"), dict) else {}
    merged_args = dict(heuristic_decision.args or {})
    for key, value in planned_args.items():
        if value in (None, "", []):
            continue
        merged_args[key] = value

    plan_steps = data.get("plan_steps")
    if isinstance(plan_steps, list):
        compact_steps = [str(step).strip() for step in plan_steps if str(step).strip()]
        if compact_steps:
            print(f"[PLAN] {heuristic_decision.intent}: {' | '.join(compact_steps[:4])}")

    return replace(
        heuristic_decision,
        normalized_query=normalized_query,
        args=merged_args,
    )


def _build_planning_prompt(text: str, decision: RouteDecision) -> str:
    return (
        "Refine this request for tool execution and preserve important constraints.\n"
        "Return JSON with this schema only:\n"
        '{"normalized_query":"","args":{"location_hint":"","target_kind":"","search_terms":""},"plan_steps":[""]}\n'
        "Rules:\n"
        "- Keep the same intent; do not change the tool category.\n"
        "- Preserve location details such as desktop, downloads, documents, music, pictures, videos, project folder, or home folder.\n"
        "- Preserve filenames, extensions, URLs, app names, and quoted strings.\n"
        "- For system_open, set args.target_kind to one of: file, folder, app, url.\n"
        "- For system_open, use args.location_hint when the user implies a likely folder.\n"
        "- For local_files, preserve the requested folder, filename fragments, and file extensions or file types.\n"
        "- For browser_open, normalized_query should be the best search phrase or page target.\n"
        "- For codex_request, normalized_query should keep all requested behavior and constraints.\n"
        "- plan_steps are short hidden execution steps, 2 to 5 items.\n"
        "- No explanation outside JSON.\n\n"
        f"Original request: {text}\n"
        f"Current intent: {decision.intent}\n"
        f"Current normalized_query: {decision.normalized_query or ''}\n"
        f"Current args: {json.dumps(decision.args or {}, ensure_ascii=False)}"
    )


def _apply_heuristics(text: str, decision: RouteDecision) -> RouteDecision:
    args = dict(decision.args or {})
    location_hint = _extract_location_hint(text)
    if location_hint and not args.get("location_hint"):
        args["location_hint"] = location_hint

    target_kind = _infer_target_kind(text, decision.normalized_query or "")
    if target_kind and not args.get("target_kind"):
        args["target_kind"] = target_kind

    normalized_query = decision.normalized_query or ""
    if decision.intent == "system_open" and target_kind == "file":
        explicit_name = _extract_filename(text)
        if explicit_name:
            normalized_query = explicit_name

    return replace(decision, normalized_query=normalized_query or None, args=args)


def _can_skip_llm_planning(text: str, decision: RouteDecision) -> bool:
    lowered = text.lower()

    if decision.intent == "codex_request":
        return bool(decision.normalized_query)

    if decision.intent in {"web_search", "fact", "current_events", "music_play"}:
        return bool(decision.normalized_query)

    if decision.intent == "browser_open":
        return bool(decision.normalized_query)

    if decision.intent == "system_open":
        if decision.args.get("action"):
            return True
        return bool(decision.normalized_query) and bool(decision.args.get("target_kind"))

    if decision.intent == "local_files":
        has_extension = bool(re.search(r"\.[a-z0-9]{1,8}\b", lowered))
        has_location = bool(decision.args.get("location_hint"))
        return bool(decision.normalized_query) and (has_extension or has_location)

    return False


def _extract_location_hint(text: str) -> str | None:
    patterns = {
        "desktop": [r"\bon my desktop\b", r"\bfrom my desktop\b", r"\bdesktop\b"],
        "downloads": [r"\bin my downloads\b", r"\bdownloads\b"],
        "documents": [r"\bin my documents\b", r"\bdocuments\b"],
        "music": [r"\bin my music\b", r"\bmusic folder\b"],
        "pictures": [r"\bin my pictures\b", r"\bpictures\b"],
        "videos": [r"\bin my videos\b", r"\bvideos\b"],
        "project": [r"\bin this project\b", r"\bin the project\b", r"\bproject folder\b"],
        "home": [r"\bin my home folder\b", r"\bhome folder\b"],
    }
    lowered = text.lower()
    for hint, variants in patterns.items():
        if any(re.search(pattern, lowered) for pattern in variants):
            return hint
    return None


def _infer_target_kind(text: str, normalized_query: str) -> str | None:
    candidate = normalized_query or text
    lowered = candidate.lower()
    if re.match(r"^https?://", lowered):
        return "url"
    if any(token in lowered for token in [" folder", " directory"]) or lowered.endswith("\\") or lowered.endswith("/"):
        return "folder"
    if _extract_filename(candidate):
        return "file"
    return None


def _extract_filename(text: str) -> str | None:
    match = re.search(r"([A-Za-z0-9_()\-\[\] ]+\.[A-Za-z0-9]{2,5})", text)
    if match:
        return match.group(1).strip()
    return None


def _parse_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise
