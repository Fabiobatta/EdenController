"""
Core/Settings.py
The launcher's own settings file, "<Name>Launcher.ini" next to the launcher.

The file is created with commented defaults the first time the launcher runs,
so users can discover every option. It is only ever read afterwards: user
edits and comments are never overwritten.

    [Launcher]              generic options, read by Core
    [<Emulator>]            emulator options, text supplied by the adapter

A small JSON state file ("<Name>Launcher.state.json") next to it remembers
things like the last game played; losing it is harmless.
"""

import json
import os
import threading

from .Log import log

LAUNCHER_SECTION = "Launcher"

# Button names accepted by kill_combo (see ControllerManagerSDL*.BUTTONS)
DEFAULT_KILL_COMBO = "back+lb+rb"

_DEFAULTS_EN = f"""\
; ============================================================================
;  Launcher settings
;  Lines starting with ';' are comments.
; ============================================================================

[{LAUNCHER_SECTION}]
; Interface language: auto (same as Windows), en, it
language = auto

; Rumble when a controller takes a player slot
; (1 pulse = Player 1, 2 pulses = Player 2, ...): true / false
rumble = true

; Interface sounds (players joining, confirmations, roulette): true / false
sounds = true
; Sound volume, 0 to 100
sound_volume = 70
; To replace a sound, put a .wav file with the same name in a "sounds"
; folder next to the launcher (join1..join8, blip1..blip8, select, back,
; move, toggle, launch, tick, win, trophy, leave, error)

; Back up a game's saves before starting it from the game grid: true / false
backup_saves = true
; Backup folder (relative to the launcher, or a full path such as a
; OneDrive folder: %USERPROFILE%\\OneDrive\\Eden saves)
backup_dir = saves_backup
; Backups kept per game (the oldest are deleted)
backup_keep = 10

; Animated background: the blurred game art drifts slowly: true / false
background_motion = true
; The selected game's border and glow take the colour of its cover
; (false = always yellow)
dynamic_colors = true

; Buttons to hold together during a game to close the emulator.
; Buttons: a b x y back start lb rb ls rs up down left right, joined by '+'
kill_combo = {DEFAULT_KILL_COMBO}

; Game grid covers: pictures in this folder (next to the launcher), named
; after the Title ID or the game name, e.g.
;   covers\\0100152000022000.png   or   covers\\Mario Kart 8 Deluxe.jpg
covers_dir = covers
; Download missing banners (backgrounds) and icons from the eShop: true / false
download_art = true
; SteamGridDB API key (free: steamgriddb.com > Preferences > API) to
; download missing covers automatically. Empty = no downloads.
steamgriddb_api_key =

; Player count shown on the covers, when the eShop's is wrong: remove
; the ';' and write Title ID or game name = players
; [Players]
; 0100A8E016236000 = 4
; Kirby's Dream Buffet = 4
"""

_DEFAULTS_IT = f"""\
; ============================================================================
;  Impostazioni del launcher
;  Le righe che iniziano con ';' sono commenti.
; ============================================================================

[{LAUNCHER_SECTION}]
; Lingua dell'interfaccia: auto (quella di Windows), it, en
language = auto

; Vibrazione di conferma quando un controller prende uno slot
; (1 impulso = Giocatore 1, 2 impulsi = Giocatore 2, ...): true / false
rumble = true

; Suoni dell'interfaccia (ingresso giocatori, conferme, roulette): true / false
sounds = true
; Volume dei suoni, da 0 a 100
sound_volume = 70
; Per cambiare un suono metti un file .wav con lo stesso nome nella cartella
; "sounds" accanto al launcher (join1..join8, blip1..blip8, select, back,
; move, toggle, launch, tick, win, trophy, leave, error)

; Backup dei salvataggi prima di avviare un gioco dalla lista: true / false
backup_saves = true
; Cartella dei backup (relativa al launcher, oppure un percorso completo,
; es. una cartella di OneDrive: %USERPROFILE%\\OneDrive\\Salvataggi Eden)
backup_dir = saves_backup
; Quanti backup tenere per ogni gioco (i piu' vecchi vengono cancellati)
backup_keep = 10

; Sfondo animato: l'immagine sfocata del gioco si muove lentamente: true / false
background_motion = true
; Il bordo e il bagliore del gioco selezionato prendono il colore della sua
; copertina (false = sempre giallo)
dynamic_colors = true

; Combinazione da tenere premuta durante il gioco per chiudere l'emulatore.
; Tasti: a b x y back start lb rb ls rs up down left right, uniti da '+'
kill_combo = {DEFAULT_KILL_COMBO}

; Copertine della lista giochi: immagini nella cartella indicata (accanto al
; launcher), chiamate con il Title ID o il nome del gioco, es.
;   covers\\0100152000022000.png   oppure   covers\\Mario Kart 8 Deluxe.jpg
covers_dir = covers
; Scarica dall'eShop i banner (sfondi) e le icone mancanti: true / false
download_art = true
; Chiave API di SteamGridDB (gratuita: steamgriddb.com > Preferences > API)
; per scaricare da sole le copertine mancanti. Vuoto = nessun download.
steamgriddb_api_key =

; Numero di giocatori mostrato sulle copertine, se quello dell'eShop e'
; sbagliato: togli il ';' e scrivi Title ID o nome del gioco = giocatori
; [Players]
; 0100A8E016236000 = 4
; Kirby's Dream Buffet = 4
"""


# The settings file is written once, in the Windows language: {"en", "it"}
LAUNCHER_DEFAULTS = {"en": _DEFAULTS_EN, "it": _DEFAULTS_IT}


def defaults_language():
    """Language of the comments in a new settings file."""
    from .I18n import detect_language
    return "it" if detect_language() == "it" else "en"


# kill_combo names -> SDLManager button attribute
BUTTON_NAMES = {
    "a": "SDL_CONTROLLER_BUTTON_A",
    "b": "SDL_CONTROLLER_BUTTON_B",
    "x": "SDL_CONTROLLER_BUTTON_X",
    "y": "SDL_CONTROLLER_BUTTON_Y",
    "back": "SDL_CONTROLLER_BUTTON_BACK",
    "select": "SDL_CONTROLLER_BUTTON_BACK",
    "start": "SDL_CONTROLLER_BUTTON_START",
    "lb": "SDL_CONTROLLER_BUTTON_LEFT_SHOULDER",
    "rb": "SDL_CONTROLLER_BUTTON_RIGHT_SHOULDER",
    "ls": "SDL_CONTROLLER_BUTTON_LEFT_STICK",
    "rs": "SDL_CONTROLLER_BUTTON_RIGHT_STICK",
    "up": "SDL_CONTROLLER_BUTTON_DPAD_UP",
    "down": "SDL_CONTROLLER_BUTTON_DPAD_DOWN",
    "left": "SDL_CONTROLLER_BUTTON_DPAD_LEFT",
    "right": "SDL_CONTROLLER_BUTTON_DPAD_RIGHT",
}


def parse_combo(text, sdl):
    """
    "back+lb+rb" -> [button constants]. Unknown names are logged and the
    default combo is used, so a typo can never leave the user without a way
    to close a frozen emulator.
    """
    names = [n.strip().lower() for n in (text or "").split("+") if n.strip()]
    unknown = [n for n in names if n not in BUTTON_NAMES]
    if not names or unknown:
        if unknown:
            log("WARNING", "Unknown kill_combo buttons, using default", ", ".join(unknown))
        names = DEFAULT_KILL_COMBO.split("+")
    return [getattr(sdl, BUTTON_NAMES[n]) for n in names]


def _parse(text):
    """
    Minimal INI parser: {section: {key: value}}; section and key names are
    case-insensitive, comments are whole lines starting with ';' or '#'.
    """
    sections = {}
    current = sections.setdefault("", {})
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in ";#":
            continue
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1].strip().lower(), {})
            continue
        if "=" not in line:
            continue
        # No trailing comments: ';' separates folder lists
        key, value = line.split("=", 1)
        current[key.strip().lower()] = value.strip().strip('"')
    return sections


class Settings:
    """Read-only view of the settings file, plus the persistent state."""

    def __init__(self, sections=None, path=None):
        self.sections = sections or {}
        self.path = path
        self.state_path = None
        self.state = {}
        self._state_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Typed getters
    # ------------------------------------------------------------------
    def get(self, section, key, default=""):
        value = self.sections.get(section.lower(), {}).get(key.lower())
        return default if value is None or value == "" else value

    def get_bool(self, section, key, default=False):
        value = self.get(section, key, None)
        if value is None:
            return default
        return value.strip().lower() in ("1", "true", "yes", "on", "si", "sì")

    def get_list(self, section, key, separator=";"):
        return [item.strip() for item in self.get(section, key, "").split(separator) if item.strip()]

    # ------------------------------------------------------------------
    # State (last game, ...) - best effort
    # ------------------------------------------------------------------
    def save_state(self, **values):
        """Merge values into the state file. Safe to call from any thread."""
        with self._state_lock:
            self.state.update(values)
            if self.state_path:
                _write_json(self.state_path, self.state)

    # ------------------------------------------------------------------
    # Caches (library scan results, ...) - disposable, best effort
    # ------------------------------------------------------------------
    def _cache_path(self, name):
        if not self.state_path:
            return None
        return self.state_path.replace(".state.json", f".{name}.json")

    def load_cache(self, name):
        path = self._cache_path(name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def save_cache(self, name, data):
        path = self._cache_path(name)
        if path:
            _write_json(path, data, indent=None)


def _write_json(path, data, indent=2):
    """Write JSON atomically; failures are logged, never raised."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
        os.replace(tmp, path)
    except Exception as e:
        log("WARNING", "Could not write", f"{path}: {e}")


def load_settings(directory, launcher_name, emulator_defaults="", language=None):
    """
    Load "<launcher_name>.ini" from `directory`, creating it with defaults if
    it does not exist. Never fails: an unreadable file means defaults.
    """
    path = os.path.join(directory, f"{launcher_name}.ini")
    language = language or defaults_language()
    if isinstance(emulator_defaults, dict):
        emulator_defaults = emulator_defaults.get(language) or emulator_defaults.get("en", "")
    defaults = LAUNCHER_DEFAULTS[language] + ("\n" + emulator_defaults if emulator_defaults else "")

    if not os.path.exists(path):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(defaults)
            log("INFO", "Settings file created", path)
        except Exception as e:
            log("WARNING", "Could not create settings file, using defaults", e)

    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            text = f.read()
        log("INFO", "Settings", path)
    except Exception:
        text = defaults

    # Options missing from an older file still get their defaults
    sections = _parse(defaults)
    for section, values in _parse(text).items():
        sections.setdefault(section, {}).update(values)

    settings = Settings(sections, path)
    settings.state_path = os.path.join(directory, f"{launcher_name}.state.json")
    try:
        with open(settings.state_path, "r", encoding="utf-8") as f:
            settings.state = json.load(f)
    except Exception:
        settings.state = {}
    return settings
