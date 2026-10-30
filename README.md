# R-Layers

**Version 0.2.0** · [Download the latest release](https://github.com/aziz-ex/R-Layers/releases/latest) · [البداية بالعربية](QUICKSTART.ar.md) · [Roadmap](ROADMAP.md) · [Changelog](CHANGELOG.md)

R-Layers manages a catalog of open-source desktop apps and their source repositories. It provides a local web interface and a terminal menu, with Arabic and English support.

**Source downloaded** means a Git repository is stored in the cache. **Installed** means R-Layers has found or linked a runnable desktop executable. These states are independent: cloning Firefox does not install Firefox.

## Requirements and startup

- Python 3.10 or newer; no third-party Python packages are needed.
- Git for source download and verification.
- A graphical session for opening desktop apps; the CLI also works without one.

```sh
python rlayers.py
python rlayers.py --web --lang ar
python rlayers.py --cli --lang en
```

The app catalog initializes automatically. The web server listens on `127.0.0.1:8765`. Close it with Ctrl+C. On Linux without a graphical session, the default interface is the terminal menu. The menu accepts both Western and Arabic-Indic digits. A console that cannot display Arabic falls back to English; the web interface supports right-to-left Arabic.

If the `python` command is unavailable, use your installation's `python3`, `py`, or full Python executable path.

Start with `python rlayers.py doctor` to check readiness without modifying the registry. Use `doctor --json` for a structured report, `--version` for the release number, or `--web --no-browser` to serve the UI without opening a browser. The source ZIP needs an existing Python installation.

## Existing installations

Detect apps already installed on the current computer:

```sh
python rlayers.py scan
python rlayers.py list
python rlayers.py launch Firefox
```

Detection checks PATH, common installation locations, and Windows App Paths. Portable apps or custom locations may need an explicit link:

```powershell
python rlayers.py link Firefox "D:\Apps\Firefox\firefox.exe"
```

```sh
python rlayers.py link Joplin /home/user/Applications/Joplin.AppImage
```

Link an executable you trust. Linking does not copy or modify it. R-Layers opens it without passing a shell command. The web interface offers detection and an Open app button for linked installations. Programs open on the machine running R-Layers.

## Native installation

Preview an installation or execute it from a terminal:

```sh
python rlayers.py install Firefox --dry-run
python rlayers.py install Firefox
```

An existing, valid executable is reused. Otherwise, R-Layers invokes the native package manager using the fixed identifiers below. It does not build the downloaded source. Installers may need system permissions or agreements; R-Layers does not bypass those prompts or automatically accept agreements. If a manager cannot proceed, run the displayed command in your terminal, then run `scan` or `link`.

| Platform / manager | Apps with install commands |
| --- | --- |
| Windows / WinGet | Joplin, Flameshot, Firefox, VLC, Blender, qutebrowser |
| Debian or Ubuntu family / APT | Nautilus, Flameshot, Firefox, VLC, Blender, qutebrowser |
| macOS / Homebrew casks | Joplin, Firefox, VLC, Blender, qutebrowser |

Debian, Kali, and Parrot use `firefox-esr`. Other APT systems use `firefox`; package availability depends on the configured repositories. APT execution requires an administrator terminal: a normal session prints the command to run with `sudo`. Other Linux package managers are not implemented. Ladybird has no automated installer in this project. Unsupported combinations return a clear error and can still use a manually installed executable through `link`.

The web interface labels unsupported installers explicitly. Its Installation command button shows the terminal command and indicates missing package managers. Link existing app accepts a full executable path without opening the app; Open app launches it separately.

References: [WinGet installation options](https://learn.microsoft.com/en-us/windows/package-manager/winget/install), [WinGet package catalog](https://github.com/microsoft/winget-pkgs), [Debian packages](https://packages.debian.org/), [Homebrew casks](https://formulae.brew.sh/cask/).

## Source management

```sh
python rlayers.py download Flameshot
python rlayers.py download-all
python rlayers.py verify Flameshot
python rlayers.py remove Firefox
python rlayers.py remove Flameshot --force
python rlayers.py credits
```

Downloads are shallow HTTPS clones from GitHub. They can be several GB for large apps. Downloads use a new staging directory and publish it only after Git succeeds. Existing unregistered files are preserved, and downloaded repositories are reused only after verification. Failed staging clones are cleaned up; interruption can leave a `.download-*` directory for manual inspection.

Verification checks the repository root, recorded commit, modified/deleted/staged/non-ignored untracked files, index flags that could hide modifications, and Git object integrity. A failure clears the previous verified flag. A verified badge records the last successful check; rerun verification after changing files. This checks source consistency, not publisher signatures or the installed desktop executable.

`remove` deletes only that app's source directory owned by R-Layers. Protected sources require `--force` in the terminal; the browser cannot supply it. Catalog entries remain available for downloading again. Desktop installations and their user settings are retained; use the operating system's package manager to uninstall them.

## Catalog and credits

License labels below are catalog metadata for attribution. The downloaded upstream version supplies its own license files and notices.

| App | Source | Catalog license | Source protection |
| --- | --- | --- | --- |
| Nautilus | [GNOME/nautilus](https://github.com/GNOME/nautilus) | GPL-3.0 | Protected |
| Joplin | [laurent22/joplin](https://github.com/laurent22/joplin) | AGPL-3.0 | Protected |
| Flameshot | [flameshot-org/flameshot](https://github.com/flameshot-org/flameshot) | MIT | Protected |
| Firefox | [mozilla-firefox/firefox](https://github.com/mozilla-firefox/firefox) | MPL-2.0 | Removable |
| Ladybird | [LadybirdBrowser/ladybird](https://github.com/LadybirdBrowser/ladybird) | BSD 2-Clause | Removable |
| qutebrowser | [qutebrowser/qutebrowser](https://github.com/qutebrowser/qutebrowser) | GPL-3.0 | Removable |
| VLC | [videolan/vlc](https://github.com/videolan/vlc) | GPL-2.0 | Removable |
| Blender | [blender/blender](https://github.com/blender/blender) | GPL-2.0 | Removable |

## Storage, migration, and recovery

The default cache is `pkg_cache/` beside the Python sources, independent of the current working directory. Set `RLAYERS_CACHE_DIR` to an absolute directory to use an isolated registry. All managed source downloads stay inside that registry's `apps/` directory; redirected paths escaping it are rejected.

Registry writes are atomic, retain the previous valid snapshot in `packages.json.bak`, and use an operating-system process lock shared by CLI/web operations. A long operation may hold the lock; another mutation times out with a busy message. Web state reads remain available. The cache is intended for a local filesystem.

Legacy registries are migrated on startup: catalog licenses, credits, and protection levels are updated; the old `installed` flag becomes `source_downloaded`; installed executable detection is separate. Old HEAD-only verification badges are cleared. Source files are retained.

Malformed data is reported without silently resetting the registry. Restore the previous valid snapshot explicitly:

```sh
python rlayers.py recover
```

Recovery preserves the current damaged bytes as `packages-damaged-*.json`. The backup is the previous write, not a complete history.

Operational logs are UTF-8 files named `pkg_cache/rlayers-<pid>.log`, rotated at approximately 1 MB with two backups per process. Old process log files can be removed when no longer needed. Cache, downloaded sources, and logs are excluded from Git.

## Dependency API

```python
from layer import PackageAbstractionLayer

layer = PackageAbstractionLayer()
layer.add_package("tools", "1.0")
layer.add_package("compiler", "1.0", dependencies=["tools"])
print(layer.resolve_dependencies("compiler"))  # ['tools', 'compiler']
layer.close()
```

Dependency resolution returns dependency-first ordering, reports missing packages and cycles, and does not install OS packages or solve version constraints.

## Tests and current limits

```sh
python -m unittest discover -s tests -v
```

Tests use temporary registries and local Git repositories, including real concurrent processes and HTTP requests. They do not download upstream apps, execute installers, or open desktop apps. Symlink checks skip if Windows does not grant symlink privileges.

GitHub Actions is configured for Windows, Linux, and macOS with Python 3.10 and 3.14. A configured workflow does not establish that those remote jobs have run. Actual desktop installation and launch still require manual checks on each target platform.

## Building a downloadable release

```sh
python scripts/build_release.py
```

This creates `dist/R-Layers-0.2.0.zip` and its `.sha256` checksum from an explicit allowlist. The ZIP excludes the cache, downloaded app sources, logs, credentials, and machine-specific installations. It includes the Python sources, tests, license, and guides. Identical input bytes produce identical archives. The release tests also unpack the ZIP and check that it runs independently of the working directory.

| Component | Implemented status |
| --- | --- |
| Backend | Source registration/download/removal with constrained paths and atomic storage |
| Verification | Commit, working tree, index flags, Git object checks; stale success cleared |
| Protection | Catalog migration; terminal-only force for managed-source removal |
| CLI / web | Arabic/English interfaces, separate source/install state, detection and launch |
| Installation | Native package-manager commands for the supported combinations above |
| Error handling | Structured HTTP errors, CLI exit codes, recoverable registry errors |
| Logging | Persistent rotating logs per process |
| Testing | Unit/integration suite and a cross-platform CI configuration |
| Documentation | Current commands, migration, recovery, support matrix, and limitations |

## شرح سريع بالعربية

شغّل `python rlayers.py --web --lang ar` لفتح الواجهة العربية، أو استخدم `--cli` لقوائم الطرفية. زر اكتشاف التطبيقات يبحث عن النسخ المثبّتة. إذا كان التطبيق في مسار مخصص، اربطه بأمر `link` مع مسار ملف التشغيل.

تنزيل المصدر وتثبيت التطبيق حالتان منفصلتان. `download` يجلب الكود، و`install` يستخدم مدير حزم النظام، و`launch` يفتح التطبيق المرتبط. حذف المصدر لا يحذف التطبيق المثبّت أو بياناته. المصادر المحمية تحتاج `--force` من الطرفية.

## License

R-Layers is distributed under the [MIT license](LICENSE). Upstream apps retain their respective licenses.
