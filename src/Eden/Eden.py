"""
Eden/Eden.py
The Eden implementation of the Core.Emulator contract.

Everything Eden-specific enters the launcher through this class: where the
binary and qt-config.ini live, which SDL major version to use, and which SDL
hints make the launcher see controllers exactly the way Eden will.
"""

import os
import sys

from Core.Emulator import Emulator
from Core.Log import log, fatal
from Core.Paths import find_appimage, read_path_override, resource_path
from Core.Process import mount_appimage, unmount_appimage

from . import Config
from .Ini import IniFile, read_bool

# Environment overrides
ENV_SDL_BACKEND = "EDEN_LAUNCHER_SDL"       # "SDL2" or "SDL3"
ENV_USER_DIR = "EDEN_LAUNCHER_USER_DIR"     # Eden's user folder (holds config/)

_SDL_LIBS = {
    "SDL3": {"win32": "SDL3.dll", "darwin": "libSDL3.dylib", "linux": "libSDL3.so.0"},
    "SDL2": {"win32": "SDL2.dll", "darwin": "libSDL2.dylib", "linux": "libSDL2-2.0.so.0"},
}


def _platform():
    if sys.platform == "win32":
        return "win32"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def _has_lib(directory, backend):
    if not directory:
        return False
    name = _SDL_LIBS[backend][_platform()]
    # Linux libraries may only exist under their unversioned or fully
    # versioned name, e.g. libSDL3.so or libSDL3.so.0.2.10
    stem = name.split(".so")[0] if _platform() == "linux" else name
    try:
        return any(f == name or f.startswith(stem) for f in os.listdir(directory))
    except OSError:
        return False


class Eden(Emulator):
    """Eden (Nintendo Switch emulator, yuzu lineage)."""

    name = "Eden"

    def __init__(self):
        super().__init__()
        self.user_dir = None
        self.backend = "SDL3"
        self.appimage_path = None
        self._sdl_dir = None

    # ------------------------------------------------------------------
    # 1. Where everything lives
    # ------------------------------------------------------------------
    def locate(self, base):
        """
        Resolve the Eden directory, binary and qt-config.ini.

        Directory priority : EdenPath.config override > launcher directory.
        User folder        : mirrors common/fs/path_util.cpp - a portable
                             "user" folder wins over the global location.
        On Linux an AppImage is mounted and self.dir becomes <mount>/usr/bin.
        """
        self.dir = read_path_override(base, self.name) or base
        platform = _platform()

        if platform == "win32":
            self.exe = os.path.join(self.dir, "eden.exe")
            portable = os.path.join(self.dir, "user")
            global_dir = os.path.join(os.getenv("APPDATA") or "", "eden")
            self.user_dir = portable if os.path.isdir(portable) else global_dir
            config_dir = os.path.join(self.user_dir, "config")

        else:
            if platform == "darwin":
                bundle = os.path.join(self.dir, "Eden.app", "Contents", "MacOS", "eden")
                self.exe = bundle if os.path.exists(bundle) else os.path.join(self.dir, "eden")
            else:
                self.appimage_path = find_appimage(self.dir, "eden")
                self.is_appimage = self.appimage_path is not None
                if self.is_appimage:
                    mount_point = mount_appimage(self.appimage_path)
                    self.dir = os.path.join(mount_point, "usr", "bin")
                self.exe = os.path.join(self.dir, "eden")

            # Eden checks "user" in its working directory, which it inherits
            # from the launcher
            portable = os.path.join(os.getcwd(), "user")
            if os.path.isdir(portable):
                self.user_dir = portable
                config_dir = os.path.join(portable, "config")
            else:
                xdg_data = os.getenv("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
                xdg_config = os.getenv("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
                self.user_dir = os.path.join(xdg_data, "eden")
                config_dir = os.path.join(xdg_config, "eden")

        # Explicit override for unusual setups (custom data directory, ...)
        override = os.getenv(ENV_USER_DIR)
        if override:
            self.user_dir = override
            config_dir = os.path.join(override, "config")

        self.config_path = os.path.join(config_dir, "qt-config.ini")
        self.profiles_dir = os.path.join(config_dir, "input")

        if not os.path.exists(self.exe):
            fatal(
                "Eden Missing",
                f"Could not find {os.path.basename(self.exe)} in:\n{self.dir}\n\n"
                "Place the launcher next to Eden, or write Eden's folder\n"
                "into EdenPath.config next to the launcher.",
                "Eden binary not found", self.exe
            )

    def log_dir(self):
        return os.path.join(self.user_dir, "log")

    # ------------------------------------------------------------------
    # 2. Runtime setup - runs before the SDL wrapper is imported
    # ------------------------------------------------------------------
    def prepare(self):
        """Pick the SDL backend, snapshot the child env, then set SDL hints."""
        self.backend, self._sdl_dir = self._pick_sdl()

        # Snapshot first: SDL reads hints from the environment, and an env
        # var would override the hints Eden sets itself. Everything below is
        # for the launcher's own SDL instance only.
        self.env = os.environ.copy()

        self._apply_eden_hints()

        if not os.path.exists(self.config_path):
            log("WARNING", "qt-config.ini not found - run Eden once to create it", self.config_path)

    def _pick_sdl(self):
        """
        Eden builds link SDL3 (older 2025 releases used SDL2). Prefer the
        library Eden ships with, then the copy bundled with the launcher,
        then whatever the system provides.
        """
        forced = (os.getenv(ENV_SDL_BACKEND) or "").upper()
        backends = [forced] if forced in ("SDL2", "SDL3") else ["SDL3", "SDL2"]

        # Directory first: the library next to Eden also tells which SDL major
        # version that Eden build uses
        candidates = [self.dir, resource_path("sdl"), os.path.dirname(os.path.abspath(sys.argv[0]))]
        for directory in candidates:
            for backend in backends:
                if _has_lib(directory, backend):
                    log("INFO", "SDL library found", f"{backend} in {directory}")
                    return backend, directory

        backend = backends[0]
        log("WARNING", "No bundled SDL library found - relying on the system", backend)
        return backend, self.dir

    def _apply_eden_hints(self):
        """
        Reproduce the hints from SDLDriver's constructor
        (input_common/drivers/sdl_driver.cpp).

        They decide which SDL joystick driver claims each pad, and the driver
        is part of the GUID: the launcher must pick the same driver as Eden or
        the GUIDs it writes will never match.
        """
        items = {}
        if os.path.exists(self.config_path):
            try:
                items = IniFile.load(self.config_path).items(Config.SECTION)
            except Exception as e:
                log("EXCEPTION", "Could not read qt-config.ini for SDL hints", e)

        raw_input = read_bool(items, "enable_raw_input", False)
        disable_wgi = read_bool(items, "disable_wgi_xinput", False)
        joycon_driver = read_bool(items, "enable_joycon_driver", True)
        procon_driver = read_bool(items, "enable_procon_driver", False)

        hints = {
            "SDL_JOYSTICK_RAWINPUT": "1" if raw_input else "0",
            "SDL_JOYSTICK_HIDAPI_STEAM": "1",
            "SDL_JOYSTICK_ENHANCED_REPORTS": "1",
            "SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS": "1",
            "SDL_JOYSTICK_HIDAPI_SWITCH": "0" if procon_driver else "1",
            "SDL_JOYSTICK_HIDAPI_XBOX": "0",
        }
        if joycon_driver:
            hints["SDL_JOYSTICK_HIDAPI_JOY_CONS"] = "0"
        else:
            hints.update({
                "SDL_JOYSTICK_HIDAPI_JOY_CONS": "1",
                "SDL_JOYSTICK_HIDAPI_COMBINE_JOY_CONS": "0",
                "SDL_JOYSTICK_HIDAPI_VERTICAL_JOY_CONS": "1",
            })
        if sys.platform == "win32" and disable_wgi:
            hints["SDL_JOYSTICK_RAWINPUT_CORRELATE_XINPUT"] = "0"
            hints["SDL_JOYSTICK_WGI"] = "0"

        os.environ.update(hints)
        log("INFO", "SDL hints", ", ".join(f"{k}={v}" for k, v in hints.items()))

    def sdl_backend(self):
        return self.backend

    def sdl_dir(self):
        return self._sdl_dir

    # ------------------------------------------------------------------
    # 3. Controller profiles
    # ------------------------------------------------------------------
    def load_profiles(self):
        ini = None
        if os.path.exists(self.config_path):
            try:
                ini = IniFile.load(self.config_path)
            except Exception as e:
                log("EXCEPTION", "Could not read qt-config.ini", e)
                fatal(
                    "Configuration Error",
                    "Could not read Eden's qt-config.ini.\n\n"
                    "Open Eden manually once so it writes a valid configuration,\n"
                    "then start this launcher again.",
                    "Config file unreadable", self.config_path
                )
        template = Config.load_template(ini)
        return Config.load_profiles(self.profiles_dir, template)

    # ------------------------------------------------------------------
    # 4. Writing the launch configuration
    # ------------------------------------------------------------------
    def write_input_config(self, assignments, hardware):
        Config.write_input(self.config_path, assignments, hardware, self.profiles)

    # ------------------------------------------------------------------
    # 5. Teardown
    # ------------------------------------------------------------------
    def cleanup(self):
        if self.is_appimage:
            unmount_appimage()
