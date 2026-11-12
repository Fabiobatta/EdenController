"""
Core/AppShared.py
Constants and small helpers shared by LauncherApp's parts (Core/App*.py).
"""


MAX_PLAYERS = 8
POLL_INTERVAL_MS = 16

# Confirmation rumble: one pulse per player number
RUMBLE_STRENGTH = 0.75
RUMBLE_PULSE_MS = 160
RUMBLE_GAP_MS = 170

# Game grid navigation: first repeat after a short hold, then continuous
NAV_REPEAT_DELAY = 0.35
NAV_REPEAT_RATE = 0.07
STICK_THRESHOLD = 0.6

# Roulette feedback on every pad
TICK_RUMBLE = (0.25, 25)
WIN_RUMBLE = (0.8, 450)

SORT_MODES = ("recent", "az", "most")
ALERT_GUARD_S = 0.6         # dialogs ignore buttons this long after they open
MIN_SESSION_S = 10          # shorter runs (crash at boot...) are not play time
SPLASH_MS = 1700            # the "let's go" screen before a game starts (its last part: the exit)
SPLASH_LAUNCH_MS = 1100     # ...when it stays until the game is on screen: the emulator starts sooner
LOADING_POLL_S = 0.25       # how often the emulator's windows are looked at while the game loads
LOADING_MAX_S = 150         # the launch screen gives way after this long, loaded or not
CLOSE_TIMEOUT_S = 5.0       # the emulator gets this long to close by itself
QUICK_EXIT_S = 20           # an emulator gone this soon after a launch...
INSTANT_EXIT_S = 5          # ...is reported if it failed, or closed sooner than this
IDLE_DIM_CHOICES = ("0", "2", "5", "10", "20")
MAX_LOOP_ERRORS_LOGGED = 5

# What the close menu reports as held (diagnostics for stuck buttons):
# (glyph, SDLManager button attribute), in display order
HELD_BUTTONS = (
    ("a", "SDL_CONTROLLER_BUTTON_A"), ("b", "SDL_CONTROLLER_BUTTON_B"),
    ("x", "SDL_CONTROLLER_BUTTON_X"), ("y", "SDL_CONTROLLER_BUTTON_Y"),
    ("lb", "SDL_CONTROLLER_BUTTON_LEFT_SHOULDER"), ("rb", "SDL_CONTROLLER_BUTTON_RIGHT_SHOULDER"),
    ("ls", "SDL_CONTROLLER_BUTTON_LEFT_STICK"), ("rs", "SDL_CONTROLLER_BUTTON_RIGHT_STICK"),
    ("back", "SDL_CONTROLLER_BUTTON_BACK"), ("start", "SDL_CONTROLLER_BUTTON_START"),
)
DPAD_BUTTONS = ("SDL_CONTROLLER_BUTTON_DPAD_UP", "SDL_CONTROLLER_BUTTON_DPAD_DOWN",
                "SDL_CONTROLLER_BUTTON_DPAD_LEFT", "SDL_CONTROLLER_BUTTON_DPAD_RIGHT")
TRIGGER_PRESSED = 0.5
HELD_POLL_S = 0.1
DIALOG_POLL_S = 0.5                # how often the emulator's windows are checked for a dialog
LOW_BATTERY = 15                   # % under which a player is told to charge their controller
BATTERY_POLL_S = 20.0              # controller batteries are read this often

# kill_combo button names -> button picture
COMBO_GLYPHS = {"select": "back", "up": "dpad", "down": "dpad", "left": "dpad", "right": "dpad"}


def match_reconnects(waiting, free_pads):
    """
    Which newly available pad takes which waiting player slot.

    A pad with the slot's old device path wins; otherwise the slot goes to a
    pad of the same model (GUID), the slot that has waited longest first.
    Identical virtual pads (Sunshine / Vibeshine) cannot be told apart, so
    for them "same model" is the best possible match.

    Args:
        waiting   (list[dict]): waiting assignments, oldest first ("path", "guid").
        free_pads (list[dict]): connected pads not used by any player ("path", "guid").

    Returns:
        list[tuple[dict, dict]]: (assignment, pad) pairs.
    """
    pairs, pads, slots = [], list(free_pads), list(waiting)
    for slot in list(slots):
        pad = next((p for p in pads if p["path"] == slot["path"]), None)
        if pad:
            pairs.append((slot, pad))
            pads.remove(pad)
            slots.remove(slot)
    for slot in slots:
        pad = next((p for p in pads if p["guid"] == slot.get("guid")), None)
        if pad:
            pairs.append((slot, pad))
            pads.remove(pad)
    return pairs


def _int(text, default):
    try:
        return int(str(text).strip())
    except ValueError:
        return default
