import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote_plus, unquote
from xml.etree import ElementTree

from alfred.tooling import Tool, ToolContext, ToolResult


@dataclass
class WebToolConfig:
    ru_weather_words: list[str]
    ru_search_words: list[str]
    ru_fact_words: list[str]


def strip_html(text: str, normalize_memory_text) -> str:
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    return normalize_memory_text(text)


class WebTool(Tool):
    name = "web"

    def __init__(self, config: WebToolConfig):
        self.config = config
        self.last_weather_location: str | None = None

    def matches(self, context: ToolContext, text: str) -> bool:
        if self._is_browser_open_request(text):
            return False
        if (
            self._is_weather_request(text)
            or self._is_weather_followup(text)
            or self._is_current_events_request(text)
            or self._is_web_search_request(text)
            or self._is_fact_lookup_request(text)
        ):
            return True
        return self._llm_detects_web_request(context, text)

    def handle(self, context: ToolContext, text: str) -> ToolResult:
        language = context.detect_language(text)
        try:
            if self._is_weather_request(text) or self._is_weather_followup(text):
                location, day_offset = self._extract_weather_request(text)
                self.last_weather_location = location
                reply_text = self._get_weather_report(context, location, language, day_offset)
            else:
                query = context.routed_query if context.routed_tool_name == "web" and context.routed_query else self._extract_search_query(text)
                normalized_query = self._normalize_search_query(context, query)
                search_queries = self._build_search_queries(context, normalized_query, language)
                print(f"[WEB] Searching web for queries: {search_queries}")
                results = self._search_web_multi(context, search_queries)
                print(f"[WEB] Search results found: {len(results)}")
                reply_text = self._answer_from_web_results(context, text, results)
        except Exception as e:
            reply_text = (
                f"Не удалось выполнить запрос из интернета: {e}"
                if language == "ru"
                else f"I could not complete the internet request: {e}"
            )
        return ToolResult(handled=True, reply_text=reply_text)

    def _is_browser_open_request(self, text: str) -> bool:
        lowered = text.lower().strip()
        patterns = [
            r"^(?:open|launch|start)\s+(?:in\s+browser|in\s+the\s+browser)\s+.+",
            r"^(?:open|launch|start)\s+.+\s+(?:in\s+browser|in\s+the\s+browser)$",
            r"^(?:открой|запусти)\s+в\s+браузере\s+.+",
            r"^(?:открой|запусти)\s+.+\s+в\s+браузере$",
        ]
        return any(re.search(pattern, lowered) for pattern in patterns)

    def _is_weather_request(self, text: str) -> bool:
        lowered = text.lower()
        patterns = ["weather", "temperature", "forecast", *self.config.ru_weather_words]
        return any(pattern in lowered for pattern in patterns)

    def _is_weather_followup(self, text: str) -> bool:
        if not self.last_weather_location:
            return False
        lowered = text.lower()
        patterns = [
            "tomorrow",
            "day after tomorrow",
            "how about",
            "what about",
            "today",
            "tonight",
            "завтра",
            "послезавтра",
            "а как",
            "а что",
            "сегодня",
        ]
        return any(pattern in lowered for pattern in patterns)

    def _extract_weather_request(self, text: str) -> tuple[str, int]:
        lowered = text.lower()
        day_offset = 0
        if "day after tomorrow" in lowered or "послезавтра" in lowered:
            day_offset = 2
        elif "tomorrow" in lowered or "завтра" in lowered:
            day_offset = 1

        matches = []
        for pattern in (
            r"\bin\s+([a-zA-Z\u0400-\u04FF0-9 .,'-]+)",
            r"\b(?:в|на)\s+([a-zA-Z\u0400-\u04FF0-9 .,'-]+)",
        ):
            matches.extend(re.findall(pattern, text, flags=re.IGNORECASE))

        if matches:
            return matches[-1].strip(" ?!.,"), day_offset
        if self.last_weather_location:
            return self.last_weather_location, day_offset
        return text.strip(" ?!."), day_offset

    def _get_weather_report(self, context: ToolContext, location: str, language: str, day_offset: int = 0) -> str:
        url = f"https://wttr.in/{quote_plus(location)}?format=j1"
        print(f"[WEB] Fetching weather from wttr.in: location={location}, day_offset={day_offset}, url={url}")
        raw = context.fetch_url(url)
        data = json.loads(raw)

        if day_offset <= 0:
            current = data["current_condition"][0]
            weather_desc = current["weatherDesc"][0]["value"]
            temp_c = current["temp_C"]
            feels_like_c = current["FeelsLikeC"]
            humidity = current["humidity"]
            wind_kph = current["windspeedKmph"]
            if language == "ru":
                return (
                    f"По данным wttr.in, текущая погода в {location}: {weather_desc}, "
                    f"температура {temp_c} градусов Цельсия, ощущается как {feels_like_c}, "
                    f"влажность {humidity} процентов, ветер {wind_kph} километров в час."
                )
            return (
                f"According to wttr dot in, current weather in {location}: {weather_desc}, "
                f"temperature {temp_c} degrees Celsius, feels like {feels_like_c}, "
                f"humidity {humidity} percent, wind {wind_kph} kilometers per hour."
            )

        forecast_days = data.get("weather", [])
        if day_offset >= len(forecast_days):
            if language == "ru":
                return f"У меня нет прогноза на {day_offset} дня вперёд для {location}."
            return f"I do not have a forecast {day_offset} days ahead for {location}."

        forecast = forecast_days[day_offset]
        hourly = forecast.get("hourly", [])
        midday = hourly[len(hourly) // 2] if hourly else {}
        weather_desc = (midday.get("weatherDesc") or [{"value": ""}])[0]["value"]
        max_temp_c = forecast.get("maxtempC", "")
        min_temp_c = forecast.get("mintempC", "")
        avg_temp_c = forecast.get("avgtempC", "")
        humidity = midday.get("humidity", "")
        wind_kph = midday.get("windspeedKmph", "")
        forecast_date = forecast.get("date", "")
        weekday = ""
        if forecast_date:
            weekday = datetime.strptime(forecast_date, "%Y-%m-%d").strftime("%A")

        if language == "ru":
            day_label = "завтра" if day_offset == 1 else "послезавтра"
            return (
                f"По данным wttr.in, погода в {location} на {day_label}, {forecast_date}: {weather_desc}, "
                f"максимум {max_temp_c}, минимум {min_temp_c}, в среднем {avg_temp_c}, "
                f"влажность {humidity} процентов, ветер {wind_kph} километров в час."
            )
        return (
            f"According to wttr dot in, weather in {location} for {weekday}, {forecast_date}: {weather_desc}, "
            f"high {max_temp_c} degrees Celsius, low {min_temp_c}, average {avg_temp_c}, "
            f"humidity {humidity} percent, wind {wind_kph} kilometers per hour."
        )

    def _is_web_search_request(self, text: str) -> bool:
        lowered = text.lower()
        patterns = [
            "search",
            "find",
            "look up",
            "google",
            "internet",
            "online",
            "найди в интернете",
            "найди в сети",
            "поищи в интернете",
            "поищи в сети",
            *self.config.ru_search_words,
        ]
        return any(pattern in lowered for pattern in patterns)

    def _is_current_events_request(self, text: str) -> bool:
        lowered = text.lower()
        patterns = [
            "latest",
            "current",
            "news",
            "what is happening",
            "situation",
            "front",
            "war",
            "update",
            "последние",
            "актуальные",
            "актуальная",
            "новости",
            "что происходит",
            "ситуация",
            "обстановка",
            "на фронте",
            "фронте",
            "войне",
            "война",
        ]
        return any(pattern in lowered for pattern in patterns)

    def _llm_detects_web_request(self, context: ToolContext, text: str) -> bool:
        lowered = text.lower().strip()
        if len(lowered.split()) <= 3 and not any(ch.isdigit() for ch in lowered):
            return False

        prompt = (
            "Classify whether the user's message requires an internet lookup.\n"
            "Reply with exactly one label:\n"
            "WEB if the user is asking to search online, check current facts, news, weather, prices, schedule, or other up to date information.\n"
            "CHAT if it is ordinary conversation or can be answered without internet lookup.\n\n"
            f"User message: {text}"
        )
        try:
            response = context.ollama_chat(
                [
                    {"role": "system", "content": "You are a strict classifier. Output only WEB or CHAT."},
                    {"role": "user", "content": prompt},
                ]
            )
            decision = context.normalize_memory_text(response["message"]["content"]).upper()
            print(f"[WEB] LLM web classifier decision: {decision} for: {text}")
            return decision.startswith("WEB")
        except Exception as e:
            print(f"[WEB] LLM web classifier failed: {e}")
            return False

    def _is_fact_lookup_request(self, text: str) -> bool:
        lowered = text.lower()
        patterns = ["date of birth", "birthday", "when was", "how old is", *self.config.ru_fact_words]
        return any(pattern in lowered for pattern in patterns)

    def _extract_search_query(self, text: str) -> str:
        query = text.strip(" ?!.,")

        prefix_patterns = [
            r"^(?:search|find|look up|google)\s+(?:for\s+)?",
            r"^(?:найди|поищи|узнай|погугли)\s+",
            r"^(?:найди|поищи)\s+в\s+интернете\s+",
            r"^(?:найди|поищи)\s+в\s+сети\s+",
            r"^(?:посмотри)\s+в\s+интернете\s+",
            r"^(?:can you search for)\s+",
            r"^(?:tell me about)\s+",
        ]
        for pattern in prefix_patterns:
            query = re.sub(pattern, "", query, flags=re.IGNORECASE).strip(" ?!.,")

        suffix_patterns = [
            r"\s+в\s+интернете$",
            r"\s+в\s+сети$",
            r"\s+on(?:\s+the)?\s+internet$",
            r"\s+online$",
        ]
        for pattern in suffix_patterns:
            query = re.sub(pattern, "", query, flags=re.IGNORECASE).strip(" ?!.,")

        cleanup_prefixes = [
            "в интернете ",
            "в сети ",
            "информацию про ",
            "информацию о ",
            "информацию об ",
            "информацию ",
            "сведения про ",
            "сведения о ",
            "про ",
            "о ",
            "об ",
        ]
        changed = True
        while changed and query:
            changed = False
            lowered = query.lower()
            for prefix in cleanup_prefixes:
                if lowered.startswith(prefix):
                    query = query[len(prefix):].strip(" ?!.,")
                    changed = True
                    break

        return query.strip(" ?!.,")

    def _normalize_search_query(self, context: ToolContext, query: str) -> str:
        prompt = (
            "Rewrite the user's web search request into one concise search engine query. "
            "Remove filler words, keep the actual topic and requested constraints such as count, recency, place, or date. "
            "Return only the final search query.\n\n"
            f"User search request: {query}"
        )
        try:
            response = context.ollama_chat(
                [
                    {"role": "system", "content": "You rewrite search requests. Output only the query."},
                    {"role": "user", "content": prompt},
                ]
            )
            normalized = context.normalize_memory_text(response["message"]["content"])
            return normalized or query
        except Exception as e:
            print(f"[WEB] Search query normalization failed: {e}")
            return query

    def _build_search_queries(self, context: ToolContext, query: str, language: str) -> list[str]:
        queries = [query]
        if language != "ru":
            return queries
        if not self._needs_english_search_fallback(query):
            return queries

        prompt = (
            "Translate this web search query to natural English for better international search results. "
            "Keep the meaning and constraints. Return only the English query.\n\n"
            f"Query: {query}"
        )
        try:
            response = context.ollama_chat(
                [
                    {"role": "system", "content": "You translate search queries. Output only the translated query."},
                    {"role": "user", "content": prompt},
                ]
            )
            english_query = context.normalize_memory_text(response["message"]["content"])
            if english_query and english_query.lower() != query.lower():
                queries.append(english_query)
                print(f"[WEB] Added English fallback query: {english_query}")
        except Exception as e:
            print(f"[WEB] English fallback query generation failed: {e}")
        return queries

    def _needs_english_search_fallback(self, query: str) -> bool:
        lowered = query.lower()
        patterns = [
            "науч",
            "открыт",
            "исследован",
            "science",
            "scientific",
            "discovery",
            "discoveries",
            "искусственный интеллект",
            "квант",
            "космос",
            "марс",
            "nasa",
            "nature",
            "physics",
            "biology",
            "medicine",
            "технолог",
            "latest",
            "последние",
        ]
        return any(pattern in lowered for pattern in patterns)

    def _search_web(self, context: ToolContext, query: str, region: str = "wt-wt") -> list[dict[str, Any]]:
        endpoints = [
            f"https://html.duckduckgo.com/html/?kl={quote_plus(region)}&q={quote_plus(query)}",
            f"https://lite.duckduckgo.com/lite/?kl={quote_plus(region)}&q={quote_plus(query)}",
        ]
        last_error = None
        for endpoint in endpoints:
            try:
                html = context.fetch_url(endpoint)
                results = self._parse_duckduckgo_results(context, html)
                if results:
                    return results
            except Exception as e:
                last_error = e
                print(f"[WEB] Search endpoint failed: {endpoint} -> {e}")

        try:
            bing_results = self._search_bing_rss(context, query, region=region)
            if bing_results:
                return bing_results
        except Exception as e:
            last_error = e
            print(f"[WEB] Bing RSS search failed for {query}: {e}")

        if last_error is not None:
            raise last_error
        return []

    def _parse_duckduckgo_results(self, context: ToolContext, html: str) -> list[dict[str, Any]]:
        results = self._parse_duckduckgo_html_results(context, html)
        if results:
            return results
        return self._parse_duckduckgo_lite_results(context, html)

    def _parse_duckduckgo_html_results(self, context: ToolContext, html: str) -> list[dict[str, Any]]:
        blocks = re.findall(
            r'(<div[^>]*class="result[^"]*"[^>]*>.*?</div>\s*</div>)',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )

        results = []
        for block in blocks[:5]:
            title_match = re.search(
                r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                block,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if not title_match:
                continue

            href, title_html = title_match.groups()
            title = strip_html(title_html, context.normalize_memory_text)
            url_match = re.search(r"uddg=([^&]+)", href)
            resolved_url = unquote(url_match.group(1)) if url_match else href

            snippet_match = re.search(
                r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>|<div[^>]*class="result__snippet"[^>]*>(.*?)</div>',
                block,
                flags=re.IGNORECASE | re.DOTALL,
            )
            snippet_html = ""
            if snippet_match:
                snippet_html = next((part for part in snippet_match.groups() if part), "")
            snippet = strip_html(snippet_html, context.normalize_memory_text)

            if title and resolved_url:
                results.append({"title": title, "url": resolved_url, "snippet": snippet})
        return results

    def _parse_duckduckgo_lite_results(self, context: ToolContext, html: str) -> list[dict[str, Any]]:
        links = re.findall(
            r'<a[^>]*rel="nofollow"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )

        results = []
        for href, title_html in links:
            title = strip_html(title_html, context.normalize_memory_text)
            url_match = re.search(r"uddg=([^&]+)", href)
            resolved_url = unquote(url_match.group(1)) if url_match else href
            if not title or not resolved_url:
                continue
            results.append({"title": title, "url": resolved_url, "snippet": ""})
            if len(results) >= 5:
                break
        return results

    def _search_bing_rss(self, context: ToolContext, query: str, region: str = "wt-wt") -> list[dict[str, Any]]:
        setlang = "ru" if region.startswith("ru") else "en"
        xml_text = context.fetch_url(
            f"https://www.bing.com/search?format=rss&setlang={quote_plus(setlang)}&q={quote_plus(query)}"
        )
        root = ElementTree.fromstring(xml_text)
        results = []
        for item in root.findall("./channel/item"):
            title = context.normalize_memory_text(item.findtext("title", default=""))
            url = context.normalize_memory_text(item.findtext("link", default=""))
            snippet = context.normalize_memory_text(item.findtext("description", default=""))
            if not title or not url:
                continue
            results.append({"title": title, "url": url, "snippet": snippet})
            if len(results) >= 5:
                break
        return results

    def _search_web_multi(self, context: ToolContext, queries: list[str]) -> list[dict[str, Any]]:
        merged = []
        seen_urls = set()
        regions = ["ru-ru", "us-en"]
        for index, query in enumerate(queries):
            region = regions[min(index, len(regions) - 1)]
            results = self._search_web(context, query, region=region)
            for result in results:
                url = result.get("url")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                merged.append(result)
                if len(merged) >= 8:
                    return merged
        return merged

    def _answer_from_web_results(self, context: ToolContext, user_text: str, results) -> str:
        language = context.detect_language(user_text)
        if not results:
            return "Я не нашёл уверенных результатов в интернете." if language == "ru" else "I could not find solid results online."

        snippets = "\n".join(
            f"{index + 1}. {result['title']} - {result['url']} - {result.get('snippet', '')}"
            for index, result in enumerate(results[:3])
        )
        language_instruction = (
            "Answer in Russian. Keep the answer natural and concise for spoken delivery."
            if language == "ru"
            else "Answer in English. Keep the answer natural and concise for spoken delivery."
        )
        prompt = (
            "Use these web search results to answer the user briefly and naturally. "
            f"{language_instruction} "
            "Mention that this answer is based on internet search. "
            "If the user asked for a specific number of items, follow that number exactly when the results support it. "
            "If useful, mention one or two source domains naturally. "
            "Do not invent facts beyond the results.\n\n"
            f"User request: {user_text}\n\n"
            f"Search results:\n{snippets}"
        )
        response = context.ollama_chat(
            [
                {"role": "system", "content": context.system_prompt},
                {"role": "user", "content": prompt},
            ]
        )
        return context.normalize_memory_text(response["message"]["content"])
