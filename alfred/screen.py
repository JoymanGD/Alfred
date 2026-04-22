import os
import subprocess
import tempfile
import time
from typing import Any, Callable

try:
    from PIL import ImageGrab
except Exception:  # pragma: no cover - optional dependency fallback
    ImageGrab = None


def analyze_screen_request(
    text: str,
    ollama_chat: Callable[[list[dict[str, Any]]], Any],
    normalize_text: Callable[[str], str],
    system_prompt: str,
    language: str,
) -> str:
    screenshot_path = capture_screen_image()
    if screenshot_path is None:
        return (
            "\u042f \u043d\u0435 \u043c\u043e\u0433\u0443 \u0441\u0447\u0438\u0442\u0430\u0442\u044c "
            "\u0438\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u0435 \u0441 \u044d\u043a\u0440\u0430\u043d\u0430 \u0441\u0435\u0439\u0447\u0430\u0441."
            if language == "ru"
            else "I can't capture the screen right now."
        )

    try:
        language_instruction = _build_language_instruction(language)
        messages = [
            {"role": "system", "content": f"{system_prompt}\n{language_instruction}"},
            {
                "role": "user",
                "content": text,
                "images": [screenshot_path],
            },
        ]
        response = ollama_chat(messages)
        return normalize_text(response["message"]["content"])
    except Exception as e:
        print(f"[SCREEN] Screen analysis failed: {e}")
        return (
            "\u042f \u043d\u0435 \u0441\u043c\u043e\u0433 \u043f\u0440\u043e\u0430\u043d\u0430\u043b\u0438\u0437\u0438\u0440\u043e\u0432\u0430\u0442\u044c "
            "\u0442\u043e, \u0447\u0442\u043e \u043d\u0430 \u044d\u043a\u0440\u0430\u043d\u0435."
            if language == "ru"
            else "I couldn't analyze what is on the screen."
        )
    finally:
        cleanup_screenshot(screenshot_path)


def capture_screen_image() -> str | None:
    if ImageGrab is None:
        return None

    previous_signature = _clipboard_image_signature()
    if not _launch_screen_snipping_overlay():
        return None

    image = _wait_for_new_clipboard_image(previous_signature)
    if image is None:
        return None

    fd, image_path = tempfile.mkstemp(prefix="alfred-screen-", suffix=".png")
    os.close(fd)
    try:
        image.save(image_path, format="PNG")
        return image_path
    except Exception as e:
        print(f"[SCREEN] Saving screenshot failed: {e}")
        cleanup_screenshot(image_path)
        return None


def _launch_screen_snipping_overlay() -> bool:
    try:
        subprocess.Popen(
            ["explorer.exe", "ms-screenclip:"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception as e:
        print(f"[SCREEN] Failed to launch screen clipping overlay: {e}")
        return False


def _wait_for_new_clipboard_image(previous_signature: str | None, timeout_seconds: float = 20.0):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        clipboard_content = _grab_clipboard_image()
        if clipboard_content is not None:
            signature = _image_signature(clipboard_content)
            if signature != previous_signature:
                return clipboard_content
        time.sleep(0.2)
    print("[SCREEN] Timed out waiting for screen selection.")
    return None


def _grab_clipboard_image():
    try:
        clipboard_content = ImageGrab.grabclipboard()
    except Exception as e:
        print(f"[SCREEN] Clipboard capture failed: {e}")
        return None

    if hasattr(clipboard_content, "save"):
        return clipboard_content
    return None


def _clipboard_image_signature() -> str | None:
    image = _grab_clipboard_image()
    if image is None:
        return None
    return _image_signature(image)


def _image_signature(image) -> str:
    try:
        return f"{image.size}:{hash(image.tobytes())}"
    except Exception:
        return f"{image.size}:{id(image)}"


def cleanup_screenshot(path: str | None):
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass


def _build_language_instruction(language: str) -> str:
    if language == "ru":
        return (
            "The user is speaking Russian. "
            "Answer in Russian even if the screenshot contains English or another language."
        )
    if language == "uk":
        return (
            "The user is speaking Ukrainian. "
            "Answer in Ukrainian even if the screenshot contains English or another language."
        )
    return (
        "Answer in the same language as the user's request, "
        "even if the screenshot contains text in another language."
    )
