import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from alfred.vision import CameraShot, VisionConfig, analyze_vision_request
from alfred.tools.vision import VisionTool


class VisionTests(unittest.TestCase):
    def test_camera_frame_is_sent_to_vision_model_and_removed(self):
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as frame:
            frame.write(b"test frame")
            frame_path = frame.name
        seen = []

        def fake_chat(messages):
            seen.append(messages)
            self.assertTrue(os.path.exists(frame_path))
            return {"message": {"content": "Вижу предмет в руке."}}

        with patch("alfred.vision.capture_camera_shot", return_value=CameraShot(frame_path, 0)):
            answer = analyze_vision_request(
                "Что у меня в руке?", VisionConfig(True, [0]), fake_chat,
                str.strip, "ru",
            )
        self.assertEqual(answer, "Вижу предмет в руке.")
        self.assertEqual(seen[0][1]["images"], [frame_path])
        self.assertIn("one is already attached", seen[0][0]["content"])
        self.assertIn("Answer only what the image supports", seen[0][0]["content"])
        self.assertIn("You are male", seen[0][0]["content"])
        self.assertFalse(os.path.exists(frame_path))

    def test_visual_correction_does_not_trigger_another_camera_capture(self):
        class FeedbackDecider:
            def choose(self, subject, question, criteria):
                assert set(criteria) == {"inspect", "feedback"}
                return "feedback"

        calls = []
        ctx = SimpleNamespace(
            decider=FeedbackDecider(), recent_history=[
                {"role": "assistant", "content": "Вижу коробку."},
                {"role": "user", "content": "Нет, это кружка."},
            ],
            language="ru", detect_language=lambda text: "ru",
            analyze_vision=lambda text: calls.append(text),
        )
        result = VisionTool().handle(ctx, "Нет, это кружка.")
        self.assertFalse(result.handled)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
