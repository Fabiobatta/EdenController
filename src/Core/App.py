"""
Core/App.py
The launcher state machine: controller detection, player assignment, profile
selection, game picker, kill combo, and the emulator process lifecycle.

Emulator-agnostic. Everything emulator-specific arrives through the Emulator
instance handed in by Core/Bootstrap.py.

Screens (self.mode):
    PLAYERS   the 8 player cards - press A to take the next player slot,
              Y to resume the last game
    GAMES     the game grid - only when the emulator offers games and no game
              was passed on the command line
    ROULETTE  "tonight we play...": a random game for everyone who joined
    TROPHIES  the achievements list (RB on the player cards)
    STATS     play statistics (X on the achievements list)
    MENU      a list menu over the screen below it: the settings (LB on the
              player cards), a game's own menu (START in the grid)
    SPLASH    the "let's go" screen between choosing a game and its start

Remembered between sessions (Settings state file): the last game, play time
and last-played date per game, favourites and the grid's sort order.

A controller that disconnects once the players are set (during a game, in
the game grid) keeps its player slot: the slot "waits" and goes back to the
same pad, or to the next pad of the same model, when one connects. Only on
the player cards does a disconnect free the slot at once.

Work kept off the 16 ms loop:
    - controllers are opened once, when they appear (sync_pads)
    - the game library is scanned on a background thread while players join
    - pictures are downloaded on a background thread (Core/Art.py)
"""

import ctypes
import os
import random
import re
import sys
import threading
import time
from tkinter import messagebox

from . import Glyphs, Library, Stats
from .Achievements import ACHIEVEMENTS, Achievements
from .Art import ArtLibrary
from .Backup import backup_saves, list_backups, restore
from .Build import BUILD
from .I18n import STRINGS, last_played_label, players_label, playtime_label, set_language, t
from .Log import log
from .Menu import Menu
from .Paths import base_dir
from .Process import answer_dialog, dialog_open, find_dialogs, launch, request_close
from .Roulette import Roulette, pick_candidates
from .Settings import DEFAULT_KILL_COMBO, LAUNCHER_SECTION, parse_combo
from .Sound import Sounds
from .Theme import ACCENT_NAMES, THEME_NAMES, apply_theme
from .Ui import COLOR, SPLASH_EXIT_MS, SPLIT_MODES, LauncherUi, player_color, split_layout, split_mode
from .Update import DOWNLOAD_PAGE, UpdateCheck

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


class LauncherApp:
    """
    Main application class for the gamepad launcher.

    Manages controller detection, assignment, and the emulator process
    lifecycle.
    """

    def __init__(self, root, emulator, sdl):
        """
        Args:
            root     (tk.Tk):    The root window.
            emulator (Emulator): Located and prepared emulator, profiles and
                                 settings loaded.
            sdl      (type):     SDLManager class from Core.Sdl.load_sdl().
        """
        self.root = root
        self.emu = emulator
        self.sdl = sdl
        settings = emulator.settings

        # Frontend arguments (Playnite, LaunchBox, Moonlight...) mean we are
        # launching a specific game rather than the emulator's own UI.
        # Without them the emulator may offer a game grid instead.
        self.has_game_args = len(sys.argv) > 1
        self.picker = not self.has_game_args and emulator.game_picker_enabled()
        self.games = []                     # every game, once scanned
        self.shown_games = []               # games on screen (after the players filter)
        self.games_loaded = not self.picker
        self.from_snapshot = False          # self.games come from the last scan, a new one is running
        self._scan_result = None            # handed over by the scan thread
        self.waiting_for_games = False      # START pressed while still scanning

        apply_theme(settings.get(LAUNCHER_SECTION, "theme", "dark").lower(),
                    settings.get(LAUNCHER_SECTION, "accent", "violet").lower())
        self.ui = LauncherUi(root, emulator.name, emulator.display_name)

        # Launcher options
        self.rumble_enabled = settings.get_bool(LAUNCHER_SECTION, "rumble", True)
        combo = settings.get(LAUNCHER_SECTION, "kill_combo", DEFAULT_KILL_COMBO)
        self.kill_buttons = parse_combo(combo, sdl)
        self.art = ArtLibrary(
            os.path.join(base_dir(), settings.get(LAUNCHER_SECTION, "covers_dir", "covers")),
            eshop=settings.get_bool(LAUNCHER_SECTION, "download_art", True),
            sgdb_key=settings.get(LAUNCHER_SECTION, "steamgriddb_api_key", ""),
            sgdb_missing=(settings.state or {}).get("covers_not_found", []))
        self._sgdb_missing_saved = set(self.art.sgdb_missing)
        self.sounds = Sounds(
            enabled=settings.get_bool(LAUNCHER_SECTION, "sounds", True),
            volume=_int(settings.get(LAUNCHER_SECTION, "sound_volume", "70"), 70),
            override_dir=os.path.join(base_dir(), "sounds"))
        self.backup_keep = _int(settings.get(LAUNCHER_SECTION, "backup_keep", "10"), 10) \
            if settings.get_bool(LAUNCHER_SECTION, "backup_saves", True) else 0
        self.backup_dir = os.path.join(base_dir(), os.path.expandvars(os.path.expanduser(
            settings.get(LAUNCHER_SECTION, "backup_dir", "saves_backup"))))
        self.ui.motion = settings.get_bool(LAUNCHER_SECTION, "background_motion", True)
        self.ui.dynamic_colors = settings.get_bool(LAUNCHER_SECTION, "dynamic_colors", True)
        self.ui.animations = settings.get_bool(LAUNCHER_SECTION, "animations", True)
        self.ui.splash_ms = SPLASH_MS
        self.idle_dim_s = max(0, settings.get_int(LAUNCHER_SECTION, "idle_dim_minutes", 5)) * 60
        self.last_input = time.monotonic()
        self.menu = Menu(self.ui, sound=lambda name: self.sounds.play(name))
        self.ui.menu = self.menu
        self._mode_before_menu = "PLAYERS"
        self._confirm = None                # what A does in a "CONFIRM" dialog
        self._alert_then = None             # what closing the "GAME_FAILED" dialog does
        self.update = UpdateCheck(settings, BUILD, settings.get_bool(LAUNCHER_SECTION, "check_updates", True))
        self._update_told = False
        self.update.start()
        self.show_hidden = False            # hidden games visible in the grid (this run only)
        self.closing = None                 # {"deadline", "then"} while the emulator is asked to close
        self.running_emu = None             # the emulator of self.process
        self.launched_at = 0.0
        self._splash_job = None
        self._exit_job = None
        self._dialog = None                 # (hwnd, title) of the emulator's dialog being asked about
        self._dialogs_seen = set()          # dialogs already asked about in this game
        self._dialog_next = 0.0
        self._mode_before_splash = "GAMES"
        self._loop_errors = 0
        self._trigger_held = {}             # {instance id: (LT, RT)} for the letter jump
        self.roulette = Roulette(self.ui, on_tick=self.on_roulette_tick, on_done=self.on_roulette_done)
        state = settings.state or {}
        self.sort_mode = state.get("sort") if state.get("sort") in SORT_MODES else SORT_MODES[0]
        self.session = None                 # {"key", "start", "game"} while a picked game runs
        self.achievements = Achievements(settings)
        self.trophy_queue = []              # unlocked, not announced yet
        self._mode_before_trophies = "PLAYERS"

        # Keyboard shortcuts for accessibility
        self.root.bind("<Return>", lambda e: self.handle_enter_key())
        self.root.bind("<Escape>", lambda e: self.handle_esc_key())
        for key, action in (("<Up>", "up"), ("<Down>", "down"), ("<Left>", "left"),
                            ("<Right>", "right"), ("<Prior>", "page_up"), ("<Next>", "page_down")):
            self.root.bind(key, lambda e, a=action: self._key_navigation(a))

        # Initialize SDL2/SDL3 controller subsystem
        self.sdl.SDL_Init()

        # State management
        self.pads = {}                      # {instance_id: {"ctrl", "path", "raw_path", "guid", "name"}}
        self._pad_order = []                # instance ids in SDL enumeration order
        self._not_gamepads = set()          # joysticks that are not gamepads (flight sticks...)
        self.assignments = []               # [{"path", "guid", "name", "profile_key", "is_editing",
                                            #   "waiting"}] - player order
        self._carried_keys = None           # path-less pad keys kept across scan_hardware()
        self._held = None                   # close menu: last held-buttons report
        self._held_next = 0.0
        self._battery_next = 0.0           # next battery reading
        self._batteries = None              # last batteries shown
        self.hid_profiles = {}              # {path: profile_key} across reconnects
        self.process = None                 # Emulator subprocess handle
        self.returning_to_launcher = False  # Flag for kill -> restart flow

        # Game grid state
        self.mode = "PLAYERS"
        self.tab = state.get("tab") if state.get("tab") in Library.TABS else "all"
        self.pinned = 0                     # games of the "Continue playing" row at the start of shown_games
        self.game_index = 0
        self.nav_direction = None           # Held navigation action ("up", "left", ...)
        self.nav_next_repeat = 0.0

        # Profile keys come from the emulator; the first one is the default
        self.profile_keys = list(self.emu.profiles.keys())
        self.default_profile = self.profile_keys[0] if self.profile_keys else ""

        self._combo = combo
        self._set_tip()
        self._set_brand()
        self.update_hints()

        if self.picker:
            self._load_snapshot()
            threading.Thread(target=self._scan_games, daemon=True).start()

        self.update_loop()

    def _set_tip(self):
        combo_markup = " + ".join(f"[{COMBO_GLYPHS.get(n, n)}]" for n in self._combo_names(self._combo))
        self.ui.set_tip(t("tip_kill", combo=combo_markup, name=self.emu.display_name))

    def _set_brand(self):
        """Footer: the build this is, and a newer one once the check found it."""
        parts = [t("brand_build", n=BUILD)] if BUILD else []
        if self.update.newer:
            parts.append(t("brand_update", n=self.update.newer))
        self.ui.set_brand("  ·  ".join(parts))

    @staticmethod
    def _combo_names(text):
        names = [n.strip().lower() for n in (text or "").split("+") if n.strip()]
        from .Settings import BUTTON_NAMES
        return names if names and all(n in BUTTON_NAMES for n in names) else DEFAULT_KILL_COMBO.split("+")

    # ========================================================================
    # KEYBOARD INPUT HANDLERS
    # ========================================================================
    def _key_navigation(self, action):
        self._woke()
        if self.ui.alert_mode or self.process or self.closing:
            return
        if self.mode == "MENU":
            self._navigate_menu(action)
        else:
            self.move_game_selection(action)

    def handle_enter_key(self):
        """Handle Enter key press (confirm action in alerts)."""
        if self._woke():
            return
        if self.ui.alert_mode == "LAUNCH":
            self.ui.close_alert()
            self.proceed()
        elif self.ui.alert_mode == "EXIT":
            self.quit()
        elif self.ui.alert_mode == "KILL_CONFIRM":
            self.kill_and_quit()
        elif self.ui.alert_mode == "CONFIRM":
            self._answer_confirm(True)
        elif self.ui.alert_mode == "EMU_DIALOG":
            self._answer_dialog(True)
        elif self.ui.alert_mode == "GAME_FAILED":
            self._close_failed_alert()
        elif self.closing or self.mode == "SPLASH":
            return
        elif self.mode == "MENU":
            self.menu.activate()
        elif self.mode in ("TROPHIES", "STATS"):
            self.close_trophies()
        elif self.mode == "ROULETTE":
            if not self.roulette.spinning:
                self.launch_game(self.roulette.chosen, from_roulette=True)
        elif self.mode == "GAMES" and not self.process:
            self.launch_selected_game()
        elif not self.process:
            self.check_launch()

    def handle_esc_key(self):
        """Handle Escape key press (cancel/back action)."""
        if self._woke():
            return
        if self.ui.alert_mode == "GAME_FAILED":
            self._close_failed_alert()
        elif self.ui.alert_mode == "EMU_DIALOG":
            self._answer_dialog(False)
        elif self.ui.alert_mode:
            self.ui.close_alert()
        elif self.mode == "SPLASH":
            self.cancel_splash()
        elif self.mode == "MENU":
            self.menu.back()
        elif self.mode in ("TROPHIES", "STATS"):
            self.close_trophies()
        elif self.mode == "ROULETTE":
            self.close_roulette()
        elif self.mode == "GAMES":
            self.show_players()
        else:
            self.ui.show_alert("EXIT")

    # ========================================================================
    # PROCESS MANAGEMENT
    # ========================================================================
    def quit(self):
        self.emu.cleanup()
        self.root.destroy()

    def _close_emulator(self, then):
        """
        Close the running emulator the gentle way - the same as its window's
        X button, so it stops the game and saves its settings - and call
        then() once it is gone. It gets CLOSE_TIMEOUT_S; an emulator that is
        frozen (or asks for confirmation) is killed after that.
        """
        self.ui.close_alert()
        if not self.process:
            then()
            return
        name = (self.running_emu or self.emu).display_name
        if request_close(self.process):
            log("INFO", f"{name} asked to close")
            self.closing = {"deadline": time.monotonic() + CLOSE_TIMEOUT_S, "then": then}
            self.ui.show_toast(t("toast_closing", name=name), COLOR["ACCENT"])
        else:
            self.process.kill()
            log("INFO", f"{name} killed (no window to close)")
            self._closed(then)

    def _poll_closing(self):
        closing = self.closing
        if self.process and self.process.poll() is None:
            if time.monotonic() < closing["deadline"]:
                return
            self.process.kill()
            log("WARNING", "The emulator did not close in time - killed")
        else:
            log("INFO", "The emulator closed by itself")
        self.closing = None
        self._closed(closing["then"])

    def _closed(self, then):
        self.process = None
        self.end_session()
        self.sounds.resume()
        then()

    def kill_and_quit(self):
        """Close the emulator and exit the launcher (close menu -> Desktop)."""
        def leave():
            self.emu.cleanup()
            self.root.quit()
            sys.exit()
        self._close_emulator(leave)

    def kill_and_restart(self):
        """Close the emulator and go back to the player cards (close menu -> Launcher)."""
        def restart():
            self.assignments = []           # fresh assignment
            self.show_players()
            self.root.deiconify()
        self._close_emulator(restart)

    def kill_and_pick(self):
        """Close the emulator and go back to the game grid, same players (close menu -> Another game)."""
        def pick():
            self.show_games()
            self.root.deiconify()
        self._close_emulator(pick)

    def _check_process(self):
        """React to the emulator exiting by itself."""
        code = self.process.poll()
        if code is None:
            return False
        emulator = self.running_emu or self.emu
        elapsed = time.monotonic() - self.launched_at
        self.process = None
        self.end_session()
        self.sounds.resume()
        self.root.deiconify()
        log("INFO", f"{emulator.display_name} exited", f"code {code} after {elapsed:.0f} s")

        # Gone right after the launch: a game that did not start. Say so,
        # with what the emulator's own log says about it.
        failed = elapsed < QUICK_EXIT_S and (code != 0 or elapsed < INSTANT_EXIT_S)
        back_to_grid = bool(self.picker and self.games)
        if failed:
            errors = emulator.last_errors()
            log("WARNING", "The game did not start", " | ".join(errors) or "no errors in the emulator's log")
            self.sounds.play("error")
            self._alert_then = None if back_to_grid else self._exit_launcher
            self.ui.show_alert(
                "GAME_FAILED", name=emulator.display_name,
                text=t("alert_failed_text", name=emulator.display_name, seconds=max(1, int(elapsed))),
                lines=errors or [t("alert_failed_no_errors", name=emulator.display_name)])
        if back_to_grid:
            # Picked from the game grid: offer it again, same players
            self.show_games()
        elif not failed:
            self._exit_launcher()           # emulator closed normally
        return False

    def _exit_launcher(self):
        self.emu.cleanup()
        self.root.quit()
        sys.exit()

    def _close_failed_alert(self):
        then, self._alert_then = self._alert_then, None
        self.ui.close_alert()
        if then:
            then()

    # ========================================================================
    # CONTROLLERS
    # ========================================================================
    def _describe(self, ctrl):
        """Name, GUID and device path of an open gamepad."""
        sdl = self.sdl
        joy = sdl.SDL_GameControllerGetJoystick(ctrl)
        psz_guid = (ctypes.c_char * 33)()
        sdl.SDL_JoystickGetGUIDString(sdl.SDL_JoystickGetGUID(joy), psz_guid, 33)
        try:
            raw = sdl.SDL_GameControllerPath(ctrl)
            raw_path = raw.decode() if raw else ""
        except Exception:
            raw_path = ""  # Unsupported platform / SDL version
        name = sdl.SDL_GameControllerName(ctrl)
        return {"ctrl": ctrl, "raw_path": raw_path, "guid": psz_guid.value.decode(),
                "name": name.decode() if name else "Controller"}

    @staticmethod
    def _assign_keys(pads, order, preferred=None):
        """
        The key identifying each pad ("path"): its device path, or - for
        pads the OS gives no path - its GUID plus a number.

        A pad keeps its key while it stays connected. A new path-less pad
        takes the lowest number free for its GUID, which is what Eden does
        with ports (SDLDriver::InitJoystick reuses the first freed slot), so
        a pad that drops and comes back lines up with the same player in
        both programs.

        Args:
            preferred (dict): {guid: [keys]} handed out first, in order, to
                              new path-less pads of that GUID - the keys the
                              pads had before an SDL re-initialisation (SDL
                              enumerates them in the same order again).
        """
        preferred = {guid: list(keys) for guid, keys in (preferred or {}).items()}
        for instance_id in order:
            pad = pads[instance_id]
            if pad.get("path"):
                continue
            if pad["raw_path"]:
                pad["path"] = pad["raw_path"]
                continue
            used = {p.get("path") for p in pads.values() if p["guid"] == pad["guid"]}
            queue = preferred.get(pad["guid"], [])
            while queue and queue[0] in used:
                queue.pop(0)
            if queue:
                pad["path"] = queue.pop(0)
                continue
            n = 0
            while f"UNK_{pad['guid']}_{n}" in used:
                n += 1
            pad["path"] = f"UNK_{pad['guid']}_{n}"

    def sync_pads(self):
        """
        Open controllers that just appeared and forget those that left.
        Cheap when nothing changed (one ID list from SDL).

        Returns:
            bool: True if the set of controllers changed.
        """
        sdl = self.sdl
        devices = sdl.SDL_GetJoystickInstanceIDs()      # [(open id, instance id)]
        order = [instance_id for _, instance_id in devices if instance_id not in self._not_gamepads]
        if order == self._pad_order:
            return False

        for instance_id in [i for i in self.pads if i not in order]:
            pad = self.pads.pop(instance_id)
            try:
                sdl.SDL_GameControllerClose(pad["ctrl"])
            except Exception:
                pass

        for open_id, instance_id in devices:
            if instance_id in self.pads or instance_id in self._not_gamepads:
                continue
            if not sdl.SDL_IsGameController(open_id):
                self._not_gamepads.add(instance_id)     # e.g. flight sticks
                continue
            ctrl = sdl.SDL_GameControllerOpen(open_id)
            if ctrl:
                self.pads[instance_id] = self._describe(ctrl)
                log("INFO", "Controller connected", self.pads[instance_id]["name"])

        self._pad_order = [i for i in order if i in self.pads]
        self._assign_keys(self.pads, self._pad_order, self._carried_keys)
        self._carried_keys = None
        return True

    def on_pads_changed(self):
        """
        Update the counter, give waiting slots back to returning controllers,
        and free or hold the slots of controllers that disconnected.
        """
        self.ui.set_pads(len(self.pads))
        self._restore_waiting()
        connected = {pad["path"] for pad in self.pads.values()}
        dropped = [a for a in self.assignments if not a.get("waiting") and a["path"] not in connected]
        if not dropped:
            return
        if self.process or self.mode != "PLAYERS":
            # Players are set: keep the slot for when the controller comes back
            for assignment in dropped:
                assignment["waiting"] = True
                assignment["waiting_since"] = time.monotonic()
                player = self.assignments.index(assignment) + 1
                log("INFO", f"Controller disconnected - Player {player} waits for it", assignment["name"])
            first = dropped[0]
            self.ui.show_toast(t("toast_waiting", n=self.assignments.index(first) + 1),
                               self.get_assigned_color(first["path"]))
            return
        dropped_color = self.get_assigned_color(dropped[0]["path"])
        self.assignments = [a for a in self.assignments if a["path"] in connected]
        self.ui.show_toast(t("toast_disconnected", name=dropped[0]["name"]), dropped_color)
        for assignment in dropped:
            log("INFO", "Controller disconnected", assignment["name"])
            self._forget_controller(assignment["path"])
        self.refresh_grid()
        self.update_hints()

    def _forget_controller(self, path):
        self.hid_profiles.pop(path, None)       # Clear profile on hardware disconnect

    def _restore_waiting(self):
        """Hand waiting slots to controllers that (re)connected."""
        waiting = sorted((a for a in self.assignments if a.get("waiting")), key=lambda a: a["waiting_since"])
        if not waiting:
            return
        used = {a["path"] for a in self.assignments if not a.get("waiting")}
        free = [{**pad, "id": instance_id} for instance_id, pad in self.pads.items() if pad["path"] not in used]
        for assignment, pad in match_reconnects(waiting, free):
            old = assignment["path"]
            if old != pad["path"]:
                # The chosen layout follows the player, not the device path
                if old in self.hid_profiles:
                    self.hid_profiles[pad["path"]] = self.hid_profiles.pop(old)
            assignment.update(path=pad["path"], guid=pad["guid"], name=pad["name"], waiting=False)
            player = self.assignments.index(assignment) + 1
            log("INFO", f"Controller reconnected -> Player {player}", pad["name"])
            self.ui.show_toast(t("toast_reconnected", n=player), self.get_assigned_color(pad["path"]))
            if not self.process:
                self.confirm_player_number(pad["id"], player)

    def drop_waiting(self):
        """Back on the player cards: slots still waiting are freed."""
        gone = [a for a in self.assignments if a.get("waiting")]
        if not gone:
            return
        self.assignments = [a for a in self.assignments if not a.get("waiting")]
        for assignment in gone:
            log("INFO", "Waiting slot freed", assignment["name"])
            self._forget_controller(assignment["path"])
        self.ui.show_toast(t("toast_disconnected", name=gone[0]["name"]))

    def held_buttons(self, ctrl):
        """Glyph names of everything held on a pad right now (stuck-button check)."""
        sdl = self.sdl
        pressed = lambda attr: sdl.SDL_GameControllerGetButton(ctrl, getattr(sdl, attr))  # noqa: E731
        try:
            left, right = sdl.get_triggers(ctrl)
            held = []
            for name, attr in HELD_BUTTONS:
                if pressed(attr):
                    held.append(name)
                if name == "rb":            # triggers right after the bumpers
                    held += [n for n, value in (("lt", left), ("rt", right)) if value > TRIGGER_PRESSED]
            if any(pressed(attr) for attr in DPAD_BUTTONS):
                held.append("dpad")
        except Exception:
            return []
        return held

    def report_held_buttons(self):
        """
        While the close menu is open: show (and log) what every player's
        controller holds. With hands off the controllers, anything listed
        is stuck before Eden (client, streaming host or driver).
        """
        now = time.monotonic()
        if now < self._held_next:
            return
        self._held_next = now + HELD_POLL_S
        pads = {pad["path"]: pad for pad in self.pads.values()}
        rows = []
        for i, assignment in enumerate(self.assignments):
            pad = None if assignment.get("waiting") else pads.get(assignment["path"])
            rows.append((f"P{i + 1}", self.get_assigned_color(assignment["path"]),
                         None if pad is None else tuple(self.held_buttons(pad["ctrl"]))))
        if rows != self._held:
            self._held = rows
            self.ui.set_alert_detail(rows)
            log("INFO", "Close menu - held buttons", " | ".join(
                f"{label} {' '.join(h).upper() or '-'}" if h is not None else f"{label} disconnected"
                for label, _, h in rows))

    def kill_combo_pressed(self):
        sdl = self.sdl
        return any(all(sdl.SDL_GameControllerGetButton(pad["ctrl"], b) for b in self.kill_buttons)
                   for pad in self.pads.values())

    # ========================================================================
    # MAIN EVENT LOOP
    # ========================================================================
    def update_loop(self):
        """
        Runs every 16 ms. An error in one pass is logged and the loop goes
        on: a launcher that stops reading the controllers cannot even be
        closed with one.
        """
        try:
            self._tick()
        except SystemExit:
            raise
        except Exception as e:
            self._loop_errors += 1
            if self._loop_errors <= MAX_LOOP_ERRORS_LOGGED:
                log("EXCEPTION", "Error in the main loop", e)
        try:
            self.root.after(POLL_INTERVAL_MS, self.update_loop)
        except Exception:
            pass                                # the window is gone: quitting

    def _tick(self):
        """
        One pass of the main loop:
        - emulator process monitoring and the kill combo
        - controller hot-plug
        - game scan results and downloaded pictures
        - menu / game grid navigation
        - gamepad button events
        """
        sdl = self.sdl
        now = time.monotonic()

        if self._scan_result is not None:
            games, self._scan_result = self._scan_result, None
            self.on_games_scanned(games)

        # ====================================================================
        # EMULATOR PROCESS MONITORING / KILL COMBO (ANY CONTROLLER)
        # ====================================================================
        if self.closing:
            self._poll_closing()
        elif self.process and not self.ui.alert_mode:
            self._check_process()
            if self.process and now >= self._dialog_next:
                self._dialog_next = now + DIALOG_POLL_S
                self._watch_dialogs()
            if self.process and not self.ui.alert_mode and self.kill_combo_pressed():
                self.root.deiconify()  # Bring launcher to foreground
                log("INFO", "Kill combo detected - showing menu")
                self._held, self._held_next = None, 0.0
                buttons = [("a", "alert_kill_launcher")]
                if self.picker and self.games:
                    buttons.append(("x", "alert_kill_games"))
                buttons += [("y", "alert_kill_desktop"), ("b", "alert_kill_cancel")]
                self.ui.show_alert("KILL_CONFIRM", buttons=tuple(buttons),
                                   name=(self.running_emu or self.emu).display_name)

        # ====================================================================
        # HOT-PLUG
        # ====================================================================
        if self.sync_pads():
            self.on_pads_changed()
        if self.ui.alert_mode == "KILL_CONFIRM":
            self.report_held_buttons()
        if self.ui.alert_mode == "EMU_DIALOG" and self._dialog and not dialog_open(self._dialog[0]):
            log("INFO", "The emulator's dialog was closed outside the launcher", self._dialog[1])
            self._dialog = None
            self.ui.close_alert()
            self.root.withdraw()
        if now >= self._battery_next and not self.process:
            self._battery_next = now + BATTERY_POLL_S
            self.poll_batteries()

        # ====================================================================
        # BACKGROUND WORK RESULTS
        # ====================================================================
        for game in self.art.take_finished():
            self.ui.games_art_changed(game)
        idle = not self.process and not self.ui.alert_mode and not self.closing
        if self.trophy_queue and idle and not self.ui.trophy_showing and self.mode != "SPLASH":
            self.announce_trophy(self.trophy_queue.pop(0))
        if self.art.sgdb_missing != self._sgdb_missing_saved:
            self._sgdb_missing_saved = set(self.art.sgdb_missing)
            self.emu.settings.save_state(covers_not_found=sorted(self._sgdb_missing_saved))
        if self.update.newer and not self._update_told and idle and self.mode == "PLAYERS":
            self._update_told = True
            self._set_brand()
            self.ui.show_toast(t("toast_update", n=self.update.newer), COLOR["ACCENT"])

        if idle and self.mode in ("GAMES", "MENU"):
            self.poll_navigation()

        # Nobody touched anything for a while: dim the screen (not during a game)
        if self.idle_dim_s and idle and now - self.last_input > self.idle_dim_s:
            self.ui.set_dim(True)

        # ====================================================================
        # GAMEPAD BUTTON EVENT PROCESSING
        # ====================================================================
        event = sdl.SDL_Event()
        while sdl.SDL_PollEvent(ctypes.byref(event)) != 0:
            if event.type == sdl.SDL_CONTROLLERBUTTONDOWN:
                button, which = sdl.get_button_info(event)
                if self._woke():
                    continue                    # that press only lit the screen again
                if self.ui.alert_mode:
                    self.on_alert_button(button)
                elif self.process or self.closing:
                    continue    # Ignore input while a game runs (no mid-game reassignment)
                elif self.mode == "SPLASH":
                    if button == sdl.SDL_CONTROLLER_BUTTON_B:
                        self.cancel_splash()
                elif self.mode == "MENU":
                    self.on_menu_button(button)
                elif self.mode == "ROULETTE":
                    self.on_roulette_button(button)
                elif self.mode in ("TROPHIES", "STATS"):
                    if button in (sdl.SDL_CONTROLLER_BUTTON_B, sdl.SDL_CONTROLLER_BUTTON_BACK,
                                  sdl.SDL_CONTROLLER_BUTTON_RIGHT_SHOULDER):
                        self.close_trophies()
                    elif button == sdl.SDL_CONTROLLER_BUTTON_X:
                        self.toggle_stats()
                elif self.mode == "GAMES":
                    self.on_games_button(button)
                else:
                    self.on_players_button(button, which)

            elif event.type == sdl.SDL_CONTROLLERAXISMOTION:
                direction, which = sdl.get_axis_motion_info(event)
                if which is None:
                    continue
                if direction != 0 and self._woke():
                    continue
                if self.mode != "PLAYERS" or self.process or self.ui.alert_mode:
                    continue
                if direction != 0 and not sdl.axis_engaged.get(which, False):
                    sdl.axis_engaged[which] = True
                    self.cycle_profile(which, direction)
                elif direction == 0:
                    sdl.axis_engaged[which] = False  # reset when stick returns to center

            elif event.type == sdl.SDL_QUIT:
                self.quit()
                return

    def _woke(self):
        """Note an input; True when it only woke the dimmed screen (and must do nothing else)."""
        self.last_input = time.monotonic()
        self.ui.hold_motion()                   # the backdrop never drifts while someone is using the launcher
        if self.ui.dimmed:
            self.ui.set_dim(False)
            return True
        return False

    def on_alert_button(self, button):
        sdl = self.sdl
        mode = self.ui.alert_mode
        if time.monotonic() - self.ui.alert_since < ALERT_GUARD_S:
            return      # a press meant for the screen underneath (e.g. A to join)
        if mode == "KILL_CONFIRM":
            if button == sdl.SDL_CONTROLLER_BUTTON_A:
                self.kill_and_restart()             # Return to the player cards
            elif button == sdl.SDL_CONTROLLER_BUTTON_X and self.picker and self.games:
                self.kill_and_pick()                # Another game, same players
            elif button == sdl.SDL_CONTROLLER_BUTTON_Y:
                self.kill_and_quit()                # Exit to desktop
            elif button == sdl.SDL_CONTROLLER_BUTTON_B:
                self.ui.close_alert()
                self.root.withdraw()                # Cancel, resume game
        elif mode == "EMU_DIALOG":
            if button in (sdl.SDL_CONTROLLER_BUTTON_A, sdl.SDL_CONTROLLER_BUTTON_B):
                self._answer_dialog(button == sdl.SDL_CONTROLLER_BUTTON_A)
        elif mode == "GAME_FAILED":
            if button in (sdl.SDL_CONTROLLER_BUTTON_A, sdl.SDL_CONTROLLER_BUTTON_B):
                self.sounds.play("back")
                self._close_failed_alert()
        elif mode == "CONFIRM":
            if button == sdl.SDL_CONTROLLER_BUTTON_A:
                self._answer_confirm(True)
            elif button == sdl.SDL_CONTROLLER_BUTTON_B:
                self._answer_confirm(False)
        elif button == sdl.SDL_CONTROLLER_BUTTON_A:
            if mode == "LAUNCH":
                self.ui.close_alert()
                self.proceed()
            elif mode == "EXIT":
                self.quit()
        elif button == sdl.SDL_CONTROLLER_BUTTON_B:
            self.ui.close_alert()
            self.sounds.play("back")

    def confirm(self, title, text, then, buttons=None):
        """Ask a yes / no question; then() runs on yes (A). buttons: other labels for A and B."""
        self._confirm = then
        self.ui.show_alert("CONFIRM", title=title, text=text, buttons=buttons)

    # ========================================================================
    # THE EMULATOR'S DIALOGS
    # ========================================================================
    def _watch_dialogs(self):
        """
        A dialog of the emulator (Eden's "Game Requires Firmware", an error)
        waits for a mouse click: show it over the launcher and let the
        players answer it with A (OK) or B (Cancel).
        """
        emulator = self.running_emu or self.emu
        dialogs = [d for d in find_dialogs(self.process, emulator.window_names()) if d[0] not in self._dialogs_seen]
        if not dialogs:
            return
        hwnd, title = dialogs[0]
        self._dialogs_seen.add(hwnd)
        self._dialog = (hwnd, title)
        log("INFO", f"{emulator.display_name} opened a dialog", title)
        self.root.deiconify()
        self.ui.show_alert("EMU_DIALOG", name=emulator.display_name, lines=[f"«{title}»"])

    def _answer_dialog(self, accept):
        dialog, self._dialog = self._dialog, None
        self.ui.close_alert()
        if self.process:
            self.root.withdraw()
        if dialog:
            answer_dialog(dialog[0], accept)
            log("INFO", "Dialog answered", f"{'OK' if accept else 'Cancel'}: {dialog[1]}")

    def _answer_confirm(self, yes):
        then, self._confirm = self._confirm, None
        self.ui.close_alert()
        self.sounds.play("select" if yes else "back")
        if yes and then:
            then()

    def on_menu_button(self, button):
        """A / B in a menu; the directions are polled (poll_navigation) so that holding repeats."""
        sdl = self.sdl
        if button == sdl.SDL_CONTROLLER_BUTTON_A:
            self.menu.activate()
        elif button in (sdl.SDL_CONTROLLER_BUTTON_B, sdl.SDL_CONTROLLER_BUTTON_START):
            self.menu.back()
        self._after_menu()

    def _after_menu(self):
        """The last menu was closed: back to the screen it was opened from."""
        if self.mode == "MENU" and not self.menu.active:
            self.mode = self._mode_before_menu
            self.nav_direction = "held"             # the press that closed it must not move the grid
            self.nav_next_repeat = float("inf")
            self.update_hints()

    def on_games_button(self, button):
        sdl = self.sdl
        if button == sdl.SDL_CONTROLLER_BUTTON_A:
            self.launch_selected_game()
        elif button == sdl.SDL_CONTROLLER_BUTTON_B:
            self.sounds.play("back")
            self.show_players()
        elif button == sdl.SDL_CONTROLLER_BUTTON_X:
            self.start_roulette()
        elif button == sdl.SDL_CONTROLLER_BUTTON_Y:
            self.toggle_filter()
        elif button == sdl.SDL_CONTROLLER_BUTTON_START:
            self.open_game_menu()
        elif button == sdl.SDL_CONTROLLER_BUTTON_BACK:
            self.toggle_favorite()
        elif button == sdl.SDL_CONTROLLER_BUTTON_LEFT_SHOULDER:
            self.switch_tab(-1)
        elif button == sdl.SDL_CONTROLLER_BUTTON_RIGHT_SHOULDER:
            self.switch_tab(1)
        # D-pad, stick and the triggers are polled in poll_navigation()

    def on_roulette_button(self, button):
        sdl = self.sdl
        if self.roulette.spinning:
            if button == sdl.SDL_CONTROLLER_BUTTON_B:
                self.close_roulette()
            return
        if button == sdl.SDL_CONTROLLER_BUTTON_A:
            self.launch_game(self.roulette.chosen, from_roulette=True)
        elif button == sdl.SDL_CONTROLLER_BUTTON_X:
            self.start_roulette(exclude=self.roulette.chosen)
        elif button == sdl.SDL_CONTROLLER_BUTTON_B:
            self.close_roulette()

    def on_players_button(self, button, which):
        sdl = self.sdl
        slot_idx = self.find_slot_by_instance(which)
        editing = slot_idx != -1 and self.assignments[slot_idx]["is_editing"]
        if slot_idx != -1 and button not in (sdl.SDL_CONTROLLER_BUTTON_A, sdl.SDL_CONTROLLER_BUTTON_B):
            self.ui.flash_slot(slot_idx, 350)          # "which card is mine?"
        if button == sdl.SDL_CONTROLLER_BUTTON_A:
            if editing:
                self.toggle_profile_edit(which)     # Confirm profile
            else:
                self.assign_player(which)
        elif button == sdl.SDL_CONTROLLER_BUTTON_B:
            if editing:
                self.assignments[slot_idx]["is_editing"] = False
                self.sounds.play("back")
                self.refresh_grid()
            else:
                self.remove_player(which)
        elif button == sdl.SDL_CONTROLLER_BUTTON_Y:
            self.resume_last_game(which)
        elif button == sdl.SDL_CONTROLLER_BUTTON_RIGHT_SHOULDER:
            self.show_trophies()
        elif button == sdl.SDL_CONTROLLER_BUTTON_LEFT_SHOULDER:
            self.open_settings()
        elif button == sdl.SDL_CONTROLLER_BUTTON_X:
            self.toggle_profile_edit(which)         # Enter/exit profile selection
        elif button in (sdl.SDL_CONTROLLER_BUTTON_DPAD_LEFT, sdl.SDL_CONTROLLER_BUTTON_DPAD_RIGHT):
            direction = -1 if button == sdl.SDL_CONTROLLER_BUTTON_DPAD_LEFT else 1
            if editing:
                self.cycle_profile(which, direction)
            else:
                self.cycle_avatar(which, direction)
        elif button == sdl.SDL_CONTROLLER_BUTTON_START:
            self.check_launch()                     # Launch / choose a game
        elif button == sdl.SDL_CONTROLLER_BUTTON_BACK:
            self.sounds.play("toggle")
            self.ui.show_alert("EXIT")

    # ========================================================================
    # CONTROLLER ASSIGNMENT LOGIC
    # ========================================================================
    def assign_player(self, instance_id):
        """Give the controller the next free player slot."""
        pad = self.pads.get(instance_id)
        if not pad or len(self.assignments) >= MAX_PLAYERS:
            return
        for i, assignment in enumerate(self.assignments):
            if assignment["path"] == pad["path"]:
                # Already playing: show which card and number it is instead
                self.ui.flash_slot(i)
                self.confirm_player_number(instance_id, i + 1)
                return

        # Restore previously selected profile for this controller, default to the first one
        profile_key = self.hid_profiles.get(pad["path"], self.default_profile)
        self.assignments.append({
            "path": pad["path"],
            "guid": pad["guid"],
            "name": pad["name"],
            "profile_key": profile_key,
            "is_editing": False,
        })
        player = len(self.assignments)
        log("INFO", f"Assigned {pad['name']} -> Player {player} | Profile: {profile_key}")
        self.refresh_grid()
        self.ui.flash_slot(player - 1, pop=True)
        self.ui.hop(player - 1, delay_ms=160)
        self.update_hints()
        self.confirm_player_number(instance_id, player)

    def confirm_player_number(self, instance_id, count):
        """
        Pulse the controller once per player number (P1 = 1 pulse, P2 = 2 ...),
        so whoever holds it knows their slot without looking at the screen.
        Each pulse has a rising blip; the last one a small chime.
        """
        def pulse(n):
            if self.process:
                return
            self.sounds.play("join" if n == count else "blip", n)
            # Looked up at pulse time: a launch may have closed the handle since
            pad = self.pads.get(instance_id)
            if pad and self.rumble_enabled:
                self.sdl.rumble(pad["ctrl"], RUMBLE_STRENGTH, RUMBLE_PULSE_MS)

        period = RUMBLE_PULSE_MS + RUMBLE_GAP_MS
        for n in range(count):
            self.root.after(n * period, pulse, n + 1)

    def rumble_all(self, strength, duration_ms):
        if not self.rumble_enabled:
            return
        for pad in self.pads.values():
            try:
                self.sdl.rumble(pad["ctrl"], strength, duration_ms)
            except Exception:
                pass

    def remove_player(self, instance_id):
        slot_idx = self.find_slot_by_instance(instance_id)
        if slot_idx != -1:
            removed = self.assignments.pop(slot_idx)
            log("INFO", f"Removed {removed['name']} from Player {slot_idx + 1}")
            self.sounds.play("leave")
            self.refresh_grid()
            self.update_hints()

    # ========================================================================
    # PROFILE SELECTION LOGIC
    # ========================================================================
    def find_slot_by_instance(self, instance_id):
        """Index into self.assignments for a controller, or -1."""
        pad = self.pads.get(instance_id)
        if not pad:
            return -1
        for i, assignment in enumerate(self.assignments):
            if assignment["path"] == pad["path"]:
                return i
        return -1

    def toggle_profile_edit(self, instance_id):
        """
        X on an assigned slot enters profile selection; A (or X) confirms.
        """
        slot_idx = self.find_slot_by_instance(instance_id)
        if slot_idx == -1:
            return
        assignment = self.assignments[slot_idx]
        assignment["is_editing"] = not assignment["is_editing"]
        self.sounds.play("toggle" if assignment["is_editing"] else "select")
        if not assignment["is_editing"]:
            log("INFO", f"Player {slot_idx + 1} profile confirmed -> {assignment['profile_key']}")
        self.refresh_grid()

    def cycle_profile(self, instance_id, direction):
        """D-pad / stick left-right while a slot is in profile selection."""
        slot_idx = self.find_slot_by_instance(instance_id)
        if slot_idx == -1 or not self.profile_keys:
            return
        assignment = self.assignments[slot_idx]
        if not assignment["is_editing"]:
            return
        current = self.profile_keys.index(assignment["profile_key"])
        assignment["profile_key"] = self.profile_keys[(current + direction) % len(self.profile_keys)]
        self.sounds.play("move")
        self.hid_profiles[assignment["path"]] = assignment["profile_key"]
        self.refresh_grid()

    # ========================================================================
    # UI UPDATE METHODS
    # ========================================================================
    def avatar_of(self, index):
        """Player index + 1's avatar (Glyphs.AVATARS name): the one they picked, else one per number."""
        chosen = (self.emu.settings.state or {}).get("avatars") or []
        name = chosen[index] if index < len(chosen) else None
        return name if name in Glyphs.AVATARS else Glyphs.AVATARS[index % len(Glyphs.AVATARS)]

    def cycle_avatar(self, instance_id, direction):
        """D-pad left / right on the player screen: the next avatar for that player."""
        slot_idx = self.find_slot_by_instance(instance_id)
        if slot_idx == -1:
            return
        names = Glyphs.AVATARS
        chosen = [self.avatar_of(i) for i in range(max(slot_idx + 1, len(self.assignments)))]
        taken = {name for i, name in enumerate(chosen) if i != slot_idx and i < len(self.assignments)}
        index = names.index(chosen[slot_idx])
        for _ in range(len(names)):                 # skip the ones other players have
            index = (index + direction) % len(names)
            if names[index] not in taken:
                break
        chosen[slot_idx] = names[index]
        self.emu.settings.save_state(avatars=chosen)
        self.sounds.play("move")
        self.refresh_grid()
        self.ui.hop(slot_idx)

    def poll_batteries(self):
        """Read the battery of every connected controller (None: wired or unknown)."""
        reader = getattr(self.sdl, "battery", None)
        if reader is None:
            return
        changed = False
        for pad in self.pads.values():
            try:
                level = reader(pad["ctrl"])
            except Exception:
                level = None
            changed |= level != pad.get("battery")
            pad["battery"] = level
        if changed:
            self.refresh_grid()

    def _battery_of(self, path):
        pad = next((p for p in self.pads.values() if p["path"] == path), None)
        return pad.get("battery") if pad else None

    def _show_batteries(self):
        """The batteries next to the clock: one per player whose controller reports one."""
        shown = [(player_color(i), self.avatar_of(i), self._battery_of(a["path"]))
                 for i, a in enumerate(self.assignments) if not a.get("waiting")]
        shown = [entry for entry in shown if entry[2]]
        if shown != self._batteries:
            self._batteries = shown
            self.ui.set_batteries(shown)

    def get_assigned_color(self, path):
        """The colour of the player this controller is: one per player number."""
        index = next((i for i, a in enumerate(self.assignments) if a["path"] == path), None)
        return COLOR["NEON_RED"] if index is None else player_color(index)

    def refresh_grid(self):
        """Build the view models for the player cards and hand them to the UI."""
        self.ui.refresh([{
            # Remove trailing index suffix, e.g. "Pro Controller (2)"
            "name": re.sub(r'\s*\(\d+\)$', '', a["name"]),
            "color": self.get_assigned_color(a["path"]),
            "profile": a["profile_key"],
            "editing": a["is_editing"],
            "avatar": self.avatar_of(i),
            "battery": self._battery_of(a["path"]),
        } for i, a in enumerate(self.assignments)])
        self._show_batteries()

    def update_hints(self):
        """The hint dock, the shortcuts next to the clock and the "continue" card for the current screen."""
        quick, resume = [], None
        if self.mode in ("ROULETTE", "TROPHIES", "STATS", "MENU", "SPLASH"):
            self.ui.set_hints([])               # they draw their own, over the screen below
            return
        elif self.mode == "GAMES":
            hints = [("a", t("hint_play")), ("x", t("hint_surprise")), ("start", t("hint_menu")),
                     ("back", t("hint_favorite")), (("lt", "rt"), t("hint_letter")), ("b", t("hint_back"))]
        else:
            if self.has_game_args:
                start = t("hint_launch_game")
            elif self.picker:
                start = t("hint_choose")
            else:
                start = t("hint_launch", name=self.emu.display_name)
            hints = [("a", t("hint_join"))]
            if self.assignments:
                hints += [("x", t("hint_layout")), ("b", t("hint_leave"))]
            hints += [("start", start), ("back", t("hint_quit"))]
            quick = [("lb", t("hint_settings")), ("rb", t("hint_trophies"))]
            resume = self._last_game() if self.picker else None
        self.ui.set_quick(quick)
        self.ui.set_resume(resume)
        self.ui.set_hints(hints)

    # ========================================================================
    # GAME LIBRARY
    # ========================================================================
    def _scan_games(self):
        """Background thread: list the games and find their pictures on disk."""
        try:
            games = self.emu.list_games()
            # [Players] in the settings file corrects the eShop player counts
            overrides = self.emu.settings.sections.get("players", {})
            for game in games:
                self.art.resolve(game)
                value = overrides.get((game.get("title_id") or "").lower()) or overrides.get(game["title"].lower())
                if value and value.strip().isdigit():
                    game["players"] = int(value)
        except Exception as e:
            log("EXCEPTION", "Game scan failed", e)
            games = []
        self._scan_result = games               # picked up by update_loop

    def rescan_games(self):
        """The game folders changed: list the games again in the background."""
        self.picker = not self.has_game_args and self.emu.game_picker_enabled()
        if not self.picker:
            return
        self.games_loaded = False
        self.shown_games = []
        threading.Thread(target=self._scan_games, daemon=True).start()

    def _load_snapshot(self):
        """
        The games of the last scan, shown at once while the folders are
        scanned again (on_games_scanned then brings in what changed).
        """
        try:
            games = Library.from_snapshot(self.emu.settings.load_cache("gamelist"))
            overrides = self.emu.settings.sections.get("players", {})
            for game in games:
                self.art.resolve(game)
                value = overrides.get((game.get("title_id") or "").lower()) or overrides.get(game["title"].lower())
                if value and value.strip().isdigit():
                    game["players"] = int(value)
        except Exception as e:
            log("EXCEPTION", "Could not read the saved game list", e)
            games = []
        if not games:
            return
        log("INFO", "Game list from the last scan", f"{len(games)} game(s)")
        self.games = games
        self.games_loaded = True
        self.from_snapshot = True
        self.order_games()
        last = self._last_game()
        if last:
            self.game_index = self.games.index(last)
            self.ui.set_backdrop(last)

    def on_games_scanned(self, games):
        log("INFO", "Game picker", f"{len(games)} game(s)")
        if self.from_snapshot:
            self.from_snapshot = False
            if self._merge_scan(games):
                return
        self.games = games
        self.games_loaded = True
        if not games:
            self.picker = False                 # nothing to pick: START starts the emulator
        else:
            self.emu.settings.save_cache("gamelist", Library.snapshot(games))
            self.order_games()
            self.art.start(self.games)          # fetch missing pictures in the background
            last = self._last_game()
            if last:
                self.game_index = self.games.index(last)
                if self.mode == "PLAYERS":
                    self.ui.set_backdrop(last)
        self.update_hints()
        if self.waiting_for_games:
            self.waiting_for_games = False
            if games:
                self.show_games()
            else:
                self.force_launch()

    def _merge_scan(self, games):
        """
        The scan finished while the grid shows the games of the last one.
        The same games (the usual case): their details are updated in place,
        the grid keeps its selection and does not move. Otherwise the new
        list replaces the old one, keeping the selected game if it is there.

        Returns:
            bool: True when everything was handled here.
        """
        if not games:
            return False
        self.emu.settings.save_cache("gamelist", Library.snapshot(games))
        old = {game["path"]: game for game in self.games}
        if set(old) == {game["path"] for game in games}:
            for game in games:
                current = old[game["path"]]
                fresh = {key: game.get(key) for key in Library.SNAPSHOT_KEYS + ("cover", "background")}
                if any(current.get(key) != value for key, value in fresh.items()):
                    current.update(fresh)
                    self.ui.grid.forget(current)
            changed = True
        else:
            selected = self.shown_games[self.game_index]["path"] if self.shown_games else None
            self.games = games
            self.order_games()
            self.shown_games = []
            changed = True
            if selected:
                self.game_index = next((i for i, g in enumerate(self.games) if g["path"] == selected), 0)
        self.art.start(self.games)
        self.update_hints()
        if changed and self.mode == "GAMES" and not self.menu.active:
            self._apply_filter()
            self._show_grid()
        return True

    def _last_game(self):
        last = (self.emu.settings.state or {}).get("last_game")
        return next((g for g in self.games if g["path"] == last), None)

    # ========================================================================
    # PLAY TIME, FAVOURITES, SORT ORDER
    # ========================================================================
    @staticmethod
    def game_key(game):
        """Identity of a game in the state file: its title ID, else its path."""
        return game.get("title_id") or game["path"]

    def order_games(self):
        """
        Copy play time / favourite flags from the state onto the games and
        sort them: favourites first, then by the chosen order.
        """
        state = self.emu.settings.state or {}
        played = state.get("play") or {}
        favorites = set(state.get("favorites") or ())
        hidden = set(state.get("hidden") or ())
        several = len(getattr(self.emu, "variants", ())) > 1
        for game in self.games:
            key = self.game_key(game)
            seconds, last = (list(played.get(key) or []) + [0, 0])[:2]
            game["playtime"], game["last_played"] = seconds, last
            game["favorite"] = key in favorites
            game["hidden"] = key in hidden
            # With more than one emulator, the grid says which one starts the game
            game["emulator_name"] = self.emulator_for(game).display_name if several else None
        by_title = lambda g: g["title"].lower()     # noqa: E731
        orders = {
            "recent": lambda g: (not g["favorite"], -(g["last_played"] or 0), by_title(g)),
            "az": lambda g: (not g["favorite"], by_title(g)),
            "most": lambda g: (not g["favorite"], -(g["playtime"] or 0), by_title(g)),
        }
        self.games = sorted(self.games, key=orders[self.sort_mode])

    def emulator_for(self, game):
        """The emulator that starts `game`: the one chosen in its menu, else the automatic one."""
        choice = ((self.emu.settings.state or {}).get("emulators") or {}).get(self.game_key(game))
        return self.emu.emulator_for(game, choice)

    def start_session(self, game, from_roulette=False):
        key = self.game_key(game)
        self.session = {"key": key, "start": time.monotonic(), "game": game, "epoch": time.time(),
                        "players": len(self.assignments)}
        played = dict(self.emu.settings.state.get("play") or {})
        seconds = (list(played.get(key) or []) + [0])[0]
        played[key] = [seconds, int(time.time())]
        self.emu.settings.save_state(last_game=game["path"], play=played)
        self.trophy_queue += self.achievements.game_started(game, key, len(self.assignments), from_roulette)

    def end_session(self):
        """The game closed (or was killed): add its play time."""
        session, self.session = self.session, None
        if not session:
            return
        elapsed = int(time.monotonic() - session["start"])
        if elapsed < MIN_SESSION_S:
            return
        played = dict(self.emu.settings.state.get("play") or {})
        seconds, last = (list(played.get(session["key"]) or []) + [0, int(time.time())])[:2]
        played[session["key"]] = [seconds + elapsed, last]
        sessions = Stats.add_session(self.emu.settings.state.get("sessions"), session["key"],
                                     session["game"]["title"], session.get("epoch", time.time() - elapsed),
                                     elapsed, session.get("players", 0))
        self.emu.settings.save_state(play=played, sessions=sessions)
        log("INFO", "Play time", f"{session['key']}: +{elapsed // 60} min")
        self.trophy_queue += self.achievements.game_ended(session["game"], session["key"], elapsed)
        if self.games:
            self.order_games()                  # "recent" / "most played" may have changed
            if session["game"] in self.games:
                self.game_index = self.games.index(session["game"])
            self.shown_games = []               # rebuilt by show_games(), selection kept

    def toggle_favorite(self):
        if not self.shown_games:
            return
        game = self.shown_games[self.game_index]
        key = self.game_key(game)
        favorites = list(self.emu.settings.state.get("favorites") or [])
        if key in favorites:
            favorites.remove(key)
        else:
            favorites.append(key)
        self.emu.settings.save_state(favorites=favorites)
        self.trophy_queue += self.achievements.favorites_changed(len(favorites))
        added = key in favorites
        self.sounds.play("star" if added else "toggle")
        self.ui.show_toast(t("toast_favorite_on" if added else "toast_favorite_off"), COLOR["GOLD"])
        self._reorder_keeping(game)
        if added:
            self.ui.grid.burst()

    def _reorder_keeping(self, game):
        """Re-sort the grid, keeping `game` selected."""
        self._regroup(game)
        self.show_games()

    def _select(self, game, in_rest=True):
        """Select `game` - in the part after the "Continue playing" row when it is there too."""
        if game not in self.shown_games:
            return
        rest = self.shown_games[self.pinned:]
        if in_rest and game in rest:
            self.game_index = self.pinned + rest.index(game)
        else:
            self.game_index = self.shown_games.index(game)

    # ========================================================================
    # ACHIEVEMENTS
    # ========================================================================
    def announce_trophy(self, achievement):
        log("INFO", "Achievement unlocked", achievement)
        self.sounds.play("trophy")
        self.rumble_all(*WIN_RUMBLE)
        self.ui.show_trophy(t(f"ach_{achievement}"), t(f"ach_{achievement}_desc"))

    def show_trophies(self):
        self._mode_before_trophies = self.mode
        self.sounds.play("toggle")
        self._show_trophy_list()
        self.update_hints()

    def _show_trophy_list(self):
        unlocked = self.achievements.unlocked
        items = []
        for achievement in ACHIEVEMENTS:
            info = unlocked.get(achievement)
            desc = t(f"ach_{achievement}_desc")
            if info and info.get("game"):
                desc += f" · {info['game']}"
            date = time.strftime("%d/%m/%Y", time.localtime(info["at"])) if info else ""
            items.append((t(f"ach_{achievement}"), desc, bool(info), date))
        self.mode = "TROPHIES"
        self.ui.show_trophies(items, t("trophies_count", n=sum(1 for i in items if i[2]), total=len(items)))

    def toggle_stats(self):
        """X on the achievements list: the statistics, and back."""
        self.sounds.play("toggle")
        if self.mode == "STATS":
            self.ui.hide_stats()
            self._show_trophy_list()
            return
        state = self.emu.settings.state or {}
        summary = Stats.summarize(state, {self.game_key(g): g["title"] for g in self.games})
        longest = summary["longest"]
        tiles = [
            (t("stats_total"), playtime_label(summary["total"]) if summary["total"] >= 60 else "—",
             t("stats_total_detail", n=summary["games"])),
            (t("stats_week"), playtime_label(summary["week"]) if summary["week"] >= 60 else "—",
             t("stats_week_detail", n=summary["week_sessions"])),
            (t("stats_longest"), playtime_label(longest[1]) if longest else "—",
             f"{longest[0]} · {time.strftime('%d/%m/%Y', time.localtime(longest[2]))}" if longest else
             t("stats_none_yet")),
            (t("stats_players"), f"{summary['players']:.1f}".rstrip("0").rstrip(".") if summary["players"] else "—",
             t("stats_players_detail", n=summary["sessions"])),
        ]
        days = [(t("weekday_short").split()[time.localtime(start).tm_wday], seconds)
                for start, seconds in summary["days"]]
        self.mode = "STATS"
        self.ui.hide_trophies()
        self.ui.show_stats({"tiles": tiles, "days": days, "games": summary["top"]})

    def close_trophies(self):
        self.ui.hide_stats()
        self.ui.hide_trophies()
        self.sounds.play("back")
        self.mode = self._mode_before_trophies
        if self.mode == "PLAYERS":
            self.drop_waiting()
            self.refresh_grid()
        self.update_hints()

    # ========================================================================
    # ROULETTE
    # ========================================================================
    def start_roulette(self, exclude=None):
        joined = len(self.assignments)
        candidates = pick_candidates([g for g in self.games if not g.get("hidden")], joined)
        if not candidates:
            self.sounds.play("error")
            self.ui.show_toast(t("roulette_none", n=joined))
            return
        pool = [g for g in candidates if g is not exclude] or candidates
        chosen = random.choice(pool)
        subtitle = t("roulette_pool", n=len(candidates), p=joined) if joined >= 2 else \
            t("roulette_pool_all", n=len(candidates))
        log("INFO", "Roulette", f"{chosen['title']} (from {len(candidates)})")
        self.mode = "ROULETTE"
        self.nav_direction = None
        self.update_hints()
        self.sounds.play("select")
        self.roulette.start(candidates, chosen, subtitle)

    def on_roulette_tick(self):
        self.sounds.play("tick")
        self.rumble_all(*TICK_RUMBLE)

    def on_roulette_done(self, game):
        self.sounds.play("win")
        self.rumble_all(*WIN_RUMBLE)
        self.ui.set_backdrop(game)
        self.trophy_queue += self.achievements.roulette_picked(game, self.game_key(game))

    def close_roulette(self):
        """Back to the grid, on the drawn game if the spin finished."""
        chosen = None if self.roulette.spinning else self.roulette.chosen
        self.roulette.close()
        self.sounds.play("back")
        self.mode = "GAMES"
        if chosen is not None:
            if chosen not in self.shown_games[self.pinned:]:
                self.tab = "all"
                self._apply_filter()
            if chosen in self.shown_games[self.pinned:]:
                self.game_index = self.shown_games.index(chosen, self.pinned)
        self.show_games()

    # ========================================================================
    # GAME GRID
    # ========================================================================
    def _apply_filter(self):
        """
        Games on screen for the current tab (Core/Library.tab_games), the
        "Continue playing" row first on the "All" tab. The selected game
        stays selected - in the same part of the list when it is in both.
        The list object is kept when its content did not change, so the
        grid does not scroll back to the top.
        """
        joined = len(self.assignments)
        tabs = Library.available_tabs(self.games, joined, self.show_hidden)
        if self.tab not in tabs:
            self.tab = "all"
        old_index, old_pinned = self.game_index, self.pinned
        selected = self.shown_games[old_index] if 0 <= old_index < len(self.shown_games) else None
        games, pinned = Library.tab_games(self.games, self.tab, joined, self.show_hidden,
                                          continue_row=self.sort_mode != "recent")
        if games != self.shown_games or pinned != self.pinned or not self.shown_games:
            self.shown_games, self.pinned = games, pinned
        if selected in self.shown_games:
            in_rest = old_index >= old_pinned and selected in self.shown_games[self.pinned:]
            self.game_index = self.shown_games.index(selected, self.pinned if in_rest else 0)
        else:
            self.game_index = min(self.game_index, max(0, len(self.shown_games) - 1))

    def _tab_entries(self):
        """[(key, label, count)] for the tabs over the grid."""
        joined = len(self.assignments)
        entries = []
        for key in Library.available_tabs(self.games, joined, self.show_hidden):
            games, pinned = Library.tab_games(self.games, key, joined, self.show_hidden, continue_row=False)
            entries.append((key, t(f"tab_{key}", n=joined), len(games)))
        return entries

    def switch_tab(self, delta=None, to=None):
        """LB / RB in the grid: the previous / next tab (or straight to one)."""
        tabs = Library.available_tabs(self.games, len(self.assignments), self.show_hidden)
        if to is None:
            current = tabs.index(self.tab) if self.tab in tabs else 0
            to = tabs[(current + delta) % len(tabs)]
        if to not in tabs or to == self.tab:
            return
        if delta:
            slide = 1 if delta > 0 else -1
        else:
            slide = 1 if self.tab not in tabs or tabs.index(to) > tabs.index(self.tab) else -1
        self.ui.grid.tab_slide = slide
        selected = self.shown_games[self.game_index] if self.shown_games else None
        self.tab = to
        self.emu.settings.save_state(tab=to)
        self.sounds.play("toggle")
        self.shown_games = []
        self._apply_filter()
        rest = self.shown_games[self.pinned:]
        self.game_index = self.pinned + rest.index(selected) if selected in rest else 0
        self._show_grid()
        self.update_hints()

    def show_games(self):
        self.mode = "GAMES"
        self.nav_direction = None
        if not self.games_loaded:
            self.waiting_for_games = True
            self.ui.show_games_loading()
        else:
            self._apply_filter()
            self._show_grid()
        self.update_hints()

    def _show_grid(self):
        """Hand the games on screen, the selection and what the header says to the UI."""
        joined = len(self.assignments)
        self.ui.show_games(self.shown_games, self.game_index, players=joined,
                           filter_players=joined if self.tab == "players" else 0,
                           sort_label=t("sort_" + self.sort_mode) if self.tab != "recent" else "",
                           pinned=self.pinned, tabs=self._tab_entries(), tab=self.tab)

    def show_players(self):
        self.mode = "PLAYERS"
        self.waiting_for_games = False
        self.drop_waiting()
        self.ui.hide_games()
        last = self._last_game()
        if last:
            self.ui.set_backdrop(last)
        self.refresh_grid()
        self.update_hints()

    def toggle_filter(self):
        """Y in the grid: the "for N players" tab, and back to all games."""
        if len(self.assignments) < 2 or not self.games_loaded:
            return
        self.switch_tab(to="all" if self.tab == "players" else "players")

    def move_game_selection(self, action):
        """
        Move the highlight in the grid.

        Args:
            action (str): "left"/"right" (one game, wrapping), "up"/"down"
                          (one row), "page_up"/"page_down" (the previous /
                          next tab).
        """
        if self.mode != "GAMES" or not self.shown_games or self.ui.alert_mode or self.process:
            return
        if action in ("page_up", "page_down"):
            self.switch_tab(-1 if action == "page_up" else 1)
            return
        index = self.ui.grid.step(self.game_index, action)
        if index != self.game_index:
            self.game_index = index
            self.sounds.play("move")
            self._show_grid()
        elif self.ui.grid.bump(action):
            self.sounds.play("edge")

    def poll_navigation(self):
        """
        Read the held direction from every pad: D-pad or left stick move one
        game/row (or one menu row / value), LB/RB a page. Holding repeats,
        like a console menu. In the grid the triggers jump to the next letter.
        """
        sdl = self.sdl
        action = None
        for instance_id, pad in self.pads.items():
            ctrl = pad["ctrl"]
            try:
                stick_x, stick_y = sdl.get_left_x(ctrl), sdl.get_left_y(ctrl)
                left, right = sdl.get_triggers(ctrl)
            except Exception:
                stick_x = stick_y = left = right = 0.0
            # Triggers: one jump per press
            held = (left > TRIGGER_PRESSED, right > TRIGGER_PRESSED)
            before = self._trigger_held.get(instance_id, (False, False))
            self._trigger_held[instance_id] = held
            if self.mode == "GAMES" and held != before and not self._woke():
                jump = -1 if held[0] and not before[0] else 1 if held[1] and not before[1] else 0
                if jump:
                    self._set_browsing(pad)
                    self.jump_letter(jump)
            if action:
                continue
            pressed = lambda button: sdl.SDL_GameControllerGetButton(ctrl, button)  # noqa: E731
            if pressed(sdl.SDL_CONTROLLER_BUTTON_DPAD_UP) or stick_y < -STICK_THRESHOLD:
                action = "up"
            elif pressed(sdl.SDL_CONTROLLER_BUTTON_DPAD_DOWN) or stick_y > STICK_THRESHOLD:
                action = "down"
            elif pressed(sdl.SDL_CONTROLLER_BUTTON_DPAD_LEFT) or stick_x < -STICK_THRESHOLD:
                action = "left"
            elif pressed(sdl.SDL_CONTROLLER_BUTTON_DPAD_RIGHT) or stick_x > STICK_THRESHOLD:
                action = "right"
            elif self.mode == "MENU" and pressed(sdl.SDL_CONTROLLER_BUTTON_LEFT_SHOULDER):
                action = "page_up"
            elif self.mode == "MENU" and pressed(sdl.SDL_CONTROLLER_BUTTON_RIGHT_SHOULDER):
                action = "page_down"
            if action:
                self._set_browsing(pad)

        now = time.monotonic()
        if action is None:
            self.nav_direction = None
            return
        if self.nav_direction == "held":
            return                              # still the press that opened / closed a menu
        if action != self.nav_direction:
            if self._woke():                    # the screen was dimmed: only wake it
                self.nav_direction = action
                self.nav_next_repeat = float("inf")
                return
            self.nav_direction = action
            self.nav_next_repeat = now + NAV_REPEAT_DELAY
        elif now >= self.nav_next_repeat:
            self.last_input = now
            self.nav_next_repeat = now + NAV_REPEAT_RATE
        else:
            return
        if self.mode == "MENU":
            self._navigate_menu(action)
        else:
            self.move_game_selection(action)

    def _set_browsing(self, pad):
        """The grid shows who is browsing: that player's number and colour on the selected tile."""
        slot = next((i for i, a in enumerate(self.assignments) if a["path"] == pad["path"]), None)
        self.ui.grid.nav = None if slot is None else (self.avatar_of(slot), player_color(slot))

    def _navigate_menu(self, action):
        if action in ("up", "down"):
            self.menu.move(-1 if action == "up" else 1)
        elif action in ("page_up", "page_down"):
            self.menu.move(-5 if action == "page_up" else 5)
        else:
            self.menu.change(-1 if action == "left" else 1)

    @staticmethod
    def _letter(game):
        first = game["title"].strip()[:1].upper()
        return first if first.isalpha() else "#"

    def jump_letter(self, direction):
        """
        LT / RT in the grid: to the first game of the previous / next letter.
        Letters only line up in the A-Z order, so the grid switches to it.
        """
        if self.mode != "GAMES" or not self.shown_games or self.process:
            return
        if self.sort_mode != "az":
            self.sort_mode = "az"
            self.emu.settings.save_state(sort=self.sort_mode)
            self._reorder_keeping(self.shown_games[self.game_index])
            self.ui.show_toast(t("hint_sort", mode=t("sort_az")), COLOR["ACCENT"])
            if self.tab == "recent":            # recent is its own order: letters need the full list
                self.switch_tab(to="all")
        pinned = self.pinned
        games, count = self.shown_games[pinned:], len(self.shown_games) - pinned
        if count <= 0:
            return
        letter = self._letter

        def group_start(i):
            while i > 0 and letter(games[i - 1]) == letter(games[i]) \
                    and bool(games[i - 1].get("favorite")) == bool(games[i].get("favorite")):
                i -= 1
            return i

        index = max(0, self.game_index - pinned)
        if direction > 0:
            start = group_start(index)
            index = start
            while index < count and group_start(index) == start:
                index += 1
            index %= count                       # past the last letter: back to the first
        else:
            start = group_start(index)
            index = group_start(start - 1) if start > 0 else group_start(count - 1)
        self.game_index = index + pinned
        self.sounds.play("move")
        self._show_grid()
        self.ui.grid.flash_letter(letter(games[index]))

    def launch_selected_game(self):
        if self.shown_games:
            self.launch_game(self.shown_games[self.game_index])

    def launch_game(self, game, from_roulette=False, firmware_checked=False):
        """Show who plays where for a moment, then start the game."""
        log("INFO", "Game selected", game["path"])
        self.roulette.close()
        self.menu.close()
        if not firmware_checked and self.emulator_for(game).needs_firmware(game):
            # Eden would stop at a mouse-only "Game Requires Firmware" dialog
            name = self.emulator_for(game).display_name
            self.sounds.play("error")
            self.confirm(t("fw_title"), t("fw_text", game=game["title"], name=name),
                         lambda: self.launch_game(game, from_roulette, firmware_checked=True),
                         buttons=(("a", "fw_anyway"), ("b", "alert_back")))
            return
        self.sounds.play("launch")
        if not self.ui.animations:
            self._launch_now(game, from_roulette)
            return
        emulator = self.emulator_for(game)
        players = [player_color(i) for i in range(len(self.assignments))]
        icons = [self.avatar_of(i) for i in range(len(self.assignments))]
        mode = split_mode(game, ((self.emu.settings.state or {}).get("split") or {}).get(self.game_key(game)))
        self._mode_before_splash = "PLAYERS" if self.mode == "PLAYERS" else "GAMES"
        self.mode = "SPLASH"
        self.update_hints()
        self.ui.set_backdrop(game)
        self.ui.show_splash(game, players, split_layout(len(players), mode),
                            emulator.display_name if len(self.emu.variants) > 1 else None,
                            icons=icons, starting=emulator.display_name)
        self._splash_job = self.root.after(SPLASH_MS, self._launch_now, game, from_roulette)
        self._exit_job = self.root.after(SPLASH_MS - SPLASH_EXIT_MS, self._splash_exit)

    def _splash_exit(self):
        self._exit_job = None
        self.ui.splash_exit()

    def cancel_splash(self):
        """B on the "let's go" screen: do not start after all."""
        if self._splash_job:
            self.root.after_cancel(self._splash_job)
            self._splash_job = None
        if self._exit_job:
            self.root.after_cancel(self._exit_job)
            self._exit_job = None
        self.ui.hide_splash()
        self.sounds.play("back")
        if self._mode_before_splash == "PLAYERS":
            self.show_players()
        else:
            self.show_games()

    def _launch_now(self, game, from_roulette=False):
        """Back up the saves, remember the game, start it."""
        self._splash_job = None
        emulator = self.emulator_for(game)
        if self.backup_keep > 0:
            root, folders = emulator.save_folders(game)
            backup_saves(game, root, folders, self.backup_dir, self.backup_keep)
        self.start_session(game, from_roulette)
        self.mode = "GAMES"
        self.force_launch(emulator.game_command(game["path"]), emulator)
        self.ui.hide_splash()

    def resume_last_game(self, instance_id):
        """Y on the player cards: straight back into the last game."""
        last = self._last_game() if self.picker and self.games_loaded else None
        if not last:
            return
        if not self.assignments:
            self.assign_player(instance_id)     # whoever pressed Y plays
        self.launch_game(last)

    # ========================================================================
    # CONFIG GENERATION & LAUNCH
    # ========================================================================
    def scan_hardware(self):
        """
        Fresh SDL enumeration performed immediately before writing the config.

        Critical: the subsystem is torn down and re-initialized so the pads are
        enumerated in the same OS order the emulator itself will see.

        Returns:
            list[dict]: [{"path", "guid", "name", "face_buttons"}, ...] in
                        enumeration order. "guid" is the raw 32-char SDL hex
                        string; turning it into an emulator-specific id is the
                        emulator's job. "face_buttons" is SDLManager.face_buttons().
        """
        sdl = self.sdl

        # Path-less pads keep their keys across the re-initialisation
        previous = {}
        for instance_id in self._pad_order:
            pad = self.pads[instance_id]
            if not pad["raw_path"]:
                previous.setdefault(pad["guid"], []).append(pad["path"])

        # Close all existing controller handles; sync_pads() reopens them later
        for pad in self.pads.values():
            sdl.SDL_GameControllerClose(pad["ctrl"])
        self.pads.clear()
        self._pad_order = []
        self._not_gamepads.clear()

        # Reinitialize SDL2/SDL3 for fresh enumeration
        sdl.SDL_QuitSubSystem(sdl.SDL_INIT_JOYSTICK | sdl.SDL_INIT_GAMECONTROLLER)
        sdl.SDL_Init()

        pads, order = {}, []
        for open_id, instance_id in sdl.SDL_GetJoystickInstanceIDs():
            if not sdl.SDL_IsGameController(open_id):
                continue
            ctrl = sdl.SDL_GameControllerOpen(open_id)
            if not ctrl:
                continue
            pads[instance_id] = self._describe(ctrl)
            order.append(instance_id)
        self._assign_keys(pads, order, previous)
        carried = {}
        for instance_id in order:
            if not pads[instance_id]["raw_path"]:
                carried.setdefault(pads[instance_id]["guid"], []).append(pads[instance_id]["path"])
        self._carried_keys = carried            # for sync_pads() reopening them

        hardware = []
        for instance_id in order:
            pad = pads[instance_id]
            try:
                face = sdl.face_buttons(pad["ctrl"])
            except Exception as e:
                log("WARNING", "Could not read face button bindings", e)
                face = None
            hardware.append({"path": pad["path"], "guid": pad["guid"], "name": pad["name"],
                             "face_buttons": face})
            sdl.SDL_GameControllerClose(pad["ctrl"])
        return hardware

    def check_launch(self):
        """Validate assignment state before launching."""
        if len(self.assignments) == 0:
            self.ui.show_alert("LAUNCH")  # Warn about no controllers
        else:
            self.proceed()

    def proceed(self):
        """After the player cards: the game grid if there is one, else launch."""
        if self.picker:
            self.show_games()
        else:
            self.force_launch()

    def force_launch(self, cmd_args=None, emulator=None):
        """
        Write the controller configuration and start the emulator.

        Args:
            cmd_args (list[str] | None): Command line to run. Defaults to the
                emulator's command with this launcher's own arguments (e.g. a
                game path from Playnite) passed straight through.
            emulator (Emulator | None): one of self.emu.variants; the main
                emulator when not given.
        """
        emulator = emulator or self.emu
        emulator.ensure_ready(self.emu)
        playing = [a for a in self.assignments if not a.get("waiting")]
        if len(playing) < len(self.assignments):
            log("WARNING", "Starting without the players whose controller is still disconnected",
                ", ".join(f"P{i + 1}" for i, a in enumerate(self.assignments) if a.get("waiting")))
        emulator.write_input_config(playing, self.scan_hardware())

        if cmd_args is None:
            cmd_args = emulator.command(sys.argv)
        log("INFO", "Launching", str(cmd_args))
        self.root.withdraw()  # Hide launcher window
        self.returning_to_launcher = False  # Clear restart flag
        self.ui.set_dim(False)

        if os.path.exists(emulator.exe):
            try:
                self.process = launch(cmd_args, emulator.env or self.emu.env)
                self.running_emu = emulator
                self.launched_at = time.monotonic()
                self._dialogs_seen = set()
                self.sounds.suspend()
            except Exception as e:
                log("EXCEPTION", "Launch failed", e)
                messagebox.showerror(
                    t("error_launch_title"),
                    t("error_launch_text", name=emulator.display_name, error=e)
                )
                sys.exit()
        else:
            log("ERROR", "Executable not found", emulator.exe)
            messagebox.showerror(
                t("error_missing_title"),
                t("error_missing_text", path=emulator.exe)
            )
            sys.exit()

    # ========================================================================
    # MENUS
    # ========================================================================
    def _open_menu(self, *args, **kwargs):
        if self.mode != "MENU":
            self._mode_before_menu = self.mode
            self.mode = "MENU"
            self.nav_direction = "held"             # the press that opened it must not move it
            self.nav_next_repeat = float("inf")
            self.update_hints()
        self.menu.open(*args, **kwargs)

    @staticmethod
    def _value_label(value):
        """Display text of an option's value ("auto" -> "Automatic"), the value itself if unnamed."""
        key = f"val_{str(value).lower()}"
        return t(key) if key in STRINGS["en"] else str(value)

    def _choice_row(self, section, key, values, default, apply=None, label=None):
        """A settings row cycling `key` of `section` through `values`, saved at once."""
        settings = self.emu.settings
        if tuple(values) == ("true", "false"):
            current = "true" if settings.get_bool(section, key, default == "true") else "false"
        else:
            current = settings.get(section, key, default).lower()
        if current not in values:
            current = default

        def change(delta):
            value = values[(values.index(current) + delta) % len(values)]
            settings.set(section, key, value)
            if apply:
                apply(value)
        return {"label": label or t(f"opt_{key}"), "value": self._value_label(current), "change": change}

    # ------------------------------------------------------------------
    # Settings (LB on the player cards)
    # ------------------------------------------------------------------
    def open_settings(self):
        self.sounds.play("toggle")
        self._open_menu(lambda: t("settings_title"), self._settings_rows,
                        subtitle=lambda: t("settings_subtitle"))

    def _settings_rows(self):
        settings, launcher = self.emu.settings, LAUNCHER_SECTION
        on_off = ("true", "false")
        row = self._choice_row

        def language(value):
            set_language(value)
            self._set_tip()
            self._set_brand()
            self.ui.redraw()

        def motion(value):
            self.ui.motion = value == "true"
            self.ui._backgrounds.clear()            # the backdrop is drawn larger when it drifts
            self.ui._drift = (0, 0)
            self.ui._apply_background()

        def colors(value):
            self.ui.dynamic_colors = value == "true"
            self.ui.grid.reset()

        def sound(_value=None):
            self.sounds.shutdown()
            self.sounds = Sounds(
                enabled=settings.get_bool(launcher, "sounds", True),
                volume=settings.get_int(launcher, "sound_volume", 70),
                override_dir=os.path.join(base_dir(), "sounds"))

        def backups(value):
            self.backup_keep = settings.get_int(launcher, "backup_keep", 10) if value == "true" else 0

        def idle(value):
            self.idle_dim_s = int(value) * 60

        def updates(value):
            self.update.enabled = value == "true" and BUILD > 0
            self.update.start()

        volume = max(0, min(100, settings.get_int(launcher, "sound_volume", 70)))

        def set_volume(delta):
            settings.set(launcher, "sound_volume", max(0, min(100, volume + 10 * delta)))
            sound()

        def sort(delta):
            self.sort_mode = SORT_MODES[(SORT_MODES.index(self.sort_mode) + delta) % len(SORT_MODES)]
            settings.save_state(sort=self.sort_mode)
            if self.games:
                self.order_games()
                self.shown_games = []

        folders, _ = self.emu.game_folders()
        rows = [
            {"label": t("settings_look"), "kind": "header"},
            row(launcher, "language", ("auto", "it", "en"), "auto", language),
            row(launcher, "theme", THEME_NAMES, "dark", lambda value: self.ui.set_theme(name=value)),
            row(launcher, "accent", ACCENT_NAMES, "violet", lambda value: self.ui.set_theme(accent=value)),
            row(launcher, "background_motion", on_off, "true", motion),
            row(launcher, "animations", on_off, "true",
                lambda value: setattr(self.ui, "animations", value == "true")),
            row(launcher, "dynamic_colors", on_off, "true", colors),
            row(launcher, "idle_dim_minutes", IDLE_DIM_CHOICES, "5", idle),
            {"label": t("settings_sound"), "kind": "header"},
            row(launcher, "sounds", on_off, "true", sound),
            {"label": t("opt_sound_volume"), "value": f"{volume}%", "change": set_volume},
            row(launcher, "rumble", on_off, "true",
                lambda value: setattr(self, "rumble_enabled", value == "true")),
            {"label": t("settings_games"), "kind": "header"},
        ]
        if not self.has_game_args and self.emu.game_picker_enabled():
            rows.append({"label": t("opt_game_dirs"), "value": t("folders_count", n=len(folders)),
                         "action": self.open_folders})
            rows.append({"label": t("gm_sort"), "value": t("sort_" + self.sort_mode), "change": sort})
            rows.append({"label": t("settings_emulators"), "value": str(len(self.emu.variants)),
                         "action": self.open_emulators})
        rows.append(row(launcher, "backup_saves", on_off, "true", backups))
        if self.emu.setting_choices:
            rows.append({"label": self.emu.display_name, "kind": "header"})
            for key, values, default in self.emu.setting_choices:
                rows.append(row(self.emu.settings_section, key, values, default))
        rows += [
            {"label": t("settings_launcher"), "kind": "header"},
            row(launcher, "check_updates", on_off, "true", updates),
            {"label": t("settings_version", version=self._version_text()), "kind": "info"},
        ]
        if self.update.newer:
            rows.append({"label": t("settings_update", n=self.update.newer, page=DOWNLOAD_PAGE), "kind": "info"})
        return rows

    @staticmethod
    def _version_text():
        from .Bootstrap import version_text
        return version_text()

    # ------------------------------------------------------------------
    # Game folders (from the settings)
    # ------------------------------------------------------------------
    def open_folders(self):
        self._folders_depth = len(self.menu.stack)
        self._folders_changed = False
        self._open_menu(t("opt_game_dirs"), self._folders_rows, subtitle=t("folders_subtitle"),
                        on_back=self._folders_closed)

    def _folders_rows(self):
        folders, own = self.emu.game_folders()
        paths = [folder for folder, _ in folders]
        rows = []
        if not own and paths:
            rows.append({"label": t("folders_from_emulator", name=self.emu.display_name), "kind": "header"})
        for path in paths:
            if own:
                rows.append({"label": path, "value": t("folders_remove"),
                             "action": lambda p=path: self._set_folders([f for f in paths if f != p])})
            else:
                rows.append({"label": path, "kind": "info"})
        if not paths:
            rows.append({"label": t("folders_none"), "kind": "info"})
        rows.append({"label": t("folders_add"), "action": lambda: self._browse(None, paths)})
        if own:
            rows.append({"label": t("folders_reset", name=self.emu.display_name),
                         "action": lambda: self._set_folders([])})
        return rows

    def _set_folders(self, folders):
        self.emu.set_game_folders(folders)
        self._folders_changed = True
        log("INFO", "Game folders set", "; ".join(folders) or "(the emulator's own)")

    def _folders_closed(self):
        if self._folders_changed:
            self.rescan_games()
            self.ui.show_toast(t("toast_rescan"), COLOR["ACCENT"])

    @staticmethod
    def _roots():
        """Where the folder browser starts: the drives on Windows, / and home elsewhere."""
        if sys.platform == "win32":
            return [f"{letter}:\\" for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.isdir(f"{letter}:\\")]
        return [path for path in ("/", os.path.expanduser("~")) if os.path.isdir(path)]

    # ------------------------------------------------------------------
    # Emulators (from the settings)
    # ------------------------------------------------------------------
    def open_emulators(self):
        self._emulators_depth = len(self.menu.stack)
        self._emulators_changed = False
        self._open_menu(t("settings_emulators"), self._emulator_rows, subtitle=t("emulators_subtitle"),
                        on_back=self._emulators_closed)

    def _emulator_rows(self):
        rows = []
        for i, emulator in enumerate(self.emu.variants):
            facts = [text for text in (t("emulators_main") if i == 0 else "",
                                       t("emulators_nx2") if emulator.supports_nx2() else "") if text]
            row = {"label": emulator.display_name, "value": "  ·  ".join(facts) or None}
            if getattr(emulator, "settings_key", None):
                row["action"] = lambda e=emulator: self.confirm(
                    t("emulators_remove") + f" {e.display_name}?", e.exe, lambda: self._remove_emulator(e))
            else:
                row["kind"] = "info"
                row["label"] = emulator.display_name + (f"   ·   {row['value']}" if row["value"] else "")
            rows.append(row)
            rows.append({"label": emulator.exe, "kind": "info"})
        rows.append({"label": t("emulators_add"), "action": lambda: self._browse_exe(None)})
        return rows

    def _remove_emulator(self, emulator):
        if self.emu.remove_variant(emulator):
            self._emulators_changed = True
        self.menu.refresh()

    def _emulators_closed(self):
        if self._emulators_changed:
            self.rescan_games()             # other game folders, other emulators for the games
            self.ui.show_toast(t("toast_rescan"), COLOR["ACCENT"])

    def _browse_exe(self, path):
        """One level of the file browser: enter a folder or pick an emulator's executable."""
        windows = sys.platform == "win32"

        def rows():
            if path is None:
                return [{"label": root, "value": "›", "action": lambda r=root: self._browse_exe(r)}
                        for root in self._roots()]
            try:
                names = sorted((n for n in os.listdir(path) if not n.startswith((".", "$"))), key=str.lower)
            except OSError:
                return [{"label": t("folders_unreadable"), "kind": "info"}]
            found = []
            for name in names:                  # executables first: that is what is being looked for
                full = os.path.join(path, name)
                if os.path.isfile(full) and (name.lower().endswith(".exe") if windows else os.access(full, os.X_OK)):
                    found.append({"label": name, "color": COLOR["OK"], "action": lambda f=full: self._add_emulator(f)})
            for name in names:
                if os.path.isdir(os.path.join(path, name)):
                    found.append({"label": name, "value": "›",
                                  "action": lambda n=name: self._browse_exe(os.path.join(path, n))})
            return found or [{"label": t("folders_none"), "kind": "info"}]
        self._open_menu(t("emulators_pick"), rows, subtitle=path or t("folders_pick_drive"))

    def _add_emulator(self, path):
        emulator = self.emu.add_variant(path)
        if emulator is None:
            self.sounds.play("error")
            self.ui.show_toast(t("emulators_not_one"))
            return
        self._emulators_changed = True
        self.ui.show_toast(t("emulators_added", name=emulator.display_name), COLOR["OK"])
        del self.menu.stack[self._emulators_depth + 1:]      # back to the list of emulators
        self.menu.refresh()

    def _browse(self, path, current):
        """One level of the folder browser: pick this folder, go up, or enter a subfolder."""
        def rows():
            if path is None:
                return [{"label": root, "action": lambda r=root: self._browse(r, current)} for root in self._roots()]
            found = [{"label": "✓  " + t("folders_use"), "color": COLOR["OK"],
                      "action": lambda: self._add_folder(path, current)}]
            try:
                names = sorted((n for n in os.listdir(path) if not n.startswith((".", "$"))
                                and os.path.isdir(os.path.join(path, n))), key=str.lower)
            except OSError:
                names = []
                found.append({"label": t("folders_unreadable"), "kind": "info"})
            for name in names:
                found.append({"label": name, "value": "›",
                              "action": lambda n=name: self._browse(os.path.join(path, n), current)})
            return found
        self._open_menu(t("folders_add"), rows, subtitle=path or t("folders_pick_drive"))

    def _add_folder(self, path, current):
        path = os.path.normpath(path)
        known = {os.path.normcase(os.path.abspath(f)) for f in current}
        if os.path.normcase(os.path.abspath(path)) not in known:
            self._set_folders(list(current) + [path])
        del self.menu.stack[self._folders_depth + 1:]       # back to the list of folders
        self.menu.refresh()

    # ------------------------------------------------------------------
    # A game's menu (START in the grid)
    # ------------------------------------------------------------------
    def open_game_menu(self):
        if not self.shown_games:
            return
        game = self.shown_games[self.game_index]
        self.sounds.play("toggle")
        self._open_menu(game["title"], lambda: self._game_rows(game), game=game,
                        info=lambda: self._game_info(game), on_back=lambda: self._game_menu_closed(game))

    def _game_info(self, game):
        lines = []
        if game.get("players"):
            lines.append(players_label(game["players"]))
        if (game.get("playtime") or 0) >= 60:
            lines.append(t("gm_played", time=playtime_label(game["playtime"])))
        if game.get("last_played"):
            lines.append(t("gm_last", when=last_played_label(game["last_played"])))
        if game.get("title_id"):
            lines.append(game["title_id"])
        lines.append(os.path.basename(game["path"]) if os.path.basename(game["path"]).lower() != "main"
                     else os.path.basename(os.path.dirname(os.path.dirname(game["path"]))))
        if game.get("custom_input"):
            lines.append("!  " + t("games_custom_input"))
        return lines

    def _state_map(self, name):
        return dict((self.emu.settings.state or {}).get(name) or {})

    def _game_rows(self, game):
        key = self.game_key(game)
        settings = self.emu.settings
        state = settings.state or {}
        def play():
            self._regroup(game)                 # a favourite / sort change made here still counts
            self.launch_game(game)
        rows = [{"label": t("gm_play"), "color": COLOR["OK"], "action": play}]

        def favorite(_delta):
            favorites = list(state.get("favorites") or [])
            favorites.remove(key) if key in favorites else favorites.append(key)
            settings.save_state(favorites=favorites)
            game["favorite"] = key in favorites
            self.trophy_queue += self.achievements.favorites_changed(len(favorites))
            self.ui.grid.forget(game)
        rows.append({"label": t("hint_favorite"), "value": t("val_true" if game.get("favorite") else "val_false"),
                     "change": favorite})

        variants = self.emu.variants
        if len(variants) > 1:
            choices = [None] + [v.display_name for v in variants]
            chosen = self._state_map("emulators").get(key)
            chosen = chosen if chosen in choices else None

            def emulator(delta):
                picked = choices[(choices.index(chosen) + delta) % len(choices)]
                stored = self._state_map("emulators")
                stored.pop(key, None)
                if picked:
                    stored[key] = picked
                settings.save_state(emulators=stored)
                game["emulator_name"] = self.emulator_for(game).display_name
            automatic = self.emu.emulator_for(game).display_name
            rows.append({"label": t("gm_emulator"), "change": emulator,
                         "value": chosen or t("gm_auto", what=automatic)})

        mode = self._state_map("split").get(key, "auto")
        mode = mode if mode in SPLIT_MODES else "auto"

        def split(delta):
            picked = SPLIT_MODES[(SPLIT_MODES.index(mode) + delta) % len(SPLIT_MODES)]
            stored = self._state_map("split")
            stored.pop(key, None)
            if picked != "auto":
                stored[key] = picked
            settings.save_state(split=stored)
        rows.append({"label": t("gm_split"), "change": split,
                     "value": t(f"split_mode_{mode}") if mode != "auto" else
                     t("gm_auto", what=t(f"split_mode_{split_mode(game)}"))})

        backups = list_backups(game, self.backup_dir)
        if backups:
            rows.append({"label": t("gm_restore"), "value": t("gm_backups", n=len(backups)),
                         "action": lambda: self._open_backups(game)})
        else:
            rows.append({"label": t("gm_no_backups"), "kind": "info"})

        def hide():
            hidden = list(state.get("hidden") or [])
            hidden.remove(key) if key in hidden else hidden.append(key)
            settings.save_state(hidden=hidden)
            game["hidden"] = key in hidden
            self.ui.show_toast(t("toast_hidden" if game["hidden"] else "toast_unhidden"), COLOR["ACCENT"])
            self.menu.back()
            self._after_menu()
        rows.append({"label": t("gm_unhide" if game.get("hidden") else "gm_hide"), "action": hide})

        rows.append({"label": t("gm_list"), "kind": "header"})

        def sort(delta):
            self.sort_mode = SORT_MODES[(SORT_MODES.index(self.sort_mode) + delta) % len(SORT_MODES)]
            settings.save_state(sort=self.sort_mode)
        rows.append({"label": t("gm_sort"), "value": t("sort_" + self.sort_mode), "change": sort})
        hidden_count = sum(1 for g in self.games if g.get("hidden"))
        if hidden_count:
            def show_hidden(_delta):
                self.show_hidden = not self.show_hidden
            rows.append({"label": t("gm_show_hidden", n=hidden_count), "change": show_hidden,
                         "value": t("val_true" if self.show_hidden else "val_false")})
        return rows

    def _regroup(self, game):
        """Sort and filter the games again, keeping `game` selected if it is still shown."""
        in_rest = self.game_index >= self.pinned
        self.order_games()
        self.shown_games = []
        self._apply_filter()
        self._select(game, in_rest)

    def _game_menu_closed(self, game):
        """Favourite, hidden, sort order may have changed: rebuild the grid around the game."""
        self._regroup(game)
        self._show_grid()

    # ------------------------------------------------------------------
    # Restoring a save backup (from a game's menu)
    # ------------------------------------------------------------------
    def _open_backups(self, game):
        def rows():
            found = []
            for backup in list_backups(game, self.backup_dir):
                when = time.strftime("%d/%m/%Y  %H:%M", time.localtime(backup["time"]))
                size = backup["size"] / 1024
                found.append({"label": when, "value": f"{size / 1024:.1f} MB" if size >= 1024 else f"{size:.0f} KB",
                              "action": lambda b=backup, w=when: self.confirm(
                                  t("restore_title"), t("restore_text", when=w, title=game["title"]),
                                  lambda: self._restore(game, b))})
            return found or [{"label": t("gm_no_backups"), "kind": "info"}]
        self._open_menu(t("gm_restore"), rows, subtitle=t("restore_subtitle", title=game["title"]), game=game)

    def _restore(self, game, backup):
        emulator = self.emulator_for(game)
        root, folders = emulator.save_folders(game)
        if not root:
            self.sounds.play("error")
            self.ui.show_toast(t("restore_failed"))
            return
        try:
            # The saves about to be replaced are kept as one more backup
            # (never at the cost of an older one)
            backup_saves(game, root, folders, self.backup_dir,
                         keep=len(list_backups(game, self.backup_dir)) + 1)
            restore(backup["path"], root, folders)
        except Exception as e:
            log("EXCEPTION", "Restore failed", e)
            self.sounds.play("error")
            self.ui.show_toast(t("restore_failed"))
            return
        self.sounds.play("win")
        self.ui.show_toast(t("restore_done"), COLOR["OK"])
        self.menu.refresh()


def _int(text, default):
    try:
        return int(str(text).strip())
    except ValueError:
        return default
