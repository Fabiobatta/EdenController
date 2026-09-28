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

from .Log import log

LAUNCHER_SECTION = "Launcher"

# Button names accepted by kill_combo (see ControllerManagerSDL*.BUTTONS)
DEFAULT_KILL_COMBO = "back+lb+rb"

LAUNCHER_DEFAULTS = f"""\
; ============================================================================
;  Impostazioni del launcher / Launcher settings
;  Righe che iniziano con ';' sono commenti. / Lines starting with ';' are comments.
; ============================================================================

[{LAUNCHER_SECTION}]
; Lingua dell'interfaccia: auto (quella di Windows), it, en
language = auto

; Vibrazione di conferma quando un controller prende uno slot
; (1 impulso = Giocatore 1, 2 impulsi = Giocatore 2, ...): true / false
rumble = true

; Combinazione da tenere premuta durante il gioco per chiudere l'emulatore.
; Tasti: a b x y back start lb rb ls rs up down left right, uniti da '+'
kill_combo = {DEFAULT_KILL_COMBO}
"""


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
        self.state.update(values)
        if not self.state_path:
            return
        try:
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log("WARNING", "Could not save launcher state", e)


def load_settings(directory, launcher_name, emulator_defaults=""):
    """
    Load "<launcher_name>.ini" from `directory`, creating it with defaults if
    it does not exist. Never fails: an unreadable file means defaults.
    """
    path = os.path.join(directory, f"{launcher_name}.ini")
    defaults = LAUNCHER_DEFAULTS + ("\n" + emulator_defaults if emulator_defaults else "")

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
