from alfred.tooling import Tool, ToolContext, ToolResult


class ImageTool(Tool):
    name = "image"

    def matches(self, context: ToolContext, text: str) -> bool:
        response = self._classify(text, context)
        return "YES" in response.upper()

    def handle(self, context: ToolContext, text: str) -> ToolResult:
        language = context.detect_language(text)
        if context.generate_image is None:
            return ToolResult(
                handled=True,
                reply_text=(
                    "Генерация изображений сейчас недоступна."
                    if language == "ru"
                    else "Image generation is unavailable right now."
                ),
            )

        prompt = self._prepare_prompt(context, text)
        print(f"[IMAGE] Optimized prompt: {prompt}")

        try:
            image_payload = context.generate_image(prompt)
        except Exception as e:
            print(f"[IMAGE] Generation failed: {e}")
            error_message = str(e).strip()
            reply_text = (
                error_message
                or (
                    "Не удалось сгенерировать изображение. Проверь настройки модели и устройства."
                    if language == "ru"
                    else "Image generation failed. Check the model and device settings."
                )
            )
            return ToolResult(handled=True, reply_text=reply_text)

        return ToolResult(
            handled=True,
            reply_text="Изображение готово." if language == "ru" else "Image generated.",
            image_payload=image_payload,
        )

    def _classify(self, text: str, context: ToolContext | None) -> str:
        if context is None:
            return "NO"
        response = context.ollama_chat(
            [
                {
                    "role": "system",
                    "content": "You classify whether the user is asking for image generation. Reply with YES or NO only.",
                },
                {"role": "user", "content": text},
            ]
        )
        return response["message"]["content"]

    def _prepare_prompt(self, context: ToolContext, text: str) -> str:
        response = context.ollama_chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You optimize image-generation prompts for Z-Image-Turbo. "
                        "Rewrite the user's request into one concise but high-quality visual prompt. "
                        "Preserve the user's intent while improving clarity, composition, subject definition, "
                        "background coherence, lighting, materials or clothing when relevant, camera framing, "
                        "and overall visual quality. "
                        "Default to one clear main subject, clean composition, coherent scene, and high detail. "
                        "Do not add unsafe content, do not add extra subjects unless clearly implied, and do not use negative prompts. "
                        "Return only the final visual prompt."
                    ),
                },
                {"role": "user", "content": text},
            ]
        )
        return context.normalize_memory_text(response["message"]["content"])
