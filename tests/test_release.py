import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import app_runtime
from diagnostics import diagnose
from layer import PROJECT_DIR
from scripts.build_release import RELEASE_FILES, build_release
from version import __version__


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rlayers-diagnostics-")
        self.root = Path(self.temp.name)
        self.discovery = patch("app_runtime.discover", return_value=None)
        self.discovery.start()

    def tearDown(self):
        self.discovery.stop()
        self.temp.cleanup()

    def test_new_cache_is_not_created(self):
        cache = self.root / "not-created"
        report = diagnose(cache)
        self.assertTrue(report["ok"])
        self.assertEqual(report["version"], __version__)
        self.assertFalse(cache.exists())

    def test_corrupt_registry_is_reported_without_being_modified(self):
        registry = self.root / "packages.json"
        registry.write_text("damaged data", encoding="utf-8")
        report = diagnose(self.root)
        self.assertFalse(report["ok"])
        self.assertEqual(registry.read_text(), "damaged data")
        self.assertEqual(list(self.root.iterdir()), [registry])

    def test_missing_git_and_package_manager_are_warnings(self):
        with patch("diagnostics.shutil.which", return_value=None):
            report = diagnose(self.root / "new")
        self.assertTrue(report["ok"])
        self.assertEqual(next(item for item in report["checks"] if item["name"] == "git")["status"], "warning")

    def test_cli_json_diagnostics_return_failure_for_corrupt_registry(self):
        (self.root / "packages.json").write_text("damaged", encoding="utf-8")
        result = subprocess.run([sys.executable, str(PROJECT_DIR / "rlayers.py"), "doctor", "--json"],
                                env={**os.environ, "RLAYERS_CACHE_DIR": str(self.root)},
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(json.loads(result.stdout)["ok"])

    def test_version_does_not_open_the_registry(self):
        cache = self.root / "new"
        result = subprocess.run([sys.executable, str(PROJECT_DIR / "rlayers.py"), "--version"],
                                env={**os.environ, "RLAYERS_CACHE_DIR": str(cache)},
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(__version__, result.stdout)
        self.assertFalse(cache.exists())

    def test_installer_support_is_distinct_from_manager_availability(self):
        with patch("app_runtime.shutil.which", return_value=None):
            supported = app_runtime.installation_status("Firefox", "Windows")
            unsupported = app_runtime.installation_status("Ladybird", "Windows")
        self.assertTrue(supported["supported"])
        self.assertFalse(supported["available"])
        self.assertEqual(supported["manager"], "winget")
        self.assertFalse(unsupported["supported"])


class ReleaseTests(unittest.TestCase):
    def test_archive_has_only_release_files_and_a_valid_checksum(self):
        with tempfile.TemporaryDirectory(prefix="rlayers-release-") as directory:
            archive, checksum = build_release(directory)
            expected = {f"R-Layers-{__version__}/{filename}" for filename in RELEASE_FILES}
            with zipfile.ZipFile(archive) as package:
                self.assertEqual(set(package.namelist()), expected)
                self.assertTrue(all("pkg_cache" not in name and ".log" not in name for name in package.namelist()))
                self.assertIsNone(package.testzip())
            digest, name = checksum.read_text().split()
            self.assertEqual(digest, hashlib.sha256(archive.read_bytes()).hexdigest())
            self.assertEqual(name, archive.name)

    def test_build_is_reproducible(self):
        with tempfile.TemporaryDirectory(prefix="rlayers-release-") as directory:
            first, _ = build_release(Path(directory) / "first")
            second, _ = build_release(Path(directory) / "second")
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_extracted_archive_can_run_from_another_directory(self):
        with tempfile.TemporaryDirectory(prefix="rlayers-release-") as directory:
            root = Path(directory)
            archive, _ = build_release(root / "dist")
            with zipfile.ZipFile(archive) as package:
                package.extractall(root / "extracted")
            result = subprocess.run([sys.executable, str(root / "extracted" / f"R-Layers-{__version__}" / "rlayers.py"), "--version"],
                                    cwd=root, env={**os.environ, "RLAYERS_CACHE_DIR": str(root / "unused-cache")},
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(__version__, result.stdout)
            self.assertFalse((root / "unused-cache").exists())

    def test_incomplete_release_does_not_create_an_archive(self):
        with tempfile.TemporaryDirectory(prefix="rlayers-release-") as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                build_release(root / "dist", root=root)
            self.assertFalse((root / "dist").exists())


if __name__ == "__main__":
    unittest.main()
