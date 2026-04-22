from alfred.tooling import Tool, ToolContext, ToolResult


class ScreenTool(Tool):
    name = "screen"

    def matches(self, context: ToolContext, text: str) -> bool:
        return False

    def handle(self, context: ToolContext, text: str) -> ToolResult:
        language = context.language or context.detect_language(text)
        if context.analyze_screen is None:
            reply_text = (
                "\u042f \u043d\u0435 \u043c\u043e\u0433\u0443 \u0441\u0435\u0439\u0447\u0430\u0441 "
                "\u043f\u043e\u0441\u043c\u043e\u0442\u0440\u0435\u0442\u044c \u043d\u0430 \u044d\u043a\u0440\u0430\u043d."
                if language == "ru"
                else "I can't look at the screen right now."
            )
            return ToolResult(handled=True, reply_text=reply_text)

        reply_text = context.analyze_screen(text)
        if context.restart_listener is not None:
            context.restart_listener()
        return ToolResult(handled=True, reply_text=reply_text)
