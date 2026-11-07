import http.server
import queue
import hashlib
import tempfile
import threading
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from alfred.browser_automation import ManagedBrowser
from alfred.downloads import DownloadManager
from alfred.extended_actions import AlfredTrash, OcrEngine, TextUndoStore, extract_pdf_text
from alfred.reminders import ReminderService, parse_due_time


class ExtendedActionTests(unittest.TestCase):
    def test_trash_restores_exact_original_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "notes.txt"
            source.write_text("important", encoding="utf-8")
            trash = AlfredTrash(root / "state" / "trash")
            item = trash.move(source)
            self.assertFalse(source.exists())
            self.assertEqual(len(trash.list_items()), 1)
            self.assertEqual(trash.restore(item["id"]), str(source))
            self.assertEqual(source.read_text(encoding="utf-8"), "important")
            self.assertEqual(trash.list_items(), [])

    def test_text_undo_refuses_to_overwrite_later_user_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "notes.txt"
            path.write_text("before", encoding="utf-8")
            undo = TextUndoStore(root / "state")
            item_id = undo.begin(path)
            path.write_text("after", encoding="utf-8")
            undo.finish(item_id, path)
            path.write_text("user changed it", encoding="utf-8")
            with self.assertRaises(ValueError):
                undo.undo_latest()
            self.assertEqual(path.read_text(encoding="utf-8"), "user changed it")

    def test_reminder_is_delivered_and_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            output = queue.Queue()
            service = ReminderService(Path(directory) / "reminders.json", output)
            service.add("Take a break", parse_due_time(seconds_from_now=0.2))
            self.assertIn("Take a break", output.get(timeout=5))
            self.assertEqual(service.list_items(), [])

    def test_managed_browser_inspects_local_page_and_fills_field(self):
        class Page(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'<html><title>Alfred test</title><body><label>Name <input placeholder="Name"></label><button>Continue</button></body></html>'
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Page)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                browser = ManagedBrowser(Path(directory) / "profile", headless=True)
                try:
                    opened = browser.open_page(f"http://127.0.0.1:{server.server_port}/")
                    self.assertEqual(opened["title"], "Alfred test")
                    self.assertEqual(browser.read_page()["title"], "Alfred test")
                    field = browser.find_elements("Name")["elements"][0]
                    self.assertEqual(field["tag"], "input")
                    browser.fill(field["id"], "Joyman")
                    self.assertEqual(browser._page().locator("input").input_value(), "Joyman")
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()

    def test_download_reports_completion_and_checksum(self):
        content = b"Alfred download test" * 1000

        class File(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), File)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                output = queue.Queue()
                manager = DownloadManager(output)
                destination = Path(directory) / "download.bin"
                checksum = hashlib.sha256(content).hexdigest()
                job = manager.start(f"http://127.0.0.1:{server.server_port}/file", destination, checksum)
                self.assertIn(str(destination), output.get(timeout=5))
                status = manager.status(job["id"])
                self.assertEqual(status["status"], "completed")
                self.assertEqual(status["sha256"], checksum)
                self.assertEqual(destination.read_bytes(), content)
                rejected = Path(directory) / "rejected.bin"
                bad_job = manager.start(f"http://127.0.0.1:{server.server_port}/file", rejected, "0" * 64)
                self.assertIn("не удалась", output.get(timeout=5))
                self.assertEqual(manager.status(bad_job["id"])["status"], "failed")
                self.assertFalse(rejected.exists())
        finally:
            server.shutdown()
            server.server_close()

    def test_pdf_and_image_text_are_extracted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / "sample.pdf"
            writer = PdfWriter()
            page = writer.add_blank_page(width=300, height=300)
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                     NameObject("/Subtype"): NameObject("/Type1"),
                                     NameObject("/BaseFont"): NameObject("/Helvetica")})
            font_ref = writer._add_object(font)
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})
            })
            stream = DecodedStreamObject()
            stream.set_data(b"BT /F1 20 Tf 20 250 Td (Hello Alfred) Tj ET")
            page[NameObject("/Contents")] = writer._add_object(stream)
            with pdf.open("wb") as output:
                writer.write(output)
            self.assertIn("Hello Alfred", extract_pdf_text(pdf)["pages"][0]["text"])

            image_path = root / "sample.png"
            image = Image.new("RGB", (700, 140), "white")
            font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 80)
            ImageDraw.Draw(image).text((20, 25), "HELLO 123", font=font, fill="black")
            image.save(image_path)
            ocr = OcrEngine()
            ocr.warmup()
            recognized = ocr.read_image(image_path)["text"].replace("\n", " ")
            self.assertIn("HELLO", recognized)
            self.assertIn("123", recognized)


if __name__ == "__main__":
    unittest.main()
