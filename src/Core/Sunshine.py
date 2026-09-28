"""
Core/Sunshine.py
Add the launcher to the app list of a Sunshine-family streaming host
(Sunshine, Vibeshine, Apollo...), so Moonlight shows it with its own cover
and starts it directly.

Those hosts keep their apps in apps.json, by default
    Windows  %ProgramFiles%\\Sunshine\\config\\apps.json    (Vibeshine too)
    Linux    ~/.config/sunshine/apps.json
or wherever "file_apps" in sunshine.conf points. The file is edited in
place: every other app and key is kept, and a copy is saved first as
apps.json.bak. Program Files is only writable by administrators, so on
Windows a denied write is retried by an elevated copy of the launcher
(one UAC prompt): "<launcher> --register-streaming <apps.json>".
"""

import json
import os
import shutil
import sys
import threading

from .Log import log

REGISTER_FLAG = "--register-streaming"
SHOW_PROMPT_FLAG = "--streaming"

_HOSTS = ("Sunshine", "Vibeshine", "Apollo", "Vibepollo")


def _read_file_apps(conf):
    """Value of file_apps in a sunshine.conf, or None."""
    try:
        with open(conf, "r", encoding="utf-8-sig") as f:
            for line in f:
                key, sep, value = line.partition("=")
                if sep and key.strip() == "file_apps" and value.strip():
                    return value.strip().strip('"')
    except OSError:
        pass
    return None


def find_apps_file(override=None):
    """apps.json of the streaming host installed on this PC, or None."""
    if override:
        return override if os.path.isfile(override) else None
    config_dirs = []
    if sys.platform == "win32":
        for base in {os.getenv("ProgramFiles"), os.getenv("ProgramW6432")} - {None}:
            config_dirs += [os.path.join(base, host, "config") for host in _HOSTS]
    else:
        home_config = os.getenv("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        config_dirs += [os.path.join(home_config, host.lower()) for host in _HOSTS]
    for config_dir in config_dirs:
        conf = os.path.join(config_dir, "sunshine.conf")
        custom = _read_file_apps(conf)
        if custom:
            path = custom if os.path.isabs(custom) else os.path.join(config_dir, custom)
            if os.path.isfile(path):
                return path
        path = os.path.join(config_dir, "apps.json")
        if os.path.isfile(path):
            return path
    return None


def launcher_command():
    """(argv list, working dir) that start this launcher."""
    if getattr(sys, "frozen", False):
        return [sys.executable], os.path.dirname(sys.executable)
    script = os.path.abspath(sys.argv[0])
    return [sys.executable, script], os.path.dirname(script)


def _quote(argv):
    return " ".join(f'"{a}"' if " " in a else a for a in argv)


def _same_program(cmd, program):
    return os.path.normcase(program) in os.path.normcase(cmd or "")


def find_entry(apps, program):
    """The app entry that starts `program`, or None."""
    return next((a for a in apps.get("apps", []) if _same_program(a.get("cmd"), program)), None)


def is_registered(apps_path):
    try:
        with open(apps_path, "r", encoding="utf-8-sig") as f:
            return find_entry(json.load(f), launcher_command()[0][-1]) is not None
    except Exception:
        return False


def register(apps_path, name, image_path):
    """
    Add or update the launcher's entry in apps.json.

    Returns:
        str: "added", "updated" or "unchanged".
    Raises:
        PermissionError: the file needs administrator rights.
    """
    with open(apps_path, "r", encoding="utf-8-sig") as f:
        apps = json.load(f)
    argv, working_dir = launcher_command()
    entry = find_entry(apps, argv[-1])
    wanted = {"cmd": _quote(argv), "working-dir": working_dir, "image-path": image_path}

    if entry is None:
        taken = {a.get("name", "").lower() for a in apps.get("apps", [])}
        label = name if name.lower() not in taken else f"{name} Launcher"
        entry = {"name": label}
        apps.setdefault("apps", []).append(entry)
        result = "added"
    elif all(entry.get(k) == v for k, v in wanted.items()):
        return "unchanged"
    else:
        result = "updated"
    entry.update(wanted)

    text = json.dumps(apps, indent=4, ensure_ascii=False)
    backup = apps_path + ".bak"
    if not os.path.exists(backup):
        shutil.copyfile(apps_path, backup)
    tmp = apps_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, apps_path)
    log("INFO", f"Streaming host app list: {result}", f"{entry['name']} -> {apps_path}")
    return result


def register_elevated(apps_path, on_done):
    """
    Windows: run "<launcher> --register-streaming <apps.json>" as
    administrator (UAC prompt) on a background thread, then call
    on_done(exit_ok) from that thread.
    """
    def run():
        ok = False
        try:
            import ctypes
            from ctypes import wintypes

            class SHELLEXECUTEINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                            ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR),
                            ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
                            ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                            ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR),
                            ("hkeyClass", wintypes.HKEY), ("dwHotKey", wintypes.DWORD),
                            ("hIcon", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

            argv, working_dir = launcher_command()
            info = SHELLEXECUTEINFO()
            info.cbSize = ctypes.sizeof(info)
            info.fMask = 0x00000040                    # SEE_MASK_NOCLOSEPROCESS
            info.lpVerb = "runas"
            info.lpFile = argv[0]
            info.lpParameters = _quote(argv[1:] + [REGISTER_FLAG, apps_path])
            info.lpDirectory = working_dir
            info.nShow = 0                             # SW_HIDE
            if ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)) and info.hProcess:
                ctypes.windll.kernel32.WaitForSingleObject(info.hProcess, 60000)
                code = wintypes.DWORD()
                ctypes.windll.kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
                ctypes.windll.kernel32.CloseHandle(info.hProcess)
                ok = code.value == 0
            else:
                log("INFO", "Elevation refused or failed")
        except Exception as e:
            log("EXCEPTION", "Elevated registration failed", e)
        on_done(ok)
    threading.Thread(target=run, daemon=True).start()


def cover_path(emulator_name):
    """Moonlight box art, written next to the launcher."""
    from .Paths import base_dir
    return os.path.join(base_dir(), f"{emulator_name}Launcher.cover.png")


def make_cover(path, icon_path, title):
    """Moonlight box art (PNG, 600x800): the launcher icon on a dark gradient."""
    from PIL import Image, ImageDraw, ImageFilter
    from . import Glyphs
    w, h = 600, 800
    cover = Image.linear_gradient("L").resize((w, h)).point(lambda v: int(v * 0.55))
    cover = Image.merge("RGB", (cover.point(lambda v: 12 + v // 5), cover.point(lambda v: 16 + v // 3),
                                cover.point(lambda v: 30 + v // 2)))
    glow = Image.new("L", (w, h), 0)
    ImageDraw.Draw(glow).ellipse((60, 110, 540, 590), fill=120)
    cover.paste((245, 217, 10), (0, 0), glow.filter(ImageFilter.GaussianBlur(90)).point(lambda v: v // 3))
    try:
        with Image.open(icon_path) as icon:
            icon = icon.convert("RGBA").resize((340, 340), Image.LANCZOS)
        cover.paste(icon, ((w - 340) // 2, 180), icon)
    except Exception:
        pass
    draw = ImageDraw.Draw(cover)
    draw.text((w // 2, 640), title.upper(), font=Glyphs.font(64), fill=(244, 245, 247), anchor="mm")
    draw.text((w // 2, 712), "LAUNCHER", font=Glyphs.font(30), fill=(245, 217, 10), anchor="mm")
    cover.save(path, "PNG")
    return path
