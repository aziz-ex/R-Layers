"""Create a source release from an explicit allowlist; never include user data."""
import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from version import __version__  # noqa: E402

RELEASE_FILES = (
    "layer.py", "storage.py", "app_runtime.py", "diagnostics.py", "rlayers.py",
    "rlayers_web.py", "version.py", "README.md", "QUICKSTART.ar.md", "CHANGELOG.md",
    "ROADMAP.md", "LICENSE", "scripts/build_release.py", "tests/test_rlayers.py",
    "tests/test_release.py", ".github/workflows/tests.yml", ".gitignore",
)


def build_release(destination, root=ROOT):
    root = Path(root).resolve()
    files = [root / filename for filename in RELEASE_FILES]
    missing = [str(path.relative_to(root)) for path in files if not path.is_file()]
    if missing:
        raise ValueError("Release files are missing: " + ", ".join(missing))
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    name = f"R-Layers-{__version__}"
    archive = destination / f"{name}.zip"
    # Fixed order, permissions and timestamps make identical sources reproducible.
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for filename, path in zip(RELEASE_FILES, files):
            item = zipfile.ZipInfo(f"{name}/{filename}", date_time=(2026, 10, 30, 0, 0, 0))
            item.external_attr = 0o100644 << 16
            item.compress_type = zipfile.ZIP_DEFLATED
            output.writestr(item, path.read_bytes())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = destination / f"{name}.sha256"
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    return archive, checksum


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    for artifact in build_release(args.output):
        print(artifact)
