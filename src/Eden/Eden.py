"""
Eden/Eden.py
The Eden implementation of the Core.Emulator contract.

Everything Eden-specific enters the launcher through this class: where the
binary and qt-config.ini live, which SDL major version to use, and which SDL
hints make the launcher see controllers exactly the way Eden will.

Also drives Eglantine, Eden's Switch 2 fork: it keeps Eden's data folder,
qt-config.ini format, SDL version and input code, and only renames the
binary (eglantine.exe). Whichever binary sits in the folder is used, Eden
first.
"""

import os
import sys

from Core.Emulator import Emulator
from Core.I18n import register, t
from Core.Log import log, fatal
from Core.Paths import base_dir, find_appimage, read_path_override, resource_path
from Core.Process import mount_appimage, unmount_appimage

from . import Config, Games, Switch
from .TitleDb import TitleDb
from .Ini import IniFile, read_bool

# Environment overrides
ENV_SDL_BACKEND = "EDEN_LAUNCHER_SDL"       # "SDL2" or "SDL3"
ENV_USER_DIR = "EDEN_LAUNCHER_USER_DIR"     # Eden's user folder (holds config/)

SETTINGS_SECTION = "Eden"

# The Eden family: (display name, binary name), preferred first. Any other
# fork works too: named in the settings (emulator_exe), or found in the
# folder by the Qt config file name compiled into it (_find_fork)
BINARIES = (("Eden", "eden"), ("Eglantine", "eglantine"), ("Lichii", "lichii"))
FORK_MARKER = b"qt-config.ini"
# Executables next to a fork that are never the emulator itself
NOT_EMULATORS = ("edenlauncher", "unins", "maintenancetool", "crashpad", "vc_redist", "updater")

# Strings compiled into the emulator binary that show how it turns off the
# controller applet: a build that still has the [UI] option contains its key
APPLET_PROBES = ((Config.APPLET_UI, b"disableControllerApplet"),
                 (Config.APPLET_MODE, b"controller_applet_mode"))
PROBE_CHUNK = 8 * 1024 * 1024


def probe_binary(path, needles):
    """The byte strings of `needles` that occur in the file at `path`."""
    found = set()
    overlap = max(len(n) for n in needles) - 1
    tail = b""
    with open(path, "rb") as f:
        while len(found) < len(needles):
            block = f.read(PROBE_CHUNK)
            if not block:
                break
            data = tail + block
            found.update(n for n in needles if n in data)
            tail = data[-overlap:]
    return found


def _pick_binary(candidates):
    """(display name, path) of the first candidate that exists, else the first one."""
    return next(((name, path) for name, path in candidates if os.path.isfile(path)), candidates[0])


def fork_name(path):
    """What the user sees for a fork's binary: "lichii.exe" -> "Lichii"."""
    stem = os.path.splitext(os.path.basename(path))[0]
    known = {binary: name for name, binary in BINARIES}
    return known.get(stem.lower()) or (stem[:1].upper() + stem[1:])


def _find_fork(directory, extension=".exe"):
    """
    An Eden-family emulator with an unknown name in `directory`: an
    executable whose binary carries the Qt frontend's config file name
    (the command-line eden-cli has none). None if there is no such file.
    """
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return None
    for name in names:
        stem, ext = os.path.splitext(name)
        lowered = stem.lower()
        if ext.lower() != extension or lowered.endswith("-cli") or lowered.startswith(NOT_EMULATORS):
            continue
        path = os.path.join(directory, name)
        try:
            if os.path.isfile(path) and probe_binary(path, [FORK_MARKER]):
                return path
        except OSError:
            continue
    return None


def data_folder_name(exe):
    """
    Name of the per-user data folder a fork may use instead of Eden's
    ("citron.exe" -> "citron"); the caller keeps it only when that folder
    really holds a config, else falls back to "eden" (Eglantine and most
    forks keep Eden's).
    """
    return os.path.splitext(os.path.basename(exe))[0].lower()

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
; The emulator to start, for an Eden fork with another name (e.g. lichii.exe).
; Empty = find eden.exe, eglantine.exe, lichii.exe or any other Eden fork
; in the launcher's folder.
emulator_exe =
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
; L'emulatore da avviare, per un fork di Eden con un altro nome (es. lichii.exe).
; Vuoto = cerca eden.exe, eglantine.exe, lichii.exe o un altro fork di Eden
; nella cartella del launcher.
emulator_exe =
"""

DEFAULT_SETTINGS = {"en": _SETTINGS_EN, "it": _SETTINGS_IT}

register({
    "en": {
        "eden_missing_title": "Eden Missing",
        "eden_missing_text": "Could not find {exe} in:\n{dir}\n\n"
                             "Place the launcher next to Eden (or a fork: Eglantine, Lichii...),\n"
                             "or write its folder into EdenPath.config next to the launcher.\n"
                             "A fork with another name: set emulator_exe in EdenLauncher.ini.",
        "eden_config_title": "Configuration Error",
        "eden_config_text": "Could not read Eden's qt-config.ini.\n\n"
                            "Open Eden manually once so it writes a valid configuration,\n"
                            "then start this launcher again.",
    },
    "it": {
        "eden_missing_title": "Eden non trovato",
        "eden_missing_text": "Impossibile trovare {exe} in:\n{dir}\n\n"
                             "Metti il launcher nella cartella di Eden (o di un fork: Eglantine, Lichii...),\n"
                             "oppure scrivi quella cartella in EdenPath.config accanto al launcher.\n"
                             "Un fork con un altro nome: imposta emulator_exe in EdenLauncher.ini.",
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
        chosen = self._chosen_exe()

        if platform == "win32":
            if chosen:
                self.dir = os.path.dirname(chosen)
                self.display_name, self.exe = fork_name(chosen), chosen
            else:
                self.display_name, self.exe = _pick_binary(
                    [(name, os.path.join(self.dir, stem + ".exe")) for name, stem in BINARIES])
                if not os.path.isfile(self.exe):
                    fork = _find_fork(self.dir)
                    if fork:
                        self.display_name, self.exe = fork_name(fork), fork
            log("INFO", "Emulator binary", self.exe)
            portable = os.path.join(self.dir, "user")
            appdata = os.getenv("APPDATA") or ""
            own = os.path.join(appdata, data_folder_name(self.exe))
            global_dir = own if os.path.isfile(os.path.join(own, "config", "qt-config.ini")) \
                else os.path.join(appdata, "eden")
            self.user_dir = portable if os.path.isdir(portable) else global_dir
            config_dir = os.path.join(self.user_dir, "config")
            self.cache_dir = os.path.join(self.user_dir, "cache")

        else:
            if platform == "darwin":
                self.display_name, self.exe = _pick_binary(
                    [(name, path) for name, stem in BINARIES
                     for path in (os.path.join(self.dir, f"{name}.app", "Contents", "MacOS", stem),
                                  os.path.join(self.dir, stem))])
            else:
                family = list(BINARIES)
                for name, stem in BINARIES:
                    self.appimage_path = find_appimage(self.dir, stem)
                    if self.appimage_path:
                        # Look for that AppImage's own binary first
                        family.sort(key=lambda entry: entry[0] != name)
                        break
                self.is_appimage = self.appimage_path is not None
                if self.is_appimage:
                    mount_point = mount_appimage(self.appimage_path)
                    self.dir = os.path.join(mount_point, "usr", "bin")
                self.display_name, self.exe = _pick_binary(
                    [(name, os.path.join(self.dir, stem)) for name, stem in family])
            if chosen and not self.is_appimage_build():
                self.dir = os.path.dirname(chosen)
                self.display_name, self.exe = fork_name(chosen), chosen

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
                folder = data_folder_name(self.exe)
                if not os.path.isfile(os.path.join(xdg_config, folder, "qt-config.ini")):
                    folder = "eden"
                self.user_dir = os.path.join(xdg_data, folder)
                config_dir = os.path.join(xdg_config, folder)
                self.cache_dir = os.path.join(xdg_cache, folder)

        # Explicit override for unusual setups (custom data directory, ...)
        override = os.getenv(ENV_USER_DIR)
        if override:
            self.user_dir = override
            config_dir = os.path.join(override, "config")
            self.cache_dir = os.path.join(override, "cache")

        self.config_path = os.path.join(config_dir, "qt-config.ini")
        self.profiles_dir = os.path.join(config_dir, "input")

        if not os.path.exists(self.exe):
            extension = os.path.splitext(self.exe)[1]
            fatal(
                t("eden_missing_title"),
                t("eden_missing_text", exe=" / ".join(stem + extension for _, stem in BINARIES), dir=self.dir),
                "Eden binary not found", self.exe
            )

    def log_dir(self):
        return os.path.join(self.user_dir, "log")

    # ------------------------------------------------------------------
    # 2. Runtime setup - runs before the SDL wrapper is imported
    # ------------------------------------------------------------------
    def prepare(self):
        """Pick the SDL backend, snapshot the child env, then set SDL hints."""
        log("INFO", "Emulator", self.display_name)
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
        scheme = self.applet_scheme() if disable_applet is not None else None
        Config.write_input(self.config_path, assignments, hardware, self.profiles,
                           force_docked=force_docked, disable_controller_applet=disable_applet,
                           applet_scheme=scheme)

    def applet_scheme(self):
        """
        Which setting turns off the controller applet in this binary: the
        [UI] option of older Eden builds, or controller_applet_mode in newer
        ones and Eglantine. Writing the new one on an old build would be
        wrong - its AppletMode has no "Disabled" and would pick LLE instead -
        so the binary is checked for the old option's key. Read once per
        binary (size and date), then remembered.
        """
        # An AppImage is mounted at a new path every run: remember it by its file
        key = self.appimage_path or self.exe
        try:
            stat = os.stat(key)
        except OSError:
            return Config.APPLET_UI
        signature = [stat.st_size, int(stat.st_mtime)]
        cache = self.settings.load_cache("emulator") if self.settings else {}
        entry = cache.get(key)
        if entry and entry[:2] == signature:
            return entry[2]
        try:
            found = probe_binary(self.exe, [needle for _, needle in APPLET_PROBES])
            scheme = next((s for s, needle in APPLET_PROBES if needle in found), None)
        except OSError as e:
            log("WARNING", "Could not read the emulator binary", e)
            scheme = None
        if scheme is None:
            log("WARNING", "Unknown controller applet option - using the [UI] one", self.exe)
            scheme = Config.APPLET_UI
        log("INFO", "Controller applet option", scheme)
        cache[key] = signature + [scheme]
        if self.settings:
            self.settings.save_cache("emulator", cache)
        return scheme

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
        icon_dir = os.path.join(base_dir(), self.settings.get("Launcher", "covers_dir", "covers"), "nup")
        games = Games.find_games(folders, header_key=header_key, cache_dir=self.cache_dir,
                                 titledb=TitleDb.load(), known=known, icon_dir=icon_dir)
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

    def _chosen_exe(self):
        """The emulator named by the emulator_exe setting (absolute, or in the emulator folder)."""
        value = self._setting("emulator_exe", "").strip().strip('"')
        if not value:
            return None
        path = value if os.path.isabs(value) else os.path.join(self.dir, value)
        if _platform() == "win32" and not os.path.splitext(path)[1]:
            path += ".exe"
        return path

    def is_appimage_build(self):
        return bool(self.appimage_path)

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
