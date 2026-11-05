import contextlib
import http.client
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import app_runtime
import rlayers
import rlayers_web as web
from background_jobs import BackgroundJobs
from layer import APPS, PROJECT_DIR, PackageAbstractionLayer
from storage import RegistryError, RegistryStore


class IsolatedCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rlayers-test-")
        self.root = Path(self.temporary.name).resolve()
        self.managers = []
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()

    def tearDown(self):
        for manager in self.managers:
            manager.close()
        self.output.__exit__(None, None, None)
        self.temporary.cleanup()

    def manager(self, directory=None):
        manager = PackageAbstractionLayer(directory or self.root / "cache")
        self.managers.append(manager)
        return manager

    def git(self, directory, *args):
        return subprocess.run(["git", "-C", str(directory), *args], check=True, capture_output=True,
                              text=True, encoding="utf-8").stdout.strip()

    def source(self, manager, name="Firefox"):
        directory = manager.apps_dir / name
        directory.mkdir(parents=True)
        self.git(directory, "init")
        self.git(directory, "config", "user.email", "test@example.invalid")
        self.git(directory, "config", "user.name", "R-Layers Test")
        self.git(directory, "config", "core.autocrlf", "false")
        (directory / "app.txt").write_text("original\n", encoding="utf-8")
        self.git(directory, "add", ".")
        self.git(directory, "commit", "-m", "initial")
        with manager.store.locked():
            manager.packages = manager.snapshot()
            manager.packages[name].update(local_path=str(directory), commit=self.git(directory, "rev-parse", "HEAD"), source_downloaded=True)
            manager._save_cache()
        return directory


class RegistryTests(IsolatedCase):
    def test_catalog_initializes_without_running_layer_script(self):
        self.assertEqual(set(self.manager().snapshot()), {app[0] for app in APPS})

    def test_legacy_protection_and_download_state_migrate(self):
        store = RegistryStore(self.root / "cache")
        with store.locked():
            store.write({"Joplin": {"type": "bundled_app", "license": "MIT", "installed": True,
                                    "local_path": "pkg_cache/apps/Joplin", "verified": True}})
        info = self.manager().get_package("Joplin")
        self.assertEqual(info["protection"], "system")
        self.assertEqual(info["license"], "AGPL-3.0")
        self.assertTrue(info["source_downloaded"])
        self.assertFalse(info["installed"])
        self.assertFalse(info["verified"])

    def test_corrupt_registry_is_preserved_and_recoverable(self):
        manager = self.manager()
        manager.add_package("first", "1")
        manager.add_package("second", "1")
        manager.cache_file.write_text("broken-json", encoding="utf-8")
        with self.assertRaises(RegistryError):
            manager.snapshot()
        restored = manager.store.recover()
        self.assertIn("first", restored)
        self.assertNotIn("second", restored)
        self.assertEqual(next(manager.cache_dir.glob("packages-damaged-*.json")).read_text(), "broken-json")

    def test_invalid_schema_is_reported(self):
        store = RegistryStore(self.root / "cache")
        store.path.write_text('{"app":{"dependencies":"invalid"}}', encoding="utf-8")
        with self.assertRaises(RegistryError):
            store.read()

    def test_failed_atomic_replace_keeps_previous_data(self):
        manager = self.manager()
        previous = manager.cache_file.read_bytes()
        with patch("storage.os.replace", side_effect=OSError("disk error")):
            self.assertFalse(manager.add_package("example", "1"))
        self.assertEqual(manager.cache_file.read_bytes(), previous)
        self.assertFalse(list(manager.cache_dir.glob(".registry-*.tmp")))

    def test_stale_instances_do_not_overwrite_changes(self):
        first, second = self.manager(), self.manager()
        self.assertTrue(first.add_package("first", "1"))
        self.assertTrue(second.add_package("second", "1"))
        self.assertTrue({"first", "second"} <= first.snapshot().keys())

    def test_concurrent_processes_preserve_all_updates(self):
        manager = self.manager()
        script = "from layer import PackageAbstractionLayer; import sys; m=PackageAbstractionLayer(sys.argv[1]); sys.exit(0 if m.add_package(sys.argv[2], '1') else 1)"
        def run(number):
            return subprocess.run([sys.executable, "-c", script, str(manager.cache_dir), f"worker{number}"],
                                  cwd=PROJECT_DIR, capture_output=True, text=True, timeout=30)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(run, range(6)))
        self.assertTrue(all(result.returncode == 0 for result in results), [r.stderr for r in results])
        self.assertTrue({f"worker{i}" for i in range(6)} <= manager.snapshot().keys())

    def test_lock_contention_reports_busy(self):
        first = RegistryStore(self.root / "cache")
        second = RegistryStore(self.root / "cache")
        with first.locked():
            with self.assertRaises(RegistryError):
                with second.locked(timeout=0.05):
                    self.fail("lock should be held")


class SourceTests(IsolatedCase):
    def test_clean_source_verifies(self):
        manager = self.manager()
        self.source(manager)
        self.assertTrue(manager.verify_app("Firefox"))

    def test_modified_deleted_staged_and_untracked_files_fail(self):
        for kind in ("modified", "deleted", "staged", "untracked"):
            with self.subTest(kind=kind):
                manager = self.manager(self.root / kind)
                directory = self.source(manager)
                self.assertTrue(manager.verify_app("Firefox"))
                if kind == "deleted":
                    (directory / "app.txt").unlink()
                elif kind == "untracked":
                    (directory / "extra.txt").write_text("extra")
                else:
                    (directory / "app.txt").write_text("changed")
                    if kind == "staged":
                        self.git(directory, "add", ".")
                self.assertFalse(manager.verify_app("Firefox"))
                self.assertFalse(manager.get_package("Firefox")["verified"])

    def test_commit_mismatch_fails(self):
        manager = self.manager()
        directory = self.source(manager)
        self.git(directory, "commit", "--allow-empty", "-m", "unexpected")
        self.assertFalse(manager.verify_app("Firefox"))

    def test_assume_unchanged_cannot_hide_modified_file(self):
        manager = self.manager()
        directory = self.source(manager)
        self.git(directory, "update-index", "--assume-unchanged", "app.txt")
        (directory / "app.txt").write_text("changed")
        self.assertFalse(manager.verify_app("Firefox"))

    def test_protection_survives_an_old_registry(self):
        manager = self.manager()
        directory = self.source(manager, "Joplin")
        with manager.store.locked():
            manager.packages = manager.snapshot()
            manager.packages["Joplin"].pop("protection")
            manager._save_cache()
        self.assertFalse(manager.remove_app("Joplin"))
        self.assertTrue(directory.exists())
        self.assertTrue(manager.remove_app("Joplin", force=True))

    def test_remove_keeps_catalog_and_external_executable(self):
        manager = self.manager()
        directory = self.source(manager)
        executable = self.root / "outside.exe"
        executable.write_bytes(b"application")
        with manager.store.locked():
            manager.packages = manager.snapshot()
            manager.packages["Firefox"].update(executable=str(executable), installed=True)
            manager._save_cache()
        self.assertTrue(manager.remove_app("Firefox"))
        self.assertFalse(directory.exists())
        self.assertTrue(executable.exists())
        self.assertTrue(manager.get_package("Firefox")["installed"])

    def test_outside_path_is_never_deleted(self):
        manager = self.manager()
        outside = self.root / "personal-data"
        outside.mkdir()
        with manager.store.locked():
            manager.packages = manager.snapshot()
            manager.packages["Firefox"]["local_path"] = str(outside)
            manager._save_cache()
        self.assertFalse(manager.remove_app("Firefox"))
        self.assertTrue(outside.exists())

    def test_symlink_escape_is_rejected(self):
        manager = self.manager()
        outside = self.root / "outside"
        outside.mkdir()
        manager.apps_dir.mkdir()
        try:
            (manager.apps_dir / "Firefox").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlink privilege unavailable")
        self.assertFalse(manager.download_app("Firefox"))
        self.assertTrue(outside.exists())

    def test_unsafe_names_and_sources_are_rejected(self):
        manager = self.manager()
        for name in ("../outside", "C:\\outside", "CON", "hello."):
            self.assertFalse(manager.add_bundled_app(name, "https://github.com/example/app", "MIT", "Test"))
        for url in ("http://github.com/example/app", "https://evil.example/app/repo", "https://user@github.com/a/b"):
            self.assertFalse(manager.add_bundled_app("Custom", url, "MIT", "Test"))
        self.assertFalse(manager.add_bundled_app("firefox", "https://github.com/example/app", "MIT", "Test"))

    def test_existing_unregistered_directory_is_preserved(self):
        manager = self.manager()
        directory = manager.apps_dir / "Firefox"
        directory.mkdir(parents=True)
        (directory / "personal.txt").write_text("keep")
        self.assertFalse(manager.download_app("Firefox"))
        self.assertEqual((directory / "personal.txt").read_text(), "keep")

    def test_failed_clone_cleans_only_owned_staging_directory(self):
        manager = self.manager()
        with patch("layer.subprocess.Popen", side_effect=subprocess.CalledProcessError(1, "git clone", stderr="clone failed")):
            self.assertFalse(manager.download_app("Firefox", timeout=1))
        self.assertEqual(list(manager.apps_dir.iterdir()), [])

    def test_cancelled_clone_cleans_its_staging_directory(self):
        manager = self.manager()
        cancel = threading.Event()
        processes = []
        real_popen = subprocess.Popen

        def long_lived_git(command, **kwargs):
            process = real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
            processes.append(process)
            return process

        def cancel_on_start(percent, message):
            if message == "Connecting to GitHub":
                cancel.set()

        with patch("layer.subprocess.Popen", side_effect=long_lived_git):
            self.assertFalse(manager.download_app("Firefox", progress_callback=cancel_on_start, cancel_event=cancel))
        self.assertEqual(list(manager.apps_dir.iterdir()), [])
        self.assertIn("cancelled", manager.last_error.lower())
        self.assertTrue(processes)
        self.assertIsNotNone(processes[0].poll())

    def test_download_and_reuse_real_local_git_repository(self):
        manager = self.manager()
        seed_manager = self.manager(self.root / "seed")
        seed = self.source(seed_manager)
        real_popen = subprocess.Popen
        def local_clone(command, **kwargs):
            if "clone" in command:
                command = [*command[:-2], str(seed), command[-1]]
            return real_popen(command, **kwargs)
        progress = []
        with patch("layer.subprocess.Popen", side_effect=local_clone):
            self.assertTrue(manager.download_app("Firefox", progress_callback=lambda percent, message: progress.append((percent, message))))
        self.assertTrue(manager.get_package("Firefox")["source_downloaded"])
        self.assertEqual(progress[-1][0], 100)
        self.assertFalse(manager.get_package("Firefox").get("installed", False))
        self.assertTrue(manager.verify_app("Firefox"))
        self.assertTrue(manager.download_app("Firefox"))


class DependencyTests(IsolatedCase):
    def test_dependency_order(self):
        manager = self.manager()
        manager.add_package("app", "1", ["compiler"])
        manager.add_package("compiler", "1", ["tools"])
        manager.add_package("tools", "1")
        self.assertEqual(manager.resolve_dependencies("app"), ["tools", "compiler", "app"])

    def test_missing_dependency_and_cycle_are_reported(self):
        manager = self.manager()
        manager.add_package("app", "1", ["missing"])
        self.assertIsNone(manager.resolve_dependencies("app"))
        manager.add_package("missing", "1", ["app"])
        self.assertIsNone(manager.resolve_dependencies("app"))


class RuntimeTests(IsolatedCase):
    def test_install_dry_run_never_executes_installer(self):
        manager = self.manager()
        with patch("app_runtime.discover", return_value=None), patch("layer.subprocess.run") as run:
            self.assertTrue(manager.install_app("Firefox", dry_run=True))
        run.assert_not_called()
        self.assertFalse(manager.get_package("Firefox").get("installed", False))

    def test_existing_app_is_linked_instead_of_reinstalled(self):
        manager = self.manager()
        with patch("app_runtime.discover", return_value=sys.executable), patch("layer.subprocess.run") as run:
            self.assertTrue(manager.install_app("Firefox"))
        run.assert_not_called()
        self.assertTrue(manager.get_package("Firefox")["installed"])

    def test_stale_link_does_not_trigger_reinstall_of_detectable_app(self):
        manager = self.manager()
        with manager.store.locked():
            manager.packages = manager.snapshot()
            manager.packages["Firefox"]["executable"] = str(self.root / "gone.exe")
            manager._save_cache()
        with patch("app_runtime.discover", return_value=sys.executable), patch("layer.subprocess.run") as run:
            self.assertTrue(manager.install_app("Firefox"))
        run.assert_not_called()

    def test_native_install_success_and_failure_update_state_correctly(self):
        manager = self.manager()
        with patch("app_runtime.discover", side_effect=[None, sys.executable]), \
                patch("app_runtime.install_command", return_value=["test-manager", "install", "Firefox"]), \
                patch("layer.shutil.which", return_value="test-manager"), patch("layer.subprocess.run") as run:
            self.assertTrue(manager.install_app("Firefox"))
        self.assertEqual(run.call_args.args[0], ["test-manager", "install", "Firefox"])
        self.assertTrue(manager.get_package("Firefox")["installed"])
        with patch("app_runtime.discover", return_value=None), \
                patch("app_runtime.install_command", return_value=["test-manager", "install", "VLC"]), \
                patch("layer.shutil.which", return_value="test-manager"), \
                patch("layer.subprocess.run", side_effect=subprocess.CalledProcessError(1, "test-manager", stderr="installer failed")):
            self.assertFalse(manager.install_app("VLC"))
        self.assertFalse(manager.get_package("VLC").get("installed", False))
        self.assertIn("installer failed", manager.last_error)

    def test_link_and_launch_use_argument_list_without_shell(self):
        manager = self.manager()
        self.assertTrue(manager.link_app("Firefox", sys.executable))
        with patch("layer.subprocess.Popen") as launch:
            self.assertTrue(manager.launch_app("Firefox"))
        self.assertEqual(launch.call_args.args[0], [str(Path(sys.executable).resolve())])
        self.assertFalse(launch.call_args.kwargs["shell"])

    def test_failed_link_does_not_mark_installed(self):
        manager = self.manager()
        self.assertFalse(manager.link_app("Firefox", str(self.root / "missing.exe")))
        self.assertFalse(manager.get_package("Firefox").get("installed", False))

    def test_install_commands_and_unsupported_apps(self):
        self.assertIn("Mozilla.Firefox", app_runtime.install_command("Firefox", "Windows"))
        self.assertEqual(app_runtime.install_command("Joplin", "Darwin"), ["brew", "install", "--cask", "joplin"])
        with patch("app_runtime.platform.freedesktop_os_release", return_value={"ID": "debian"}):
            self.assertEqual(app_runtime.install_command("Firefox", "Linux")[-1], "firefox-esr")
        with self.assertRaises(ValueError):
            app_runtime.install_command("Ladybird", "Windows")

    def test_scan_does_not_claim_missing_apps_are_installed(self):
        manager = self.manager()
        with patch("app_runtime.discover", side_effect=lambda name: sys.executable if name == "Firefox" else None):
            self.assertTrue(manager.scan_apps())
        self.assertTrue(manager.get_package("Firefox")["installed"])
        self.assertFalse(manager.get_package("VLC")["installed"])


class BackgroundJobTests(IsolatedCase):
    def wait_for_status(self, jobs, job_id, status, timeout=5):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            current = next(job for job in jobs.snapshot() if job["id"] == job_id)
            if current["status"] == status:
                return current
            time.sleep(0.02)
        self.fail(f"job did not reach {status}: {jobs.snapshot()}")

    def test_download_job_reports_progress_and_can_be_cancelled(self):
        manager = self.manager()
        jobs = BackgroundJobs(manager)
        started = threading.Event()

        def slow_download(name, progress_callback, cancel_event):
            started.set()
            progress_callback(35, "Receiving repository data")
            cancel_event.wait(5)
            manager.last_error = "Operation cancelled by user."
            return False

        try:
            with patch.object(manager, "download_app", side_effect=slow_download):
                job = jobs.submit("download", "firefox")
                self.assertTrue(started.wait(2))
                jobs.cancel(job["id"])
                result = self.wait_for_status(jobs, job["id"], "cancelled")
            self.assertEqual(result["name"], "Firefox")
            self.assertEqual(result["progress"], 35)
            self.assertFalse(jobs.has_active())
        finally:
            jobs.close(wait=True)

    def test_job_history_survives_service_recreation_and_marks_active_as_interrupted(self):
        manager = self.manager()
        jobs = BackgroundJobs(manager)
        try:
            with patch.object(manager, "verify_app", return_value=True):
                job = jobs.submit("verify", "Firefox")
                result = self.wait_for_status(jobs, job["id"], "succeeded")
            self.assertEqual(result["progress"], 100)
        finally:
            jobs.close(wait=True)
        reopened = BackgroundJobs(manager)
        try:
            self.assertEqual(reopened.snapshot()[0]["status"], "succeeded")
        finally:
            reopened.close(wait=True)

        payload = [{"id": "a" * 32, "action": "download", "name": "VLC", "status": "running",
                    "progress": 10, "message": "Downloading", "created_at": "now", "updated_at": "now", "error": ""}]
        jobs.path.write_text(json.dumps(payload), encoding="utf-8")
        recovered = BackgroundJobs(manager)
        try:
            self.assertEqual(recovered.snapshot()[0]["status"], "interrupted")
        finally:
            recovered.close(wait=True)

        history_path = manager.cache_dir / "web-jobs.json"
        history_path.write_text("damaged history", encoding="utf-8")
        unreadable = BackgroundJobs(manager)
        try:
            with patch.object(manager, "verify_app", return_value=True):
                with self.assertRaises(OSError):
                    unreadable.submit("verify", "Firefox")
            self.assertEqual(history_path.read_text(encoding="utf-8"), "damaged history")
        finally:
            unreadable.close(wait=True)


class WebTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        web.shutdown_jobs()
        self.manager_instance = self.manager()
        self.layer_patch = patch.object(web, "layer", self.manager_instance)
        self.layer_patch.start()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        web.shutdown_jobs()
        self.layer_patch.stop()
        super().tearDown()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        status, data = response.status, response.read()
        connection.close()
        return status, data

    def test_arabic_state_and_invalid_language(self):
        status, data = self.request("GET", "/api/state?lang=ar")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(data)["rtl"])
        self.assertEqual(web.state("../../x")["lang"], "en")
        self.assertFalse(next(app for app in json.loads(data)["apps"] if app["name"] == "Ladybird")["installation"]["supported"])

    def test_browser_can_link_an_existing_app_without_launching_it(self):
        body = json.dumps({"action": "link", "name": "Firefox", "executable": sys.executable})
        with patch("layer.subprocess.Popen") as launch:
            status, data = self.request("POST", "/api/action", body, {"X-Token": web.TOKEN})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(data)["ok"])
        self.assertTrue(self.manager_instance.get_package("Firefox")["installed"])
        launch.assert_not_called()

    def test_browser_starts_a_background_verification_and_persists_its_result(self):
        body = json.dumps({"action": "verify", "name": "Firefox"})
        with patch.object(self.manager_instance, "verify_app", return_value=True):
            status, data = self.request("POST", "/api/jobs", body, {"X-Token": web.TOKEN})
            self.assertEqual(status, 202)
            job_id = json.loads(data)["job"]["id"]
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                state = json.loads(self.request("GET", "/api/state")[1])
                job = next(item for item in state["jobs"] if item["id"] == job_id)
                if job["status"] == "succeeded":
                    break
                time.sleep(0.02)
            self.assertEqual(job["status"], "succeeded")
            self.assertEqual(state["jobs"][0]["id"], job_id)

    def test_browser_can_cancel_a_background_download(self):
        started = threading.Event()

        def wait_for_cancel(name, progress_callback, cancel_event):
            started.set()
            progress_callback(22, "Receiving repository data from GitHub")
            cancel_event.wait(5)
            self.manager_instance.last_error = "Operation cancelled by user."
            return False

        with patch.object(self.manager_instance, "download_app", side_effect=wait_for_cancel):
            body = json.dumps({"action": "download", "name": "Firefox"})
            status, data = self.request("POST", "/api/jobs", body, {"X-Token": web.TOKEN})
            self.assertEqual(status, 202)
            job_id = json.loads(data)["job"]["id"]
            self.assertTrue(started.wait(2))
            status, data = self.request("POST", f"/api/jobs/{job_id}/cancel", headers={"X-Token": web.TOKEN})
            self.assertEqual(status, 200)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                state = json.loads(self.request("GET", "/api/state")[1])
                job = next(item for item in state["jobs"] if item["id"] == job_id)
                if job["status"] == "cancelled":
                    break
                time.sleep(0.02)
            self.assertEqual(job["status"], "cancelled")
            self.assertEqual(job["progress"], 22)

    def test_long_actions_cannot_bypass_background_job_api(self):
        body = json.dumps({"action": "download", "name": "Firefox"})
        status, data = self.request("POST", "/api/action", body, {"X-Token": web.TOKEN})
        self.assertEqual(status, 400)
        self.assertIn("/api/jobs", json.loads(data)["error"])

    def test_mutation_requires_token_and_valid_host(self):
        body = json.dumps({"action": "remove", "name": "Firefox"})
        self.assertEqual(self.request("POST", "/api/action", body)[0], 403)
        self.assertEqual(self.request("GET", "/api/state", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.request("POST", "/api/action", body, {"X-Token": web.TOKEN, "Origin": "https://evil.example"})[0], 403)

    def test_browser_cannot_force_remove_protected_source(self):
        directory = self.source(self.manager_instance, "Joplin")
        body = json.dumps({"action": "remove", "name": "Joplin", "force": True})
        status, data = self.request("POST", "/api/action", body, {"X-Token": web.TOKEN})
        self.assertEqual(status, 400)
        self.assertFalse(json.loads(data)["ok"])
        self.assertTrue(directory.exists())

    def test_bad_types_and_body_size_return_errors(self):
        self.assertEqual(self.request("POST", "/api/action", '{"action":[],"name":"Firefox"}', {"X-Token": web.TOKEN})[0], 400)
        self.assertEqual(self.request("POST", "/api/action", "x" * 4097, {"X-Token": web.TOKEN})[0], 413)

    def test_corrupt_registry_returns_structured_error(self):
        self.manager_instance.cache_file.write_text("bad", encoding="utf-8")
        status, data = self.request("GET", "/api/state")
        self.assertEqual(status, 503)
        self.assertIn("preserved", json.loads(data)["error"])

    def test_busy_browser_action_does_not_block_state(self):
        with web.LOCK:
            body = json.dumps({"action": "scan", "name": ""})
            self.assertEqual(self.request("POST", "/api/action", body, {"X-Token": web.TOKEN})[0], 409)
            self.assertEqual(self.request("GET", "/api/state")[0], 200)


class CliTests(IsolatedCase):
    def cli(self, *args, cache=None):
        return subprocess.run([sys.executable, str(PROJECT_DIR / "rlayers.py"), *args], cwd=self.root,
                              env={**os.environ, "RLAYERS_CACHE_DIR": str(cache or self.root / "cache"), "PYTHONIOENCODING": "utf-8"},
                              capture_output=True, text=True, encoding="utf-8", timeout=20)

    def test_help_works_without_touching_corrupt_registry(self):
        store = RegistryStore(self.root / "cache")
        store.path.write_text("broken", encoding="utf-8")
        self.assertEqual(self.cli("--help").returncode, 0)
        self.assertEqual(store.path.read_text(), "broken")

    def test_cli_recovery_runs_before_manager_initialization(self):
        manager = self.manager()
        manager.add_package("saved", "1")
        manager.add_package("latest", "1")
        manager.cache_file.write_text("broken", encoding="utf-8")
        result = self.cli("recover")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("saved", manager.snapshot())
        self.assertNotIn("latest", manager.snapshot())

    def test_list_from_another_working_directory(self):
        result = self.cli("list", "--lang", "ar")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Firefox", result.stdout)

    def test_failure_exit_codes_and_mutually_exclusive_flags(self):
        self.assertEqual(self.cli("verify", "Missing").returncode, 1)
        self.assertEqual(self.cli("link", "Firefox").returncode, 2)
        self.assertEqual(self.cli("--web", "--cli").returncode, 2)

    def test_arabic_indic_numbers(self):
        for raw, expected in (("١٢", 12), ("\u200f٢", 2), ("q", 0), ("bad", -1)):
            with patch("builtins.input", return_value=raw):
                self.assertEqual(rlayers.pick("> "), expected)


if __name__ == "__main__":
    unittest.main()
