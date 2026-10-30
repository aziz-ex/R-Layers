# Project status and next steps

## v0.2.0 — usable source release

- Atomic registry writes, recovery, cross-process locking, and persistent logs.
- Source verification, constrained removal paths, and legacy protection migration.
- Separate downloaded-source and installed-executable states.
- Existing application detection, linking, launching, and native install commands.
- Arabic/English CLI and local web interface, including guided first steps.
- Read-only `doctor` diagnostics and `--version`.
- A versioned source ZIP with SHA-256 checksum and an explicit file allowlist.
- Unit/integration tests and a Windows/Linux/macOS CI matrix.

## Next milestone — installation validation and background jobs

1. Validate native installers on disposable Windows, Debian/Ubuntu, and macOS machines. Test launch, missing managers, permission prompts, and installer failures. CI tests currently simulate installer execution.
2. Move long source downloads and checks into background jobs with progress, cancellation, and status after a browser reload.
3. Produce standalone application builds once data-directory behavior, installer support, and release signing have been validated.

Unsupported installer/platform combinations remain explicit. Source verification checks Git consistency; publisher-signature verification and automatic source builds are not implemented.
