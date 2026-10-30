"""Read-only readiness checks; useful even when the registry cannot be opened."""
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import app_runtime
from layer import APPS, DEFAULT_CACHE
from storage import RegistryStore
from version import __version__


def diagnose(cache_dir=None):
    cache = Path(cache_dir or os.environ.get("RLAYERS_CACHE_DIR") or DEFAULT_CACHE).expanduser().resolve()
    checks = []

    def check(name, status, detail):
        checks.append({"name": name, "status": status, "detail": detail})

    check("python", "ok" if sys.version_info >= (3, 10) else "error", platform.python_version())
    git = shutil.which("git")
    if git:
        try:
            result = subprocess.run([git, "--version"], check=True, capture_output=True,
                                    text=True, encoding="utf-8", errors="replace", timeout=5)
            check("git", "ok", result.stdout.strip())
        except (OSError, subprocess.SubprocessError) as exc:
            check("git", "warning", f"Git could not start: {exc}")
    else:
        check("git", "warning", "Install Git to download and verify source repositories.")
    registry = cache / "packages.json"
    packages = {}
    if registry.exists():
        try:
            packages = RegistryStore.validate(json.loads(registry.read_text(encoding="utf-8")))
            check("registry", "ok", str(registry))
        except (OSError, ValueError) as exc:
            check("registry", "error", f"{exc}. Restore with: python rlayers.py recover")
    elif cache.exists() and not cache.is_dir():
        check("registry", "error", f"Cache path is not a directory: {cache}")
    else:
        check("registry", "ok", "A new registry will be created at first startup.")
    apps = []
    for name, *_ in APPS:
        info = packages.get(name, {})
        executable = None
        if info.get("executable"):
            try:
                executable = app_runtime.valid_executable(info["executable"])
            except (OSError, ValueError):
                pass
        try:
            executable = executable or app_runtime.discover(name)
        except OSError:
            executable = None
        apps.append({"name": name, "executable": executable,
                     "installation": app_runtime.installation_status(name)})
    managers = sorted({app["installation"]["manager"] for app in apps if app["installation"]["manager"]})
    for manager in managers:
        available = shutil.which(manager)
        check(manager, "ok" if available else "warning", available or "Unavailable; use a manual installation and link its executable.")
    return {"version": __version__, "system": platform.system(), "cache": str(cache),
            "ok": all(item["status"] != "error" for item in checks), "checks": checks, "apps": apps}


def print_report(report):
    print(f"R-Layers {report['version']} | {report['system']}")
    print(f"Cache: {report['cache']}")
    for check in report["checks"]:
        print(f"[{check['status'].upper()}] {check['name']}: {check['detail']}")
    print("\nDesktop applications:")
    for app in report["apps"]:
        status = app["installation"]
        state = (app["executable"] or ("Installer: " + status["manager"] if status["available"] else
                 "Install manually, then link the executable"))
        print(f"  {app['name']}: {state}")
