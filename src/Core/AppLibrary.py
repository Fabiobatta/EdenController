"""
Core/AppLibrary.py
The games: scanning the library, play time and favourites, achievements,
the roulette and the game grid with its tabs (a part of LauncherApp,
see Core/App.py).
"""


import random


import threading
import time


from . import Library, Stats
from .Achievements import ACHIEVEMENTS


from .I18n import playtime_label, t
from .Log import log


from .Roulette import pick_candidates


from .Ui import COLOR, player_color


from .AppShared import (MIN_SESSION_S, NAV_REPEAT_DELAY, NAV_REPEAT_RATE, STICK_THRESHOLD, TICK_RUMBLE,
                        TRIGGER_PRESSED, WIN_RUMBLE)


class LibraryMixin:

    # ========================================================================
    # GAME LIBRARY
    # ========================================================================
    def _scan_games(self):
        """Background thread: list the games and find their pictures on disk."""
        try:
            games = self.emu.list_games()
            # [Players] in the settings file corrects the eShop player counts
            for game in games:
                self.art.resolve(game)
                game["players_db"] = game.get("players")
                self._apply_players_override(game)
        except Exception as e:
            log("EXCEPTION", "Game scan failed", e)
            games = []
        self._scan_result = games               # picked up by update_loop

    @staticmethod
    def players_key(game):
        """The [Players] key of a game: its title ID, else its title."""
        return (game.get("title_id") or game["title"]).replace("=", " ").replace(";", " ").strip()

    def players_override(self, game):
        """The player count set for `game` in [Players] (the game menu or by hand), or None."""
        overrides = self.emu.settings.sections.get("players", {})
        value = overrides.get(self.players_key(game).lower()) or overrides.get(game["title"].lower())
        return int(value) if value and value.strip().isdigit() and int(value) > 0 else None

    def _apply_players_override(self, game):
        """[Players] corrects or fills in the eShop player count (Switch 2 games often have none)."""
        value = self.players_override(game)
        if value:
            game["players"] = value

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
            for game in games:
                self.art.resolve(game)
                self._apply_players_override(game)
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
        # The recently played games have their own tab, not a row on top
        games, pinned = Library.tab_games(self.games, self.tab, joined, self.show_hidden, continue_row=False)
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
