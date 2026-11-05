
#!/usr/bin/env python3
"""R-Layers entry point: picks the right interface for the current session.

  python3 rlayers.py                   graphical session: web UI, otherwise terminal menu
  python3 rlayers.py --web | --cli     force one of them
  python3 rlayers.py list
  python3 rlayers.py download|verify|remove NAME [--force]
  python3 rlayers.py download-all

Terminal menus use numbers only, so they work with any keyboard layout.
Needs rlayers_web.py and layer.py in the same folder.
"""
import argparse
import json
import os
import platform
import sys
import threading
import unicodedata
import webbrowser
from http.server import ThreadingHTTPServer

import rlayers_web as web
from rlayers_web import RTL, detect_system, get_layer, load_strings
from layer import DEFAULT_CACHE
from storage import RegistryError, RegistryStore
from version import __version__

CLI = {
    "en": {
        "cli_prompt": "> ", "cli_sep": ", ",
        "cli_all": "Download all sources", "cli_lang": "Change language",
        "cli_quit": "Quit", "cli_back": "Back",
        "cli_bad": "Invalid number, choose one of the numbers shown.",
        "cli_bye": "Goodbye.", "cli_cancelled": "Cancelled.", "cli_yes_no": "1 = yes, 0 = no",
        "cli_all_warn": "This downloads the source code of {n} apps. It can be several GB "
                        "(Firefox and Blender are the largest) and take a long time.",
        "cli_type_name": "Protected app. Type its name to confirm removal: ",
        "cli_fallback": "This console cannot display '{lang}', showing English.",
    },
    "ar": {
        "cli_sep": "، ",
        "cli_all": "تنزيل كل المصادر", "cli_lang": "تغيير اللغة",
        "cli_quit": "خروج", "cli_back": "رجوع",
        "cli_bad": "رقم غير صحيح، اختر رقماً من الأرقام الظاهرة.",
        "cli_bye": "إلى اللقاء.", "cli_cancelled": "تم الإلغاء.", "cli_yes_no": "1 = نعم، 0 = لا",
        "cli_all_warn": "سيتم تنزيل كود المصدر لـ {n} تطبيقات. قد يصل الحجم إلى عدة غيغابايت "
                        "(Firefox وBlender الأكبر) وقد يستغرق وقتاً طويلاً.",
        "cli_type_name": "تطبيق محمي. اكتب اسمه لتأكيد الحذف: ",
    },
}

# invisible direction marks that a terminal may paste in front of what you type
STRIP = dict.fromkeys(map(ord, "\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\ufeff"))


def clean(text):
    return text.translate(STRIP).strip()


def pick(prompt):
    """Read a menu number. Accepts Western and Arabic-Indic digits.

    Returns the number, None for an empty line, or -1 for anything that is not a number.
    """
    raw = clean(input(prompt))
    if raw.lower() in ("q", "quit", "exit"):
        return 0
    if not raw:
        return None
    if len(raw) > 6:
        return -1
    digits = ""
    for ch in raw:
        d = unicodedata.decimal(ch, None)
        if d is None:
            return -1
        digits += str(d)
    return int(digits)


def can_show(code):
    """Can this console print the language? The Linux text console has no Arabic glyphs."""
    if code == "en":
        return True
    if (sys.stdout.encoding or "").lower().replace("-", "") != "utf8":
        return False
    return not (code in RTL and os.environ.get("TERM") == "linux")


def strings_for(code):
    fallback = None
    if not can_show(code):
        fallback, code = code, "en"
    base, mode = load_strings(code)
    t = dict(CLI["en"])
    if mode == "translated":
        t.update(CLI.get(code, {}))
    t.update(base)
    return t, mode, fallback


def apps():
    return [(n, i) for n, i in get_layer().snapshot().items() if i.get("type") == "bundled_app"]


def label(t, info):
    parts = [t["installed"] if info.get("installed") else t["not_installed"]]
    parts.append(t["source_downloaded"] if info.get("source_downloaded") else t["no_source"])
    if info.get("verified"):
        parts.append(t["verified"])
    parts.append(t["system_p"] if info.get("protection") == "system" else t["removable_p"])
    return t["cli_sep"].join(parts)


def show_list(t):
    items = apps()
    if not items:
        print(t["no_apps"])
    for n, (name, info) in enumerate(items, 1):
        print(f" {n}. {name}  {info.get('license', '')}  -  {label(t, info)}")
    return items


def run(action, name, force=False, dry_run=False, executable=None):
    layer = get_layer()
    if action == "remove":
        return layer.remove_app(name, force=force)
    if action == "install":
        return layer.install_app(name, dry_run=dry_run)
    if action == "link":
        return layer.link_app(name, executable)
    funcs = {"download": layer.download_app, "verify": layer.verify_app, "launch": layer.launch_app}
    if action not in funcs:
        raise ValueError(f"Unknown action: {action}")
    return funcs[action](name)


def download_all():
    return get_layer().download_all()


def app_menu(t, name, info):
    print(f"\n{name}: {label(t, info)}")
    print(f" 1. {t['download']}\n 2. {t['verify']}\n 3. {t['remove']}\n 0. {t['cli_back']}")
    print(f" 4. {t['install']}\n 5. {t['launch']}")
    choice = pick(t["cli_prompt"])
    if choice in (0, None):
        return
    if choice == 1:
        run("download", name)
    elif choice == 2:
        run("verify", name)
    elif choice == 4:
        run("install", name)
    elif choice == 5:
        run("launch", name)
    elif choice == 3 and info.get("protection") == "system":
        # a conscious decision: the exact name must be typed
        if clean(input(t["cli_type_name"])) == name:
            run("remove", name, force=True)
        else:
            print(t["cli_cancelled"])
    elif choice == 3:
        print(t["confirm_remove"].format(n=name))
        print(t["cli_yes_no"])
        if pick(t["cli_prompt"]) == 1:
            run("remove", name)
        else:
            print(t["cli_cancelled"])
    else:
        print(t["cli_bad"])


def language_menu(t, current):
    options = [("", t["auto"]), ("en", "English"), ("ar", "العربية")]
    for n, (_, name) in enumerate(options, 1):
        print(f" {n}. {name}")
    print(f" 0. {t['cli_back']}")
    choice = pick(t["cli_prompt"])
    return options[choice - 1][0] if choice and 1 <= choice <= len(options) else current


def menu(lang=""):
    system = detect_system()
    try:
        while True:
            t, mode, fallback = strings_for(lang or system["lang"])
            print(f"\n{t['title']} - {t['subtitle']}")
            print(f"{t['kernel']}: {system['kernel']} {system['release']} ({system['machine']})")
            print(f"{t['syslang']}: {system['lang']} | {t['translation']}: {t['mode_' + mode]}")
            if fallback:
                print(t["cli_fallback"].format(lang=fallback))
            items = show_list(t)
            all_no, lang_no = len(items) + 1, len(items) + 2
            scan_no = len(items) + 3
            print(f" {all_no}. {t['cli_all']}\n {lang_no}. {t['cli_lang']}\n {scan_no}. {t['scan']}\n 0. {t['cli_quit']}")
            choice = pick(t["cli_prompt"])
            if choice is None:
                continue
            if choice == 0:
                break
            if 1 <= choice <= len(items):
                app_menu(t, *items[choice - 1])
            elif choice == all_no and items:
                print(t["cli_all_warn"].format(n=len(items)))
                print(t["cli_yes_no"])
                if pick(t["cli_prompt"]) == 1:
                    download_all()
                else:
                    print(t["cli_cancelled"])
            elif choice == lang_no:
                lang = language_menu(t, lang)
            elif choice == scan_no:
                get_layer().scan_apps()
            else:
                print(t["cli_bad"])
    except (EOFError, KeyboardInterrupt):
        print()
    print(strings_for(lang or system["lang"])[0]["cli_bye"])


def graphical():
    if platform.system() in ("Windows", "Darwin"):
        return True
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    try:
        webbrowser.get()
        return True
    except webbrowser.Error:
        return False


def serve_web(lang="", open_browser=True):
    try:
        server = ThreadingHTTPServer((web.HOST, web.PORT), web.Handler)
    except OSError as exc:
        print(f"Cannot start the web UI on port {web.PORT}: {exc}\nUse: python3 rlayers.py --cli")
        return False
    url = f"http://{web.HOST}:{web.PORT}"
    print(f"R-Layers UI running at {url} (Ctrl+C to stop)")
    if lang:
        from urllib.parse import urlencode
        url += "/?" + urlencode({"lang": lang})
    if open_browser:
        timer = threading.Timer(0.5, lambda: webbrowser.open(url))
        timer.daemon = True
        timer.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
        web.shutdown_jobs()
    return True


def main():
    p = argparse.ArgumentParser(prog="rlayers.py", description="R-Layers package and app manager")
    p.add_argument("--version", action="version", version=f"R-Layers {__version__}")
    p.add_argument("command", nargs="?",
                   choices=["list", "download", "download-all", "verify", "remove", "scan", "install", "link", "launch", "recover", "credits", "doctor"])
    p.add_argument("name", nargs="?")
    p.add_argument("executable", nargs="?", help="native executable path for link")
    p.add_argument("--dry-run", action="store_true", help="preview an install without executing it")
    p.add_argument("--json", action="store_true", help="machine-readable doctor report")
    p.add_argument("--no-browser", action="store_true", help="serve the web UI without opening a browser")
    p.add_argument("--force", action="store_true", help="allow removing a protected app")
    interface = p.add_mutually_exclusive_group()
    interface.add_argument("--web", action="store_true", help="force the web UI")
    interface.add_argument("--cli", action="store_true", help="force the terminal menu")
    p.add_argument("--lang", default="", help="interface language code, e.g. en or ar")
    a = p.parse_args()
    if a.json and a.command != "doctor":
        p.error("--json is only valid with doctor")
    if a.no_browser and (a.command or a.cli):
        p.error("--no-browser requires the web interface")
    if a.dry_run and a.command != "install":
        p.error("--dry-run is only valid with install")
    if a.force and a.command != "remove":
        p.error("--force is only valid with remove")
    if a.executable and a.command != "link":
        p.error("an executable path is only valid with link")
    if a.command in ("doctor", "recover", "list", "scan", "download-all", "credits") and a.name:
        p.error(f"'{a.command}' does not take an app name")
    if a.command == "doctor":
        from diagnostics import diagnose, print_report
        report = diagnose()
        if a.json:
            print(json.dumps(report, ensure_ascii=True, indent=2))
        else:
            print_report(report)
        return 0 if report["ok"] else 1
    if a.command == "recover":
        store = RegistryStore(os.environ.get("RLAYERS_CACHE_DIR") or DEFAULT_CACHE)
        store.recover()
        print(f"Registry restored from {store.backup}; original bytes were preserved.")
        return
    # Lazy initialization keeps --help and recovery available even with a broken registry.
    get_layer()

    if a.command == "list":
        show_list(strings_for(a.lang or detect_system()["lang"])[0])
    elif a.command == "download-all":
        sys.exit(0 if download_all() else 1)
    elif a.command == "scan":
        sys.exit(0 if get_layer().scan_apps() else 1)
    elif a.command == "credits":
        get_layer().print_credits()
    elif a.command:
        if not a.name:
            p.error(f"'{a.command}' needs an app name")
        if a.command == "link" and not a.executable:
            p.error("link needs the native executable path")
        sys.exit(0 if run(a.command, a.name, force=a.force, dry_run=a.dry_run, executable=a.executable) else 1)
    elif a.web or a.no_browser or (graphical() and not a.cli):
        sys.exit(0 if serve_web(a.lang, open_browser=not a.no_browser) else 1)
    else:
        if not a.cli:
            print("No graphical session found, using the terminal menu (--web to force the web UI).")
        menu(a.lang)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RegistryError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

