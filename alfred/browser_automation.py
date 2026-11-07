"""A controlled Edge/Chrome session for inspected browser interactions."""

import os
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse


class ManagedBrowser:
    def __init__(self, profile_dir: Path, *, headless: bool = False):
        self.profile_dir = profile_dir
        self.headless = headless
        self._playwright = None
        self._context = None
        self._pages = {}
        self._next_page_id = 1
        self._active_id = None

    def _start(self):
        if self._context is not None:
            return
        from playwright.sync_api import sync_playwright

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        executable = os.getenv("ALFRED_BROWSER_EXECUTABLE")
        if not executable:
            candidates = [
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            ]
            executable = next((path for path in candidates if Path(path).exists()), None)
        if not executable or not Path(executable).is_file():
            self._playwright.stop()
            self._playwright = None
            raise RuntimeError("Edge or Chrome was not found; set ALFRED_BROWSER_EXECUTABLE")
        try:
            self._context = self._playwright.chromium.launch_persistent_context(
                str(self.profile_dir), executable_path=executable, headless=self.headless,
                accept_downloads=True, no_viewport=True,
            )
            self._context.set_default_timeout(10000)
        except Exception:
            self._playwright.stop()
            self._playwright = None
            raise

    def _register(self, page) -> int:
        for page_id, existing in self._pages.items():
            if existing is page:
                return page_id
        page_id = self._next_page_id
        self._next_page_id += 1
        self._pages[page_id] = page
        if self._active_id is None:
            self._active_id = page_id
        return page_id

    def _page(self, page_id=None):
        self._start()
        for page in self._context.pages:
            self._register(page)
        selected = page_id if page_id is not None else self._active_id
        page = self._pages.get(selected)
        if page_id is None and (page is None or page.is_closed()) and self._context.pages:
            page = self._context.pages[-1]
            self._active_id = self._register(page)
        if page is None or page.is_closed():
            raise ValueError("No active Alfred browser tab; open a page first")
        return page

    def open_page(self, url: str) -> dict:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("An HTTP(S) URL is required")
        self._start()
        page = self._context.new_page()
        page_id = self._register(page)
        self._active_id = page_id
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        page.bring_to_front()
        return {"tab_id": page_id, "title": page.title(), "url": page.url}

    def list_tabs(self) -> list[dict]:
        self._start()
        tabs = []
        for page in self._context.pages:
            if page.is_closed():
                continue
            page_id = self._register(page)
            tabs.append({"tab_id": page_id, "title": page.title(), "url": page.url,
                         "active": self._active_id == page_id})
        return tabs

    def select_tab(self, tab_id: int) -> dict:
        page = self._page(tab_id)
        page.bring_to_front()
        self._active_id = tab_id
        return {"tab_id": tab_id, "title": page.title(), "url": page.url}

    def read_page(self) -> dict:
        page = self._page()
        return {"tab_id": self._active_id, "url": page.url, "title": page.title(),
                "text": page.locator("body").inner_text(timeout=10000)[:16000]}

    def find_elements(self, query: str = "") -> dict:
        page = self._page()
        controls = page.evaluate("""() => Array.from(document.querySelectorAll(
            'a,button,input,textarea,select,[role="button"],[role="link"],[contenteditable="true"]'
        )).filter(el => el.getClientRects().length).slice(0, 300).map((el, index) => {
            const id = 'alfred-' + index;
            el.setAttribute('data-alfred-id', id);
            const label = el.getAttribute('aria-label') || el.getAttribute('placeholder') ||
                el.innerText || (el.type === 'password' ? '' : el.value) || el.getAttribute('title') || '';
            return {id, tag: el.tagName.toLowerCase(), type: el.getAttribute('type') || '',
                label: String(label).trim().slice(0, 160), href: el.getAttribute('href') || ''};
        })""")
        needle = query.casefold().strip()
        found = [item for item in controls if needle in (item["label"] + " " + item["href"]).casefold()] if needle else controls
        if not found:
            found = controls
        return {"tab_id": self._active_id, "url": page.url, "elements": found[:40],
                "total_visible_controls": len(controls)}

    def describe_element(self, element_id: str) -> dict:
        if not element_id.startswith("alfred-") or not element_id[7:].isdigit():
            raise ValueError("Use an element ID returned by browser_find_elements")
        page = self._page()
        locator = page.locator(f'[data-alfred-id="{element_id}"]')
        if locator.count() != 1:
            raise ValueError("Element is no longer present; inspect the page again")
        return {"tab_id": self._active_id, "url": page.url, "id": element_id,
                "tag": locator.evaluate("el => el.tagName.toLowerCase()"),
                "type": locator.get_attribute("type") or "",
                "label": locator.evaluate("el => (el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.innerText || (el.type === 'password' ? '' : el.value) || '').trim().slice(0,160)"),
                "href": locator.get_attribute("href") or ""}

    def click(self, element_id: str) -> dict:
        page = self._page()
        previous_pages = {id(item) for item in self._context.pages}
        page.locator(f'[data-alfred-id="{element_id}"]').click()
        for new_page in self._context.pages:
            tab_id = self._register(new_page)
            if id(new_page) not in previous_pages:
                self._active_id = tab_id
        return {"tab_id": self._active_id, "url": page.url, "title": page.title()}

    def fill(self, element_id: str, value: str) -> dict:
        page = self._page()
        page.locator(f'[data-alfred-id="{element_id}"]').fill(value)
        return {"tab_id": self._active_id, "url": page.url, "element_id": element_id, "characters": len(value)}

    def download(self, element_id: str, destination: Path) -> dict:
        page = self._page()
        with page.expect_download(timeout=30000) as pending:
            page.locator(f'[data-alfred-id="{element_id}"]').click()
        download = pending.value
        if destination.exists():
            raise FileExistsError(str(destination))
        with tempfile.NamedTemporaryFile(prefix="alfred-browser-", suffix=".part", dir=destination.parent, delete=False) as temporary:
            temporary_path = Path(temporary.name)
        try:
            download.save_as(str(temporary_path))
            with temporary_path.open("rb") as source, destination.open("xb") as output:
                try:
                    shutil.copyfileobj(source, output)
                except Exception:
                    output.close()
                    destination.unlink(missing_ok=True)
                    raise
        finally:
            temporary_path.unlink(missing_ok=True)
        return {"path": str(destination), "name": download.suggested_filename, "size": destination.stat().st_size}

    def back(self) -> dict:
        page = self._page()
        page.go_back(wait_until="domcontentloaded")
        return {"tab_id": self._active_id, "url": page.url, "title": page.title()}

    def close(self):
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
