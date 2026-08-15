import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from alfred.planner import PlanItem, PlanState, PlanStore
from alfred.tooling import Tool, ToolContext, ToolResult


@dataclass
class PlanToolConfig:
    storage_path: str


class PlanTool(Tool):
    name = "plan"

    def __init__(self, config: PlanToolConfig):
        self.store = PlanStore(config.storage_path)

    def matches(self, context: ToolContext, text: str) -> bool:
        lowered = text.lower().strip()
        trigger_patterns = [
            r"\bplan\b",
            r"\bto[- ]do\b",
            r"\btask list\b",
            r"\bchecklist\b",
            r"\broadmap\b",
            r"\bstep\s+\d+\b",
        ]
        return any(re.search(pattern, lowered) for pattern in trigger_patterns)

    def handle(self, context: ToolContext, text: str) -> ToolResult:
        language = context.language or context.detect_language(text)
        action = self._resolve_action(context, text)
        if action == "create":
            return self._handle_create(context, text, language)
        if action == "show":
            return self._handle_show(language)
        if action == "add":
            return self._handle_add(context, text, language)
        if action == "complete":
            return self._handle_complete(context, text, language)
        if action == "remove":
            return self._handle_remove(context, text, language)
        if action == "clear":
            return self._handle_clear(language)

        reply_text = "I need a planning action." if language != "ru" else "Мне нужно понять действие для плана."
        return ToolResult(handled=True, reply_text=reply_text)

    def _handle_create(self, context: ToolContext, text: str, language: str) -> ToolResult:
        goal = self._extract_goal(context, text)
        if not goal:
            reply_text = "Tell me what the plan should be for." if language != "ru" else "Скажи, для чего составить план."
            return ToolResult(handled=True, reply_text=reply_text)

        steps = self._generate_steps(context, goal)
        state = PlanState(
            goal=goal,
            items=[PlanItem(id=index + 1, text=step) for index, step in enumerate(steps)],
        )
        self.store.save(state)
        return ToolResult(handled=True, reply_text=self._summarize_plan_created(goal, steps, language))

    def _handle_show(self, language: str) -> ToolResult:
        state = self.store.load()
        if not state.goal and not state.items:
            reply_text = "There is no active plan." if language != "ru" else "Активного плана нет."
            return ToolResult(handled=True, reply_text=reply_text)
        return ToolResult(handled=True, reply_text=self._format_state(state, language))

    def _handle_add(self, context: ToolContext, text: str, language: str) -> ToolResult:
        item_text = self._extract_item_text(context, text)
        if not item_text:
            reply_text = "Tell me what to add to the plan." if language != "ru" else "Скажи, что добавить в план."
            return ToolResult(handled=True, reply_text=reply_text)

        state = self.store.load()
        state.items.append(PlanItem(id=len(state.items) + 1, text=item_text))
        if not state.goal:
            state.goal = "General plan"
        self.store.save(state)
        reply_text = (
            f"Added step {len(state.items)}: {item_text}."
            if language != "ru"
            else f"Добавил шаг {len(state.items)}: {item_text}."
        )
        return ToolResult(handled=True, reply_text=reply_text)

    def _handle_complete(self, context: ToolContext, text: str, language: str) -> ToolResult:
        state = self.store.load()
        item = self._find_item(state, context, text)
        if item is None:
            reply_text = "I could not find that plan step." if language != "ru" else "Не нашел такой шаг плана."
            return ToolResult(handled=True, reply_text=reply_text)

        item.done = True
        item.completed_at = self._now()
        self.store.save(state)
        reply_text = (
            f"Marked step {item.id} done."
            if language != "ru"
            else f"Отметил шаг {item.id} как выполненный."
        )
        return ToolResult(handled=True, reply_text=reply_text)

    def _handle_remove(self, context: ToolContext, text: str, language: str) -> ToolResult:
        state = self.store.load()
        item = self._find_item(state, context, text)
        if item is None:
            reply_text = "I could not find that plan step." if language != "ru" else "Не нашел такой шаг плана."
            return ToolResult(handled=True, reply_text=reply_text)

        removed_id = item.id
        state.items = [existing for existing in state.items if existing.id != removed_id]
        self.store.save(state)
        reply_text = (
            f"Removed step {removed_id}."
            if language != "ru"
            else f"Удалил шаг {removed_id}."
        )
        return ToolResult(handled=True, reply_text=reply_text)

    def _handle_clear(self, language: str) -> ToolResult:
        self.store.clear()
        reply_text = "Cleared the plan." if language != "ru" else "Очистил план."
        return ToolResult(handled=True, reply_text=reply_text)

    def _resolve_action(self, context: ToolContext, text: str) -> str:
        routed_action = ((context.routed_args or {}).get("action") or "").strip().lower()
        if routed_action in {"create", "show", "add", "complete", "remove", "clear"}:
            return routed_action

        lowered = text.lower()
        if any(phrase in lowered for phrase in ["show my plan", "what is my plan", "what's my plan", "list my plan"]):
            return "show"
        if any(phrase in lowered for phrase in ["clear my plan", "delete my plan", "reset my plan"]):
            return "clear"
        if any(phrase in lowered for phrase in ["complete step", "mark step", "finish step", "done with step"]):
            return "complete"
        if any(phrase in lowered for phrase in ["remove step", "delete step"]):
            return "remove"
        if any(phrase in lowered for phrase in ["add to my plan", "add a step", "add step"]):
            return "add"
        if "plan" in lowered or "checklist" in lowered:
            return "create"
        return ""

    def _extract_goal(self, context: ToolContext, text: str) -> str:
        if context.routed_query:
            return context.routed_query.strip()

        patterns = [
            r"^(?:make|create|build|write)\s+(?:me\s+)?a\s+plan\s+for\s+(.+)$",
            r"^(?:make|create|build|write)\s+(?:me\s+)?a\s+checklist\s+for\s+(.+)$",
            r"^(?:plan)\s+(.+)$",
        ]
        for pattern in patterns:
            match = re.search(pattern, text.strip(), flags=re.IGNORECASE)
            if match:
                return match.group(1).strip(" .!?")
        return ""

    def _extract_item_text(self, context: ToolContext, text: str) -> str:
        routed_item = ((context.routed_args or {}).get("item") or "").strip()
        if routed_item:
            return routed_item

        patterns = [
            r"^(?:add)\s+(.+?)\s+(?:to\s+my\s+plan)$",
            r"^(?:add)\s+step\s+(.+)$",
            r"^(?:add)\s+(.+)$",
        ]
        for pattern in patterns:
            match = re.search(pattern, text.strip(), flags=re.IGNORECASE)
            if match:
                return match.group(1).strip(" .!?")
        return ""

    def _find_item(self, state: PlanState, context: ToolContext, text: str) -> PlanItem | None:
        step_value = ((context.routed_args or {}).get("step") or "").strip()
        if not step_value:
            match = re.search(r"\bstep\s+(\d+)\b", text, flags=re.IGNORECASE)
            if match:
                step_value = match.group(1)

        if step_value.isdigit():
            target_id = int(step_value)
            for item in state.items:
                if item.id == target_id:
                    return item

        cleaned = text.lower()
        cleaned = re.sub(r"^(?:remove|delete|complete|finish|mark)\s+", "", cleaned).strip(" .!?")
        for item in state.items:
            if item.text.lower() == cleaned:
                return item
        for item in state.items:
            if cleaned and cleaned in item.text.lower():
                return item
        return None

    def _generate_steps(self, context: ToolContext, goal: str) -> list[str]:
        prompt = (
            "Create a short actionable plan and return JSON only.\n"
            'Schema: {"steps":["step 1","step 2","step 3"]}\n'
            "Rules:\n"
            "- 3 to 7 steps.\n"
            "- Each step must be brief, concrete, and imperative.\n"
            "- No markdown.\n"
            f"Goal: {goal}"
        )
        try:
            response = context.ollama_chat(
                [
                    {"role": "system", "content": "You output valid JSON only."},
                    {"role": "user", "content": prompt},
                ]
            )
            raw = context.normalize_memory_text(response["message"]["content"])
            data = self._parse_json(raw)
            steps = [str(step).strip() for step in data.get("steps") or [] if str(step).strip()]
            if steps:
                return steps[:7]
        except Exception as exc:
            print(f"[PLAN] Plan generation failed: {exc}")

        return [f"Define the goal details for {goal}", "Do the first concrete step", "Review progress and adjust"]

    def _parse_json(self, raw: str) -> dict:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
            if not match:
                raise
            return json.loads(match.group(0))

    def _format_state(self, state: PlanState, language: str) -> str:
        if language == "ru":
            header = f"Текущий план: {state.goal}." if state.goal else "Текущий план."
            lines = [
                f"Шаг {item.id}. {'Готово' if item.done else 'Не готово'}. {item.text}."
                for item in state.items
            ]
        else:
            header = f"Current plan: {state.goal}." if state.goal else "Current plan."
            lines = [
                f"Step {item.id}. {'Done' if item.done else 'Pending'}. {item.text}."
                for item in state.items
            ]
        return " ".join([header, *lines]).strip()

    def _summarize_plan_created(self, goal: str, steps: list[str], language: str) -> str:
        preview = " ".join([f"{index + 1}. {step}." for index, step in enumerate(steps[:3])])
        if language == "ru":
            return f"Составил план для {goal}. {preview}".strip()
        return f"Created a plan for {goal}. {preview}".strip()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
