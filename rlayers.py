#!/usr/bin/env python3
"""R-Layers entry point: picks the right interface for the current session.
 
  python3 rlayers.py                   graphical session: web UI, otherwise terminal menu
  python3 rlayers.py --web | --cli     force one of them
  python3 rlayers.py list
  python3 rlayers.py download|verify|remove NAME [--force]
 
Needs rlayers_web.py and layer.py in the same folder.
"""
import argparse
import os
import platform
import sys
import threading
import webbrowser
from http.server import ThreadingHTTPServer
 
import rlayers_web as web
from rlayers_web import RTL, detect_system, layer, load_strings
 
CLI = {
    "en": {
        "cli_pick": "Number = open app, l = language, q = quit",
        "cli_actions": "d = download, v = verify, r = remove, b = back",
        "cli_bad": "Invalid choice.", "cli_bye": "Goodbye.", "cli_cancelled": "Cancelled.",
        "cli_type_name": "Protected app. Type its name to confirm removal: ",
        "cli_lang_ask": "Language code (en, ar), empty for system default: ",
        "cli_fallback": "This console cannot display '{lang}', showing English.",
        "cli_prompt": "> ",
    },
    "ar": {
        "cli_pick": "رقم = فتح تطبيق، l = اللغة، q = خروج",
        "cli_actions": "d = تنزيل، v = تحقق، r = حذف، b = رجوع",
        "cli_bad": "اختيار غير صحيح.", "cli_bye": "إلى اللقاء.", "cli_cancelled": "تم الإلغاء.",
        "cli_type_name": "تطبيق محمي. اكتب اسمه لتأكيد الحذف: ",
        "cli_lang_ask": "رمز اللغة (en, ar)، أو اتركه فارغاً لحسب النظام: ",
    },
}
 
 
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
    layer.packages = layer._load_cache()  # pick up changes made elsewhere
    return [(n, i) for n, i in layer.packages.items() if i.get("type") == "bundled_app"]
 
 
def label(t, info):
    parts = [t["installed"] if info.get("installed") else t["not_installed"]]
    if info.get("verified"):
        parts.append(t["verified"])
    parts.append(t["system_p"] if info.get("protection") == "system" else t["removable_p"])
    return ", ".join(parts)
 
 
def show_list(t):
    items = apps()
    if not items:
        print(t["no_apps"])
    for n, (name, info) in enumerate(items, 1):
        print(f" {n}) {name}  [{info.get('license', '')}]  {label(t, info)}")
    return items
 
 
def run(action, name, force=False):
    layer.packages = layer._load_cache()
    if name not in layer.packages:
        print(f"'{name}' not registered")
        return False
    if action == "download":
        return layer.download_app(name)
    if action == "verify":
        return layer.verify_app(name)
    return layer.remove_app(name, force=force)
 
 
def app_menu(t, name, info):
    protected = info.get("protection") == "system"
    print(f"\n{name}: {label(t, info)}")
    print(t["cli_actions"])
    choice = input(t["cli_prompt"]).strip().lower()
    if choice == "d":
        run("download", name)
    elif choice == "v":
        run("verify", name)
    elif choice == "r":
        if protected:  # a conscious decision: the exact name must be typed
            if input(t["cli_type_name"]).strip() != name:
                print(t["cli_cancelled"])
                return
            run("remove", name, force=True)
        elif input(t["confirm_remove"].format(n=name) + " [y/N] ").strip().lower() == "y":
            run("remove", name)
        else:
            print(t["cli_cancelled"])
    elif choice not in ("b", ""):
        print(t["cli_bad"])
 
 
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
            print(t["cli_pick"])
            choice = input(t["cli_prompt"]).strip().lower()
            if choice == "q":
                break
            if choice == "l":
                lang = input(t["cli_lang_ask"]).strip().lower()
            elif choice.isdigit() and 1 <= int(choice) <= len(items):
                name, info = items[int(choice) - 1]
                app_menu(t, name, info)
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
 
 
def serve_web():
    try:
        server = ThreadingHTTPServer((web.HOST, web.PORT), web.Handler)
    except OSError as exc:
        print(f"Cannot start the web UI on port {web.PORT}: {exc}\nUse: python3 rlayers.py --cli")
        return
    url = f"http://{web.HOST}:{web.PORT}"
    print(f"R-Layers UI running at {url} (Ctrl+C to stop)")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
 
 
def main():
    p = argparse.ArgumentParser(prog="rlayers.py", description="R-Layers package and app manager")
    p.add_argument("command", nargs="?", choices=["list", "download", "verify", "remove"])
    p.add_argument("name", nargs="?")
    p.add_argument("--force", action="store_true", help="allow removing a protected app")
    p.add_argument("--web", action="store_true", help="force the web UI")
    p.add_argument("--cli", action="store_true", help="force the terminal menu")
    p.add_argument("--lang", default="", help="interface language code, e.g. en or ar")
    a = p.parse_args()
 
    if a.command == "list":
        show_list(strings_for(a.lang or detect_system()["lang"])[0])
    elif a.command:
        if not a.name:
            p.error(f"'{a.command}' needs an app name")
        sys.exit(0 if run(a.command, a.name, force=a.force) else 1)
    elif a.web or (graphical() and not a.cli):
        serve_web()
    else:
        if not a.cli:
            print("No graphical session found, using the terminal menu (--web to force the web UI).")
        menu(a.lang)
 
 
if __name__ == "__main__":
    main()
 