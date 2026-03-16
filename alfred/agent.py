import os
from dataclasses import dataclass, replace

from alfred.router import analyze_request
from alfred.tooling import ToolContext, ToolRegistry
from alfred.tools.image import ImageTool
from alfred.tools.music import MusicTool, MusicToolConfig
from alfred.tools.system import SystemTool, SystemToolConfig
from alfred.tools.web import WebTool, WebToolConfig


@dataclass
class AgentConfig:
    system_prompt: str
    ru_weather_words: list[str]
    ru_search_words: list[str]
    ru_fact_words: list[str]


def build_tool_registry(config: AgentConfig) -> ToolRegistry:
    return ToolRegistry(
        SystemTool(
            SystemToolConfig(
                app_aliases={
                    "browser": "https://www.google.com",
                    "youtube": "https://www.youtube.com",
                    "google": "https://www.google.com",
                    "\u0431\u0440\u0430\u0443\u0437\u0435\u0440": "https://www.google.com",
                    "\u044e\u0442\u0443\u0431": "https://www.youtube.com",
                    "steam": r"C:\Program Files (x86)\Steam\Steam.exe",
                    "\u0441\u0442\u0438\u043c": r"C:\Program Files (x86)\Steam\Steam.exe",
                    "discord": os.path.join(os.environ.get("LOCALAPPDATA", ""), "Discord", "Update.exe"),
                    "telegram": os.path.join(os.environ.get("APPDATA", ""), "Telegram Desktop", "Telegram.exe"),
                    "\u0442\u0435\u043b\u0435\u0433\u0440\u0430\u043c": os.path.join(os.environ.get("APPDATA", ""), "Telegram Desktop", "Telegram.exe"),
                    "chrome": os.path.join(os.environ.get("ProgramFiles", ""), "Google", "Chrome", "Application", "chrome.exe"),
                    "\u0445\u0440\u043e\u043c": os.path.join(os.environ.get("ProgramFiles", ""), "Google", "Chrome", "Application", "chrome.exe"),
                    "vscode": os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Microsoft VS Code", "Code.exe"),
                    "vs code": os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Microsoft VS Code", "Code.exe"),
                    "visual studio code": os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Microsoft VS Code", "Code.exe"),
                    "obsidian": os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Obsidian", "Obsidian.exe"),
                    "notepad": "notepad.exe",
                    "\u0431\u043b\u043e\u043a\u043d\u043e\u0442": "notepad.exe",
                    "calculator": "calc.exe",
                    "\u043a\u0430\u043b\u044c\u043a\u0443\u043b\u044f\u0442\u043e\u0440": "calc.exe",
                    "explorer": "explorer.exe",
                    "\u043f\u0440\u043e\u0432\u043e\u0434\u043d\u0438\u043a": "explorer.exe",
                }
            )
        ),
        WebTool(
            WebToolConfig(
                ru_weather_words=config.ru_weather_words,
                ru_search_words=config.ru_search_words,
                ru_fact_words=config.ru_fact_words,
            )
        ),
        ImageTool(),
        MusicTool(MusicToolConfig()),
    )


def agent_loop(
    event_in,
    event_out,
    event_img,
    tool_registry: ToolRegistry,
    conversation_memory,
    config: AgentConfig,
    ollama_chat,
    normalize_memory_text,
    detect_language,
    fetch_url,
    fetch_json,
    generate_image_payload,
):
    print("[AGENT] Router started.")
    while True:
        user_text = event_in.get()
        if not user_text:
            language = "ru"
            reply_text = "\u0414\u0430?"
            conversation_memory.append("assistant", reply_text)
            event_out.put(reply_text)
            continue

        conversation_memory.append("user", user_text)
        language = detect_language(user_text)
        tool_context = ToolContext(
            user_text=user_text,
            language=language,
            system_prompt=config.system_prompt,
            ollama_chat=ollama_chat,
            normalize_memory_text=normalize_memory_text,
            detect_language=detect_language,
            fetch_url=fetch_url,
            fetch_json=fetch_json,
            generate_image=generate_image_payload,
        )
        route_decision = analyze_request(tool_context, user_text, tool_registry)
        routed_context = replace(
            tool_context,
            routed_intent=route_decision.intent,
            routed_tool_name=route_decision.tool_name,
            routed_query=route_decision.normalized_query,
            routed_args=route_decision.args,
        )

        routed_tool = tool_registry.get_tool(route_decision.tool_name) if route_decision.tool_name else None
        if routed_tool is not None and route_decision.intent != "chat":
            tool_result = routed_tool.handle(routed_context, user_text)
        else:
            matched_tool = tool_registry.first_matching_tool(tool_context, user_text)
            if matched_tool is not None:
                print(f"[ROUTER] Fast-path tool match: {matched_tool.name} for: {user_text}")
                tool_result = matched_tool.handle(tool_context, user_text)
            else:
                tool_result = None
        if tool_result is not None and tool_result.handled:
            if tool_result.image_payload is not None:
                event_img.put(tool_result.image_payload)
            reply_text = tool_result.reply_text or (
                "\u0413\u043e\u0442\u043e\u0432\u043e." if language == "ru" else "Done."
            )
            conversation_memory.append("assistant", reply_text)
            event_out.put(reply_text)
            continue

        try:
            messages = conversation_memory.build_messages(config.system_prompt, user_text)
            response = ollama_chat(messages)
            reply_text = normalize_memory_text(response["message"]["content"])
        except Exception as e:
            reply_text = (
                f"\u041e\u0448\u0438\u0431\u043a\u0430 Ollama: {e}"
                if language == "ru"
                else f"Ollama error: {e}"
            )

        conversation_memory.append("assistant", reply_text)
        event_out.put(reply_text)
