"""Discover existing desktop apps and prepare native package-manager installs."""
import os
import platform
import shutil
from pathlib import Path

# Executable names and relative locations are explicit, never read as commands from JSON.
EXECUTABLES = {
    "Nautilus": ("nautilus",), "Joplin": ("Joplin", "joplin-desktop"),
    "Flameshot": ("flameshot",), "Firefox": ("firefox", "firefox-esr"),
    "Ladybird": ("Ladybird", "ladybird"), "qutebrowser": ("qutebrowser",),
    "VLC": ("vlc", "VLC"), "Blender": ("blender",),
}
WINDOWS_PATHS = {
    "Joplin": ("Joplin/Joplin.exe", "Programs/Joplin/Joplin.exe"),
    "Flameshot": ("Flameshot/bin/flameshot.exe", "Flameshot/flameshot.exe"),
    "Firefox": ("Mozilla Firefox/firefox.exe",),
    "VLC": ("VideoLAN/VLC/vlc.exe",),
    "qutebrowser": ("qutebrowser/qutebrowser.exe", "Programs/qutebrowser/qutebrowser.exe"),
    "Blender": ("Blender Foundation/Blender*/blender.exe",),
}
WINGET = {
    "Joplin": "Joplin.Joplin", "Flameshot": "Flameshot.Flameshot",
    "Firefox": "Mozilla.Firefox", "VLC": "VideoLAN.VLC",
    "Blender": "BlenderFoundation.Blender", "qutebrowser": "qutebrowser.qutebrowser",
}
APT = {name: name.lower() for name in ("Nautilus", "Flameshot", "Firefox", "VLC", "Blender", "qutebrowser")}
BREW = {"Joplin": "joplin", "Firefox": "firefox", "VLC": "vlc", "Blender": "blender", "qutebrowser": "qutebrowser"}


def valid_executable(value):
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Executable not found: {path}")
    if os.name == "nt":
        if path.suffix.lower() != ".exe":
            raise ValueError("Choose a native .exe application, not a script or shortcut.")
    elif not os.access(path, os.X_OK):
        raise ValueError(f"File is not executable: {path}")
    return str(path)


def discover(name):
    for command in EXECUTABLES.get(name, ()):
        found = shutil.which(command)
        if found:
            try:
                return valid_executable(found)
            except ValueError:
                pass
    candidates = []
    if platform.system() == "Windows":
        roots = [Path(os.environ[key]) for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA") if os.environ.get(key)]
        for root in roots:
            for relative in WINDOWS_PATHS.get(name, ()):
                candidates.extend(sorted(root.glob(relative), reverse=True))
        # App Paths covers custom installation locations registered by native installers.
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for command in EXECUTABLES.get(name, ()):
                try:
                    with winreg.OpenKey(hive, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{command}.exe") as key:
                        candidates.append(Path(winreg.QueryValue(key, None).strip('"')))
                except OSError:
                    pass
    elif platform.system() == "Darwin":
        for root in (Path("/Applications"), Path.home() / "Applications"):
            for command in EXECUTABLES.get(name, ()):
                candidates.append(root / f"{name}.app" / "Contents" / "MacOS" / command)
    elif platform.system() == "Linux":
        candidates.extend((Path.home() / "Applications" / f"{name}.AppImage",
                           Path.home() / ".local" / "share" / name.lower() / f"{name}.AppImage"))
    for candidate in candidates:
        try:
            return valid_executable(candidate)
        except (OSError, ValueError):
            continue
    return None


def install_command(name, system=None):
    """A fixed argument list, suitable for preview or shell=False execution."""
    system = system or platform.system()
    if system == "Windows" and name in WINGET:
        return ["winget", "install", "--id", WINGET[name], "--exact", "--source", "winget", "--disable-interactivity"]
    if system == "Linux" and name in APT:
        package = APT[name]
        if name == "Firefox":
            try:
                distro = platform.freedesktop_os_release().get("ID", "")
            except OSError:
                distro = ""
            if distro in ("debian", "kali", "parrot"):
                package = "firefox-esr"
        return ["apt-get", "install", "--", package]
    if system == "Darwin" and name in BREW:
        return ["brew", "install", "--cask", BREW[name]]
    raise ValueError(f"No automatic installer for {name} on {system}. Install it from its upstream instructions, then use 'link'.")


def installation_status(name, system=None):
    """Describe support without installing anything or changing the registry."""
    try:
        command = install_command(name, system)
    except ValueError:
        return {"supported": False, "available": False, "manager": "", "command": []}
    return {"supported": True, "available": bool(shutil.which(command[0])),
            "manager": command[0], "command": command}
