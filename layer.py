"""R-Layers source registry and desktop app management."""
import functools
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlparse

import app_runtime
from storage import RegistryError, RegistryStore

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_CACHE = PROJECT_DIR / "pkg_cache"
ALLOWED_HOSTS = {"github.com"}
APPS = [
    ("Nautilus", "https://github.com/GNOME/nautilus", "GPL-3.0", "GNOME Project", "system"),
    ("Joplin", "https://github.com/laurent22/joplin", "AGPL-3.0", "Laurent Cozic and Joplin Contributors", "system"),
    ("Flameshot", "https://github.com/flameshot-org/flameshot", "MIT", "Flameshot Contributors", "system"),
    ("Firefox", "https://github.com/mozilla-firefox/firefox", "MPL-2.0", "Mozilla", "removable"),
    ("Ladybird", "https://github.com/LadybirdBrowser/ladybird", "BSD 2-Clause", "Ladybird Browser Initiative", "removable"),
    ("qutebrowser", "https://github.com/qutebrowser/qutebrowser", "GPL-3.0", "qutebrowser contributors", "removable"),
    ("VLC", "https://github.com/videolan/vlc", "GPL-2.0", "VideoLAN Organization", "removable"),
    ("Blender", "https://github.com/blender/blender", "GPL-2.0", "Blender Foundation", "removable"),
]
RETIRED = ["World Clock"]


def now():
    return datetime.now(timezone.utc).isoformat()


def operation(method):
    """Serialize complete mutations across CLI/web processes and reload stale state."""
    @functools.wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._operation_lock:
            self.last_error = ""
            try:
                with self.store.locked():
                    self.packages = self.store.read()
                    if self._migrate():
                        self._save_cache()
                    result = method(self, *args, **kwargs)
                    self.logger.info("%s %s: %s", method.__name__, args[:1], bool(result))
                    return result
            except (OSError, ValueError, RegistryError, subprocess.SubprocessError) as exc:
                self.last_error = str(exc)
                if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
                    self.last_error += "\n" + exc.stderr.strip()[-2000:]
                self.logger.exception("%s failed", method.__name__)
                print(f"Error: {self.last_error}")
                return False
    return wrapped


class PackageAbstractionLayer:
    def __init__(self, cache_dir=None):
        self.store = RegistryStore(cache_dir or os.environ.get("RLAYERS_CACHE_DIR") or DEFAULT_CACHE)
        self.cache_dir = self.store.directory
        self.cache_file = self.store.path
        self.apps_dir = self.cache_dir / "apps"
        self._operation_lock = threading.RLock()
        self.last_error = ""
        self.logger = logging.getLogger(f"rlayers.{self.cache_dir}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        if not self.logger.handlers:
            handler = RotatingFileHandler(self.cache_dir / f"rlayers-{os.getpid()}.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            self.logger.addHandler(handler)
        with self.store.locked():
            self.packages = self.store.read()
            if self._migrate():
                self._save_cache()

    @staticmethod
    def _validate_name(name):
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}", name):
            raise ValueError("Invalid package name. Use letters, digits, spaces, dots, underscores or hyphens.")
        reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
        if name.endswith((".", " ")) or name.split(".")[0].upper() in reserved:
            raise ValueError("Package name is reserved or ambiguous on Windows.")

    @staticmethod
    def _validate_url(url):
        parsed = urlparse(url)
        if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or parsed.query or parsed.fragment
                or not re.fullmatch(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?", parsed.path)):
            raise ValueError("Source must be an HTTPS GitHub repository URL.")

    def _migrate(self):
        changed = False
        for name, info in self.packages.items():
            self._validate_name(name)
            if info.get("type") == "bundled_app" and "source_downloaded" not in info:
                info["source_downloaded"] = bool(info.get("installed") and info.get("local_path"))
                info["installed"] = False
                info["verified"] = False
                changed = True
        for name, url, license_name, credit, protection in APPS:
            info = self.packages.setdefault(name, {"type": "bundled_app", "added_at": now()})
            metadata = {"type": "bundled_app", "license": license_name, "credit_to": credit, "protection": protection}
            if not info.get("source_downloaded") or not info.get("source_url"):
                metadata["source_url"] = url
            for field, value in metadata.items():
                if info.get(field) != value:
                    info[field] = value
                    changed = True
        return changed

    def _load_cache(self):
        return self.store.read()

    def _save_cache(self):
        self.store.write(self.packages)

    def snapshot(self):
        return self.store.read()

    def close(self):
        for handler in self.logger.handlers[:]:
            handler.close()
            self.logger.removeHandler(handler)

    def _app_path(self, name):
        self._validate_name(name)
        if self.apps_dir.resolve() != self.cache_dir / "apps":
            raise ValueError("The apps directory must not redirect outside its cache.")
        path = self.apps_dir / name
        if path.resolve() != path:
            raise ValueError(f"Refusing redirected app path: {path}")
        return path

    def _recorded_path(self, name, info):
        expected = self._app_path(name)
        value = info.get("local_path")
        if not value:
            return expected
        path = Path(value)
        if not path.is_absolute():
            path = PROJECT_DIR / path
        if path.resolve() != expected:
            raise ValueError(f"Refusing source path outside its managed app directory: {path}")
        return expected

    def _app(self, name):
        self._validate_name(name)
        info = self.packages.get(name)
        if not info or info.get("type") != "bundled_app":
            raise ValueError(f"'{name}' is not a registered app")
        return info

    @staticmethod
    def _git(path, *args):
        return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=120).stdout.strip()

    def _inspect_source(self, name, info):
        path = self._recorded_path(name, info)
        if not path.is_dir() or not (path / ".git").is_dir() or (path / ".git").resolve() != path / ".git":
            raise ValueError(f"No managed Git source found for '{name}'")
        if Path(self._git(path, "rev-parse", "--show-toplevel")).resolve() != path:
            raise ValueError("Git repository root does not match the app directory")
        commit = self._git(path, "rev-parse", "HEAD")
        if not info.get("commit") or commit != info["commit"]:
            raise ValueError(f"'{name}' commit does not match the recorded download")
        flags = self._git(path, "ls-files", "-v").splitlines()
        if any(line and (line[0].islower() or line[0] == "S") for line in flags):
            raise ValueError("Cannot verify files hidden by assume-unchanged or skip-worktree index flags")
        if self._git(path, "status", "--porcelain", "--untracked-files=all", "--ignore-submodules=none"):
            raise ValueError(f"'{name}' contains modified, deleted, staged or untracked files")
        self._git(path, "fsck", "--no-dangling")
        return commit

    @operation
    def add_package(self, name, version, dependencies=None):
        self._validate_name(name)
        dependencies = dependencies or []
        if not isinstance(dependencies, list):
            raise ValueError("Dependencies must be a list of package names")
        for dep in dependencies:
            self._validate_name(dep)
        if any(known.casefold() == name.casefold() for known in self.packages):
            print(f"Package {name} already exists")
            return False
        self.packages[name] = {"version": str(version), "dependencies": dependencies, "added_at": now(), "cached": True}
        self._save_cache()
        print(f"Package {name} v{version} added successfully")
        return True

    def get_package(self, name):
        return self.snapshot().get(name)

    def package_exists(self, name):
        return name in self.snapshot()

    @operation
    def add_bundled_app(self, name, source_url, license_name, credit_to, protection="removable"):
        self._validate_name(name)
        self._validate_url(source_url)
        if protection not in ("system", "removable"):
            raise ValueError("Protection must be 'system' or 'removable'")
        if any(known.casefold() == name.casefold() for known in self.packages):
            print(f"App {name} already registered")
            return False
        self.packages[name] = {"type": "bundled_app", "source_url": source_url, "license": license_name,
                               "credit_to": credit_to, "protection": protection, "added_at": now()}
        self._save_cache()
        return True

    def print_credits(self):
        print("\nCredits:")
        for name, info in self.snapshot().items():
            if info.get("type") == "bundled_app":
                print(f"  {name} — by {info.get('credit_to', '')} ({info.get('license', '')})")
                print(f"    Source: {info.get('source_url', '')}")

    @operation
    def download_app(self, name, apps_dir=None, timeout=1800):
        info = self._app(name)
        self._validate_url(info.get("source_url", ""))
        if apps_dir is not None and Path(apps_dir).resolve() != self.apps_dir:
            raise ValueError("Downloads must stay in this registry's apps directory")
        dest = self._app_path(name)
        if dest.exists():
            if not info.get("commit"):
                raise ValueError(f"Unregistered files exist at {dest}; move them aside before downloading.")
            info["verified"] = False
            self._save_cache()
            commit = self._inspect_source(name, info)
        else:
            self.apps_dir.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=f".download-{name}-", dir=self.apps_dir))
            print(f"Downloading source for '{name}'...", flush=True)
            try:
                subprocess.run(["git", "-c", "core.longpaths=true", "clone", "--depth", "1", "--",
                                info["source_url"], str(staging)], check=True, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=timeout,
                               env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
                commit = self._git(staging, "rev-parse", "HEAD")
                staging.rename(dest)
            finally:
                if staging.exists():
                    self._rmtree(staging)
        info.update(local_path=str(dest), commit=commit, source_downloaded=True, verified=False)
        self._save_cache()
        print(f"Source downloaded: {dest} ({commit[:7]}). Use install or link to run the app.")
        return True

    def download_all(self, apps_dir=None):
        names = [name for name, info in self.snapshot().items() if info.get("type") == "bundled_app"]
        results = [self.download_app(name, apps_dir) for name in names]
        count = sum(map(bool, results))
        print(f"Downloaded: {count}; failed: {len(results) - count}")
        return all(results)

    @operation
    def update_source(self, name, new_url):
        self._validate_url(new_url)
        info = self._app(name)
        if info.get("source_downloaded"):
            raise ValueError("Remove the managed source before changing its URL")
        if name in {app[0] for app in APPS}:
            raise ValueError("Built-in source URLs are maintained by the catalog")
        info["source_url"] = new_url
        self._save_cache()
        return True

    @operation
    def verify_app(self, name):
        info = self._app(name)
        info["verified"] = False
        self._save_cache()
        commit = self._inspect_source(name, info)
        info.update(verified=True, verified_at=now())
        self._save_cache()
        print(f"'{name}' source verified ({commit[:7]}): clean working tree and valid Git objects.")
        return True

    @staticmethod
    def _force_remove_readonly(func, path, exc_info):
        os.chmod(path, 0o700)
        func(path)

    def _rmtree(self, path):
        path = Path(path)
        if path.resolve().parent != self.apps_dir or path.is_symlink():
            raise ValueError(f"Refusing deletion outside the managed apps directory: {path}")
        if not path.exists():
            return
        try:
            shutil.rmtree(path, onexc=self._force_remove_readonly)
        except TypeError:
            shutil.rmtree(path, onerror=self._force_remove_readonly)

    @operation
    def remove_app(self, name, force=False):
        info = self._app(name)
        if info.get("protection") == "system" and not force:
            raise ValueError(f"'{name}' is protected. Use 'remove {name} --force' in the terminal.")
        path = self._recorded_path(name, info)
        if path.exists():
            if not info.get("local_path"):
                raise ValueError("Unregistered files occupy this app directory; refusing deletion")
            self._rmtree(path)
        for field in ("local_path", "commit", "verified_at"):
            info.pop(field, None)
        info.update(source_downloaded=False, verified=False)
        self._save_cache()
        print(f"Removed managed source for '{name}'. Its catalog entry and desktop installation are retained.")
        return True

    def resolve_dependencies(self, package_name):
        packages = self.snapshot()
        ordered, active, done = [], set(), set()

        def visit(name):
            if name not in packages:
                raise ValueError(f"Missing dependency: {name}")
            if name in active:
                raise ValueError(f"Dependency cycle detected at: {name}")
            if name in done:
                return
            active.add(name)
            for dep in packages[name].get("dependencies", []):
                visit(dep)
            active.remove(name)
            done.add(name)
            ordered.append(name)

        try:
            visit(package_name)
        except (ValueError, RecursionError) as exc:
            print(f"Error: {exc}")
            self.logger.warning("Dependency resolution failed: %s", exc)
            return None
        return ordered

    def list_packages(self):
        for name, info in self.snapshot().items():
            print(f"  {name} v{info.get('version', 'N/A')}")

    @operation
    def scan_apps(self):
        for name, info in self.packages.items():
            if info.get("type") != "bundled_app":
                continue
            executable = None
            if info.get("executable"):
                try:
                    executable = app_runtime.valid_executable(info["executable"])
                except ValueError:
                    pass
            executable = executable or app_runtime.discover(name)
            info["installed"] = bool(executable)
            if executable:
                info["executable"] = executable
                print(f"Found {name}: {executable}")
            else:
                info.pop("executable", None)
                print(f"Not detected: {name} (use link for a custom location)")
        self._save_cache()
        return True

    @operation
    def link_app(self, name, executable):
        info = self._app(name)
        info.update(executable=app_runtime.valid_executable(executable), installed=True)
        self._save_cache()
        print(f"Linked '{name}' to {info['executable']}")
        return True

    @operation
    def install_app(self, name, dry_run=False):
        info = self._app(name)
        existing = info.get("executable")
        if existing:
            try:
                app_runtime.valid_executable(existing)
            except ValueError:
                existing = None
        existing = existing or app_runtime.discover(name)
        if existing:
            try:
                executable = app_runtime.valid_executable(existing)
            except ValueError:
                executable = None
            if executable:
                if not dry_run:
                    info.update(executable=executable, installed=True)
                    self._save_cache()
                print(f"Already installed: {executable}")
                return True
        command = app_runtime.install_command(name)
        print("Install command: " + subprocess.list2cmdline(command))
        if dry_run:
            return True
        if not shutil.which(command[0]):
            raise ValueError(f"Package manager '{command[0]}' is unavailable. Install manually and use link.")
        if command[0] == "apt-get" and hasattr(os, "geteuid") and os.geteuid() != 0:
            raise ValueError("APT installation needs an administrator terminal. Run the displayed command with sudo, then scan.")
        subprocess.run(command, check=True, timeout=1800)
        executable = app_runtime.discover(name)
        if not executable:
            raise ValueError("Installer finished but executable was not detected. Use link with its actual path.")
        info.update(executable=executable, installed=True)
        self._save_cache()
        return True

    @operation
    def launch_app(self, name):
        info = self._app(name)
        executable = info.get("executable") or app_runtime.discover(name)
        if not executable:
            raise ValueError("App executable not found. Use scan, link or install first.")
        try:
            executable = app_runtime.valid_executable(executable)
        except ValueError:
            info["installed"] = False
            info.pop("executable", None)
            self._save_cache()
            raise
        subprocess.Popen([executable], cwd=str(Path(executable).parent), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False)
        info.update(executable=executable, installed=True)
        self._save_cache()
        print(f"Launched '{name}'")
        return True


if __name__ == "__main__":
    try:
        manager = PackageAbstractionLayer()
        manager.print_credits()
        print("\nNext: python rlayers.py")
    except (RegistryError, OSError) as error:
        print(f"Error: {error}")
        raise SystemExit(1)
