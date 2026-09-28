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

import sys

from .I18n import set_language
from .Log import init_log, log
from .Paths import base_dir
from .Sdl import load_sdl
from .Settings import LAUNCHER_SECTION, load_settings
from . import Sunshine

LAUNCHER_VERSION = "2.1.0"


def _register_streaming(emulator, apps_path):
    """Elevated helper run: add the app entry, exit 0 on success."""
    try:
        Sunshine.register(apps_path, emulator.name, Sunshine.cover_path(emulator.name))
        sys.exit(0)
    except Exception as e:
        log("EXCEPTION", "Streaming host registration failed", e)
        sys.exit(1)


def run(emulator):
    """
    Boot the launcher for a given emulator and hand control to the UI loop.

    Args:
        emulator (Emulator): A fresh instance from an emulator package.
    """
    # Launcher-only flags never reach the emulator (and do not count as
    # "a game was passed" for the game picker)
    flags = [a for a in sys.argv[1:] if a in (Sunshine.REGISTER_FLAG, Sunshine.SHOW_PROMPT_FLAG)]
    register_target = None
    if Sunshine.REGISTER_FLAG in sys.argv:
        position = sys.argv.index(Sunshine.REGISTER_FLAG)
        register_target = sys.argv[position + 1] if position + 1 < len(sys.argv) else None
        del sys.argv[position:position + 2]
    sys.argv = [a for a in sys.argv if a not in flags]
    emulator.show_streaming_prompt = Sunshine.SHOW_PROMPT_FLAG in flags

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

    if register_target:
        _register_streaming(emulator, register_target)

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
