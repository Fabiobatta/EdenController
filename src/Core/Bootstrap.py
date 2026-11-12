"""
Core/Bootstrap.py
The startup sequence, shared by every emulator entry script.

The order below is not cosmetic - each step depends on the previous one:

    settings    "<Name>Launcher.ini" and the UI language, before anything
                can show a dialog
    locate()    resolves the emulator directory, so log_dir() has an answer
    init_log()  starts the logger, so everything after it is recorded
    prepare()   detects the emulator version and snapshots the child env,
                which must happen before SDL publishes its own SDL_* vars
    load_sdl()  imports the SDL wrapper (binds the shared library)
    profiles    discovered once, then owned by the emulator instance

Nothing here may fail silently: the launcher is a windowed program without
a console, so an unexpected error anywhere in the sequence is written to
"<Name>Launcher.crash.log" next to the launcher and shown in a dialog
(report_crash). Errors inside the running UI are logged the same way and
the UI keeps going.
"""

import os
import sys
import time
import traceback

from .Build import BUILD
from .I18n import set_language, t
from .Log import init_log, log
from .Paths import base_dir
from .Sdl import load_sdl
from .Settings import LAUNCHER_SECTION, load_settings

LAUNCHER_VERSION = "2.1.0"


def version_text():
    return LAUNCHER_VERSION + (f" (build {BUILD})" if BUILD else "")


def crash_log_path(name):
    return os.path.join(base_dir(), f"{name}Launcher.crash.log")


def write_crash(name, where, details):
    """Append an error report to the crash log; returns its path (None if unwritable)."""
    path = crash_log_path(name)
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} | {name}Launcher {version_text()} | "
                    f"{sys.platform} | {where}\n{details.rstrip()}\n\n")
        return path
    except OSError:
        return None


def report_crash(name, where):
    """
    The launcher cannot go on (call from an except block): keep the
    traceback in the crash log and the log, tell the user, exit.
    """
    details = traceback.format_exc()
    path = write_crash(name, where, details)
    try:
        log("EXCEPTION", f"Launcher crashed ({where})", sys.exc_info()[1])
    except Exception:
        pass
    last_line = details.strip().splitlines()[-1] if details.strip() else ""
    message = t("crash_text", error=last_line, path=path or "-")
    try:
        from tkinter import messagebox
        messagebox.showerror(t("crash_title"), message)
    except Exception:
        if sys.platform == "win32":             # Tk itself is what failed
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(None, message, t("crash_title"), 0x10)
            except Exception:
                pass
    sys.exit(1)


def run(emulator):
    """
    Boot the launcher for a given emulator and hand control to the UI loop.

    Args:
        emulator (Emulator): A fresh instance from an emulator package.
    """
    if os.environ.get(SELFTEST_ENV):
        sys.exit(selftest(emulator))
    try:
        _run(emulator)
    except SystemExit:
        raise
    except BaseException:
        report_crash(emulator.name, "startup")


SELFTEST_ENV = "EDEN_LAUNCHER_SELFTEST"


def selftest(emulator):
    """
    Check that this build can start at all, without an emulator and without
    showing anything: every module imports, Tk opens a window, the bundled
    SDL library loads. Used by the release workflow on the freshly built
    exe; writes "<Name>Launcher.selftest.ok" next to it, or the crash log.

    Returns:
        int: the exit code (0 = fine).
    """
    try:
        import tkinter as tk
        from . import App, AppLaunch, AppLibrary, AppMenus, AppPlayers, Library, Menu, Stats, Theme, Update  # noqa: F401
        from .Paths import resource_path
        root = tk.Tk()
        root.withdraw()
        root.update()
        root.destroy()
        notes = [f"{emulator.name}Launcher {version_text()}", f"tk {tk.TkVersion}"]
        if sys.platform == "win32":
            sdl = load_sdl("SDL3", resource_path("sdl"))
            notes.append(f"sdl {sdl.__name__}")
        with open(os.path.join(base_dir(), f"{emulator.name}Launcher.selftest.ok"), "w", encoding="utf-8") as f:
            f.write("\n".join(notes) + "\n")
        return 0
    except BaseException:
        write_crash(emulator.name, "selftest", traceback.format_exc())
        return 1


def _run(emulator):
    # 0. Launcher settings and UI language, first so that even the
    #    "emulator not found" dialog is translated
    emulator.settings = load_settings(base_dir(), f"{emulator.name}Launcher", emulator.default_settings)
    language = set_language(emulator.settings.get(LAUNCHER_SECTION, "language", "auto"))

    # 1. Where is the emulator?
    emulator.locate(base_dir())

    # 2. Logging (path-dependent, so it cannot start any earlier)
    init_log(emulator.log_dir(), LAUNCHER_VERSION, f"{emulator.name}Launcher")
    log("INFO", f"=== {emulator.name}Launcher", version_text() + " ===")
    log("INFO", "OS", sys.platform + (" (AppImage)" if emulator.is_appimage else ""))
    log("INFO", f"{emulator.name} dir", emulator.dir)
    log("INFO", "Config", emulator.config_path)
    log("INFO", "Executable", emulator.exe)
    log("INFO", "Settings", emulator.settings.path)
    log("INFO", "Language", language)

    # 3. Version, SDL backend choice, environment (before SDL is imported)
    emulator.prepare()

    # 4. Bind the SDL shared library
    sdl = load_sdl(emulator.sdl_backend(), emulator.sdl_dir())

    # 5. UI
    # Import App (and transitively Ui) BEFORE creating the window: Ui.py
    # makes the process DPI-aware at import time, which must happen before
    # any window exists or Windows scales the fullscreen window twice.
    import tkinter as tk
    from .App import LauncherApp

    root = tk.Tk()

    def callback_error(kind, value, trace):
        # An error in a UI callback: keep it, keep running
        details = "".join(traceback.format_exception(kind, value, trace))
        write_crash(emulator.name, "ui", details)
        log("ERROR", "Error in the UI", details)
    root.report_callback_exception = callback_error

    emulator.profiles = emulator.load_profiles()

    LauncherApp(root, emulator, sdl)
    fine_timers(True)
    try:
        root.mainloop()
    finally:
        fine_timers(False)


def fine_timers(on):
    """
    Windows wakes timers on a 15.6 ms tick by default, so a 16 ms Tk timer
    fires every 31 ms - animations at an uneven 30 fps and controllers read
    half as often. timeBeginPeriod(1) asks for 1 ms ticks while the
    launcher runs (games and video players do the same); undone on exit.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        winmm = ctypes.WinDLL("winmm")
        (winmm.timeBeginPeriod if on else winmm.timeEndPeriod)(1)
    except Exception as e:
        log("WARNING", "Could not change the timer resolution", e)
