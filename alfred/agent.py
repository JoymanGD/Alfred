import os
import time
from dataclasses import dataclass, replace

from alfred.execution_planner import refine_route_with_planning
from alfred.computer_actions import ComputerActionRunner
from alfred.router import analyze_request
from alfred.runtime import build_reply_language_instruction
from alfred.tooling import ToolContext, ToolRegistry
from alfred.tools.codex import CodexTool, CodexToolConfig
from alfred.tools.image import ImageTool
from alfred.tools.local_files import LocalFilesTool, LocalFilesToolConfig
from alfred.tools.music import MusicTool, MusicToolConfig
from alfred.tools.plan import PlanTool, PlanToolConfig
from alfred.tools.screen import ScreenTool
from alfred.tools.system import SystemTool
from alfred.tools.vision import VisionTool
from alfred.tools.web import WebTool
from alfred.vane_search import VaneSearchClient
from alfred.vision import VisionConfig


@dataclass
class AgentConfig:
    base_dir: str
    codex_exe: str
    system_prompt: str
    plan_storage_path: str
    vane_url: str = ""
    vane_chat_model: str = "hf.co/HauhauCS/Gemma4-12B-QAT-Uncensored-HauhauCS-Balanced:Q4_K_M"
    vane_embedding_model: str = "nomic-embed-text:latest"
    vane_timeout: float = 30.0
    searxng_url: str = ""
    vision_config: VisionConfig | None = None
    ocr_engine: object | None = None


def build_tool_registry(config: AgentConfig) -> ToolRegistry:
    return ToolRegistry(
        SystemTool(),
        LocalFilesTool(
            LocalFilesToolConfig(
                base_dir=config.base_dir,
            )
        ),
        WebTool(
            VaneSearchClient(
                config.vane_url, config.vane_chat_model,
                config.vane_embedding_model, config.vane_timeout,
            ) if config.vane_url else None,
            searxng_url=config.searxng_url,
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
    decider,
    normalize_memory_text,
    detect_language,
    fetch_url,
    fetch_json,
    generate_image_payload,
):
    print("[AGENT] Router started.")
    computer_actions = ComputerActionRunner(tool_registry, config.base_dir, event_out_queue=event_out,
                                            ocr_engine=config.ocr_engine)
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
            recent_history=conversation_memory.recent_history(12),
            ollama_chat=ollama_chat,
            ollama_structured_chat=getattr(ollama_chat, "structured", None),
            decider=decider,
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
        if computer_actions.pending is not None:
            try:
                reply_text = computer_actions.resolve_confirmation(tool_context)
            except Exception as exc:
                computer_actions.pending = None
                reply_text = (f"Не удалось проверить подтверждение: {exc}" if language == "ru"
                              else f"Could not verify confirmation: {exc}")
            if reply_text != "new_request":
                conversation_memory.append("assistant", reply_text)
                event_out.put(reply_text)
                continue
        try:
            wants_computer_actions = computer_actions.wants_actions(tool_context)
        except Exception as exc:
            print(f"[ACTIONS] Planner gate failed: {exc}")
            wants_computer_actions = False
        if wants_computer_actions:
            try:
                reply_text = computer_actions.run(tool_context)
            except Exception as exc:
                reply_text = (f"Не удалось выполнить действие: {exc}" if language == "ru"
                              else f"Could not perform action: {exc}")
            conversation_memory.append("assistant", reply_text)
            event_out.put(reply_text)
            continue
        route_started_at = time.perf_counter()
        try:
            route_decision = analyze_request(tool_context, user_text, tool_registry)
        except RuntimeError as exc:
            reply_text = (
                f"Не удалось определить действие: {exc}"
                if language == "ru"
                else f"I could not determine the action: {exc}"
            )
            conversation_memory.append("assistant", reply_text)
            event_out.put(reply_text)
            continue
        route_elapsed = time.perf_counter() - route_started_at
        if route_decision.intent == "unsupported_physical_action":
            reply_text = (
                "Я не могу управлять предметами или освещением без подключённого устройства."
                if language == "ru" else
                "I can't control physical objects or room lighting without a connected device."
            )
            conversation_memory.append("assistant", reply_text)
            event_out.put(reply_text)
            continue
        if route_decision.intent in {"system_open", "browser_open", "local_files", "music_play", "music_control", "codex_request"}:
            reply_text = computer_actions.run(tool_context)
            conversation_memory.append("assistant", reply_text)
            event_out.put(reply_text)
            continue
        planning_started_at = time.perf_counter()
        route_decision = refine_route_with_planning(tool_context, user_text, route_decision)
        planning_elapsed = time.perf_counter() - planning_started_at
        print(
            f"[PERF] Routing={route_elapsed:.3f}s Planning={planning_elapsed:.3f}s "
            f"intent={route_decision.intent} tool={route_decision.tool_name}"
        )
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
            tool_started_at = time.perf_counter()
            tool_result = routed_tool.handle(routed_context, user_text)
            tool_elapsed = time.perf_counter() - tool_started_at
            print(f"[PERF] Tool {route_decision.tool_name} handled request in {tool_elapsed:.3f}s")
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
            llm_started_at = time.perf_counter()
            response = ollama_chat(messages)
            llm_elapsed = time.perf_counter() - llm_started_at
            print(f"[PERF] Main chat LLM replied in {llm_elapsed:.3f}s")
            if response.get("total_duration") is not None:
                print(
                    "[PERF] Ollama timings: "
                    f"load={(response.get('load_duration') or 0) / 1_000_000_000:.3f}s, "
                    f"prefill={(response.get('prompt_eval_duration') or 0) / 1_000_000_000:.3f}s, "
                    f"decode={(response.get('eval_duration') or 0) / 1_000_000_000:.3f}s, "
                    f"input_tokens={response.get('prompt_eval_count') or 0}, "
                    f"output_tokens={response.get('eval_count') or 0}"
                )
            raw_reply_text = response.get("message", {}).get("content", "")
            reply_text = normalize_memory_text(raw_reply_text)
            if not reply_text:
                print(f"[LLM] Empty reply from model for: {user_text}")
                reply_text = (
                    "Извини, у меня не получилось сформировать ответ. Попробуй переформулировать запрос."
                    if language == "ru"
                    else "Sorry, I couldn't form a reply. Please try rephrasing your request."
                )
            elif route_decision.intent == "chat":
                reply_text = _maybe_answer_from_web(tool_context, tool_registry, user_text, reply_text)
        except Exception as e:
            reply_text = (
                f"\u041e\u0448\u0438\u0431\u043a\u0430 Ollama: {e}"
                if language == "ru"
                else f"Ollama error: {e}"
            )

        conversation_memory.append("assistant", reply_text)
        event_out.put(reply_text)


def _maybe_answer_from_web(context: ToolContext, tool_registry: ToolRegistry, user_text: str, draft: str) -> str:
    try:
        decision = context.decider.choose(
            {
                "recent_conversation": context.recent_history or [],
                "user_message": user_text,
                "assistant_draft": draft,
            },
            "What should Alfred do with this draft? Consider the full conversation and the source needed to answer.",
            {
                "search": "The user asked about a named subject or requested facts, and the draft says it does not know, is uncertain, or deflects without answering. Find facts online before answering.",
                "vision": "The user asks what is physically visible near them right now, or the draft requests a photo for such a question. Alfred should inspect the camera directly.",
                "reply": "The user did not ask for factual information, or the draft already gives a useful factual answer. Questions about Alfred's abilities are reply.",
            },
        )
    except Exception as exc:
        print(f"[WEB] Automatic search decision failed: {exc}")
        return draft
    if decision == "vision":
        if context.analyze_vision is None:
            return "Я не могу сейчас посмотреть через камеру." if context.language == "ru" else "I cannot access the camera right now."
        print("[VISION] Chat answer requires camera; inspecting now.")
        return context.analyze_vision(user_text)
    if decision != "search":
        return draft
    failure_reply = (
        "Я не смог проверить это в интернете. Можете дать ссылку на источник или уточнить, что именно вас интересует?"
        if context.language == "ru"
        else "I could not verify this online. Can you share a source or clarify what you want to know?"
    )
    web_tool = tool_registry.get_tool("web")
    if not isinstance(web_tool, WebTool):
        return failure_reply
    print("[WEB] Chat answer needs verification; searching before reply.")
    try:
        return web_tool.search_and_answer(context, user_text, automatic=True) or failure_reply
    except Exception as exc:
        print(f"[WEB] Automatic search failed: {exc}")
        return failure_reply
