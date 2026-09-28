"""
Eden/Games.py
Find the games shown by the launcher's game picker.

Folders come from the launcher settings (game_dirs) or, when that is empty,
from the game folders already configured in Eden ([UI] Paths\\gamedirs in
qt-config.ini), so most users need to configure nothing.

Titles come from file names, cleaned up. Update and DLC files that sit next
to the base game are hidden: a scene-style name carries the title ID, and
base games are the only IDs ending in "000" (updates end in "800", DLC in
anything else).
"""

import os
import re

from Core.Log import log

from .Ini import unquote

GAME_EXTENSIONS = (".nsp", ".xci", ".nro")

# Eden's built-in pseudo folders in the game dir list
_VIRTUAL_DIRS = {"SDMC", "UserNAND", "SysNAND"}

_TITLE_ID = re.compile(r"\[([0-9A-Fa-f]{16})\]")
_BRACKETS = re.compile(r"\[[^\]]*\]")
_SPACES = re.compile(r"\s+")


def eden_game_dirs(ui_items):
    """
    Game folders configured in Eden.

    Args:
        ui_items (dict): [UI] section of qt-config.ini (IniFile.items("UI")).

    Returns:
        list[tuple[str, bool]]: (folder, deep_scan) pairs.
    """
    try:
        size = int(unquote(ui_items.get("Paths\\gamedirs\\size", "0")))
    except ValueError:
        size = 0

    dirs = []
    for i in range(1, size + 1):
        prefix = f"Paths\\gamedirs\\{i}\\"
        path = unquote(ui_items.get(prefix + "path", "")).strip()
        if not path or path in _VIRTUAL_DIRS:
            continue
        # Config::ReadBooleanSetting: "\default=true" means the default (false)
        deep = False
        if ui_items.get(prefix + "deep_scan\\default", "false").strip().lower() != "true":
            deep = unquote(ui_items.get(prefix + "deep_scan", "false")).strip().lower() == "true"
        dirs.append((os.path.normpath(path), deep))
    return dirs


def clean_title(filename):
    """"Super Game [0100ABCD12340000][v0] (USA).nsp" -> "Super Game (USA)"."""
    stem = os.path.splitext(filename)[0]
    title = _BRACKETS.sub(" ", stem).replace("_", " ")
    title = _SPACES.sub(" ", title).strip(" -.")
    return title or stem


def is_base_game(filename):
    """False for updates and DLC recognisable by their title ID."""
    match = _TITLE_ID.search(filename)
    return match is None or match.group(1).lower().endswith("000")


def _files(folder, deep):
    if deep:
        for root, subdirs, files in os.walk(folder):
            subdirs[:] = sorted(d for d in subdirs if not d.startswith("."))
            for name in sorted(files):
                yield os.path.join(root, name)
    else:
        for name in sorted(os.listdir(folder)):
            yield os.path.join(folder, name)


def find_games(folders):
    """
    Scan folders for games.

    Args:
        folders (list[tuple[str, bool]]): (folder, deep_scan) pairs.

    Returns:
        list[dict]: [{"title", "path"}] sorted by title, one entry per file;
                    titles shared by several files get the extension appended.
    """
    games = []
    seen = set()
    for folder, deep in folders:
        if not os.path.isdir(folder):
            log("WARNING", "Game folder not found", folder)
            continue
        try:
            for path in _files(folder, deep):
                name = os.path.basename(path)
                if not name.lower().endswith(GAME_EXTENSIONS) or not os.path.isfile(path):
                    continue
                if not is_base_game(name):
                    continue
                key = os.path.normcase(os.path.abspath(path))
                if key in seen:
                    continue
                seen.add(key)
                games.append({"title": clean_title(name), "path": path})
        except OSError as e:
            log("WARNING", "Could not scan game folder", f"{folder}: {e}")

    # Same title in several formats: tell them apart
    counts = {}
    for game in games:
        counts[game["title"].lower()] = counts.get(game["title"].lower(), 0) + 1
    for game in games:
        if counts[game["title"].lower()] > 1:
            game["title"] += f" ({os.path.splitext(game['path'])[1][1:].upper()})"

    games.sort(key=lambda g: g["title"].lower())
    return games
