import queue
import threading
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from alfred.addressing import VoiceAddressGate
from alfred.decider import IntentDecider
from alfred.input import LocalInputServer
from alfred.listen import mic_listener


class Memory:
    def recent_history(self, limit):
        return [
            {"role": "user", "content": "Как дела?"},
            {"role": "assistant", "content": "Хорошо. Чем помочь?"},
        ][-limit:]


class Decider:
    def __init__(self, *choices):
        self.choices = choices
        self.calls = []

    def choose(self, subject, question, criteria):
        self.calls.append((subject, question, criteria))
        choice = self.choices[min(len(self.calls) - 1, len(self.choices) - 1)]
        assert choice in criteria
        return choice


class VoiceAddressingTests(unittest.TestCase):
    def test_microphone_forwards_complete_utterance_without_wake_word(self):
        class Stream:
            def __init__(self, **kwargs):
                self.blocks = iter([1.0, 1.0, 1.0, 0.0, 0.0, 0.0])

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, block_size):
                return np.full((block_size, 1), next(self.blocks), dtype=np.float32), False

        class Sink:
            is_ready = True

            def submit_voice_candidate(self, text, **context):
                self.received = (text, context)
                raise SystemExit

        sink = Sink()
        asr = SimpleNamespace(transcribe=lambda audio, **kwargs: ([SimpleNamespace(text="Открой браузер")], None))
        with patch("alfred.listen.sd.InputStream", Stream):
            with self.assertRaises(SystemExit):
                mic_listener(
                    sink, lambda: lambda pcm, rate: SimpleNamespace(item=lambda: float(pcm.mean())),
                    lambda: asr, 0.01, 0.05, 0.01, 8.0,
                    threading.Event(), queue.Queue(), queue.Queue(), [], str.strip,
                )
        self.assertEqual(sink.received[0], "Открой браузер")
        self.assertFalse(sink.received[1]["followup_active"])

    def test_decider_uses_complete_cached_snapshot_when_remote_revision_changes(self):
        with tempfile.TemporaryDirectory() as cache:
            snapshot = (
                Path(cache) / "models--Mapika--decider-4b-GGUF"
                / "snapshots" / "older-revision"
            )
            snapshot.mkdir(parents=True)
            (snapshot / "decider-4b-v2.1-Q4_K_M.gguf").touch()
            (snapshot / "tokenizer.json").write_text("{}", encoding="utf-8")
            with (patch("huggingface_hub.constants.HF_HUB_CACHE", cache),
                  patch("decider.infer.Decider") as constructor):
                model = IntentDecider(
                    "Mapika/decider-4b-GGUF", "decider-4b-v2.1-Q4_K_M.gguf"
                )
                model._get_model()
            self.assertEqual(constructor.call_args.args[0], str(snapshot))

    def test_direct_request_without_name_is_submitted_after_decider_accepts(self):
        decider = Decider("assistant", "assistant", "computer")
        incoming = queue.Queue()
        interrupted = threading.Event()
        server = LocalInputServer(
            incoming, interrupted, voice_address_gate=VoiceAddressGate(decider, Memory())
        )
        accepted = server._handle_voice_candidate({
            "text": "Открой браузер", "followup_active": False, "during_tts": False,
        })
        self.assertTrue(accepted)
        self.assertEqual(incoming.get_nowait(), "Открой браузер")
        self.assertTrue(interrupted.is_set())
        self.assertEqual(decider.calls[0][0]["recent_conversation"], [])
        self.assertIn("recent_assistant_reply", decider.calls[1][0])
        self.assertIn("assistant_capabilities", decider.calls[1][0])
        self.assertIn("media", decider.calls[1][2])
        self.assertNotIn("echo", decider.calls[1][2])

    def test_ambient_speech_is_ignored_without_interrupting_audio(self):
        decider = Decider("new_speech", "media")
        incoming = queue.Queue()
        interrupted = threading.Event()
        server = LocalInputServer(
            incoming, interrupted, voice_address_gate=VoiceAddressGate(decider, Memory())
        )
        accepted = server._handle_voice_candidate({
            "text": "Следующий ход в партии", "followup_active": True, "during_tts": True,
        })
        self.assertFalse(accepted)
        self.assertTrue(incoming.empty())
        self.assertFalse(interrupted.is_set())
        self.assertTrue(decider.calls[1][0]["conversation_continuation_window"])
        self.assertTrue(decider.calls[1][0]["speech_started_during_assistant_audio"])

    def test_decider_can_reject_its_own_spoken_reply_as_echo(self):
        decider = Decider("echo")
        gate = VoiceAddressGate(decider, Memory())
        self.assertFalse(gate.accepts(
            "Хорошо. Чем помочь?", followup_active=True, during_tts=True,
        ))
        self.assertIn("echo", decider.calls[0][2])

    def test_short_reply_after_tts_is_not_treated_as_echo(self):
        decider = Decider("new_speech", "assistant", "assistant", "conversation")
        gate = VoiceAddressGate(decider, Memory())
        self.assertTrue(gate.accepts(
            "Thanks", followup_active=True, during_tts=False, recent_tts=True,
        ))
        self.assertEqual(len(decider.calls), 4)
        self.assertNotIn("echo", decider.calls[1][2])

    def test_external_addressee_is_rejected_before_capability_check(self):
        decider = Decider("other_person")
        gate = VoiceAddressGate(decider, Memory())
        self.assertFalse(gate.accepts(
            "Маша, открой браузер", followup_active=False, during_tts=False,
        ))
        self.assertEqual(len(decider.calls), 1)

    def test_unclear_first_pass_can_be_resolved_by_capabilities(self):
        decider = Decider("unclear", "assistant", "conversation")
        gate = VoiceAddressGate(decider, Memory())
        self.assertTrue(gate.accepts(
            "Что у меня на экране?", followup_active=False, during_tts=False,
        ))
        self.assertEqual(len(decider.calls), 3)

    def test_physical_request_without_address_is_ignored(self):
        decider = Decider("assistant", "other_person", "physical_request")
        gate = VoiceAddressGate(decider, Memory())
        self.assertFalse(gate.accepts(
            "Bring me a cup", followup_active=False, during_tts=False,
        ))
        self.assertIn("assistant_limitations", decider.calls[1][0])

    def test_information_question_about_physical_task_remains_addressed(self):
        decider = Decider("assistant", "other_person", "information_question", "conversation")
        gate = VoiceAddressGate(decider, Memory())
        self.assertTrue(gate.accepts(
            "How do I carry this?", followup_active=False, during_tts=False,
        ))

    def test_unaddressed_environment_command_is_ignored_after_capability_check(self):
        decider = Decider("assistant", "assistant", "environment", "command", "not_named")
        gate = VoiceAddressGate(decider, Memory())
        self.assertFalse(gate.accepts(
            "Turn on the room lights", followup_active=False, during_tts=False,
        ))

    def test_unresolved_fragment_is_ignored_even_after_assistant_reply(self):
        decider = Decider("assistant", "assistant", "ambiguous")
        gate = VoiceAddressGate(decider, Memory())
        self.assertFalse(gate.accepts(
            "Home", followup_active=True, during_tts=False,
        ))

    def test_named_environment_request_can_receive_honest_answer(self):
        decider = Decider("assistant", "assistant", "environment", "command", "named")
        gate = VoiceAddressGate(decider, Memory())
        self.assertTrue(gate.accepts(
            "Alfred, turn on the room lights", followup_active=False, during_tts=False,
        ))


if __name__ == "__main__":
    unittest.main()
