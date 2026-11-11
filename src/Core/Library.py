"""
Core/Library.py
Which games the grid shows, and in which order: pure functions on the game
dicts, no UI.

    tabs        All · Favourites · Recent · For N players · Switch 2, the
                ones that have games (LB / RB in the grid)
    continue    on the "All" tab, the games played lately come first, in a
                row of their own ("Continue playing"), and then every game
                in the chosen order - the same game can be in both
    snapshot    the list of games of the last scan, kept so the grid can be
                shown at once on the next start while the folders are
                scanned again in the background
"""

import os
import time

TABS = ("all", "nx2", "favorites", "recent", "players")
CONTINUE_MAX = 5                # games in the "Continue playing" row
CONTINUE_DAYS = 30              # played this recently to be there
CONTINUE_MIN_GAMES = 9          # a shorter list gets no separate row
SNAPSHOT_KEYS = ("title", "path", "title_id", "image", "players", "players_db", "icon_url", "banner_url", "nx2",
                 "custom_input")
SNAPSHOT_VERSION = 1


def visible(games, show_hidden=False):
    return [g for g in games if show_hidden or not g.get("hidden")]


def available_tabs(games, joined, show_hidden=False):
    """
    The tabs over the grid, in order: All and Switch 2 always (an empty
    Switch 2 tab says how to fill it), Favourites once there is one, Recent
    once a game was played, For N players once two or more joined.
    """
    games = visible(games, show_hidden)
    tabs = ["all", "nx2"]
    if any(g.get("favorite") for g in games):
        tabs.append("favorites")
    if any(g.get("last_played") for g in games):
        tabs.append("recent")
    if joined >= 2:
        tabs.append("players")
    return tabs


def tab_games(games, tab, joined, show_hidden=False, now=None, continue_row=True):
    """
    The games on screen for `tab`, in order, and how many of them form the
    "Continue playing" row at the start (0 when there is none).

    Args:
        games (list[dict]): every game, already in the chosen order, with
                            "favorite", "last_played", "hidden" filled in.

    Returns:
        (list[dict], int)
    """
    shown = visible(games, show_hidden)
    if tab == "favorites":
        return [g for g in shown if g.get("favorite")], 0
    if tab == "recent":
        return sorted((g for g in shown if g.get("last_played")), key=lambda g: -g["last_played"]), 0
    if tab == "players":
        return [g for g in shown if (g.get("players") or 0) >= joined], 0
    if tab == "nx2":
        return [g for g in shown if g.get("nx2")], 0
    if not continue_row or len(shown) < CONTINUE_MIN_GAMES:
        return shown, 0
    now = time.time() if now is None else now
    recent = sorted((g for g in shown if g.get("last_played") and now - g["last_played"] <= CONTINUE_DAYS * 86400),
                    key=lambda g: -g["last_played"])[:CONTINUE_MAX]
    return recent + shown, len(recent)


def snapshot(games):
    """What is kept of a scan: the fields the grid needs, nothing that is worked out later."""
    return {"version": SNAPSHOT_VERSION,
            "games": [{key: game.get(key) for key in SNAPSHOT_KEYS} for game in games]}


def from_snapshot(data, exists=os.path.exists):
    """Games from snapshot(), the ones whose file is still there; [] when there is nothing usable."""
    if not isinstance(data, dict) or data.get("version") != SNAPSHOT_VERSION:
        return []
    games = []
    for entry in data.get("games") or []:
        if not isinstance(entry, dict) or not entry.get("path") or not entry.get("title"):
            continue
        if exists(entry["path"]):
            games.append({key: entry.get(key) for key in SNAPSHOT_KEYS})
    return games
