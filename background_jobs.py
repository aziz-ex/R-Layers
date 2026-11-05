"""Persistent, single-process background jobs for the local web interface."""
import json
import os
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone


ACTIVE = {"queued", "running"}
TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}
MAX_HISTORY = 40
MAX_PENDING = 8


def _now():
    return datetime.now(timezone.utc).isoformat()


class BackgroundJobs:
    """Run source downloads and checks without holding an HTTP request open."""

    def __init__(self, manager):
        self.manager = manager
        self.path = manager.cache_dir / "web-jobs.json"
        self._lock = threading.RLock()
        self._load_failed = False
        self._jobs = self._load()
        self._cancel = {}
        self._last_progress_save = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rlayers-job")
        changed = False
        for job in self._jobs:
            if job.get("status") in ACTIVE:
                job.update(status="interrupted", message="The web server stopped before this job finished.",
                            cancel_requested=False, updated_at=_now())
                changed = True
        if changed:
            self._save_safely()

    def _load(self):
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError:
            self._load_failed = True
            return []
        try:
            data = json.loads(raw)
            if not isinstance(data, list):
                self._load_failed = True
                return []
            result = []
            for item in data[-MAX_HISTORY:]:
                valid = (isinstance(item, dict) and isinstance(item.get("id"), str)
                         and isinstance(item.get("name"), str) and item.get("action") in ("download", "verify")
                         and isinstance(item.get("status"), str) and item.get("status") in ACTIVE | TERMINAL)
                if valid:
                    result.append(item)
                else:
                    self._load_failed = True
            return result
        except ValueError:
            self._load_failed = True
            return []

    def _save(self):
        if self._load_failed and self.path.exists():
            raise OSError("Preserving unreadable background job history.")
        self.manager.cache_dir.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".web-jobs-", suffix=".tmp", dir=self.manager.cache_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(self._jobs[-MAX_HISTORY:], stream, ensure_ascii=False, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def _save_safely(self):
        try:
            self._save()
        except OSError:
            # The work itself remains usable if the optional history file cannot be written.
            pass

    @staticmethod
    def _public(job):
        return {key: job.get(key) for key in (
            "id", "action", "name", "status", "progress", "message", "created_at", "updated_at", "error",
            "cancel_requested",
        )}

    def snapshot(self):
        with self._lock:
            return [self._public(job) for job in reversed(self._jobs[-12:])]

    def has_active(self):
        with self._lock:
            return any(job.get("status") in ACTIVE for job in self._jobs)

    def _find(self, job_id):
        return next((job for job in self._jobs if job.get("id") == job_id), None)

    def submit(self, action, name):
        if action not in ("download", "verify"):
            raise ValueError("Only source downloads and verification can run as background jobs.")
        self.manager._validate_name(name)
        packages = self.manager.snapshot()
        canonical = next((known for known, item in packages.items()
                          if known.casefold() == name.casefold() and item.get("type") == "bundled_app"), None)
        if canonical is None:
            raise ValueError(f"'{name}' is not a registered app")
        with self._lock:
            active = [job for job in self._jobs if job.get("status") in ACTIVE]
            if len(active) >= MAX_PENDING:
                raise ValueError("The background job queue is full. Try again when a job finishes.")
            if any(job.get("name", "").casefold() == canonical.casefold() for job in active):
                raise ValueError(f"A source job for '{canonical}' is already active.")
            job = {
                "id": uuid.uuid4().hex,
                "action": action,
                "name": canonical,
                "status": "queued",
                "progress": 0,
                "message": "Waiting for the current job to finish.",
                "created_at": _now(),
                "updated_at": _now(),
                "error": "",
                "cancel_requested": False,
            }
            event = threading.Event()
            self._jobs.append(job)
            self._cancel[job["id"]] = event
            self._last_progress_save[job["id"]] = time.monotonic()
            self._prune()
            try:
                self._save()
            except OSError:
                self._jobs.remove(job)
                self._cancel.pop(job["id"], None)
                raise
            self._executor.submit(self._run, job["id"], event)
            return self._public(job)

    def _prune(self):
        while len(self._jobs) > MAX_HISTORY:
            index = next((i for i, job in enumerate(self._jobs) if job.get("status") not in ACTIVE), None)
            if index is None:
                break
            self._jobs.pop(index)

    def _update(self, job_id, **values):
        with self._lock:
            job = self._find(job_id)
            if job is None:
                return
            job.update(values, updated_at=_now())
            should_save = "progress" not in values or time.monotonic() - self._last_progress_save.get(job_id, 0) >= 0.5
            if should_save:
                self._last_progress_save[job_id] = time.monotonic()
                self._save_safely()

    def _progress(self, job_id, percent, message):
        if percent is not None:
            percent = max(0, min(100, int(percent)))
        self._update(job_id, progress=percent, message=str(message)[:240])

    def _run(self, job_id, event):
        with self._lock:
            job = self._find(job_id)
            if job is None or job.get("status") != "queued":
                return
            if event.is_set():
                job.update(status="cancelled", message="Cancelled before starting.", updated_at=_now())
                self._cancel.pop(job_id, None)
                self._save_safely()
                return
            job.update(status="running", progress=0, message="Starting.", cancel_requested=False, updated_at=_now())
            action, name = job["action"], job["name"]
            self._save_safely()

        try:
            method = self.manager.download_app if action == "download" else self.manager.verify_app
            ok = method(name, progress_callback=lambda percent, message: self._progress(job_id, percent, message),
                        cancel_event=event)
            error = self.manager.last_error if not ok else ""
        except Exception as exc:  # the server process must survive a failed worker
            ok, error = False, str(exc)

        with self._lock:
            job = self._find(job_id)
            self._cancel.pop(job_id, None)
            if job is None:
                return
            if event.is_set() and not ok:
                job.update(status="cancelled", message="Cancelled. Temporary download files were cleaned up.", error="", cancel_requested=False)
            elif ok:
                job.update(status="succeeded", progress=100, message=f"{action.title()} complete.", error="", cancel_requested=False)
            else:
                job.update(status="failed", message=f"{action.title()} failed.", error=(error or "Operation failed.")[-1000:], cancel_requested=False)
            job["updated_at"] = _now()
            self._save_safely()

    def cancel(self, job_id):
        with self._lock:
            job = self._find(job_id)
            if job is None:
                raise KeyError("Background job was not found.")
            if job.get("action") != "download" or job.get("status") not in ACTIVE:
                raise ValueError("This job cannot be cancelled.")
            event = self._cancel.get(job_id)
            if event is None:
                raise ValueError("This job is no longer running.")
            event.set()
            if job.get("status") == "queued":
                job.update(status="cancelled", message="Cancelled before starting.", cancel_requested=False, updated_at=_now())
                self._cancel.pop(job_id, None)
            else:
                job.update(message="Cancelling download…", cancel_requested=True, updated_at=_now())
            self._save_safely()
            return self._public(job)

    def close(self, wait=False):
        with self._lock:
            for event in self._cancel.values():
                event.set()
        self._executor.shutdown(wait=wait, cancel_futures=not wait)
