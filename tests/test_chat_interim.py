import queue
import threading
import unittest
from types import SimpleNamespace

from alfred.audio import speaker_loop
from alfred.tooling import AssistantOutput


class OutputQueue:
    def __init__(self, *items):
        self.items = iter(items)

    def get(self):
        return next(self.items)


class InputServer:
    def __init__(self):
        self.events = []

    def has_chat_clients(self):
        return True

    def notify_assistant_message(self, text):
        self.events.append(("message", text))

    def notify_tts_started(self):
        self.events.append(("tts_started", None))

    def notify_tts_playback_started(self, reply_id):
        self.events.append(("playback_started", reply_id))

    def notify_tts_finished(self):
        self.events.append(("tts_finished", None))

    def notify_assistant_replied(self, reply_id):
        self.events.append(("replied", reply_id))


class AudioEngine:
    tts_backend = SimpleNamespace(_device="test")

    def synthesize_audio(self, text):
        return [], 16000

    def play_audio_interruptible(self, audio, sample_rate, interrupt_event, playing_event, on_started=None):
        if on_started:
            on_started()


class InterimOutputTests(unittest.TestCase):
    def test_interim_message_does_not_unlock_chat_prompt(self):
        server = InputServer()
        followups = queue.Queue()
        outputs = OutputQueue(AssistantOutput("Checking online", interim=True), "Final answer")
        with self.assertRaises(StopIteration):
            speaker_loop(
                outputs,
                queue.Queue(),
                AudioEngine(),
                threading.Event(),
                threading.Event(),
                ".",
                followups,
                server,
            )
        events = server.events
        self.assertIn(("message", "Checking online"), events)
        self.assertIn(("message", "Final answer"), events)
        self.assertEqual([event for event, _ in events].count("playback_started"), 1)
        self.assertEqual([event for event, _ in events].count("replied"), 1)
        self.assertEqual(followups.qsize(), 1)
        self.assertLess(
            events.index(("message", "Final answer")),
            next(index for index, event in enumerate(events) if event[0] == "playback_started"),
        )


if __name__ == "__main__":
    unittest.main()
