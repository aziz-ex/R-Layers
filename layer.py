import json
import os
import subprocess
import shutil
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse
 
ALLOWED_HOSTS = {"github.com"}
 
# Apps shipped with R-Layers: (name, source, license, credit, protection)
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
 
# Entries dropped from the catalog; they are removed from the registry on the next run
RETIRED = ["World Clock"]
 
 
class PackageAbstractionLayer:
    """A layer that reads packages and stores them in a cache."""
 
    def __init__(self, cache_dir="./pkg_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        self.cache_file = self.cache_dir / "packages.json"
        self.packages = self._load_cache()
 
    def _load_cache(self):
        """Load packages from cache if it exists."""
        if self.cache_file.exists():
            with open(self.cache_file, 'r') as f:
                return json.load(f)
        return {}
 
    def _save_cache(self):
        """Save packages to cache."""
        with open(self.cache_file, 'w') as f:
            json.dump(self.packages, f, indent=2)
 
    def add_package(self, name, version, dependencies=None):
        """Add a new package."""
        if name in self.packages:
            print(f"Package {name} already exists")
            return False
 
        self.packages[name] = {
            "version": version,
            "dependencies": dependencies or [],
            "added_at": datetime.now().isoformat(),
            "cached": True
        }
        self._save_cache()
        print(f"Package {name} v{version} added successfully")
        return True
 
    def get_package(self, name):
        """Get a package from cache."""
        if name in self.packages:
            print(f"Loading {name} from cache...")
            return self.packages[name]
        print(f"Package {name} not found")
        return None
 
    def package_exists(self, name):
        """Check if a package is already cached."""
        return name in self.packages
 
    def add_bundled_app(self, name, source_url, license_name, credit_to, protection="removable"):
        """Register an open-source app to be bundled, with attribution.
 
        protection:
          - "removable" (default): can be deleted normally, e.g. a browser.
          - "system": protected app, e.g. a file manager. Can only be
            removed by explicitly calling remove_app(name, force=True)
            from the terminal.
        """
        if name in self.packages:
            print(f"App {name} already registered")
            return False
 
        if protection not in ("removable", "system"):
            print(f"Invalid protection level '{protection}', must be 'removable' or 'system'")
            return False
 
        self.packages[name] = {
            "type": "bundled_app",
            "source_url": source_url,
            "license": license_name,
            "credit_to": credit_to,
            "protection": protection,
            "added_at": datetime.now().isoformat()
        }
        self._save_cache()
        print(f"App {name} registered (credit: {credit_to}, protection: {protection})")
        return True
 
    def print_credits(self):
        """Print attribution for all bundled apps."""
        print("\nCredits:")
        for name, info in self.packages.items():
            if info.get("type") == "bundled_app":
                print(f"  {name} — by {info['credit_to']} ({info['license']})")
                print(f"    Source: {info['source_url']}")
 
    def download_app(self, name, apps_dir="pkg_cache/apps", timeout=1800):
        """Clone a bundled app's repo and update its record."""
        if name not in self.packages:
            print(f"'{name}' not registered")
            return False
 
        info = self.packages[name]
        if info.get("type") != "bundled_app":
            print(f"'{name}' is not a bundled app")
            return False
 
        url = info.get("source_url", "")
        host = urlparse(url).hostname or ""
        if urlparse(url).scheme != "https" or host not in ALLOWED_HOSTS:
            print(f"Refusing to clone from untrusted URL: {url}")
            return False
 
        dest = Path(apps_dir) / name
        if (dest / ".git").exists():
            print(f"'{name}' already downloaded at {dest}")
            return True
        self._rmtree(dest)  # leftover of an interrupted clone
 
        Path(apps_dir).mkdir(parents=True, exist_ok=True)
        print(f"Downloading '{name}' (large projects can take several minutes)...", flush=True)
 
        # never wait for a password prompt: a wrong link must fail fast
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        try:
            subprocess.run(
                ["git", "-c", "core.longpaths=true", "clone", "--depth", "1", "--", url, str(dest)],
                check=True,
                timeout=timeout,
                capture_output=True,
                text=True,
                env=env,
            )
        except subprocess.CalledProcessError as e:
            print(f"Clone failed for '{name}': {e.stderr.strip()}")
            self._rmtree(dest)
            return False
        except subprocess.TimeoutExpired:
            print(f"Clone timed out for '{name}' after {timeout} seconds")
            self._rmtree(dest)
            return False
        except FileNotFoundError:
            print("git is not installed (Kali/Debian: sudo apt install git)")
            return False
 
        commit_hash = subprocess.run(
            ["git", "-C", str(dest), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True
        ).stdout.strip()
 
        info["local_path"] = str(dest)
        info["commit"] = commit_hash
        info["installed"] = True
        self._save_cache()
        print(f"'{name}' downloaded to {dest} (commit {commit_hash[:7]})")
        return True
 
    def download_all(self, apps_dir="pkg_cache/apps"):
        """Download every registered bundled app, one after the other."""
        names = [n for n, i in self.packages.items() if i.get("type") == "bundled_app"]
        done, failed = [], []
        for number, name in enumerate(names, 1):
            print(f"\n[{number}/{len(names)}] {name}")
            (done if self.download_app(name, apps_dir) else failed).append(name)
        print(f"\nDownloaded: {len(done)}   Failed: {len(failed)}")
        if failed:
            print("Failed: " + ", ".join(failed))
        return not failed
 
    def update_source(self, name, new_url):
        """Fix the source link of a registered app that is not downloaded yet."""
        info = self.packages.get(name)
        if not info or info.get("type") != "bundled_app" or info.get("source_url") == new_url:
            return False
        if info.get("installed"):
            print(f"'{name}' is already downloaded; remove it first to change its source")
            return False
        info["source_url"] = new_url
        self._save_cache()
        print(f"'{name}' source updated to {new_url}")
        return True
 
    def verify_app(self, name):
        """Verify a downloaded app is intact and matches the recorded commit."""
        if name not in self.packages:
            print(f"'{name}' not registered")
            return False
 
        info = self.packages[name]
        local_path = info.get("local_path")
        recorded_commit = info.get("commit")
 
        if not local_path or not Path(local_path).exists():
            print(f"'{name}' local files not found at {local_path}")
            return False
 
        if not (Path(local_path) / ".git").exists():
            print(f"'{name}' does not have .git history (not a cloned repo)")
            return False
 
        if not any(Path(local_path).iterdir()):
            print(f"'{name}' folder is empty")
            return False
 
        try:
            current_commit = subprocess.run(
                ["git", "-C", str(local_path), "rev-parse", "HEAD"],
                capture_output=True, text=True, check=True
            ).stdout.strip()
        except subprocess.CalledProcessError as e:
            print(f"Failed to verify commit for '{name}': {e.stderr.strip()}")
            return False
 
        if current_commit != recorded_commit:
            print(f"'{name}' commit mismatch: recorded {str(recorded_commit)[:7]}, "
                  f"current {current_commit[:7]}")
            return False
 
        info["verified"] = True
        self._save_cache()
        print(f"\u2713 '{name}' verified successfully ({current_commit[:7]})")
        return True
 
    @staticmethod
    def _force_remove_readonly(func, path, exc_info):
        """Clear read-only flag (common in .git folders on Windows) and retry."""
        os.chmod(path, 0o777)
        func(path)
 
    def _rmtree(self, path):
        """Delete a folder tree, including read-only git files. Does nothing if missing."""
        if not Path(path).exists():
            return
        try:
            shutil.rmtree(path, onexc=self._force_remove_readonly)  # Python 3.12+
        except TypeError:
            shutil.rmtree(path, onerror=self._force_remove_readonly)
 
    def remove_app(self, name, force=False):
        """Remove a downloaded app's local files and registration.
 
        System-protected apps (protection='system') require force=True
        to be removed. Removable apps are deleted normally.
        """
        if name not in self.packages:
            print(f"'{name}' not registered")
            return False
 
        info = self.packages[name]
        protection = info.get("protection", "removable")
 
        if protection == "system" and not force:
            print(f"'{name}' is a protected system app and cannot be removed "
                  f"without confirmation.")
            print(f"To remove it, run: layer.remove_app('{name}', force=True)")
            return False
 
        local_path = info.get("local_path")
        if local_path and Path(local_path).exists():
            self._rmtree(local_path)
            print(f"Deleted files at {local_path}")
 
        del self.packages[name]
        self._save_cache()
        print(f"'{name}' removed from registry.")
        return True
 
    def resolve_dependencies(self, package_name):
        """Resolve dependencies automatically."""
        if package_name not in self.packages:
            return None
 
        resolved = {package_name}
        to_process = [package_name]
 
        while to_process:
            current = to_process.pop(0)
            deps = self.packages[current].get("dependencies", [])
 
            for dep in deps:
                if dep not in resolved:
                    resolved.add(dep)
                    to_process.append(dep)
 
        print(f"Resolved dependencies for {package_name}:")
        for pkg in resolved:
            print(f"  - {pkg}")
 
        return resolved
 
    def list_packages(self):
        """Display all packages."""
        if not self.packages:
            print("No packages found")
            return
 
        print("\nAvailable packages:")
        for name, info in self.packages.items():
            print(f"  {name} v{info.get('version', 'N/A')}")
            if info.get('dependencies'):
                print(f"    Dependencies: {', '.join(info['dependencies'])}")
 
 
if __name__ == "__main__":
    layer = PackageAbstractionLayer()
 
    layer.add_package("kernel", "6.1.0")
    layer.add_package("gcc", "13.0", dependencies=["binutils"])
    layer.add_package("binutils", "2.40")
    layer.add_package("make", "4.3", dependencies=["gcc"])
 
    layer.list_packages()
 
    print("\n" + "="*50)
    layer.resolve_dependencies("make")
 
    print("\n" + "="*50)
    print("Registering apps...")
    for name, url, license_name, credit, protection in APPS:
        layer.add_bundled_app(name, url, license_name, credit, protection=protection)
        layer.update_source(name, url)  # fixes a wrong link registered earlier
 
    for name in RETIRED:
        if layer.package_exists(name):
            layer.remove_app(name)
 
    layer.print_credits()
    print("\nNext: python3 rlayers.py")
 