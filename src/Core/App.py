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
import sys
import threading
import time

from . import Library
from .Achievements import Achievements
from .Art import ArtLibrary
from .Build import BUILD
from .I18n import t
from .Log import log
from .Menu import Menu
from .Paths import base_dir
from .Process import can_watch_windows, dialog_open
from .Roulette import Roulette
from .Settings import DEFAULT_KILL_COMBO, LAUNCHER_SECTION, parse_combo
from .Sound import Sounds
from .Theme import apply_theme
from .Ui import COLOR, LauncherUi
from .Update import UpdateCheck
from .AppLaunch import LaunchMixin
from .AppLibrary import LibraryMixin
from .AppMenus import MenusMixin
from .AppPlayers import PlayersMixin
from .AppShared import (ALERT_GUARD_S, BATTERY_POLL_S, COMBO_GLYPHS, DIALOG_POLL_S, MAX_LOOP_ERRORS_LOGGED,
                        POLL_INTERVAL_MS, SORT_MODES, SPLASH_MS, _int, match_reconnects)


__all__ = ["LauncherApp", "match_reconnects"]       # match_reconnects: from AppShared, for the tests


class LauncherApp(PlayersMixin, LibraryMixin, LaunchMixin, MenusMixin):
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
        self.ui.light_games = settings.get(LAUNCHER_SECTION, "grid_style", "light").lower() != "dark"
        self.ui.animations = settings.get_bool(LAUNCHER_SECTION, "animations", True)
        self.ui.splash_ms = SPLASH_MS
        # The launch screen stays over the emulator until the game is on screen (Windows: its windows show it)
        self.ui.splash_waits = can_watch_windows() and settings.get_bool(LAUNCHER_SECTION, "loading_screen", True)
        self._loading = None                # {"since", "next"} while the game loads behind the launch screen
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
        self._low_batteries = {}            # path -> lowest level read during the game (under LOW_BATTERY)
        self._updater = None                # Core.Update.Updater while a newer build downloads
        self._update_shown = None           # the download percentage on screen
        self._update_check = None           # thread of a "check now" from the settings
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
        elif self.ui.alert_mode == "BUSY":
            return
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
        elif self.ui.alert_mode == "BUSY":
            return
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
            if self.process and self._loading:
                self._watch_loading(now)
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
            if not self._loading:
                self.root.withdraw()
        if self._updater or self._update_check:
            self._watch_update()
        if now >= self._battery_next:                   # during the game too: low batteries are told after it
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
                if not self._loading:
                    self.root.withdraw()            # Cancel, resume game
        elif mode == "BUSY":
            return                                  # an update installing: nothing to answer
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
