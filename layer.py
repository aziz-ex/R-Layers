import json
import os
import subprocess
import shutil
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse

ALLOWED_HOSTS = {"github.com"}


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

    def add_bundled_app(self, name, source_url, license_name, credit_to):
        """Register an open-source app to be bundled, with attribution."""
        if name in self.packages:
            print(f"App {name} already registered")
            return False

        self.packages[name] = {
            "type": "bundled_app",
            "source_url": source_url,
            "license": license_name,
            "credit_to": credit_to,
            "added_at": datetime.now().isoformat()
        }
        self._save_cache()
        print(f"App {name} registered (credit: {credit_to})")
        return True

    def print_credits(self):
        """Print attribution for all bundled apps."""
        print("\nCredits:")
        for name, info in self.packages.items():
            if info.get("type") == "bundled_app":
                print(f"  {name} — by {info['credit_to']} ({info['license']})")
                print(f"    Source: {info['source_url']}")

    def download_app(self, name, apps_dir="pkg_cache/apps"):
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
        if dest.exists():
            print(f"'{name}' already downloaded at {dest}")
            return True

        Path(apps_dir).mkdir(parents=True, exist_ok=True)

        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", "--", url, str(dest)],
                check=True,
                timeout=120,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            print(f"Clone failed for '{name}': {e.stderr.strip()}")
            shutil.rmtree(dest, ignore_errors=True)
            return False
        except subprocess.TimeoutExpired:
            print(f"Clone timed out for '{name}'")
            shutil.rmtree(dest, ignore_errors=True)
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
    layer.get_package("gcc")

    print("\n" + "="*50)
    print(f"Does 'gcc' exist? {layer.package_exists('gcc')}")
    print(f"Does 'python' exist? {layer.package_exists('python')}")

    print("\n" + "="*50)
    layer.add_bundled_app(
        "Flameshot",
        "https://github.com/flameshot-org/flameshot",
        "MIT",
        "Flameshot Contributors"
    )
    layer.add_bundled_app(
        "Joplin",
        "https://github.com/laurent22/joplin",
        "MIT",
        "Laurent Cozic and Joplin Contributors"
    )
    layer.add_bundled_app(
        "Ladybird",
        "https://github.com/LadybirdBrowser/ladybird",
        "BSD 2-Clause",
        "Ladybird Browser Initiative"
    )
    layer.print_credits()

    print("\n" + "="*50)
    layer.download_app("Flameshot")