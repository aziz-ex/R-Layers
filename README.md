# R-Layers

A lightweight package abstraction layer that manages application packages, resolves dependencies automatically, and tracks attribution for bundled open-source applications.

## Status
Early work-in-progress (v0.1). Core backend logic is functional; a UI-facing presentation layer is planned next.

## Features
- Local caching of package metadata
- Automatic dependency resolution
- Attribution tracking for bundled open-source apps (name, license, source)

## Bundled open-source apps (with credit)
- **Flameshot** (MIT) — https://github.com/flameshot-org/flameshot
- **Joplin** (MIT) — https://github.com/laurent22/joplin
- **Ladybird** (BSD 2-Clause) — https://github.com/LadybirdBrowser/ladybird

## Requirements
- Python 3.10+

## Run it
```bash
python layer.py
```