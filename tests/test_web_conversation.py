import json
import queue
import unittest
import webbrowser
from types import SimpleNamespace
from unittest.mock import patch

from alfred.agent import _maybe_answer_from_web
from alfred.conversation import ConversationMemory
from alfred.router import analyze_request
from alfred.runtime import detect_language
from alfred.tooling import AssistantOutput, ToolRegistry
from alfred.tools.web import AnswerAttempt, SearchPlan, WebTool, extract_html_sections, find_source_quote, strip_html
from alfred.vane_search import VaneAnswer, VaneSearchClient


class FakeDecider:
    def __init__(self, *choices):
        self.choices = iter(choices)

    def choose(self, subject, question, criteria, **kwargs):
        choice = next(self.choices)
        assert choice in criteria
        return choice


def context(decider, responses=()):
    replies = iter(responses)
    ctx = SimpleNamespace(
        user_text="ты можешь искать в интернете, если у тебя нет инфы по этому поводу?",
        language="ru",
        system_prompt="Answer accurately.",
        recent_history=[
            {"role": "user", "content": "смотрю сериал Lanterns. знаешь такой?"},
            {"role": "assistant", "content": "Я не уверен, что знаю этот сериал."},
            {"role": "user", "content": "это сериал 2026 года про зеленых фонарей"},
        ],
        decider=decider,
        ollama_chat=lambda messages: {"message": {"content": next(replies)}},
        normalize_memory_text=lambda value: value,
        fetch_url=lambda url, **kwargs: "",
        detect_language=lambda value: "ru",
        routed_intent="web_search",
        routed_tool_name="web",
        routed_query=None,
    )
    ctx.ollama_structured_chat = lambda messages: (
        {"message": {"content": json.dumps({"operation": "none"})}}
        if "Translate the exact question into a query over individually numbered" in messages[0]["content"]
        else ctx.ollama_chat(messages)
    )
    return ctx


class WebConversationTests(unittest.TestCase):
    def test_russian_search_announcement_is_spoken_once_across_repeated_web_steps(self):
        request = "Поищи первые три хода для лондонского дебюта в шахматах"
        self.assertEqual(detect_language(request), "ru")
        web = WebTool()
        ctx = context(FakeDecider())
        ctx.user_text = request
        ctx.language = detect_language(request)
        ctx.event_out_queue = queue.Queue()
        with (patch.object(web, "_plan_search", return_value=SearchPlan("лондонский дебют")),
              patch.object(web, "_search_web", return_value=[]),
              patch.object(web, "_fetch_page_evidence", return_value=""),
              patch.object(web, "_answer_from_web_results", return_value=AnswerAttempt()),
              patch.object(web, "_clarify_search_failure", return_value="Не удалось проверить.")):
            web.search_and_answer(ctx, request)
            web.search_and_answer(ctx, request)
        self.assertEqual(ctx.event_out_queue.qsize(), 1)
        self.assertEqual(ctx.event_out_queue.get_nowait().text, "Сейчас проверю информацию в интернете.")

    def test_vane_uses_local_ollama_models_and_sourced_answer(self):
        client = VaneSearchClient("http://127.0.0.1:3000", "gemma4:12b", "nomic-embed-text")
        requests = []
        def fake_request(path, payload=None, timeout=None):
            requests.append((path, payload))
            if path == "/api/providers":
                return {"providers": [{
                    "id": "local", "name": "Ollama",
                    "chatModels": [{"key": "gemma4:12b"}],
                    "embeddingModels": [{"key": "nomic-embed-text"}],
                }]}
            return {
                "message": "The last event is on October 4, 2026. [1]",
                "sources": [{
                    "content": "Event 8 is on October 4, 2026.",
                    "metadata": {"url": "https://example.org/schedule"},
                }],
            }
        client._request = fake_request
        answer = client.search("When is the last event?", ["Tell me about this event"], "en")
        self.assertEqual(answer.text, "The last event is on October 4, 2026.")
        self.assertEqual(answer.sources, ("https://example.org/schedule",))
        self.assertEqual(requests[1][1]["chatModel"], {"providerId": "local", "key": "gemma4:12b"})
        self.assertEqual(requests[1][1]["embeddingModel"], {"providerId": "local", "key": "nomic-embed-text"})

    def test_vane_answer_is_checked_before_use(self):
        answer = VaneAnswer(
            "The last event is on October 4, 2026.",
            ("https://example.org/schedule",),
            ("Source: https://example.org/schedule\nEvent 8 is on October 4, 2026.",),
        )
        client = SimpleNamespace(search=lambda *args: answer)
        web = WebTool(client)
        ctx = context(FakeDecider(), (
            json.dumps({"query": "last event date", "evidence_needed": "Date", "answer_kind": "date_time"}),
            json.dumps({"supported": True}),
        ))
        with patch.object(web, "_search_web") as old_search:
            result = web.search_and_answer(ctx, "When is the last event?")
        self.assertEqual(result, answer.text)
        old_search.assert_not_called()

    def test_local_searxng_results_feed_existing_source_reader(self):
        web = WebTool(searxng_url="http://127.0.0.1:8080")
        ctx = context(FakeDecider())
        ctx.fetch_json = lambda url, **kwargs: {
            "results": [{
                "title": "Event schedule",
                "url": "https://example.org/schedule",
                "content": "The final event is on October 4, 2026.",
            }]
        }
        with patch.object(web, "_parse_duckduckgo_results") as direct_parser:
            results = web._search_web(ctx, "final event date", region="us-en")
        self.assertEqual(results[0]["url"], "https://example.org/schedule")
        self.assertIn("October 4", results[0]["snippet"])
        direct_parser.assert_not_called()

    def test_capability_question_does_not_open_browser(self):
        for mistaken_intent in ("browser_open", "web_search", "fact"):
            with self.subTest(mistaken_intent=mistaken_intent):
                ctx = context(FakeDecider(mistaken_intent, "discuss", "ask", "local"))
                decision = analyze_request(ctx, ctx.user_text, ToolRegistry())
                self.assertEqual(decision.intent, "chat")
                self.assertIsNone(decision.tool_name)

    def test_factual_followup_is_grounded_before_chat_answer(self):
        ctx = context(FakeDecider("chat", "ask", "web"))
        ctx.recent_history = [
            {"role": "user", "content": "смотрю сериал Lanterns, слышал о нём?"},
            {"role": "assistant", "content": "Это история про выживание в космосе."},
        ]
        decision = analyze_request(ctx, "что ты о нём слышал?", ToolRegistry())
        self.assertEqual(decision.intent, "fact")
        self.assertEqual(decision.tool_name, "web")

    def test_current_physical_scene_routes_to_camera_after_uncertain_primary_route(self):
        ctx = context(FakeDecider("chat", "ask", "vision"))
        decision = analyze_request(ctx, "Что у меня в руке?", ToolRegistry())
        self.assertEqual(decision.intent, "vision")
        self.assertEqual(decision.tool_name, "vision")

    def test_direct_physical_command_is_not_sent_to_chat_model(self):
        ctx = context(FakeDecider("unsupported_physical_action", "execute_now"))
        decision = analyze_request(ctx, "Bring me a cup", ToolRegistry())
        self.assertEqual(decision.intent, "unsupported_physical_action")
        self.assertIsNone(decision.tool_name)

    def test_question_about_physical_task_is_still_a_question(self):
        ctx = context(FakeDecider("unsupported_physical_action", "discuss", "ask", "local"))
        decision = analyze_request(ctx, "How can I carry this?", ToolRegistry())
        self.assertEqual(decision.intent, "chat")

    def test_chat_recovery_uses_camera_instead_of_search(self):
        ctx = context(FakeDecider("vision"))
        inspected = []
        ctx.analyze_vision = lambda text: inspected.append(text) or "Вижу красную кружку."
        web = WebTool()
        with patch.object(web, "search_and_answer") as search:
            answer = _maybe_answer_from_web(
                ctx, ToolRegistry(web), "Что у меня в руке?", "Пришлите картинку."
            )
        self.assertEqual(answer, "Вижу красную кружку.")
        self.assertEqual(inspected, ["Что у меня в руке?"])
        search.assert_not_called()

    def test_search_query_uses_user_history_without_fabricated_assistant_claims(self):
        web = WebTool()
        ctx = context(FakeDecider())
        ctx.recent_history = [
            {"role": "user", "content": "смотрю сериал Lanterns, слышал о нём?"},
            {"role": "assistant", "content": "Это история про выживание в космосе."},
            {"role": "user", "content": "какого года сериал?"},
        ]
        prompts = []
        def answer_query(messages):
            prompts.append(messages[-1]["content"])
            return {"message": {"content": json.dumps({"query": "Lanterns TV series release year"})}}
        ctx.ollama_chat = answer_query
        self.assertEqual(web._normalize_search_query(ctx, "какого года сериал?"), "Lanterns TV series release year")
        self.assertIn("Lanterns", prompts[0])
        self.assertNotIn("выживание в космосе", prompts[0])

    def test_chat_history_does_not_duplicate_latest_user_message(self):
        memory = ConversationMemory(12, lambda text: text.strip())
        memory.append("user", "какого года сериал?")
        messages = memory.build_messages("system", "какого года сериал?")
        self.assertEqual([message["role"] for message in messages], ["system", "user"])

    def test_explicit_search_answers_in_chat(self):
        web = WebTool()
        ctx = context(
            FakeDecider("supported"),
            (json.dumps({"query": "Lanterns 2026 Green Lantern series"}), json.dumps({
                "answer": "Это сериал о Зелёных Фонарях.",
                "quotes": ["Green Lantern television series"], "followup_query": "",
            }), json.dumps({"supported": True})),
        )
        ctx.fetch_url = lambda url, **kwargs: "<html><body>" + "Green Lantern television series. " * 8 + "</body></html>"
        with patch.object(web, "_search_web", return_value=[
            {"title": "Lanterns", "url": "https://example.org/lanterns", "snippet": "2026 Green Lantern television series"}
        ]) as search, patch.object(webbrowser, "open") as browser:
            answer = web.handle(ctx, "найди информацию о Lanterns")
        self.assertIn("Зелёных Фонарях", answer.reply_text)
        self.assertEqual(search.call_args.args[1], "Lanterns 2026 Green Lantern series")
        browser.assert_not_called()

    def test_uncertain_chat_answer_triggers_contextual_search(self):
        web = WebTool()
        registry = ToolRegistry(web)
        ctx = context(
            FakeDecider("search", "supported"),
            (json.dumps({"query": "Lanterns 2026 Green Lantern series"}), json.dumps({
                "answer": "Я нашёл сведения о сериале Lanterns.",
                "quotes": ["Green Lantern series"], "followup_query": "",
            }), json.dumps({"supported": True})),
        )
        ctx.fetch_url = lambda url, **kwargs: "<html><body>" + "Green Lantern series. " * 12 + "</body></html>"
        ctx.event_out_queue = queue.Queue()
        announcements = []
        def search_after_announcement(*args, **kwargs):
            announcements.append(ctx.event_out_queue.get_nowait())
            return [{"title": "Lanterns", "url": "https://example.org/lanterns", "snippet": "Green Lantern series"}]
        with patch.object(web, "_search_web", side_effect=search_after_announcement) as search, patch.object(webbrowser, "open") as browser:
            answer = _maybe_answer_from_web(ctx, registry, "смотрю сериал Lanterns. знаешь такой?", "Не уверен, что знаю его.")
        self.assertIn("нашёл сведения", answer)
        self.assertEqual(search.call_args.args[1], "Lanterns 2026 Green Lantern series")
        self.assertIsInstance(announcements[0], AssistantOutput)
        self.assertTrue(announcements[0].interim)
        self.assertIn("проверю информацию в интернете", announcements[0].text)
        browser.assert_not_called()

    def test_search_parser_keeps_snippet_after_nested_divs(self):
        html = (
            '<div class="result results_links web-result">'
            '<div class="result__body"><a class="result__a" href="https://example.org/lanterns">Lanterns</a>'
            '<div class="result__extras"><div class="result__url">example.org</div></div>'
            '<a class="result__snippet">Hal Jordan &amp; John Stewart investigate a murder.</a></div></div>'
        )
        results = WebTool()._parse_duckduckgo_html_results(context(FakeDecider()), html)
        self.assertEqual(results[0]["snippet"], "Hal Jordan & John Stewart investigate a murder.")

    def test_html_text_excludes_attribute_metadata(self):
        html = '<main><span data-mw="planned total 99 > hidden">Visible text</span><script>wrong count 99</script></main>'
        self.assertEqual(strip_html(html, lambda value: " ".join(value.split())), "Visible text")

    def test_article_sections_keep_detailed_evidence_separate_from_summary(self):
        html = (
            "<main><h1>Project Borealis</h1><p>Eight milestones planned.</p>"
            "<h2>Progress</h2><p>Seven milestones completed by October 1; "
            "the eighth is scheduled for October 4.</p></main>"
        )
        sections = extract_html_sections(html, lambda value: " ".join(value.split()))
        self.assertEqual(sections[0][0], "Project Borealis")
        self.assertEqual(sections[1][0], "Progress")
        self.assertIn("Seven milestones completed", sections[1][1])
        self.assertNotIn("Seven milestones completed", sections[0][1])

    def test_ellipsis_quote_recovers_exact_contiguous_source_span(self):
        source = "Seven milestones completed by October 1; the eighth is scheduled for October 4."
        match = find_source_quote(
            source, "Seven milestones completed ... eighth is scheduled for October 4",
            lambda value: " ".join(value.split()),
        )
        self.assertIn("by October 1; the", match)

    def test_current_released_count_reads_dated_source_page(self):
        web = WebTool()
        prompts = []
        ctx = context(FakeDecider("supported"))
        ctx.recent_history = [
            {"role": "user", "content": "смотрю сериал Lanterns"},
            {"role": "user", "content": "сколько всего серий там?"},
            {"role": "user", "content": "сколько уже вышло?"},
        ]
        def answer(messages):
            prompts.append(messages[-1]["content"])
            if len(prompts) == 1:
                return {"message": {"content": json.dumps({
                    "query": "Lanterns episodes released", "as_of_today": True
                })}}
            if len(prompts) == 3:
                return {"message": {"content": json.dumps({"supported": True})}}
            return {"message": {"content": json.dumps({
                "answer": "Уже вышло семь серий.",
                "quotes": ["Seven episodes had aired by October 1, 2026"],
                "followup_query": "",
            })}}
        ctx.ollama_chat = answer
        ctx.fetch_url = lambda url, **kwargs: (
            "<html><body>Lanterns episode schedule: "
            "Episode 1 August 16, 2026; Episode 7 September 27, 2026; "
            "Episode 8 October 4, 2026. Seven episodes had aired by October 1, 2026. This page gives the first season release calendar."
            " The page lists each episode with its planned date so viewers can distinguish aired episodes from upcoming episodes."
            "</body></html>"
        )
        with patch.object(web, "_search_web", return_value=[
            {"title": "Lanterns release schedule", "url": "https://example.org/schedule", "snippet": "Eight episodes total"}
        ]) as search:
            reply = web.search_and_answer(ctx, "сколько уже вышло?")
        self.assertIn("семь", reply)
        self.assertIn("Episode 7 September 27, 2026", prompts[1])
        self.assertIn("Episode 8 October 4, 2026", prompts[1])
        self.assertEqual(len(prompts), 3)
        self.assertEqual(search.call_args.args[1], "Lanterns episodes released")

    def test_source_reader_skips_blocked_page_and_uses_next_result(self):
        ctx = context(FakeDecider())
        def fetch(url, **kwargs):
            if url.endswith("blocked"):
                return "JavaScript is disabled. Verify that you are not a robot."
            return "<html><body>" + "Project Borealis milestone log. " * 8 + "Seven milestones completed by September 29; milestone eight planned for October 4.</body></html>"
        ctx.fetch_url = fetch
        evidence = WebTool()._fetch_page_evidence(ctx, [
            {"url": "https://example.org/blocked"},
            {"url": "https://example.org/progress"},
        ])
        self.assertIn("Seven milestones completed", evidence)
        self.assertNotIn("robot", evidence)

    def test_source_reader_selects_relevant_later_passage(self):
        ctx = context(FakeDecider("passage_3"))
        ctx.ollama_chat = lambda messages: {"message": {"content": json.dumps({"index": 3})}}
        ctx.fetch_url = lambda url, **kwargs: (
            "<html><body>" + "Background context. " * 320
            + "Seven milestones were completed by October 1, 2026. "
            + "The eighth milestone is scheduled for October 4, 2026. "
            + "</body></html>"
        )
        evidence = WebTool()._fetch_page_evidence(
            ctx, [{"url": "https://example.org/progress"}],
            question="How many milestones are complete?",
        )
        self.assertIn("Seven milestones were completed", evidence)
        self.assertIn("eighth milestone is scheduled", evidence)

    def test_web_json_calls_use_structured_ollama(self):
        web = WebTool()
        ctx = context(FakeDecider())
        ctx.ollama_chat = lambda messages: self.fail("Plain chat was used for JSON extraction")
        ctx.ollama_structured_chat = lambda messages: {
            "message": {"content": json.dumps({"query": "Project Borealis overview"})}
        }
        self.assertEqual(
            web._plan_search(ctx, "Tell me about Project Borealis").query,
            "Project Borealis overview",
        )

    def test_related_fact_is_rejected_and_search_is_refined(self):
        web = WebTool()
        ctx = context(FakeDecider("same"))
        replies = iter([
            json.dumps({"query": "Project Borealis milestones", "evidence_needed": "Completed milestones"}),
            json.dumps({"answer": "Twelve milestones are planned.", "quotes": ["Twelve milestones are planned"],
                        "followup_query": "Project Borealis completed milestone log"}),
            json.dumps({"supported": False}),
            json.dumps({"answer": "", "quotes": [],
                        "followup_query": "Project Borealis completed milestone log"}),
            json.dumps({"answer": "Seven milestones are complete.", "quotes": ["Seven milestones are complete"],
                        "followup_query": "Project Borealis progress report"}),
            json.dumps({"supported": True}),
        ])
        ctx.ollama_chat = lambda messages: {"message": {"content": next(replies)}}
        ctx.fetch_url = lambda url, **kwargs: (
            "<html><body>" + ("Seven milestones are complete. " if url.endswith("progress")
                              else "Twelve milestones are planned. ") * 9 + "</body></html>"
        )
        with patch.object(web, "_search_web", side_effect=[
            [{"title": "Plan", "url": "https://example.org/plan", "snippet": "Twelve milestones are planned"}],
            [{"title": "Progress", "url": "https://example.org/progress", "snippet": "Seven milestones are complete"}],
        ]) as search:
            answer = web.search_and_answer(ctx, "How many milestones are complete?")
        self.assertEqual(answer, "Seven milestones are complete.")
        self.assertEqual(search.call_args_list[1].args[1], "Project Borealis completed milestone log")

    def test_empty_model_output_retries_same_evidence_without_topic_drift(self):
        web = WebTool()
        ctx = context(FakeDecider("supported"), (
            json.dumps({"query": "Project Borealis overview", "backup_query": "unrelated streaming site"}),
            "",
            json.dumps({
                "answer": "Project Borealis is a research mission.",
                "quotes": ["Project Borealis is a research mission"],
                "followup_query": "",
            }),
            json.dumps({"supported": True}),
        ))
        ctx.fetch_url = lambda url, **kwargs: "<html><body>" + "Project Borealis is a research mission. " * 8 + "</body></html>"
        with patch.object(web, "_search_web", return_value=[{
            "title": "Project Borealis",
            "url": "https://example.org/borealis",
            "snippet": "Project Borealis is a research mission",
        }]) as search:
            answer = web.search_and_answer(ctx, "Have you heard of Project Borealis?")
        self.assertEqual(answer, "Project Borealis is a research mission.")
        search.assert_called_once()

    def test_search_refinement_that_changes_topic_is_rejected(self):
        web = WebTool()
        ctx = context(FakeDecider("drift"), (
            json.dumps({"query": "Project Borealis overview"}),
            json.dumps({"answer": "", "quotes": [], "followup_query": "unrelated streaming site"}),
        ))
        with patch.object(web, "_search_web", return_value=[{
            "title": "Project Borealis",
            "url": "https://example.org/borealis",
            "snippet": "General project summary",
        }]) as search:
            answer = web.search_and_answer(ctx, "Have you heard of Project Borealis?")
        self.assertTrue(answer.endswith("?"))
        search.assert_called_once()

    def test_total_count_does_not_answer_current_count(self):
        web = WebTool()
        ctx = context(FakeDecider("unsupported"))
        replies = iter([
            json.dumps({"query": "Lanterns episodes", "evidence_needed": "Already released episodes"}),
            json.dumps({"answer": "Вышло восемь серий.", "quotes": ["Число серий 8"], "followup_query": ""}),
            json.dumps({"supported": False}),
            json.dumps({"answer": "", "quotes": [], "followup_query": ""}),
        ])
        ctx.ollama_chat = lambda messages: {"message": {"content": next(replies)}}
        ctx.fetch_url = lambda url, **kwargs: "<html><body>" + "Число серий 8. " * 20 + "</body></html>"
        with patch.object(web, "_search_web", return_value=[
            {"title": "Lanterns", "url": "https://example.org/series", "snippet": "Число серий 8"}
        ]):
            reply = web.search_and_answer(ctx, "а сколько вышло уже серий?")
        self.assertNotIn("восемь", reply)
        self.assertTrue(reply.endswith("?"))

    def test_rejected_related_fact_is_corrected_from_same_source(self):
        web = WebTool()
        replies = iter([
            json.dumps({"query": "Project Borealis progress", "evidence_needed": "Completed milestones"}),
            json.dumps({"answer": "Eight milestones are planned.",
                        "quotes": ["Eight milestones are planned"], "followup_query": ""}),
            json.dumps({"supported": False, "reason": "Planned total does not answer completed count."}),
            json.dumps({"answer": "Seven milestones are complete.",
                        "quotes": ["Seven milestones are complete"], "followup_query": ""}),
            json.dumps({"supported": True}),
        ])
        ctx = context(FakeDecider())
        ctx.ollama_chat = lambda messages: {"message": {"content": next(replies)}}
        ctx.fetch_url = lambda url, **kwargs: (
            "<html><body>" + "Eight milestones are planned. Seven milestones are complete. " * 5 + "</body></html>"
        )
        with patch.object(web, "_search_web", return_value=[{
            "title": "Project progress", "url": "https://example.org/progress", "snippet": "Eight planned"
        }]) as search:
            answer = web.search_and_answer(ctx, "How many milestones are complete?")
        self.assertEqual(answer, "Seven milestones are complete.")
        search.assert_called_once()

    def test_stale_search_snippet_cannot_support_current_answer(self):
        web = WebTool()
        ctx = context(FakeDecider(), (
            json.dumps({
                "answer": "Six milestones are complete.",
                "quotes": ["Six milestones are complete"],
                "followup_query": "",
            }),
            json.dumps({"answer": "", "quotes": [], "followup_query": ""}),
        ))
        result = web._answer_from_web_results(
            ctx, "How many milestones are complete now?",
            [{"title": "Old snapshot", "url": "https://example.org/old", "snippet": "Six milestones are complete"}],
            query="Project Borealis progress",
            page_evidence="Source: https://example.org/current\nPage text: Seven milestones were complete by October 1, 2026.",
        )
        self.assertEqual(result.answer, "")

    def test_reader_keeps_one_source_per_answer_attempt(self):
        web = WebTool()
        ctx = context(FakeDecider())
        ctx.fetch_url = lambda url, **kwargs: (
            "<html><body>" + ("First source. " if url.endswith("one") else "Second source. ") * 20 + "</body></html>"
        )
        evidence = web._fetch_page_evidence(ctx, [
            {"url": "https://example.org/one"},
            {"url": "https://example.org/two"},
        ])
        self.assertIn("First source", evidence)
        self.assertNotIn("Second source", evidence)

    def test_unanswered_first_source_tries_next_page(self):
        web = WebTool()
        ctx = context(FakeDecider(), (json.dumps({"query": "Project Borealis progress"}),))
        ctx.fetch_url = lambda url, **kwargs: (
            "<html><body>" + ("First source. " if url.endswith("one") else "Second source. ") * 20 + "</body></html>"
        )
        with patch.object(web, "_search_web", return_value=[
            {"title": "First", "url": "https://example.org/one", "snippet": ""},
            {"title": "Second", "url": "https://example.org/two", "snippet": ""},
        ]) as search, patch.object(web, "_answer_from_web_results", side_effect=[
            AnswerAttempt(), AnswerAttempt("Seven milestones are complete.")
        ]) as answer_from_sources:
            answer = web.search_and_answer(ctx, "How many milestones are complete?")
        self.assertEqual(answer, "Seven milestones are complete.")
        search.assert_called_once()
        self.assertIn("Second source", answer_from_sources.call_args_list[1].kwargs["page_evidence"])

    def test_general_dated_record_count_uses_verified_source_dates(self):
        web = WebTool()
        ctx = context(FakeDecider())
        replies = iter([
            {"operation": "count", "where": [{"field": "date", "op": "lte", "value": "2026-10-01"}]},
            {"rows": [
                {"id": "1", "date_iso": "2026-09-01", "date_quote": "September 1, 2026"},
                {"id": "2", "date_iso": "2026-09-27", "date_quote": "September 27, 2026"},
                {"id": "3", "date_iso": "2026-10-04", "date_quote": "October 4, 2026"},
            ]},
        ])
        ctx.ollama_structured_chat = lambda messages: {
            "message": {"content": json.dumps(next(replies))}
        }
        evidence = (
            "Source: https://example.org/events\nPage text: "
            "1 September 1, 2026. 2 September 27, 2026. 3 October 4, 2026."
        )
        answer = web._query_source_records(
            ctx, "How many milestones have happened already?", evidence, "Completed milestones",
        )
        self.assertIn("2 из 3", answer)

    def test_source_record_lookup_returns_requested_date(self):
        web = WebTool()
        ctx = context(FakeDecider())
        replies = iter([
            {"operation": "select", "where": [{"field": "ordinal", "op": "eq", "value": "3"}], "select": "date"},
            {"rows": [
                {"id": "1", "date_iso": "2026-09-01", "date_quote": "September 1, 2026"},
                {"id": "2", "date_iso": "2026-09-27", "date_quote": "September 27, 2026"},
                {"id": "3", "date_iso": "2026-10-04", "date_quote": "October 4, 2026"},
            ]},
        ])
        ctx.ollama_structured_chat = lambda messages: {
            "message": {"content": json.dumps(next(replies))}
        }
        evidence = (
            "Source: https://example.org/events\nPage text: "
            "1 September 1, 2026. 2 September 27, 2026. 3 October 4, 2026."
        )
        answer = web._query_source_records(ctx, "When is item 3?", evidence, "Date of item 3")
        self.assertIn("2026-10-04", answer)
        self.assertNotIn("3 of 3", answer)

    def test_relative_last_item_selects_last_verified_row(self):
        web = WebTool()
        ctx = context(FakeDecider())
        replies = iter([
            {"operation": "select", "where": [], "select": "date",
             "order_by": "ordinal", "order": "desc", "limit": 1},
            {"rows": [
                {"id": "1", "date_iso": "2026-09-01", "date_quote": "September 1, 2026"},
                {"id": "2", "date_iso": "2026-09-27", "date_quote": "September 27, 2026"},
                {"id": "3", "date_iso": "2026-10-04", "date_quote": "October 4, 2026"},
            ]},
        ])
        ctx.ollama_structured_chat = lambda messages: {
            "message": {"content": json.dumps(next(replies))}
        }
        evidence = (
            "Source: https://example.org/events\nPage text: "
            "1 September 1, 2026. 2 September 27, 2026. 3 October 4, 2026."
        )
        answer = web._query_source_records(ctx, "When is the last item?", evidence, "Last item's date")
        self.assertIn("2026-10-04", answer)

    def test_missing_date_on_one_page_does_not_prove_date_unavailable(self):
        web = WebTool()
        ctx = context(FakeDecider(), (
            json.dumps({"answer": "The final date is unavailable.",
                        "quotes": ["There are 8 episodes in total"], "followup_query": ""}),
            json.dumps({"answer": "", "quotes": [], "followup_query": ""}),
        ))
        source = "Source: https://example.org/series\nPage text: There are 8 episodes in total."
        with patch.object(web, "_query_source_records", return_value=None):
            attempt = web._answer_from_web_results(
                ctx, "When is the final episode?", [{"url": "https://example.org/series"}],
                page_evidence=source, answer_kind="date_time",
            )
        self.assertEqual(attempt.answer, "")

    def test_date_question_never_uses_dated_count(self):
        web = WebTool()
        ctx = context(FakeDecider(), (
            json.dumps({
                "answer": "The next item is scheduled for October 4, 2026.",
                "quotes": ["The next item is scheduled for October 4, 2026."],
                "followup_query": "",
            }),
            json.dumps({"supported": True}),
        ))
        source = (
            "Source: https://example.org/schedule\n"
            "Page text: The next item is scheduled for October 4, 2026."
        )
        with patch.object(web, "_query_source_records", return_value=None) as calculator:
            answer = web._answer_from_web_results(
                ctx, "When is the next item?", [{
                    "title": "Schedule", "url": "https://example.org/schedule", "snippet": "",
                }], page_evidence=source, answer_kind="date_time",
            )
        self.assertEqual(answer.answer, "The next item is scheduled for October 4, 2026.")
        calculator.assert_called_once()

    def test_search_plan_preserves_requested_answer_kind(self):
        web = WebTool()
        ctx = context(FakeDecider(), (json.dumps({
            "query": "Project Borealis next milestone date",
            "evidence_needed": "Date of the next milestone",
            "answer_kind": "date_time",
        }),))
        plan = web._plan_search(ctx, "When is the next milestone?")
        self.assertEqual(plan.answer_kind, "date_time")

    def test_failed_source_calculation_does_not_guess_from_total(self):
        web = WebTool()
        ctx = context(FakeDecider())
        replies = iter([
            {"operation": "count", "where": [{"field": "date", "op": "lte", "value": "2026-10-01"}]},
            {"rows": []},
        ])
        ctx.ollama_structured_chat = lambda messages: {
            "message": {"content": json.dumps(next(replies))}
        }
        ctx.ollama_chat = lambda messages: self.fail("Unverified chat answer was requested")
        attempt = web._answer_from_web_results(
            ctx, "How many items have happened already?",
            [{"title": "Project status", "url": "https://example.org/status", "snippet": "Eight items total"}],
            page_evidence="Source: https://example.org/status\nPage text: Eight items total.",
        )
        self.assertEqual(attempt.answer, "")

    def test_no_topic_does_not_search(self):
        web = WebTool()
        ctx = context(FakeDecider(), (json.dumps({"query": ""}),))
        with patch.object(web, "_search_web") as search:
            answer = web.search_and_answer(ctx, ctx.user_text)
        self.assertIsNone(answer)
        search.assert_not_called()

    def test_broad_question_does_not_run_dated_record_query(self):
        web = WebTool()
        ctx = context(FakeDecider(), (
            json.dumps({
                "answer": "Lanterns is a television series.",
                "quotes": ["Lanterns is a television series"],
                "followup_query": "",
            }),
            json.dumps({"supported": True}),
        ))
        source = "Source: https://example.org/series\nPage text: Lanterns is a television series. " * 8
        with patch.object(web, "_query_source_records") as record_query:
            attempt = web._answer_from_web_results(
                ctx, "Tell me about Lanterns", [{"url": "https://example.org/series"}],
                page_evidence=source, answer_kind="other",
            )
        self.assertEqual(attempt.answer, "Lanterns is a television series.")
        record_query.assert_not_called()

    def test_failed_search_asks_a_specific_question(self):
        web = WebTool()
        ctx = context(FakeDecider())
        replies = iter([
            {"query": "Project Borealis", "evidence_needed": "An overview", "answer_kind": "other"},
            {"question": "Which aspect of Project Borealis interests you most?"},
        ])
        ctx.ollama_structured_chat = lambda messages: {
            "message": {"content": json.dumps(next(replies))}
        }
        with patch.object(web, "_search_web", return_value=[]):
            answer = web.search_and_answer(ctx, "Tell me about Project Borealis")
        self.assertEqual(answer, "Which aspect of Project Borealis interests you most?")

    def test_empty_search_does_not_follow_unverified_backup_query(self):
        web = WebTool()
        ctx = context(FakeDecider())
        ctx.ollama_chat = lambda messages: {"message": {"content": json.dumps({
            "query": "Project Borealis summary",
            "backup_query": "Project Borealis completed milestone report",
        })}}
        with patch.object(web, "_search_web", return_value=[]) as search:
            answer = web.search_and_answer(ctx, "How many milestones are complete?")
        self.assertTrue(answer.endswith("?"))
        search.assert_called_once()

    def test_failed_automatic_search_does_not_send_unverified_draft(self):
        web = WebTool()
        ctx = context(FakeDecider("search"))
        with patch.object(web, "search_and_answer", side_effect=OSError("offline")):
            answer = _maybe_answer_from_web(
                ctx,
                ToolRegistry(web),
                "что ты о нём слышал?",
                "Это точно сериал про выживание в космосе.",
            )
        self.assertNotIn("выживание в космосе", answer)
        self.assertTrue(answer.endswith("?"))


if __name__ == "__main__":
    unittest.main()
