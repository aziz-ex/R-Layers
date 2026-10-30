# Changelog

## 0.2.0 — 2026-10-30

### Added

- Arabic/English terminal and local web interfaces with guided setup, installed-app detection, linking, and launching.
- Native installation commands for supported WinGet, APT, and Homebrew combinations, including an install preview.
- `doctor` readiness diagnostics, JSON output, `--version`, and `--no-browser`.
- Atomic JSON storage, a previous-valid backup, explicit recovery, process locking, and rotating logs.
- A reproducible source-release builder and SHA-256 checksum, excluding caches and local data.
- Arabic quick start, a project roadmap, and a cross-platform unit/integration test matrix.

### Fixed

- Verification now checks the source working tree, recorded commit, hidden-index flags, and Git object consistency; failed checks clear an earlier success badge.
- Built-in protection, attribution, and license metadata migrate into older registries.
- Download and removal paths are constrained to each registry's managed sources.
- Dependency resolution reports missing dependencies and cycles and returns dependency-first ordering.
- Existing desktop installations are distinct from source downloads, and unsupported installers are labeled in the UI.

### Compatibility

- Requires Python 3.10 or newer; the downloadable ZIP is source code, not a standalone executable.
- Legacy registries migrate automatically; source files and desktop installations are retained.
- `remove` now removes managed source files while retaining the catalog entry and any desktop installation.
- Native installer execution needs manual platform validation; automated tests use simulated installers.
