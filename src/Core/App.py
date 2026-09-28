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

Remembered between sessions (Settings state file): the last game, play time
and last-played date per game, favourites and the grid's sort order.

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

from . import Sunshine
from .Art import ArtLibrary
from .Backup import backup_saves
from .I18n import t
from .Log import log
from .Paths import base_dir, resource_path
from .Process import launch
from .Roulette import Roulette, pick_candidates
from .Settings import DEFAULT_KILL_COMBO, LAUNCHER_SECTION, parse_combo
from .Sound import Sounds
from .Ui import COLOR, COLOR_POOL, LauncherUi

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

# kill_combo button names -> button picture
COMBO_GLYPHS = {"select": "back", "up": "dpad", "down": "dpad", "left": "dpad", "right": "dpad"}


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
        self._scan_result = None            # handed over by the scan thread
        self.waiting_for_games = False      # START pressed while still scanning

        self.ui = LauncherUi(root, emulator.name)

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
        self.ui.slideshow_seconds = _int(settings.get(LAUNCHER_SECTION, "slideshow_seconds", "8"), 8)
        self.ui.slides_wanted = self.art.want_screens
        self.roulette = Roulette(self.ui, on_tick=self.on_roulette_tick, on_done=self.on_roulette_done)
        state = settings.state or {}
        self.sort_mode = state.get("sort") if state.get("sort") in SORT_MODES else SORT_MODES[0]
        self.session = None                 # {"key", "start"} while a picked game runs
        self._stream_apps = None            # apps.json offered in the "add to Moonlight" dialog
        self._stream_result = None          # elevated registration outcome (thread -> loop)

        # Keyboard shortcuts for accessibility
        self.root.bind("<Return>", lambda e: self.handle_enter_key())
        self.root.bind("<Escape>", lambda e: self.handle_esc_key())
        for key, action in (("<Up>", "up"), ("<Down>", "down"), ("<Left>", "left"),
                            ("<Right>", "right"), ("<Prior>", "page_up"), ("<Next>", "page_down")):
            self.root.bind(key, lambda e, a=action: self.move_game_selection(a))

        # Initialize SDL2/SDL3 controller subsystem
        self.sdl.SDL_Init()

        # State management
        self.pads = {}                      # {instance_id: {"ctrl", "path", "raw_path", "guid", "name"}}
        self._pad_order = []                # instance ids in SDL enumeration order
        self._not_gamepads = set()          # joysticks that are not gamepads (flight sticks...)
        self.assignments = []               # [{"path", "name", "profile_key", "is_editing"}] - player order
        self.color_pool = list(COLOR_POOL)
        random.shuffle(self.color_pool)
        self.hid_colors = {}                # {path: color} for the session
        self.hid_profiles = {}              # {path: profile_key} across reconnects
        self.process = None                 # Emulator subprocess handle
        self.returning_to_launcher = False  # Flag for kill -> restart flow

        # Game grid state
        self.mode = "PLAYERS"
        self.filter_on = False
        self.game_index = 0
        self.nav_direction = None           # Held navigation action ("up", "left", ...)
        self.nav_next_repeat = 0.0

        # Profile keys come from the emulator; the first one is the default
        self.profile_keys = list(self.emu.profiles.keys())
        self.default_profile = self.profile_keys[0] if self.profile_keys else ""

        combo_markup = " + ".join(f"[{COMBO_GLYPHS.get(n, n)}]" for n in self._combo_names(combo))
        self.ui.set_tip(t("tip_kill", combo=combo_markup, name=emulator.name))
        self.update_hints()

        if self.picker:
            threading.Thread(target=self._scan_games, daemon=True).start()

        self.update_loop()
        self.root.after(1200, self.offer_streaming)

    @staticmethod
    def _combo_names(text):
        names = [n.strip().lower() for n in (text or "").split("+") if n.strip()]
        from .Settings import BUTTON_NAMES
        return names if names and all(n in BUTTON_NAMES for n in names) else DEFAULT_KILL_COMBO.split("+")

    # ========================================================================
    # KEYBOARD INPUT HANDLERS
    # ========================================================================
    def handle_enter_key(self):
        """Handle Enter key press (confirm action in alerts)."""
        if self.ui.alert_mode == "LAUNCH":
            self.ui.close_alert()
            self.proceed()
        elif self.ui.alert_mode == "EXIT":
            self.quit()
        elif self.ui.alert_mode == "KILL_CONFIRM":
            self.kill_and_quit()
        elif self.ui.alert_mode == "STREAM":
            self.ui.close_alert()
            self.register_streaming()
        elif self.mode == "ROULETTE":
            if not self.roulette.spinning:
                self.launch_game(self.roulette.chosen)
        elif self.mode == "GAMES" and not self.process:
            self.launch_selected_game()
        elif not self.process:
            self.check_launch()

    def handle_esc_key(self):
        """Handle Escape key press (cancel/back action)."""
        if self.ui.alert_mode:
            self.ui.close_alert()
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

    def kill_and_quit(self):
        """Kill the emulator and exit the launcher (kill menu -> Desktop)."""
        if self.process:
            self.process.kill()
            log("INFO", f"{self.emu.name} killed - exiting to desktop")
        self.end_session()
        self.emu.cleanup()
        self.root.quit()
        sys.exit()

    def kill_and_restart(self):
        """
        Kill the emulator and return to the launcher (kill menu -> Launcher).

        Sets a flag to prevent automatic exit when the process terminates.
        """
        self.returning_to_launcher = True
        if self.process:
            self.process.kill()
            log("INFO", f"{self.emu.name} killed - returning to launcher")
        self.process = None
        self.end_session()

        # Reset launcher state for fresh assignment
        self.assignments = []
        self.ui.close_alert()
        self.show_players()
        self.root.deiconify()

    def _check_process(self):
        """React to the emulator exiting. Returns True if the loop should stop here."""
        if self.process.poll() is None:
            return False
        self.process = None
        self.end_session()
        self.root.deiconify()
        if self.returning_to_launcher:
            # User chose "Launcher" from the kill menu - fresh assignment
            log("INFO", f"{self.emu.name} exited - returning to launcher")
            self.returning_to_launcher = False
            self.assignments = []
            self.show_players()
        elif self.picker and self.games:
            # Picked from the game grid: offer it again, same players
            log("INFO", f"{self.emu.name} exited - back to the game grid")
            self.show_games()
        else:
            # Emulator closed normally or crashed - exit launcher
            log("INFO", f"{self.emu.name} exited - closing launcher")
            self.emu.cleanup()
            self.root.quit()
            sys.exit()
        return False

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
    def _assign_keys(pads, order):
        """
        The key identifying each pad ("path"): its device path, or - for
        pads the OS gives no path - its GUID plus its order among path-less
        pads with that GUID. Computed the same way on every scan, so a pad
        keeps its key across the SDL re-initialisation in scan_hardware().
        """
        pathless = {}
        for instance_id in order:
            pad = pads[instance_id]
            if pad["raw_path"]:
                pad["path"] = pad["raw_path"]
            else:
                n = pathless.get(pad["guid"], 0)
                pathless[pad["guid"]] = n + 1
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
        self._assign_keys(self.pads, self._pad_order)
        return True

    def on_pads_changed(self):
        """Update the counter and drop players whose controller disconnected."""
        self.ui.set_pads(len(self.pads))
        connected = {pad["path"] for pad in self.pads.values()}
        dropped = [a for a in self.assignments if a["path"] not in connected]
        if not dropped:
            return
        self.assignments = [a for a in self.assignments if a["path"] in connected]
        self.ui.show_toast(t("toast_disconnected", name=dropped[0]["name"]),
                           self.hid_colors.get(dropped[0]["path"], COLOR["NEON_RED"]))
        for assignment in dropped:
            log("INFO", "Controller disconnected", assignment["name"])
            color = self.hid_colors.pop(assignment["path"], None)
            if color:
                self.color_pool.append(color)
            self.hid_profiles.pop(assignment["path"], None)  # Clear profile on hardware disconnect
        self.refresh_grid()
        if self.mode == "GAMES":
            self.show_games()                   # badges depend on the number of players
        elif self.mode == "PLAYERS":
            self.update_hints()

    def kill_combo_pressed(self):
        sdl = self.sdl
        return any(all(sdl.SDL_GameControllerGetButton(pad["ctrl"], b) for b in self.kill_buttons)
                   for pad in self.pads.values())

    # ========================================================================
    # MAIN EVENT LOOP
    # ========================================================================
    def update_loop(self):
        """
        Main event processing loop (runs every 16ms).

        Handles:
        - Emulator process monitoring and the kill combo
        - Controller hot-plug
        - Game scan results and downloaded pictures
        - Game grid navigation
        - Gamepad button events
        """
        sdl = self.sdl

        if self._scan_result is not None:
            games, self._scan_result = self._scan_result, None
            self.on_games_scanned(games)

        # ====================================================================
        # EMULATOR PROCESS MONITORING / KILL COMBO (ANY CONTROLLER)
        # ====================================================================
        if self.process and not self.ui.alert_mode:
            self._check_process()
            if self.process and self.kill_combo_pressed():
                self.root.deiconify()  # Bring launcher to foreground
                log("INFO", "Kill combo detected - showing menu")
                self.ui.show_alert("KILL_CONFIRM")

        # ====================================================================
        # HOT-PLUG
        # ====================================================================
        if self.sync_pads():
            self.on_pads_changed()

        # ====================================================================
        # BACKGROUND WORK RESULTS
        # ====================================================================
        for game in self.art.take_finished():
            self.ui.games_art_changed(game)
        if self._stream_result is not None:
            ok, self._stream_result = self._stream_result, None
            self.on_streaming_registered("added" if ok else None)
        if self.art.sgdb_missing != self._sgdb_missing_saved:
            self._sgdb_missing_saved = set(self.art.sgdb_missing)
            self.emu.settings.save_state(covers_not_found=sorted(self._sgdb_missing_saved))

        if self.mode == "GAMES" and not self.ui.alert_mode and not self.process:
            self.poll_game_navigation()

        # ====================================================================
        # GAMEPAD BUTTON EVENT PROCESSING
        # ====================================================================
        event = sdl.SDL_Event()
        while sdl.SDL_PollEvent(ctypes.byref(event)) != 0:
            if event.type == sdl.SDL_CONTROLLERBUTTONDOWN:
                button, which = sdl.get_button_info(event)
                if self.ui.alert_mode:
                    self.on_alert_button(button)
                elif self.process:
                    continue    # Ignore input while a game runs (no mid-game reassignment)
                elif self.mode == "ROULETTE":
                    self.on_roulette_button(button)
                elif self.mode == "GAMES":
                    self.on_games_button(button)
                else:
                    self.on_players_button(button, which)

            elif event.type == sdl.SDL_CONTROLLERAXISMOTION:
                if self.mode != "PLAYERS" or self.process:
                    continue
                direction, which = sdl.get_axis_motion_info(event)
                if which is not None:
                    if direction != 0 and not sdl.axis_engaged.get(which, False):
                        sdl.axis_engaged[which] = True
                        self.cycle_profile(which, direction)
                    elif direction == 0:
                        sdl.axis_engaged[which] = False  # reset when stick returns to center

            elif event.type == sdl.SDL_QUIT:
                self.quit()
                return

        # Schedule next update
        self.root.after(POLL_INTERVAL_MS, self.update_loop)

    def on_alert_button(self, button):
        sdl = self.sdl
        mode = self.ui.alert_mode
        if time.monotonic() - self.ui.alert_since < ALERT_GUARD_S:
            return      # a press meant for the screen underneath (e.g. A to join)
        if mode == "STREAM":
            if button == sdl.SDL_CONTROLLER_BUTTON_A:
                self.ui.close_alert()
                self.register_streaming()
            elif button == sdl.SDL_CONTROLLER_BUTTON_Y:
                self.ui.close_alert()
                self.sounds.play("back")
                self.emu.settings.save_state(streaming_prompt="never")
                log("INFO", "Streaming host prompt disabled")
            elif button == sdl.SDL_CONTROLLER_BUTTON_B:
                self.ui.close_alert()
                self.sounds.play("back")
        elif mode == "KILL_CONFIRM":
            if button == sdl.SDL_CONTROLLER_BUTTON_A:
                self.kill_and_restart()             # Return to launcher
            elif button == sdl.SDL_CONTROLLER_BUTTON_Y:
                self.kill_and_quit()                # Exit to desktop
            elif button == sdl.SDL_CONTROLLER_BUTTON_B:
                self.ui.close_alert()
                self.root.withdraw()                # Cancel, resume game
        elif button == sdl.SDL_CONTROLLER_BUTTON_A:
            if mode == "LAUNCH":
                self.ui.close_alert()
                self.proceed()
            elif mode == "EXIT":
                self.quit()
        elif button == sdl.SDL_CONTROLLER_BUTTON_B:
            self.ui.close_alert()
            self.sounds.play("back")

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
            self.cycle_sort()
        elif button == sdl.SDL_CONTROLLER_BUTTON_BACK:
            self.toggle_favorite()
        # D-pad, stick and LB/RB are polled in poll_game_navigation()

    def on_roulette_button(self, button):
        sdl = self.sdl
        if self.roulette.spinning:
            if button == sdl.SDL_CONTROLLER_BUTTON_B:
                self.close_roulette()
            return
        if button == sdl.SDL_CONTROLLER_BUTTON_A:
            self.launch_game(self.roulette.chosen)
        elif button == sdl.SDL_CONTROLLER_BUTTON_X:
            self.start_roulette(exclude=self.roulette.chosen)
        elif button == sdl.SDL_CONTROLLER_BUTTON_B:
            self.close_roulette()

    def on_players_button(self, button, which):
        sdl = self.sdl
        slot_idx = self.find_slot_by_instance(which)
        editing = slot_idx != -1 and self.assignments[slot_idx]["is_editing"]
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
        elif button == sdl.SDL_CONTROLLER_BUTTON_X:
            self.toggle_profile_edit(which)         # Enter/exit profile selection
        elif button == sdl.SDL_CONTROLLER_BUTTON_DPAD_LEFT:
            self.cycle_profile(which, -1)
        elif button == sdl.SDL_CONTROLLER_BUTTON_DPAD_RIGHT:
            self.cycle_profile(which, 1)
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
        if any(a["path"] == pad["path"] for a in self.assignments):
            return  # Same controller can't be two players

        # Restore previously selected profile for this controller, default to the first one
        profile_key = self.hid_profiles.get(pad["path"], self.default_profile)
        self.assignments.append({
            "path": pad["path"],
            "name": pad["name"],
            "profile_key": profile_key,
            "is_editing": False,
        })
        player = len(self.assignments)
        log("INFO", f"Assigned {pad['name']} -> Player {player} | Profile: {profile_key}")
        self.refresh_grid()
        self.ui.flash_slot(player - 1)
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
    def get_assigned_color(self, path):
        """Persistent colour for a controller during the session."""
        if path not in self.hid_colors:
            if not self.color_pool:
                self.color_pool = list(COLOR_POOL)
            self.hid_colors[path] = self.color_pool.pop(0)
        return self.hid_colors[path]

    def refresh_grid(self):
        """Build the view models for the player cards and hand them to the UI."""
        self.ui.refresh([{
            # Remove trailing index suffix, e.g. "Pro Controller (2)"
            "name": re.sub(r'\s*\(\d+\)$', '', a["name"]),
            "color": self.get_assigned_color(a["path"]),
            "profile": a["profile_key"],
            "editing": a["is_editing"],
        } for a in self.assignments])

    def update_hints(self):
        """Footer button hints for the current screen."""
        if self.mode == "ROULETTE":
            hints = []                          # the roulette draws its own
        elif self.mode == "GAMES":
            hints = [(("lb", "rb"), t("hint_page")), ("x", t("hint_surprise"))]
            joined = len(self.assignments)
            if joined >= 2:
                hints.append(("y", t("hint_filter_off") if self.filter_on else t("hint_filter_on", n=joined)))
            hints += [("start", t("hint_sort", mode=t("sort_" + self.sort_mode))),
                      ("back", t("hint_favorite")),
                      ("a", t("hint_play")), ("b", t("hint_back"))]
        else:
            if self.has_game_args:
                start = t("hint_launch_game")
            elif self.picker:
                start = t("hint_choose")
            else:
                start = t("hint_launch", name=self.emu.name)
            hints = [("a", t("hint_join"))]
            last = self._last_game() if self.picker else None
            if last:
                title = last["title"] if len(last["title"]) <= 26 else last["title"][:25].rstrip() + "…"
                hints.append(("y", t("hint_resume", title=title)))
            hints += [("start", start), ("back", t("hint_quit"))]
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

    def on_games_scanned(self, games):
        self.games = games
        self.games_loaded = True
        log("INFO", "Game picker", f"{len(games)} game(s)")
        if not games:
            self.picker = False                 # nothing to pick: START starts the emulator
        else:
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
        for game in self.games:
            key = self.game_key(game)
            seconds, last = (list(played.get(key) or []) + [0, 0])[:2]
            game["playtime"], game["last_played"] = seconds, last
            game["favorite"] = key in favorites
        by_title = lambda g: g["title"].lower()     # noqa: E731
        orders = {
            "recent": lambda g: (not g["favorite"], -(g["last_played"] or 0), by_title(g)),
            "az": lambda g: (not g["favorite"], by_title(g)),
            "most": lambda g: (not g["favorite"], -(g["playtime"] or 0), by_title(g)),
        }
        self.games = sorted(self.games, key=orders[self.sort_mode])

    def start_session(self, game):
        key = self.game_key(game)
        self.session = {"key": key, "start": time.monotonic(), "game": game}
        played = dict(self.emu.settings.state.get("play") or {})
        seconds = (list(played.get(key) or []) + [0])[0]
        played[key] = [seconds, int(time.time())]
        self.emu.settings.save_state(last_game=game["path"], play=played)

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
        self.emu.settings.save_state(play=played)
        log("INFO", "Play time", f"{session['key']}: +{elapsed // 60} min")
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
        self.sounds.play("toggle")
        self.ui.show_toast(t("toast_favorite_on" if key in favorites else "toast_favorite_off"), COLOR["ACCENT"])
        self._reorder_keeping(game)

    def cycle_sort(self):
        self.sort_mode = SORT_MODES[(SORT_MODES.index(self.sort_mode) + 1) % len(SORT_MODES)]
        self.emu.settings.save_state(sort=self.sort_mode)
        self.sounds.play("toggle")
        self.ui.show_toast(t("hint_sort", mode=t("sort_" + self.sort_mode)), COLOR["ACCENT"])
        self._reorder_keeping(self.shown_games[self.game_index] if self.shown_games else None)

    def _reorder_keeping(self, game):
        """Re-sort the grid, keeping `game` selected."""
        self.order_games()
        self.shown_games = []
        self._apply_filter()
        if game in self.shown_games:
            self.game_index = self.shown_games.index(game)
        self.show_games()

    # ========================================================================
    # ROULETTE
    # ========================================================================
    def start_roulette(self, exclude=None):
        joined = len(self.assignments)
        candidates = pick_candidates(self.games, joined)
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

    def close_roulette(self):
        """Back to the grid, on the drawn game if the spin finished."""
        chosen = None if self.roulette.spinning else self.roulette.chosen
        self.roulette.close()
        self.sounds.play("back")
        self.mode = "GAMES"
        if chosen is not None:
            if chosen not in self.shown_games:
                self.filter_on = False
                self.shown_games = self.games
            self.game_index = self.shown_games.index(chosen)
        self.show_games()

    # ========================================================================
    # GAME GRID
    # ========================================================================
    def _apply_filter(self):
        """Games on screen: all, or only those that take every joined player."""
        joined = len(self.assignments)
        if joined < 2:
            self.filter_on = False
        selected = self.shown_games[self.game_index] if 0 <= self.game_index < len(self.shown_games) else None
        if self.filter_on:
            self.shown_games = [g for g in self.games if (g.get("players") or 0) >= joined]
        else:
            self.shown_games = self.games
        if selected in self.shown_games:
            self.game_index = self.shown_games.index(selected)
        else:
            self.game_index = min(self.game_index, max(0, len(self.shown_games) - 1))

    def show_games(self):
        self.mode = "GAMES"
        self.nav_direction = None
        if not self.games_loaded:
            self.waiting_for_games = True
            self.ui.show_games_loading()
        else:
            if not self.shown_games:
                self.shown_games = self.games
            self._apply_filter()
            joined = len(self.assignments)
            self.ui.show_games(self.shown_games, self.game_index, players=joined,
                               filter_players=joined if self.filter_on else 0)
        self.update_hints()

    def show_players(self):
        self.mode = "PLAYERS"
        self.waiting_for_games = False
        self.ui.hide_games()
        last = self._last_game()
        if last:
            self.ui.set_backdrop(last)
        self.refresh_grid()
        self.update_hints()

    def toggle_filter(self):
        if len(self.assignments) < 2 or not self.games_loaded:
            return
        self.filter_on = not self.filter_on
        self.sounds.play("toggle")
        self.show_games()

    def move_game_selection(self, action):
        """
        Move the highlight in the grid.

        Args:
            action (str): "left"/"right" (one game, wrapping), "up"/"down"
                          (one row), "page_up"/"page_down" (one screen).
        """
        if self.mode != "GAMES" or not self.shown_games or self.ui.alert_mode or self.process:
            return
        count = len(self.shown_games)
        columns = self.ui.games_columns()
        index = self.game_index
        if action == "left":
            index = (index - 1) % count
        elif action == "right":
            index = (index + 1) % count
        elif action == "up":
            index = index - columns if index - columns >= 0 else index
        elif action == "down":
            if index + columns < count:
                index += columns
            elif index // columns < (count - 1) // columns:
                index = count - 1          # partial last row: go to its last game
        elif action == "page_up":
            index = max(0, index - self.ui.games_page())
        elif action == "page_down":
            index = min(count - 1, index + self.ui.games_page())
        if index != self.game_index:
            self.game_index = index
            self.sounds.play("move")
            joined = len(self.assignments)
            self.ui.show_games(self.shown_games, index, players=joined,
                               filter_players=joined if self.filter_on else 0)

    def poll_game_navigation(self):
        """
        Read the held direction from every pad: D-pad or left stick move one
        game/row, LB/RB a page. Holding repeats, like a console menu.
        """
        sdl = self.sdl
        action = None
        for pad in self.pads.values():
            ctrl = pad["ctrl"]
            try:
                stick_x, stick_y = sdl.get_left_x(ctrl), sdl.get_left_y(ctrl)
            except Exception:
                stick_x = stick_y = 0.0
            pressed = lambda button: sdl.SDL_GameControllerGetButton(ctrl, button)  # noqa: E731
            if pressed(sdl.SDL_CONTROLLER_BUTTON_DPAD_UP) or stick_y < -STICK_THRESHOLD:
                action = "up"
            elif pressed(sdl.SDL_CONTROLLER_BUTTON_DPAD_DOWN) or stick_y > STICK_THRESHOLD:
                action = "down"
            elif pressed(sdl.SDL_CONTROLLER_BUTTON_DPAD_LEFT) or stick_x < -STICK_THRESHOLD:
                action = "left"
            elif pressed(sdl.SDL_CONTROLLER_BUTTON_DPAD_RIGHT) or stick_x > STICK_THRESHOLD:
                action = "right"
            elif pressed(sdl.SDL_CONTROLLER_BUTTON_LEFT_SHOULDER):
                action = "page_up"
            elif pressed(sdl.SDL_CONTROLLER_BUTTON_RIGHT_SHOULDER):
                action = "page_down"
            if action:
                break

        now = time.monotonic()
        if action is None:
            self.nav_direction = None
        elif action != self.nav_direction:
            self.nav_direction = action
            self.nav_next_repeat = now + NAV_REPEAT_DELAY
            self.move_game_selection(action)
        elif now >= self.nav_next_repeat:
            self.nav_next_repeat = now + NAV_REPEAT_RATE
            self.move_game_selection(action)

    def launch_selected_game(self):
        if self.shown_games:
            self.launch_game(self.shown_games[self.game_index])

    def launch_game(self, game):
        """Back up the saves, remember the game, start it."""
        log("INFO", "Game selected", game["path"])
        self.roulette.close()
        self.sounds.play("launch")
        if self.backup_keep > 0:
            root, folders = self.emu.save_folders(game)
            backup_saves(game, root, folders, self.backup_dir, self.backup_keep)
        self.start_session(game)
        self.mode = "GAMES"
        self.force_launch(self.emu.game_command(game["path"]))

    def resume_last_game(self, instance_id):
        """Y on the player cards: straight back into the last game."""
        last = self._last_game() if self.picker and self.games_loaded else None
        if not last:
            return
        if not self.assignments:
            self.assign_player(instance_id)     # whoever pressed Y plays
        self.launch_game(last)

    # ========================================================================
    # STREAMING HOST ("add to Moonlight")
    # ========================================================================
    def offer_streaming(self):
        """Ask once per start to add the launcher to Sunshine/Vibeshine, until it is there or refused."""
        settings = self.emu.settings
        forced = getattr(self.emu, "show_streaming_prompt", False)
        if self.process or self.ui.alert_mode or self.has_game_args:
            return
        if self.assignments and not forced:
            return      # players are joining: do not put a dialog under their A presses
        if not forced and (not settings.get_bool(LAUNCHER_SECTION, "moonlight_prompt", True)
                           or (settings.state or {}).get("streaming_prompt") == "never"):
            return
        path = Sunshine.find_apps_file(settings.get(LAUNCHER_SECTION, "apps_json", "") or None)
        if not path:
            log("INFO", "No Sunshine/Vibeshine app list found")
            return
        if Sunshine.is_registered(path):
            if forced:
                self.ui.show_toast(t("toast_stream_same"), COLOR["OK"])
            return
        self._stream_apps = path
        self.sounds.play("toggle")
        self.ui.show_alert("STREAM")

    def register_streaming(self):
        path = self._stream_apps
        if not path:
            return
        self.sounds.play("select")
        cover = Sunshine.cover_path(self.emu.name)
        try:
            Sunshine.make_cover(cover, resource_path(os.path.join("assets", f"{self.emu.name}LauncherPNG.png")),
                                self.emu.name)
        except Exception as e:
            log("WARNING", "Could not write the Moonlight cover", e)
        try:
            self.on_streaming_registered(Sunshine.register(path, self.emu.name, cover))
        except PermissionError:
            if sys.platform != "win32":
                self.on_streaming_registered(None)
                return
            log("INFO", "apps.json needs administrator rights - asking Windows")
            Sunshine.register_elevated(path, lambda ok: setattr(self, "_stream_result", ok))
        except Exception as e:
            log("EXCEPTION", "Could not update the streaming host app list", e)
            self.on_streaming_registered(None)

    def on_streaming_registered(self, result):
        if result in ("added", "updated"):
            self.ui.show_toast(t("toast_stream_added"), COLOR["OK"])
        elif result == "unchanged":
            self.ui.show_toast(t("toast_stream_same"), COLOR["OK"])
        else:
            self.sounds.play("error")
            self.ui.show_toast(t("toast_stream_failed"))

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
        self._assign_keys(pads, order)

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

    def force_launch(self, cmd_args=None):
        """
        Write the controller configuration and start the emulator.

        Args:
            cmd_args (list[str] | None): Command line to run. Defaults to the
                emulator's command with this launcher's own arguments (e.g. a
                game path from Playnite) passed straight through.
        """
        self.emu.write_input_config(self.assignments, self.scan_hardware())

        if cmd_args is None:
            cmd_args = self.emu.command(sys.argv)
        log("INFO", "Launching", str(cmd_args))
        self.root.withdraw()  # Hide launcher window
        self.returning_to_launcher = False  # Clear restart flag

        if os.path.exists(self.emu.exe):
            try:
                self.process = launch(cmd_args, self.emu.env)
            except Exception as e:
                log("EXCEPTION", "Launch failed", e)
                messagebox.showerror(
                    t("error_launch_title"),
                    t("error_launch_text", name=self.emu.name, error=e)
                )
                sys.exit()
        else:
            log("ERROR", "Executable not found", self.emu.exe)
            messagebox.showerror(
                t("error_missing_title"),
                t("error_missing_text", path=self.emu.exe)
            )
            sys.exit()


def _int(text, default):
    try:
        return int(str(text).strip())
    except ValueError:
        return default
