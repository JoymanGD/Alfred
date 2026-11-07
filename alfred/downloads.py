"""Background downloads with progress, integrity checks and completion notices."""

import hashlib
import shutil
import threading
import urllib.request
import uuid
from pathlib import Path


class DownloadManager:
    def __init__(self, output_queue=None):
        self.output_queue = output_queue
        self._jobs = {}
        self._lock = threading.Lock()

    def start(self, url: str, destination: Path, expected_sha256: str = "") -> dict:
        job_id = uuid.uuid4().hex[:12]
        job = {"id": job_id, "url": url, "destination": str(destination), "status": "queued",
               "bytes_downloaded": 0, "total_bytes": None, "sha256": None, "error": None}
        with self._lock:
            self._jobs[job_id] = job
        threading.Thread(target=self._run, args=(job_id, expected_sha256.lower()), daemon=True,
                         name=f"alfred-download-{job_id}").start()
        return dict(job)

    def status(self, job_id: str | None = None) -> list[dict] | dict:
        with self._lock:
            if job_id:
                if job_id not in self._jobs:
                    raise ValueError("Download job not found")
                return dict(self._jobs[job_id])
            return [dict(job) for job in self._jobs.values()]

    def _update(self, job_id: str, **changes):
        with self._lock:
            self._jobs[job_id].update(changes)

    def _run(self, job_id: str, expected_sha256: str):
        job = self.status(job_id)
        destination = Path(job["destination"])
        temporary = destination.with_name(destination.name + f".{job_id}.part")
        try:
            self._update(job_id, status="downloading")
            request = urllib.request.Request(job["url"], headers={"User-Agent": "Alfred/1.0"})
            digest = hashlib.sha256()
            with urllib.request.urlopen(request, timeout=30) as response:
                if response.headers.get_content_type() == "text/html" and destination.suffix.lower() not in {".html", ".htm"}:
                    raise ValueError("URL returned a webpage rather than a file")
                total = response.headers.get("Content-Length")
                self._update(job_id, total_bytes=int(total) if total and total.isdigit() else None)
                with temporary.open("xb") as output:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        digest.update(chunk)
                        self._update(job_id, bytes_downloaded=output.tell())
            actual = digest.hexdigest()
            if expected_sha256 and actual != expected_sha256:
                raise ValueError("SHA-256 does not match the expected checksum")
            with temporary.open("rb") as source, destination.open("xb") as output:
                try:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                except Exception:
                    output.close()
                    destination.unlink(missing_ok=True)
                    raise
            temporary.unlink()
            self._update(job_id, status="completed", sha256=actual)
            if self.output_queue is not None:
                self.output_queue.put(f"Загрузка завершена: {destination}")
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            self._update(job_id, status="failed", error=str(exc))
            if self.output_queue is not None:
                self.output_queue.put(f"Загрузка файла {destination.name} не удалась: {exc}")
