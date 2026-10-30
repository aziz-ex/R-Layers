"""Atomic JSON storage and a process lock, using only the standard library."""
import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path


class RegistryError(RuntimeError):
    pass


class RegistryStore:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "packages.json"
        self.backup = self.directory / "packages.json.bak"
        self.lock_path = self.directory / "packages.lock"
        self._thread_lock = threading.RLock()

    @contextmanager
    def locked(self, timeout=15):
        """Serialize complete read/modify/write operations across CLI/web processes."""
        with self._thread_lock:
            with self.lock_path.open("a+b") as handle:
                handle.seek(0, os.SEEK_END)
                if not handle.tell():
                    handle.write(b"0")
                    handle.flush()
                deadline = time.monotonic() + timeout
                while True:
                    try:
                        handle.seek(0)
                        if os.name == "nt":
                            import msvcrt
                            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError as exc:
                        if time.monotonic() >= deadline:
                            raise RegistryError("Another R-Layers operation is busy. Try again shortly.") from exc
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def validate(data):
        if not isinstance(data, dict):
            raise ValueError("registry must be an object")
        for name, info in data.items():
            if not isinstance(name, str) or not isinstance(info, dict):
                raise ValueError("each package must have an object record")
            deps = info.get("dependencies", [])
            if not isinstance(deps, list) or any(not isinstance(dep, str) for dep in deps):
                raise ValueError("dependencies must be a list of names")
            for field in ("source_url", "local_path", "commit", "executable", "protection", "license", "credit_to", "type"):
                if field in info and not isinstance(info[field], str):
                    raise ValueError(f"{field} must be text")
            for field in ("installed", "source_downloaded", "verified"):
                if field in info and not isinstance(info[field], bool):
                    raise ValueError(f"{field} must be a boolean")
        return data

    def read(self):
        try:
            return self.validate(json.loads(self.path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise RegistryError(
                f"Cannot read {self.path}: {exc}. Original data was preserved. "
                "Use 'python rlayers.py recover' to restore the last valid backup."
            ) from exc

    def _replace(self, target, data):
        fd, temporary = tempfile.mkstemp(prefix=".registry-", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            # Windows readers can briefly hold a handle that disallows replacement.
            for attempt in range(20):
                try:
                    os.replace(temporary, target)
                    break
                except PermissionError:
                    if attempt == 19:
                        raise
                    time.sleep(0.025)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def write(self, data):
        """Caller holds locked(); keep a valid previous snapshot before replacing."""
        self.validate(data)
        if self.path.exists():
            self._replace(self.backup, self.read())
        self._replace(self.path, data)

    def recover(self):
        with self.locked():
            try:
                data = self.validate(json.loads(self.backup.read_text(encoding="utf-8")))
            except (OSError, ValueError) as exc:
                raise RegistryError(f"No valid backup at {self.backup}: {exc}") from exc
            # Retain the damaged bytes for manual inspection; never overwrite a previous copy.
            if self.path.exists():
                fd, preserved = tempfile.mkstemp(prefix="packages-damaged-", suffix=".json", dir=self.directory)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(self.path.read_bytes())
            self._replace(self.path, data)
            return data
