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
import re
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
# A string only a build that boots Switch 2 containers carries (a log message
# of core/file_sys/nup_container.cpp)
NX2_PROBE = b"NUP directory tree"
PROBE_CHUNK = 8 * 1024 * 1024
EMULATORS_SECTION = "emulators"
LOG_FILE = "eden_log.txt"
LOG_TAIL_BYTES = 256 * 1024


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


_LOG_LINE = re.compile(r"^\[\s*[\d.]+\]\s+(?P<category>\S+)\s+<(?P<level>\w+)>\s+(?P<rest>.*)$")
_LOG_SOURCE = re.compile(r"^\S+:\S*:\d+:\s*")


def parse_log_errors(text, limit=3):
    """
    Error and critical messages of a yuzu-style log, newest last:

        [  12.345678] Loader <Error> core/loader/nsp.cpp:Load:112: message

    becomes "Loader: message". Repeated messages are listed once.
    """
    messages = []
    for line in text.splitlines():
        match = _LOG_LINE.match(line.strip())
        if not match or match.group("level") not in ("Error", "Critical"):
            continue
        message = f"{match.group('category')}: {_LOG_SOURCE.sub('', match.group('rest')).strip()}"
        if message in messages:
            messages.remove(message)
        messages.append(message)
    return messages[-limit:]


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
; The "Are you sure you want to close?" question Eden asks when its window is
; closed during a game. It cannot be answered with a gamepad, and it stops
; the launcher from closing Eden cleanly:
;   off  = never ask (recommended)
;   keep = leave Eden's setting alone
confirm_close = off

; More emulators of the Eden family for the same game list: one per line,
; any name = the emulator's exe or folder. Switch 2 games then start with
; the first one that runs them (Eglantine), the others with the emulator
; above; each game's menu ([start] in the grid) can pick another one.
[Emulators]
; Eglantine = D:\\Emulators\\Eglantine\\eglantine.exe
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
; La domanda "Vuoi davvero chiudere?" che Eden fa quando si chiude la finestra
; durante un gioco. Con il gamepad non si puo' rispondere, e impedisce al
; launcher di chiudere Eden in modo pulito:
;   off  = non chiedere mai (consigliato)
;   keep = non toccare l'impostazione di Eden
confirm_close = off

; Altri emulatori della famiglia Eden per la stessa lista di giochi: uno per
; riga, un nome qualsiasi = l'exe o la cartella dell'emulatore. I giochi
; Switch 2 partono con il primo che li supporta (Eglantine), gli altri con
; l'emulatore qui sopra; dal menu di ogni gioco ([start] nella lista) se ne
; puo' scegliere un altro.
[Emulators]
; Eglantine = D:\\Emulatori\\Eglantine\\eglantine.exe
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
        "opt_docked": "TV (docked) mode",
        "opt_controller_applet": "The games' own controllers window",
        "opt_confirm_close": "Ask before closing the emulator",
        "opt_fullscreen": "Start games in fullscreen",
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
        "opt_docked": "Modalità TV (docked)",
        "opt_controller_applet": "Finestra dei controller dei giochi",
        "opt_confirm_close": "Chiedi conferma prima di chiudere l'emulatore",
        "opt_fullscreen": "Avvia i giochi a schermo intero",
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
    settings_section = SETTINGS_SECTION
    setting_choices = (
        ("docked", ("auto", "always", "never"), "auto"),
        ("controller_applet", ("off", "on", "keep"), "off"),
        ("confirm_close", ("off", "keep"), "off"),
        ("fullscreen", ("true", "false"), "true"),
    )

    def __init__(self):
        super().__init__()
        self.user_dir = None
        self.cache_dir = None
        self.backend = "SDL3"
        self.appimage_path = None
        self._sdl_dir = None
        self.config_dir = None
        self.variants = [self]              # every emulator found, this one first
        self.is_variant = False
        self.settings_key = None            # its name under [Emulators], when it comes from there
        self._profiles_loaded = False
        self._facts = {}                    # _binary_fact() answers of this run

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

        Also finds the other emulators of the family this launcher can start
        (self.variants): the other known binaries in the same folder and the
        ones listed under [Emulators] in the settings.
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

        self._locate_data(platform)

        # Explicit override for unusual setups (custom data directory, ...)
        override = os.getenv(ENV_USER_DIR)
        if override:
            self.user_dir = override
            self.config_dir = os.path.join(override, "config")
            self.cache_dir = os.path.join(override, "cache")
            self.config_path = os.path.join(self.config_dir, "qt-config.ini")
            self.profiles_dir = os.path.join(self.config_dir, "input")

        if not os.path.exists(self.exe):
            extension = os.path.splitext(self.exe)[1]
            fatal(
                t("eden_missing_title"),
                t("eden_missing_text", exe=" / ".join(stem + extension for _, stem in BINARIES), dir=self.dir),
                "Eden binary not found", self.exe
            )
        self.variants = [self] + self._find_variants(platform)

    def _locate_data(self, platform):
        """user_dir, config and cache folders of the emulator at self.dir / self.exe."""
        if platform == "win32":
            portable = os.path.join(self.dir, "user")
            appdata = os.getenv("APPDATA") or ""
            own = os.path.join(appdata, data_folder_name(self.exe))
            global_dir = own if os.path.isfile(os.path.join(own, "config", "qt-config.ini")) \
                else os.path.join(appdata, "eden")
            self.user_dir = portable if os.path.isdir(portable) else global_dir
            self.config_dir = os.path.join(self.user_dir, "config")
            self.cache_dir = os.path.join(self.user_dir, "cache")
        else:
            # Eden checks "user" in its working directory, which it inherits
            # from the launcher
            portable = os.path.join(os.getcwd(), "user")
            if os.path.isdir(portable):
                self.user_dir = portable
                self.config_dir = os.path.join(portable, "config")
                self.cache_dir = os.path.join(portable, "cache")
            else:
                xdg_data = os.getenv("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
                xdg_config = os.getenv("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
                xdg_cache = os.getenv("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
                folder = data_folder_name(self.exe)
                if not os.path.isfile(os.path.join(xdg_config, folder, "qt-config.ini")):
                    folder = "eden"
                self.user_dir = os.path.join(xdg_data, folder)
                self.config_dir = os.path.join(xdg_config, folder)
                self.cache_dir = os.path.join(xdg_cache, folder)
        self.config_path = os.path.join(self.config_dir, "qt-config.ini")
        self.profiles_dir = os.path.join(self.config_dir, "input")

    # ------------------------------------------------------------------
    # 1b. The other emulators of the family
    # ------------------------------------------------------------------
    def _find_variants(self, platform):
        """
        Emulators besides this one: known binaries next to it (they share
        its data folder) and the [Emulators] entries of the settings (each
        with its own folder, portable or not).
        """
        if self.is_appimage or self.settings is None:
            return []
        extension = ".exe" if platform == "win32" else ""
        paths = [(None, os.path.join(self.dir, stem + extension)) for _, stem in BINARIES]
        for name, value in (self.settings.sections.get(EMULATORS_SECTION) or {}).items():
            path = os.path.expandvars(os.path.expanduser(value.strip().strip('"')))
            if not path:
                continue
            if os.path.isdir(path):
                _, found = _pick_binary([(n, os.path.join(path, stem + extension)) for n, stem in BINARIES])
                path = found if os.path.isfile(found) else (_find_fork(path, extension or ".exe") or found)
            paths.append((name, path))

        variants, seen = [], {os.path.normcase(os.path.abspath(self.exe))}
        for settings_key, path in paths:
            key = os.path.normcase(os.path.abspath(path))
            if key in seen or not os.path.isfile(path):
                continue
            seen.add(key)
            variant = Eden()
            variant.settings = self.settings
            variant.is_variant = True
            variant.settings_key = settings_key      # set for the ones listed in the settings
            variant.dir, variant.exe = os.path.dirname(os.path.abspath(path)), path
            variant.display_name = fork_name(path)
            if platform == "win32":
                variant._locate_data(platform)
            else:
                # Started with the launcher's working directory, like this one
                variant.user_dir, variant.config_dir, variant.cache_dir = \
                    self.user_dir, self.config_dir, self.cache_dir
                variant.config_path, variant.profiles_dir = self.config_path, self.profiles_dir
            variant.variants = [variant]
            variants.append(variant)
            log("INFO", "Also available", f"{variant.display_name}: {path} | {variant.config_path}")
        return variants

    def add_variant(self, path):
        """
        Add the emulator at `path` (an exe of the Eden family) to the
        settings and to self.variants.

        Returns:
            Eden | None: the emulator, None when the file is not one.
        """
        try:
            if not os.path.isfile(path) or not probe_binary(path, [FORK_MARKER]):
                return None
        except OSError:
            return None
        wanted = os.path.normcase(os.path.abspath(path))
        find = lambda: next((v for v in self.variants                           # noqa: E731
                             if os.path.normcase(os.path.abspath(v.exe)) == wanted), None)
        existing = find()
        if existing:
            return existing
        taken = self.settings.sections.get(EMULATORS_SECTION) or {}
        name, n = fork_name(path), 2
        while name.lower() in taken:
            name, n = f"{fork_name(path)}{n}", n + 1
        self.settings.set("Emulators", name, path)
        self.variants = [self] + self._find_variants(_platform())
        log("INFO", "Emulator added", path)
        return find()

    def remove_variant(self, variant):
        """Take an emulator added through the settings off the list again."""
        key = getattr(variant, "settings_key", None)
        if not key or variant not in self.variants:
            return False
        self.settings.set("Emulators", key, None)
        self.variants = [self] + self._find_variants(_platform())
        log("INFO", "Emulator removed", variant.exe)
        return True

    def emulator_for(self, game, choice=None):
        """
        The emulator that starts `game`.

        Args:
            choice (str | None): display name picked for this game by the
                                 user, if any (and still installed).

        A Switch 2 game goes to the first emulator that runs them; anything
        else to this one.
        """
        if choice:
            match = next((v for v in self.variants if v.display_name.lower() == choice.lower()), None)
            if match:
                return match
        if game and game.get("nx2"):
            return next((v for v in self.variants if v.supports_nx2()), self)
        return self

    def supports_nx2(self):
        """True for a build that boots Switch 2 games (Eglantine and what derives from it)."""
        return self._binary_fact("nx2", lambda: bool(probe_binary(self.exe, [NX2_PROBE])))

    def _binary_fact(self, name, find):
        """
        Something read from the emulator binary, found once per binary (size
        and date) and remembered in the "emulator" cache: {path: {"sig": [...],
        name: value}}.
        """
        if name in self._facts:
            return self._facts[name]
        # An AppImage is mounted at a new path every run: remember it by its file
        key = self.appimage_path or self.exe
        try:
            stat = os.stat(key)
        except OSError:
            return None
        signature = [stat.st_size, int(stat.st_mtime)]
        cache = self.settings.load_cache("emulator") if self.settings else {}
        entry = cache.get(key)
        if not isinstance(entry, dict) or entry.get("sig") != signature:
            entry = {"sig": signature}
        if name not in entry:
            try:
                entry[name] = find()
            except OSError as e:
                log("WARNING", "Could not read the emulator binary", e)
                return None
            cache[key] = entry
            if self.settings:
                self.settings.save_cache("emulator", cache)
        self._facts[name] = entry[name]
        return entry[name]

    def ensure_ready(self, main):
        """
        Make an emulator found by _find_variants() usable for a launch: the
        child environment of the main one, and its own controller profiles
        (the main one's when its config cannot be read).
        """
        if not self.is_variant or self._profiles_loaded:
            return
        self.env = main.env
        self.backend = main.backend
        try:
            ini = IniFile.load(self.config_path) if os.path.exists(self.config_path) else None
            self.profiles = Config.load_profiles(self.profiles_dir, Config.load_template(ini))
        except Exception as e:
            log("EXCEPTION", f"Could not read {self.display_name}'s configuration, using the main profiles", e)
            self.profiles = main.profiles
        self._profiles_loaded = True

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
                           applet_scheme=scheme,
                           never_confirm_stop=self._setting("confirm_close", "off").lower() == "off")

    def applet_scheme(self):
        """
        Which setting turns off the controller applet in this binary: the
        [UI] option of older Eden builds, or controller_applet_mode in newer
        ones and Eglantine. Writing the new one on an old build would be
        wrong - its AppletMode has no "Disabled" and would pick LLE instead -
        so the binary is checked for the old option's key. Read once per
        binary (size and date), then remembered.
        """
        def find():
            found = probe_binary(self.exe, [needle for _, needle in APPLET_PROBES])
            scheme = next((s for s, needle in APPLET_PROBES if needle in found), None)
            if scheme is None:
                log("WARNING", "Unknown controller applet option - using the [UI] one", self.exe)
            log("INFO", "Controller applet option", scheme or Config.APPLET_UI)
            return scheme or Config.APPLET_UI
        return self._binary_fact("applet", find) or Config.APPLET_UI

    # ------------------------------------------------------------------
    # 4b. What happened: the emulator's own log, per-game input settings
    # ------------------------------------------------------------------
    def last_errors(self, limit=3):
        """
        The last error lines of the emulator's log (eden_log.txt), newest
        last, without timestamps and source locations - what to show when a
        game closed right after starting.

        Returns:
            list[str]: at most `limit` messages ("Loader: Failed to ...").
        """
        path = os.path.join(self.log_dir(), LOG_FILE)
        try:
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - LOG_TAIL_BYTES))
                text = f.read().decode("utf-8", "replace")
        except OSError:
            return []
        return parse_log_errors(text, limit)

    def custom_input_titles(self):
        """Title IDs whose per-game configuration overrides the launcher's players."""
        return Config.custom_input_titles(self.config_dir) if self.config_dir else set()

    # ------------------------------------------------------------------
    # 6. Game picker
    # ------------------------------------------------------------------
    def game_picker_enabled(self):
        return self.settings.get_bool(SETTINGS_SECTION, "game_picker", True)

    def game_folders(self):
        """
        Where the games are looked for.

        Returns:
            (list[tuple[str, bool]], bool): (folder, deep scan) pairs, and
            True when they come from the launcher's own game_dirs setting
            rather than from the folders configured in the emulators.
        """
        folders = [(os.path.expandvars(os.path.expanduser(d)), True)
                   for d in self.settings.get_list(SETTINGS_SECTION, "game_dirs")]
        if folders:
            return folders, True
        seen = set()
        for emulator in self.variants:
            if not os.path.exists(emulator.config_path):
                continue
            try:
                found = Games.eden_game_dirs(IniFile.load(emulator.config_path).items("UI"))
            except Exception as e:
                log("EXCEPTION", f"Could not read {emulator.display_name}'s game folders", e)
                continue
            for folder, deep in found:
                key = os.path.normcase(os.path.abspath(folder))
                if key not in seen:
                    seen.add(key)
                    folders.append((folder, deep))
        return folders, False

    def set_game_folders(self, folders):
        self.settings.set(SETTINGS_SECTION, "game_dirs", ";".join(folders))

    def list_games(self):
        """Runs on a background thread (see Core/App.py)."""
        if not self.game_picker_enabled():
            return []

        folders, _ = self.game_folders()
        log("INFO", "Game folders", "; ".join(f for f, _ in folders) or "none")

        # prod.keys lets the launcher read title IDs inside NSP/XCI files
        header_key = None
        for emulator in self.variants:
            keys_file = os.path.join(emulator.user_dir, "keys", "prod.keys")
            header_key = Switch.load_header_key(keys_file)
            if header_key:
                break
        if not header_key:
            log("WARNING", "No header_key in prod.keys - updates/DLC recognised by name only",
                os.path.join(self.user_dir, "keys", "prod.keys"))
        known = self.settings.load_cache("library")
        icon_dir = os.path.join(base_dir(), self.settings.get("Launcher", "covers_dir", "covers"), "nup")
        games = Games.find_games(folders, header_key=header_key, cache_dir=self.cache_dir,
                                 titledb=TitleDb.load(), known=known, icon_dir=icon_dir)
        self.settings.save_cache("library", known)

        # Games whose own Eden configuration replaces the launcher's players
        custom = {}
        for game in games:
            emulator = self.emulator_for(game)
            if id(emulator) not in custom:
                custom[id(emulator)] = emulator.custom_input_titles()
            game["custom_input"] = (game.get("title_id") or "").upper() in custom[id(emulator)]
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
