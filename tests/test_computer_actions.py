import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from alfred.computer_actions import ComputerActionRunner
from alfred.action_catalog import load_catalog


class FakeDecider:
    def __init__(self, *answers):
        self.answers = iter(answers)

    def choose(self, subject, question, criteria, **kwargs):
        answer = next(self.answers)
        assert answer in criteria
        return answer


def context(responses, decider=None, text="Переименуй файл"):
    replies = iter(responses)
    return SimpleNamespace(
        user_text=text,
        language="ru",
        recent_history=[{"role": "user", "content": text}],
        decider=decider or FakeDecider(),
        ollama_chat=lambda messages: {"message": {"content": json.dumps(next(replies), ensure_ascii=False)}},
    )


class ComputerActionTests(unittest.TestCase):
    def test_multi_step_search_then_rename_requires_exact_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "old.txt"
            destination = Path(directory) / "new.txt"
            source.write_text("content", encoding="utf-8")
            steps = [
                {"action": "search_files", "args": {"root": directory, "query": "old", "recursive": False}},
                {"action": "rename_path", "args": {"source": str(source), "destination": str(destination)}},
                {"action": "reply", "text": "Готово: файл переименован."},
            ]
            runner = ComputerActionRunner(None, directory)
            ctx = context(steps, FakeDecider("file_discovery", "file_management", "approve", "file_management"))
            prompt = runner.run(ctx)
            self.assertIn(str(source), prompt)
            self.assertIn(str(destination), prompt)
            self.assertTrue(source.exists())
            self.assertFalse(destination.exists())
            answer = runner.resolve_confirmation(ctx)
            self.assertEqual(answer, "Готово: файл переименован.")
            self.assertFalse(source.exists())
            self.assertTrue(destination.exists())

    def test_delete_rejection_leaves_file_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "keep.txt"
            target.write_text("important", encoding="utf-8")
            runner = ComputerActionRunner(None, directory)
            ctx = context([{"action": "delete_path", "args": {"path": str(target)}}], FakeDecider("file_management", "reject"))
            self.assertIn(str(target), runner.run(ctx))
            self.assertEqual(runner.resolve_confirmation(ctx), "Отменено.")
            self.assertTrue(target.exists())

    def test_changed_target_invalidates_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "file.txt"
            target.write_text("first", encoding="utf-8")
            runner = ComputerActionRunner(None, directory)
            ctx = context([{"action": "delete_path", "args": {"path": str(target)}}], FakeDecider("file_management", "approve"))
            runner.run(ctx)
            target.write_text("changed and longer", encoding="utf-8")
            self.assertIn("не выполнено", runner.resolve_confirmation(ctx))
            self.assertTrue(target.exists())

    def test_catalog_has_executable_categories_and_confirmation_policies(self):
        catalog = load_catalog()
        self.assertGreaterEqual(len(catalog["actions"]), 25)
        self.assertGreaterEqual(len(catalog["categories"]), 8)
        self.assertEqual(catalog["actions"]["delete_path"]["confirmation"], "always")

    def test_planner_receives_only_selected_category_actions(self):
        prompts = []
        ctx = context([], FakeDecider("file_discovery"))
        def reply(messages):
            prompts.append(messages[0]["content"])
            return {"message": {"content": '{"action":"reply","text":"Nothing to do"}'}}
        ctx.ollama_chat = reply
        runner = ComputerActionRunner(None, ".")
        self.assertEqual(runner.run(ctx), "Nothing to do")
        self.assertIn("search_files:", prompts[0])
        self.assertNotIn("delete_path:", prompts[0])

    def test_create_file_and_copy_require_correct_catalog_category(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.txt"
            copy = Path(directory) / "copy.txt"
            runner = ComputerActionRunner(None, directory)
            ctx = context([
                {"action": "create_text_file", "args": {"path": str(source), "text": "hello"}},
                {"action": "copy_path", "args": {"source": str(source), "destination": str(copy)}},
                {"action": "reply", "text": "Готово"},
            ], FakeDecider("file_content", "file_management", "approve", "file_management"))
            self.assertIn(str(copy), runner.run(ctx))
            self.assertTrue(source.exists())
            self.assertFalse(copy.exists())
            self.assertEqual(runner.resolve_confirmation(ctx), "Готово")
            self.assertEqual(copy.read_text(encoding="utf-8"), "hello")

    def test_zip_with_parent_path_is_rejected_after_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "unsafe.zip"
            destination = Path(directory) / "extracted"
            outside = Path(directory) / "outside.txt"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("../outside.txt", "wrong place")
            runner = ComputerActionRunner(None, directory)
            ctx = context([
                {"action": "extract_zip", "args": {"source": str(archive), "destination": str(destination)}}
            ], FakeDecider("file_management", "approve"))
            self.assertIn(str(archive), runner.run(ctx))
            self.assertIn("не выполнено", runner.resolve_confirmation(ctx))
            self.assertFalse(destination.exists())
            self.assertFalse(outside.exists())

    def test_zip_round_trip_keeps_relative_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "notes"
            nested = source / "nested"
            nested.mkdir(parents=True)
            (nested / "entry.txt").write_text("saved", encoding="utf-8")
            archive = Path(directory) / "notes.zip"
            extracted = Path(directory) / "unpacked"
            runner = ComputerActionRunner(None, directory)
            runner._execute(None, "create_zip", {"source": str(source), "destination": str(archive)})
            runner._execute(None, "extract_zip", {"source": str(archive), "destination": str(extracted)})
            self.assertEqual((extracted / "notes" / "nested" / "entry.txt").read_text(encoding="utf-8"), "saved")

    def test_browser_click_waits_for_exact_element_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = ComputerActionRunner(None, directory)
            element = {"url": "https://example.com/order", "id": "alfred-3", "tag": "button",
                       "label": "Place order", "href": "", "tab_id": 1}
            clicked = Mock(return_value={"url": element["url"]})
            runner.browser = SimpleNamespace(describe_element=lambda element_id: element, click=clicked)
            ctx = context([
                {"action": "browser_click_element", "args": {"element_id": "alfred-3"}},
                {"action": "reply", "text": "Clicked"},
            ], FakeDecider("browser_interaction", "approve", "browser_interaction"))
            prompt = runner.run(ctx)
            self.assertIn("Place order", prompt)
            clicked.assert_not_called()
            self.assertEqual(runner.resolve_confirmation(ctx), "Clicked")
            clicked.assert_called_once_with("alfred-3")

    def test_recoverable_delete_can_be_restored_after_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "keep.txt"
            target.write_text("keep me", encoding="utf-8")
            runner = ComputerActionRunner(None, directory)
            remove_context = context([
                {"action": "move_to_trash", "args": {"path": str(target)}},
                {"action": "reply", "text": "Moved"},
            ], FakeDecider("file_management", "approve", "file_management"))
            self.assertIn(str(target), runner.run(remove_context))
            self.assertTrue(target.exists())
            self.assertEqual(runner.resolve_confirmation(remove_context), "Moved")
            self.assertFalse(target.exists())
            item_id = runner.trash.list_items()[0]["id"]
            restore_context = context([
                {"action": "restore_trash", "args": {"item_id": item_id}},
                {"action": "reply", "text": "Restored"},
            ], FakeDecider("file_management", "approve", "file_management"))
            self.assertIn(str(target), runner.run(restore_context))
            self.assertFalse(target.exists())
            self.assertEqual(runner.resolve_confirmation(restore_context), "Restored")
            self.assertEqual(target.read_text(encoding="utf-8"), "keep me")

    def test_literal_text_edit_can_be_undone(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "note.txt"
            target.write_text("first value", encoding="utf-8")
            runner = ComputerActionRunner(None, directory)
            edit_context = context([
                {"action": "replace_text_in_file", "args": {"path": str(target), "old": "first", "new": "second", "all": False}},
                {"action": "reply", "text": "Edited"},
            ], FakeDecider("file_content", "approve", "file_content"))
            self.assertIn(str(target), runner.run(edit_context))
            self.assertEqual(target.read_text(encoding="utf-8"), "first value")
            self.assertEqual(runner.resolve_confirmation(edit_context), "Edited")
            self.assertEqual(target.read_text(encoding="utf-8"), "second value")
            undo_context = context([
                {"action": "undo_last_text_edit", "args": {}},
                {"action": "reply", "text": "Undone"},
            ], FakeDecider("file_content", "approve", "file_content"))
            runner.run(undo_context)
            self.assertEqual(runner.resolve_confirmation(undo_context), "Undone")
            self.assertEqual(target.read_text(encoding="utf-8"), "first value")


if __name__ == "__main__":
    unittest.main()
