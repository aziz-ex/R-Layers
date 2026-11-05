# Project status and next steps

## v0.3.0 — background source jobs

- Web downloads and source checks run outside the request that started them.
- Git clone progress is shown in the browser; users can cancel downloads and the owned staging directory is removed.
- The browser polls job state and restores recent progress/results after reload.
- Job history is atomically stored separately from the package registry. Jobs left active after a server restart are marked interrupted.
- Arabic and English interfaces show job status and errors. The CLI keeps synchronous source commands.
- Verification reports checkpoints while checking the repository, recorded commit, worktree, and Git objects.
- Tests cover background lifecycle, persisted history, web endpoints, progress, and cancellation cleanup.

## Next milestone — native installation validation

1. Validate installers on disposable Windows, Debian/Ubuntu, and macOS machines. Check launch detection, missing managers, permission prompts, and installer failures. CI still simulates installer execution.
2. Build standalone application packages after install behavior, data-directory handling, and release signing are validated.

Unsupported installer/platform combinations remain explicit. Source verification checks Git consistency; publisher-signature verification and automatic source builds are not implemented. Background job history is local to the web server and keeps only recent entries.
