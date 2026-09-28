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
"""

from .I18n import set_language
from .Log import init_log, log
from .Paths import base_dir
from .Sdl import load_sdl
from .Settings import LAUNCHER_SECTION, load_settings

LAUNCHER_VERSION = "2.1.0"


def run(emulator):
    """
    Boot the launcher for a given emulator and hand control to the UI loop.

    Args:
        emulator (Emulator): A fresh instance from an emulator package.
    """
    import sys

    # 0. Launcher settings and UI language, first so that even the
    #    "emulator not found" dialog is translated
    emulator.settings = load_settings(base_dir(), f"{emulator.name}Launcher", emulator.default_settings)
    language = set_language(emulator.settings.get(LAUNCHER_SECTION, "language", "auto"))

    # 1. Where is the emulator?
    emulator.locate(base_dir())

    # 2. Logging (path-dependent, so it cannot start any earlier)
    init_log(emulator.log_dir(), LAUNCHER_VERSION, f"{emulator.name}Launcher")
    log("INFO", f"=== {emulator.name}Launcher", LAUNCHER_VERSION + " ===")
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
    emulator.profiles = emulator.load_profiles()

    LauncherApp(root, emulator, sdl)
    root.mainloop()
