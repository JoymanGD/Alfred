from alfred.tooling import Tool, ToolContext, ToolResult


class VisionTool(Tool):
    name = "vision"

    def matches(self, context: ToolContext, text: str) -> bool:
        return False

    def handle(self, context: ToolContext, text: str) -> ToolResult:
        try:
            action = context.decider.choose(
                {"recent_conversation": context.recent_history, "user_message": text},
                "Does the user request a fresh inspection of the camera scene, or are they only correcting or describing what is there? Consider the conversation, not isolated words.",
                {
                    "inspect": "The user wants Alfred to look through the camera now or answer a visual question that requires a new frame.",
                    "feedback": "The user is correcting Alfred's previous visual description or telling Alfred what is present, without asking for another look. Respond conversationally and acknowledge the correction.",
                },
            )
        except Exception as exc:
            print(f"[VISION] Inspection decision failed: {exc}")
            action = "feedback"
        if action == "feedback":
            print("[VISION] User supplied visual feedback; no new frame requested.")
            return ToolResult(handled=False)

        language = context.language or context.detect_language(text)
        if context.analyze_vision is None:
            reply_text = (
                "\u042f \u043d\u0435 \u043c\u043e\u0433\u0443 \u0441\u0435\u0439\u0447\u0430\u0441 "
                "\u043f\u043e\u0441\u043c\u043e\u0442\u0440\u0435\u0442\u044c \u0447\u0435\u0440\u0435\u0437 \u043a\u0430\u043c\u0435\u0440\u0443."
                if language == "ru"
                else "I can't look through the camera right now."
            )
            return ToolResult(handled=True, reply_text=reply_text)

        reply_text = context.analyze_vision(text)
        return ToolResult(handled=True, reply_text=reply_text)
