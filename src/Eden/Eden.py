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
from Core.I18n import register, t
from Core.Log import log, fatal
from Core.Paths import find_appimage, read_path_override, resource_path
from Core.Process import mount_appimage, unmount_appimage

from . import Config, Games, Switch
from .TitleDb import TitleDb
from .Ini import IniFile, read_bool

# Environment overrides
ENV_SDL_BACKEND = "EDEN_LAUNCHER_SDL"       # "SDL2" or "SDL3"
ENV_USER_DIR = "EDEN_LAUNCHER_USER_DIR"     # Eden's user folder (holds config/)

SETTINGS_SECTION = "Eden"

_SETTINGS_EN = f"""\
[{SETTINGS_SECTION}]
; Default A/B/X/Y layout:
;   Xbox     = every button does what its label says (bottom A = Switch A)
;   Nintendo = by position, like Eden's auto-mapping
; or the name of a profile saved in Eden. Each player can change it with X.
layout = {Config.XBOX_PROFILE}

; Docked (TV) mode, which many games need for more than one controller:
;   auto   = turn it on with 2 or more players
;   always = always turn it on
;   never  = leave Eden's setting alone
docked = auto

; The "controllers" window some games open at start or before multiplayer.
; It cannot be used with a gamepad, so by default it is skipped and the game
; uses the players assigned here:
;   off  = skip it (recommended)
;   on   = show it
;   keep = leave Eden's setting alone
controller_applet = off

; Game grid when the launcher starts without a game: true / false
game_picker = true
; Game folders separated by ';' (subfolders included).
; Empty = the folders already configured in Eden.
game_dirs =
; Start games from the grid in fullscreen: true / false
fullscreen = true
"""

_SETTINGS_IT = f"""\
[{SETTINGS_SECTION}]
; Layout predefinito dei tasti A/B/X/Y:
;   Xbox     = ogni tasto fa quello che c'e' scritto sopra (A in basso = A di Switch)
;   Nintendo = per posizione, come la mappatura automatica di Eden
; oppure il nome di un profilo salvato in Eden. Si cambia per giocatore con X.
layout = {Config.XBOX_PROFILE}

; Modalita' TV (docked), necessaria a molti giochi per piu' controller:
;   auto   = attivala quando ci sono 2 o piu' giocatori
;   always = attivala sempre
;   never  = non toccare l'impostazione di Eden
docked = auto

; Finestra "controller" che alcuni giochi aprono all'avvio o prima del
; multigiocatore. Non si puo' usare con il gamepad, quindi di default viene
; saltata e il gioco usa i giocatori assegnati qui:
;   off  = saltala (consigliato)
;   on   = mostrala
;   keep = non toccare l'impostazione di Eden
controller_applet = off

; Lista dei giochi quando il launcher parte senza un gioco: true / false
game_picker = true
; Cartelle dei giochi separate da ';' (sottocartelle incluse).
; Vuoto = usa le cartelle gia' configurate in Eden.
game_dirs =
; Avvia i giochi della lista a schermo intero: true / false
fullscreen = true
"""

DEFAULT_SETTINGS = {"en": _SETTINGS_EN, "it": _SETTINGS_IT}

register({
    "en": {
        "eden_missing_title": "Eden Missing",
        "eden_missing_text": "Could not find {exe} in:\n{dir}\n\n"
                             "Place the launcher next to Eden, or write Eden's folder\n"
                             "into EdenPath.config next to the launcher.",
        "eden_config_title": "Configuration Error",
        "eden_config_text": "Could not read Eden's qt-config.ini.\n\n"
                            "Open Eden manually once so it writes a valid configuration,\n"
                            "then start this launcher again.",
    },
    "it": {
        "eden_missing_title": "Eden non trovato",
        "eden_missing_text": "Impossibile trovare {exe} in:\n{dir}\n\n"
                             "Metti il launcher nella cartella di Eden, oppure scrivi\n"
                             "la cartella di Eden in EdenPath.config accanto al launcher.",
        "eden_config_title": "Errore di configurazione",
        "eden_config_text": "Impossibile leggere qt-config.ini di Eden.\n\n"
                            "Apri Eden una volta a mano per creare una configurazione valida,\n"
                            "poi riavvia il launcher.",
    },
})

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
    default_settings = DEFAULT_SETTINGS

    def __init__(self):
        super().__init__()
        self.user_dir = None
        self.cache_dir = None
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
            self.cache_dir = os.path.join(self.user_dir, "cache")

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
                self.cache_dir = os.path.join(portable, "cache")
            else:
                xdg_data = os.getenv("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
                xdg_config = os.getenv("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
                xdg_cache = os.getenv("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
                self.user_dir = os.path.join(xdg_data, "eden")
                config_dir = os.path.join(xdg_config, "eden")
                self.cache_dir = os.path.join(xdg_cache, "eden")

        # Explicit override for unusual setups (custom data directory, ...)
        override = os.getenv(ENV_USER_DIR)
        if override:
            self.user_dir = override
            config_dir = os.path.join(override, "config")
            self.cache_dir = os.path.join(override, "cache")

        self.config_path = os.path.join(config_dir, "qt-config.ini")
        self.profiles_dir = os.path.join(config_dir, "input")

        if not os.path.exists(self.exe):
            fatal(
                t("eden_missing_title"),
                t("eden_missing_text", exe=os.path.basename(self.exe), dir=self.dir),
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
                    t("eden_config_title"),
                    t("eden_config_text"),
                    "Config file unreadable", self.config_path
                )
        template = Config.load_template(ini)
        profiles = Config.load_profiles(self.profiles_dir, template)

        # The profile named by "layout" becomes the default (first) one
        wanted = self._setting("layout", Config.DEFAULT_PROFILE)
        match = next((k for k in profiles if k.lower() == wanted.lower()), None)
        if match is None:
            log("WARNING", "layout setting names an unknown profile, using default", wanted)
            return profiles
        log("INFO", "Default layout", match)
        return {match: profiles[match], **{k: v for k, v in profiles.items() if k != match}}

    # ------------------------------------------------------------------
    # 4. Writing the launch configuration
    # ------------------------------------------------------------------
    def write_input_config(self, assignments, hardware):
        docked = self._setting("docked", "auto").lower()
        force_docked = docked == "always" or (docked == "auto" and len(assignments) >= 2)
        applet = self._setting("controller_applet", "off").lower()
        disable_applet = {"off": True, "on": False}.get(applet)  # "keep" -> None
        Config.write_input(self.config_path, assignments, hardware, self.profiles,
                           force_docked=force_docked, disable_controller_applet=disable_applet)

    # ------------------------------------------------------------------
    # 6. Game picker
    # ------------------------------------------------------------------
    def game_picker_enabled(self):
        return self.settings.get_bool(SETTINGS_SECTION, "game_picker", True)

    def list_games(self):
        """Runs on a background thread (see Core/App.py)."""
        if not self.game_picker_enabled():
            return []

        folders = [(os.path.expandvars(os.path.expanduser(d)), True)
                   for d in self.settings.get_list(SETTINGS_SECTION, "game_dirs")]
        if not folders and os.path.exists(self.config_path):
            try:
                folders = Games.eden_game_dirs(IniFile.load(self.config_path).items("UI"))
            except Exception as e:
                log("EXCEPTION", "Could not read Eden's game folders", e)
        log("INFO", "Game folders", "; ".join(f for f, _ in folders) or "none")

        # prod.keys lets the launcher read title IDs inside NSP/XCI files
        keys_file = os.path.join(self.user_dir, "keys", "prod.keys")
        header_key = Switch.load_header_key(keys_file)
        if not header_key:
            log("WARNING", "No header_key in prod.keys - updates/DLC recognised by name only", keys_file)
        known = self.settings.load_cache("library")
        games = Games.find_games(folders, header_key=header_key, cache_dir=self.cache_dir,
                                 titledb=TitleDb.load(), known=known)
        self.settings.save_cache("library", known)
        return games

    def game_command(self, path):
        command = [self.exe]
        if self.settings.get_bool(SETTINGS_SECTION, "fullscreen", True):
            command.append("-f")
        return command + ["-g", path]

    def save_folders(self, game):
        """
        <user>/nand/user/save/0000000000000000/<profile id>/<TITLEID>, one per
        Eden user profile (plus the all-zero "device" profile).
        """
        title_id = (game.get("title_id") or "").upper()
        root = os.path.join(self.user_dir, "nand", "user", "save")
        if not title_id:
            return None, []
        folders = []
        users = os.path.join(root, "0000000000000000")
        try:
            for user in sorted(os.listdir(users)):
                for name in os.listdir(os.path.join(users, user)):
                    path = os.path.join(users, user, name)
                    if name.upper() == title_id and os.path.isdir(path):
                        folders.append(path)
        except OSError:
            pass            # no saves yet
        return root, folders

    def _setting(self, key, default):
        if self.settings is None:
            return default
        return self.settings.get(SETTINGS_SECTION, key, default)

    # ------------------------------------------------------------------
    # 5. Teardown
    # ------------------------------------------------------------------
    def cleanup(self):
        if self.is_appimage:
            unmount_appimage()
