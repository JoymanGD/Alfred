import os
from dataclasses import dataclass, replace

from alfred.execution_planner import refine_route_with_planning
from alfred.router import analyze_request
from alfred.runtime import build_reply_language_instruction
from alfred.tooling import ToolContext, ToolRegistry
from alfred.tools.codex import CodexTool, CodexToolConfig
from alfred.tools.image import ImageTool
from alfred.tools.local_files import LocalFilesTool, LocalFilesToolConfig
from alfred.tools.music import MusicTool, MusicToolConfig
from alfred.tools.plan import PlanTool, PlanToolConfig
from alfred.tools.screen import ScreenTool
from alfred.tools.system import SystemTool, SystemToolConfig
from alfred.tools.vision import VisionTool
from alfred.tools.web import WebTool, WebToolConfig
from alfred.vision import VisionConfig


@dataclass
class AgentConfig:
    base_dir: str
    codex_exe: str
    system_prompt: str
    ru_weather_words: list[str]
    ru_search_words: list[str]
    ru_fact_words: list[str]
    plan_storage_path: str
    vision_config: VisionConfig | None = None


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
        LocalFilesTool(
            LocalFilesToolConfig(
                base_dir=config.base_dir,
            )
        ),
        WebTool(
            WebToolConfig(
                ru_weather_words=config.ru_weather_words,
                ru_search_words=config.ru_search_words,
                ru_fact_words=config.ru_fact_words,
            )
        ),
        CodexTool(
            CodexToolConfig(
                codex_exe=config.codex_exe,
                project_dir=config.base_dir,
            )
        ),
        PlanTool(
            PlanToolConfig(
                storage_path=config.plan_storage_path,
            )
        ),
        ScreenTool(),
        VisionTool(),
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
    router_ollama_chat,
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
            recent_history=conversation_memory.recent_history(4),
            ollama_chat=ollama_chat,
            router_ollama_chat=router_ollama_chat,
            normalize_memory_text=normalize_memory_text,
            detect_language=detect_language,
            fetch_url=fetch_url,
            fetch_json=fetch_json,
            generate_image=generate_image_payload,
            analyze_vision=getattr(config, "analyze_vision", None),
            analyze_screen=getattr(config, "analyze_screen", None),
            restart_listener=getattr(config, "restart_listener", None),
            assistant_control=getattr(config, "assistant_control", None),
            event_out_queue=event_out,
        )
        route_decision = analyze_request(tool_context, user_text, tool_registry)
        route_decision = refine_route_with_planning(tool_context, user_text, route_decision)
        routed_context = replace(
            tool_context,
            routed_intent=route_decision.intent,
            routed_tool_name=route_decision.tool_name,
            routed_query=route_decision.normalized_query,
            routed_args=route_decision.args,
        )

        routed_tool = tool_registry.get_tool(route_decision.tool_name) if route_decision.tool_name else None
        if routed_tool is not None:
            print(
                f"[ROUTER] Dispatching routed tool: {route_decision.tool_name} "
                f"for intent={route_decision.intent}"
            )
            tool_result = routed_tool.handle(routed_context, user_text)
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
            reply_language_instruction = normalize_memory_text(
                f"{config.system_prompt}\n{build_reply_language_instruction(language)}"
            )
            messages = conversation_memory.build_messages(reply_language_instruction, user_text)
            response = ollama_chat(messages)
            raw_reply_text = response.get("message", {}).get("content", "")
            reply_text = normalize_memory_text(raw_reply_text)
            if not reply_text:
                print(f"[LLM] Empty reply from model for: {user_text}")
                reply_text = (
                    "Извини, у меня не получилось сформировать ответ. Попробуй переформулировать запрос."
                    if language == "ru"
                    else "Sorry, I couldn't form a reply. Please try rephrasing your request."
                )
        except Exception as e:
            reply_text = (
                f"\u041e\u0448\u0438\u0431\u043a\u0430 Ollama: {e}"
                if language == "ru"
                else f"Ollama error: {e}"
            )

        conversation_memory.append("assistant", reply_text)
        event_out.put(reply_text)
