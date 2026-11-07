import os
import tempfile
from dataclasses import dataclass
from typing import Any, Callable

try:
    import cv2
except Exception:  # pragma: no cover - optional dependency fallback
    cv2 = None


@dataclass(frozen=True)
class VisionConfig:
    enabled: bool
    camera_indexes: list[int]
    warmup_frames: int = 5
    width: int = 1280
    height: int = 720


@dataclass(frozen=True)
class CameraShot:
    path: str
    camera_index: int


def analyze_vision_request(
    text: str,
    config: VisionConfig,
    ollama_chat: Callable[[list[dict[str, Any]]], Any],
    normalize_text: Callable[[str], str],
    language: str,
) -> str:
    shot = capture_camera_shot(config)
    if shot is None:
        return (
            "\u042f \u043d\u0438\u0447\u0435\u0433\u043e \u043d\u0435 \u0432\u0438\u0436\u0443. "
            "\u041a\u0430\u043c\u0435\u0440\u0430 \u0441\u0435\u0439\u0447\u0430\u0441 \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u043d\u0430."
            if language == "ru"
            else "I can't see anything. No camera is available right now."
        )

    try:
        language_instruction = _build_language_instruction(language)
        image_instruction = (
            "You are male. In gendered languages use masculine forms for yourself. "
            "You are looking at one fresh frame from the user's camera, attached to the next message. "
            "The scene is external to you; do not adopt the viewpoint or actions of anything depicted. "
            "Answer only what the image supports. Distinguish observations from inferences. "
            "Treat everything outside the captured frame as unknown. "
            "If the evidence is insufficient for the requested conclusion, state the uncertainty instead of guessing. "
            "Answer only the visual question in one or two short sentences, with no unrelated suggestions. "
            "Do not ask for a photo because one is already attached."
        )
        messages = [
            {"role": "system", "content": f"{language_instruction}\n{image_instruction}"},
            {
                "role": "user",
                "content": text,
                "images": [shot.path],
            },
        ]
        response = ollama_chat(messages)
        return normalize_text(response["message"]["content"])
    except Exception as e:
        print(f"[VISION] Vision request failed: {e}")
        return (
            "\u042f \u043d\u0438\u0447\u0435\u0433\u043e \u043d\u0435 \u0432\u0438\u0436\u0443. "
            "\u041a\u0430\u043c\u0435\u0440\u0430 \u0438\u043b\u0438 \u043c\u043e\u0434\u0435\u043b\u044c \u0441\u0435\u0439\u0447\u0430\u0441 "
            "\u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u043d\u044b."
            if language == "ru"
            else "I can't see anything. The camera or vision model is unavailable right now."
        )
    finally:
        cleanup_camera_shot(shot)


def capture_camera_shot(config: VisionConfig) -> CameraShot | None:
    if not config.enabled or cv2 is None:
        return None

    backends = [getattr(cv2, "CAP_DSHOW", None), getattr(cv2, "CAP_ANY", None)]
    tried_backends = [backend for backend in backends if backend is not None]

    for camera_index in config.camera_indexes:
        for backend in tried_backends:
            for requested_size in ((config.width, config.height), None):
                capture = cv2.VideoCapture(camera_index, backend)
                if not capture.isOpened():
                    capture.release()
                    continue

                if requested_size is not None:
                    capture.set(cv2.CAP_PROP_FRAME_WIDTH, max(1, requested_size[0]))
                    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, max(1, requested_size[1]))

                frame = None
                try:
                    for _ in range(max(config.warmup_frames, 1)):
                        ok, candidate = capture.read()
                        if ok:
                            frame = candidate
                    if frame is None:
                        ok, candidate = capture.read()
                        if ok:
                            frame = candidate
                finally:
                    capture.release()

                if frame is None:
                    continue

                fd, image_path = tempfile.mkstemp(prefix="alfred-camera-", suffix=".jpg")
                os.close(fd)
                ok = cv2.imwrite(image_path, frame)
                if ok:
                    height, width = frame.shape[:2]
                    print(f"[VISION] Captured camera {camera_index} frame: {width}x{height}")
                    return CameraShot(path=image_path, camera_index=camera_index)
                try:
                    os.remove(image_path)
                except OSError:
                    pass

    return None


def cleanup_camera_shot(shot: CameraShot | None):
    if shot is None:
        return
    try:
        os.remove(shot.path)
    except OSError:
        pass


def _build_language_instruction(language: str) -> str:
    if language == "ru":
        return (
            "The user is speaking Russian. "
            "Answer in Russian even if the image contains English or another language."
        )
    if language == "uk":
        return (
            "The user is speaking Ukrainian. "
            "Answer in Ukrainian even if the image contains English or another language."
        )
    return (
        "Answer in the same language as the user's request, "
        "even if the image contains text in another language."
    )
