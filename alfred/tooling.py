from dataclasses import dataclass
from typing import Any, Callable, Protocol


@dataclass
class ToolContext:
    user_text: str
    language: str
    system_prompt: str
    ollama_chat: Callable[[list[dict[str, str]]], Any]
    normalize_memory_text: Callable[[str], str]
    detect_language: Callable[[str], str]
    fetch_url: Callable[[str], str]
    fetch_json: Callable[..., dict[str, Any]]
    generate_image: Callable[[str], Any] | None = None
    analyze_vision: Callable[[str], str] | None = None
    analyze_screen: Callable[[str], str] | None = None
    restart_listener: Callable[[], None] | None = None
    event_out_queue: Any = None
    routed_intent: str | None = None
    routed_tool_name: str | None = None
    routed_query: str | None = None
    routed_args: dict[str, Any] | None = None


@dataclass
class ToolResult:
    handled: bool
    reply_text: str | None = None
    target: str | None = None
    image_payload: Any = None


class Tool(Protocol):
    name: str

    def matches(self, context: ToolContext, text: str) -> bool:
        ...

    def handle(self, context: ToolContext, text: str) -> ToolResult:
        ...


class ToolRegistry:
    def __init__(self, *tools: Tool):
        self.tools = list(tools)

    def first_matching_tool(self, context: ToolContext, text: str) -> Tool | None:
        for tool in self.tools:
            if tool.matches(context, text):
                return tool
        return None

    def get_tool(self, name: str) -> Tool | None:
        for tool in self.tools:
            if tool.name == name:
                return tool
        return None

    def dispatch(self, context: ToolContext, text: str) -> ToolResult:
        tool = self.first_matching_tool(context, text)
        if tool is not None:
            return tool.handle(context, text)
        return ToolResult(handled=False)
