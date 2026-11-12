"""
Core/AppPlayers.py
The controllers: hot-plug, the players who joined, their icons, batteries
and button layouts (a part of LauncherApp, see Core/App.py).
"""

import ctypes
import re
import time

from . import Glyphs
from .I18n import t
from .Log import log
from .Ui import COLOR, player_color
from .AppShared import (DPAD_BUTTONS, HELD_BUTTONS, HELD_POLL_S, LOW_BATTERY, MAX_PLAYERS, RUMBLE_GAP_MS,
                        RUMBLE_PULSE_MS, RUMBLE_STRENGTH, TRIGGER_PRESSED, match_reconnects)


class PlayersMixin:

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
            if self.process and level and level[0] < LOW_BATTERY and not level[1]:
                # In the game: noted, and told when the players are back in the launcher
                self._low_batteries[pad["path"]] = min(level[0], self._low_batteries.get(pad["path"], 100))
        if changed:
            self.refresh_grid()

    def report_low_batteries(self):
        """Back from a game: name the players whose controller needs charging."""
        low, self._low_batteries = self._low_batteries, {}
        for assignment in self.assignments:
            battery = self._battery_of(assignment["path"])
            if battery and battery[0] < LOW_BATTERY and not battery[1]:
                low[assignment["path"]] = min(battery[0], low.get(assignment["path"], 100))
        players = [(i, low[a["path"]]) for i, a in enumerate(self.assignments) if a["path"] in low]
        if not players:
            return
        log("INFO", "Low batteries", ", ".join(f"P{i + 1} {level}%" for i, level in players))
        self.ui.show_toast(t("toast_battery_low", players=", ".join(f"P{i + 1} {level}%" for i, level in players)),
                           player_color(players[0][0]))

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
