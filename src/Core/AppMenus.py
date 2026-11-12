"""
Core/AppMenus.py
The drawer menus: settings, game folders, emulators, a game's menu and its
save backups (a part of LauncherApp, see Core/App.py).
"""

import os
import sys
import threading
import time

from .Backup import backup_saves, list_backups, restore
from .Build import BUILD
from .I18n import STRINGS, last_played_label, players_label, playtime_label, set_language, t
from .Log import log
from .Paths import base_dir
from .Settings import LAUNCHER_SECTION
from .Sound import Sounds
from .Theme import ACCENT_NAMES, THEME_NAMES
from .Ui import COLOR, SPLIT_MODES, split_mode
from .Process import can_watch_windows, start_detached
from .Update import Updater
from .AppShared import IDLE_DIM_CHOICES, SORT_MODES


class MenusMixin:

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

        def grid_style(value):
            self.ui.light_games = value != "dark"
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
            row(launcher, "grid_style", ("light", "dark"), "light", grid_style),
            row(launcher, "accent", ACCENT_NAMES, "violet", lambda value: self.ui.set_theme(accent=value)),
            row(launcher, "background_motion", on_off, "true", motion),
            row(launcher, "animations", on_off, "true",
                lambda value: setattr(self.ui, "animations", value == "true")),
            row(launcher, "loading_screen", on_off, "true",
                lambda value: setattr(self.ui, "splash_waits", can_watch_windows() and value == "true")),
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
            {"label": t("health_title"), "action": self.open_health},
            row(launcher, "check_updates", on_off, "true", updates),
        ]
        if self.update.newer:
            rows.append({"label": t("upd_install", n=self.update.newer), "color": COLOR["OK"],
                         "action": self._ask_update})
        elif BUILD > 0:
            rows.append({"label": t("upd_check_now"), "action": self._check_updates_now})
        rows.append({"label": t("settings_version", version=self._version_text()), "kind": "info"})
        return rows

    # ------------------------------------------------------------------
    # Updating the launcher from the controller
    # ------------------------------------------------------------------
    @staticmethod
    def can_self_update():
        """Only the Windows exe replaces itself (from source there is nothing to swap)."""
        return sys.platform == "win32" and bool(getattr(sys, "frozen", False))

    def _check_updates_now(self):
        if self._update_check:
            return
        self.ui.show_toast(t("upd_checking"), COLOR["ACCENT"])
        self._update_check = threading.Thread(target=self.update.run, name="update-check", daemon=True)
        self._update_check.start()

    def _ask_update(self):
        build = self.update.newer
        if not self.can_self_update():
            self.ui.show_toast(t("upd_only_exe"), COLOR["WARN"])
            return
        self.confirm(t("upd_title", n=build), t("upd_text", n=build), self._start_update,
                     buttons=(("a", "upd_go"), ("b", "alert_back")))

    def _start_update(self):
        build = self.update.newer
        self.menu.close()
        self._after_menu()
        log("INFO", "Update requested", f"build {build}")
        self._updater = Updater(build, sys.executable)
        self._updater.start()
        self._update_shown = 0
        self.ui.show_alert("BUSY", title=t("upd_title", n=build), text=t("upd_progress", pct=0))

    def _watch_update(self):
        """Called by the main loop while an update is checked for or downloads."""
        check = self._update_check
        if check and not check.is_alive():
            self._update_check = None
            if self.update.newer:
                self.ui.show_toast(t("toast_update", n=self.update.newer), COLOR["ACCENT"])
                self._set_brand()
            else:
                self.ui.show_toast(t("upd_latest"), COLOR["OK"])
            if self.mode == "MENU":
                self.menu.refresh()
        updater = self._updater
        if not updater:
            return
        if updater.state == "downloading":
            pct = int(updater.progress * 100)
            if pct != self._update_shown:
                self._update_shown = pct
                self.ui.show_alert("BUSY", title=t("upd_title", n=updater.build), text=t("upd_progress", pct=pct))
        elif updater.state == "ready":
            self._updater = None
            try:
                updater.install()
                start_detached([updater.exe] + sys.argv[1:])
            except Exception as e:
                log("EXCEPTION", "Could not install the update", e)
                self._update_failed(updater.build, str(e))
                return
            log("INFO", "Update installed - restarting", f"build {updater.build}")
            self.ui.show_alert("BUSY", title=t("upd_title", n=updater.build), text=t("upd_restarting"))
            self.root.update()
            self.quit()
        elif updater.state == "failed":
            self._updater = None
            self._update_failed(updater.build, updater.error)

    def _update_failed(self, build, error):
        self.sounds.play("error")
        self._alert_then = None
        self.ui.show_alert("GAME_FAILED", title=t("upd_failed"), text=t("upd_failed_text"), lines=[error or "?"])

    # ------------------------------------------------------------------
    # The health check (Settings > Check)
    # ------------------------------------------------------------------
    def open_health(self):
        self._open_menu(t("health_title"), self._health_rows, subtitle=t("health_subtitle"))

    def _health_rows(self):
        colors = {"ok": COLOR["OK"], "warn": COLOR["WARN"], "bad": COLOR["BAD"]}
        marks = {"ok": "✓", "warn": "!", "bad": "✗"}
        nothing = lambda: None                                  # noqa: E731  (selectable, so the list scrolls)

        def check(label, state, value, action=None):
            return {"label": label, "value": f"{value}  {marks.get(state, '')}".strip(), "action": action or nothing,
                    "value_color": colors.get(state)}

        rows = []
        games = self.games or []
        nx2_games = [g for g in games if g.get("nx2")]
        for emulator in self.emu.variants:
            role = t("hc_role_nx2") if emulator.supports_nx2() else t("hc_role_main")
            rows.append({"label": f"{emulator.display_name} · {role}", "kind": "header"})
            for key, state, detail in emulator.health():
                value = {"ok": t(f"hc_{key}_ok"), "warn": t(f"hc_{key}_warn"), "bad": t(f"hc_{key}_bad")}[state]
                if key == "program" and detail and state == "ok":
                    value = detail
                rows.append(check(t(f"hc_{key}"), state, value))
        if nx2_games:
            able = [v for v in self.emu.variants if v.supports_nx2()]
            rows.append(check(t("hc_nx2_games", n=len(nx2_games)), "ok" if able else "bad",
                              able[0].display_name if able else t("hc_nx2_missing"), self.open_emulators))

        rows.append({"label": t("settings_games"), "kind": "header"})
        folders, _ = self.emu.game_folders()
        reachable = sum(1 for folder, _deep in folders if os.path.isdir(folder))
        rows.append(check(t("opt_game_dirs"), "ok" if folders and reachable == len(folders) else
                          "bad" if not reachable else "warn",
                          t("hc_folders", ok=reachable, n=len(folders)), self.open_folders))
        no_players = sum(1 for g in games if not g.get("players"))
        rows.append(check(t("hc_games"), "ok" if games else "warn",
                          t("hc_games_count", n=len(games), nx2=len(nx2_games))))
        rows.append(check(t("hc_no_players"), "ok" if not no_players else "warn", str(no_players)))

        rows.append({"label": t("hc_controllers"), "kind": "header"})
        assigned = {a["path"]: i for i, a in enumerate(self.assignments)}
        for pad in self.pads.values():
            player = assigned.get(pad["path"])
            battery = pad.get("battery")
            parts = [f"P{player + 1}" if player is not None else t("hc_not_playing")]
            if battery:
                parts.append(f"{battery[0]}%")
            state = "warn" if battery and battery[0] < 15 and not battery[1] else "ok"
            rows.append(check(pad["name"], state, " · ".join(parts)))
        if not self.pads:
            rows.append(check(t("hc_controllers"), "warn", t("hc_no_controllers")))

        rows.append({"label": t("settings_launcher"), "kind": "header"})
        if self.update.newer:
            rows.append(check(t("hc_build", n=BUILD or "-"), "warn", t("hc_build_newer", n=self.update.newer),
                              self._ask_update))
        else:
            rows.append(check(t("hc_build", n=BUILD or "-"), "ok", t("hc_build_latest")))
        rows.append({"label": t("hc_logs", path=self.emu.log_dir()), "kind": "info"})
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

        # Local players: the eShop count, or one set here (Switch 2 games often have none)
        counts = (None, 1, 2, 3, 4, 6, 8)
        own = self.players_override(game)
        own = own if own in counts else None
        known = game.get("players_db")

        def players(delta):
            picked = counts[(counts.index(own) + delta) % len(counts)]
            if game["title"].lower() != self.players_key(game).lower() and \
                    game["title"].lower() in settings.sections.get("players", {}):
                settings.set("Players", game["title"], None)        # one written by hand under the title
            settings.set("Players", self.players_key(game), picked)
            game["players"] = picked or known
            self.ui.grid.forget(game)
        rows.append({"label": t("gm_players"), "change": players,
                     "value": str(own) if own else t("gm_auto", what=players_label(known) if known else "?")})

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
