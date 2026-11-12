"""
Core/AppLaunch.py
Starting and closing the emulator: the launch screen and the game loading
behind it, the emulator's dialogs, the close menu, writing the controller
configuration (a part of LauncherApp, see Core/App.py).
"""


import os


import sys

import time
from tkinter import messagebox


from .Backup import backup_saves

from .I18n import t
from .Log import log


from .Process import answer_dialog, bring_to_front, find_dialogs, game_shown, launch, request_close


from .Ui import COLOR, SPLASH_EXIT_MS, player_color, split_layout, split_mode


from .AppShared import (CLOSE_TIMEOUT_S, INSTANT_EXIT_S, LOADING_MAX_S, LOADING_POLL_S, QUICK_EXIT_S,
                        SPLASH_LAUNCH_MS, SPLASH_MS)


class LaunchMixin:

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
        if self._loading or self.ui._splash is not None:
            self._end_loading()
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
        if self._loading or self.ui._splash is not None:
            self._end_loading()             # gone while loading: no launch screen left behind
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
        if self.process and not self._loading:
            self.root.withdraw()
        if dialog:
            answer_dialog(dialog[0], accept)
            log("INFO", "Dialog answered", f"{'OK' if accept else 'Cancel'}: {dialog[1]}")

    def launch_game(self, game, from_roulette=False, firmware_checked=False):
        """Show who plays where for a moment, then start the game."""
        log("INFO", "Game selected", game["path"])
        self.roulette.close()
        self.menu.close()
        if game.get("nx2") and not self.emulator_for(game).supports_nx2():
            # Eden cannot boot a Switch 2 game: say which emulator it needs instead of failing
            log("WARNING", "No Switch 2 emulator for", game["title"])
            self.sounds.play("error")
            self._alert_then = None
            self.ui.show_alert("GAME_FAILED", title=t("nx2_missing_title"), text=t("nx2_missing_text"),
                               lines=[game["title"]])
            return
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
        if self.ui.splash_waits:
            self.ui.splash_ms = SPLASH_LAUNCH_MS    # the screen stays: the cover goes only when the game shows
            self._splash_job = self.root.after(SPLASH_LAUNCH_MS, self._launch_now, game, from_roulette)
        else:
            self.ui.splash_ms = SPLASH_MS
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
        waits = self.ui.splash_waits and self.ui._splash is not None and self.ui.animations
        self.force_launch(emulator.game_command(game["path"]), emulator, keep_splash=waits)
        if not self._loading:
            self.ui.hide_splash()

    # ------------------------------------------------------------------
    # The launch screen over the emulator until the game is on screen
    # ------------------------------------------------------------------
    def _watch_loading(self, now):
        loading = self._loading
        if now < loading["next"]:
            return
        loading["next"] = now + LOADING_POLL_S
        if game_shown(self.process):
            log("INFO", "The game is on screen", f"after {now - loading['since']:.1f} s")
            self._reveal_game()
        elif now - loading["since"] > LOADING_MAX_S:
            log("WARNING", "The game did not show in time - showing the emulator anyway")
            self._reveal_game()

    def _reveal_game(self):
        """The game drew its first frame: the cover sinks into the dark, then the emulator is in front."""
        self._loading = None
        self.ui.splash_exit()
        self.root.after(SPLASH_EXIT_MS + 40, self._step_aside)

    def _step_aside(self):
        if not self.process or self._loading:
            return
        # The emulator first, while the launcher is still the foreground window: Windows lets
        # only the foreground program hand the front to another one
        bring_to_front(self.process)
        self._end_loading()
        self.root.withdraw()

    def _end_loading(self):
        """The launch screen is no longer kept over the emulator."""
        self._loading = None
        try:
            self.root.attributes("-topmost", False)
        except Exception:
            pass
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

    def force_launch(self, cmd_args=None, emulator=None, keep_splash=False):
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
        if keep_splash:
            # The launch screen stays in front of the emulator while it loads the game
            self.root.attributes("-topmost", True)
            self.root.lift()
        else:
            self.root.withdraw()  # Hide launcher window
        self.returning_to_launcher = False  # Clear restart flag
        self.ui.set_dim(False)

        if os.path.exists(emulator.exe):
            try:
                self.process = launch(cmd_args, emulator.env or self.emu.env)
                self.running_emu = emulator
                self.launched_at = time.monotonic()
                self._dialogs_seen = set()
                if keep_splash:
                    self._loading = {"since": self.launched_at, "next": 0.0}
                    self.ui.splash_loading()
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
