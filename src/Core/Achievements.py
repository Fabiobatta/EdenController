"""
Core/Achievements.py
Small rewards for using the launcher: "Serata in 4", "Maratona"...

Pure bookkeeping on the settings state file - no UI. Core/App.py reports
what happens (a game starts, a game ends, the roulette picks, favourites
change); each call returns the achievements it unlocked, which the App
announces. Unlocked ones are stored as

    state["achievements"] = {id: {"at": epoch, "game": title or None}}
    state["roulette_picks"] = {game key: times the roulette picked it}

Play time comes from state["play"] ({key: [seconds, last epoch]}), kept by
the App.
"""

import time

# Every achievement, in the order of the list screen; names and descriptions
# are the I18n strings "ach_<id>" and "ach_<id>_desc"
ACHIEVEMENTS = (
    "first_game", "party2", "party4", "party8", "roulette", "destiny", "variety",
    "collector", "favorites", "marathon", "devoted", "veteran", "century", "night_owl",
)

MARATHON_S = 3 * 3600
DEVOTED_S = 10 * 3600
VETERAN_S = 50 * 3600
CENTURY_S = 100 * 3600
COLLECTOR_GAMES = 10
VARIETY_GAMES = 3               # different games in one launcher session
DESTINY_PICKS = 3               # the roulette picked the same game this often
FAVORITES = 5
NIGHT_HOURS = range(2, 5)       # 02:00-04:59


class Achievements:
    def __init__(self, settings):
        self.settings = settings
        self.session_games = set()          # games played since the launcher started

    # ------------------------------------------------------------------
    @property
    def unlocked(self):
        return dict((self.settings.state or {}).get("achievements") or {})

    def _unlock(self, ids, game=None, now=None):
        """Record ids not unlocked yet; return those."""
        done = self.unlocked
        new = [i for i in ids if i not in done]
        if new:
            stamp = int(time.time() if now is None else now)
            for i in new:
                done[i] = {"at": stamp, "game": game["title"] if game else None}
            self.settings.save_state(achievements=done)
        return new

    def _played(self):
        return (self.settings.state or {}).get("play") or {}

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    def game_started(self, game, key, players, from_roulette=False, now=None):
        now = time.time() if now is None else now
        self.session_games.add(key)
        ids = ["first_game"]
        if players >= 2:
            ids.append("party2")
        if players >= 4:
            ids.append("party4")
        if players >= 8:
            ids.append("party8")
        if from_roulette:
            ids.append("roulette")
        if len(self.session_games) >= VARIETY_GAMES:
            ids.append("variety")
        if len(self._played()) >= COLLECTOR_GAMES:
            ids.append("collector")
        if time.localtime(now).tm_hour in NIGHT_HOURS:
            ids.append("night_owl")
        return self._unlock(ids, game, now)

    def game_ended(self, game, key, elapsed, now=None):
        now = time.time() if now is None else now
        played = self._played()
        seconds = (list(played.get(key) or []) + [0])[0]
        total = sum((list(v) + [0])[0] for v in played.values())
        ids = []
        if elapsed >= MARATHON_S:
            ids.append("marathon")
        if seconds >= DEVOTED_S:
            ids.append("devoted")
        if seconds >= VETERAN_S:
            ids.append("veteran")
        if total >= CENTURY_S:
            ids.append("century")
        if time.localtime(now).tm_hour in NIGHT_HOURS:
            ids.append("night_owl")
        return self._unlock(ids, game, now)

    def roulette_picked(self, game, key):
        picks = dict((self.settings.state or {}).get("roulette_picks") or {})
        picks[key] = picks.get(key, 0) + 1
        self.settings.save_state(roulette_picks=picks)
        return self._unlock(["destiny"], game) if picks[key] >= DESTINY_PICKS else []

    def favorites_changed(self, count):
        return self._unlock(["favorites"]) if count >= FAVORITES else []
