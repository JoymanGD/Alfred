import os
import tempfile
import unittest
from unittest.mock import patch

from alfred.screen import analyze_screen_request


class ScreenTests(unittest.TestCase):
    def test_screen_request_uses_image_evidence_without_assistant_persona(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as image:
            image.write(b"test image")
            image_path = image.name
        messages_seen = []

        def fake_chat(messages):
            messages_seen.extend(messages)
            self.assertTrue(os.path.exists(image_path))
            return {"message": {"content": "На экране рисунок."}}

        with patch("alfred.screen.capture_screen_image", return_value=image_path):
            answer = analyze_screen_request(
                "Что показано на экране?", fake_chat, str.strip,
                "ru",
            )
        self.assertEqual(answer, "На экране рисунок.")
        self.assertEqual(messages_seen[1]["images"], [image_path])
        self.assertIn("Answer only what the visible evidence supports", messages_seen[0]["content"])
        self.assertNotIn("You are Alfred", messages_seen[0]["content"])
        self.assertFalse(os.path.exists(image_path))


if __name__ == "__main__":
    unittest.main()
