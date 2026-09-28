"""
Eden/Games.py
Find the games shown by the launcher's game picker.

Folders come from the launcher settings (game_dirs) or, when that is empty,
from the game folders already configured in Eden ([UI] Paths\\gamedirs in
qt-config.ini), so most users need to configure nothing.

Every NSP/XCI is identified by the title ID stored inside it (Switch.py), so
updates and DLC are hidden even when the file name does not say what they
are. With the title ID, the official name and icon come from Eden's own game
list cache. Files that cannot be read fall back to name-based guesses.
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
_EXTRA_WORDS = re.compile(r"\b(update|upd|patch|dlc)\b", re.IGNORECASE)
_VERSION = re.compile(r"(\bv\d+(\.\d+)+\b|\b\d+\.\d+\.\d+\b)", re.IGNORECASE)


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


def looks_like_extra(filename):
    """
    Name-only guess for files that could not be opened: "Update", "DLC" or a
    version number ("v1.28.0", "1.3.2") mark an update or DLC.
    """
    stem = os.path.splitext(filename)[0]
    match = _TITLE_ID.search(stem)
    if match:
        return not match.group(1).lower().endswith("000")
    return bool(_EXTRA_WORDS.search(stem) or _VERSION.search(stem))


def eden_cached(cache_dir, title_id):
    """
    (name, icon path) Eden cached for a title in <cache>/game_list, written
    the first time Eden lists the game ("Cache game list metadata", on by
    default). Either may be None.
    """
    if not cache_dir or not title_id:
        return None, None
    base = os.path.join(cache_dir, "game_list", title_id.upper())
    name = None
    try:
        with open(base + ".appname.txt", "r", encoding="utf-8") as f:
            name = f.read().strip() or None
    except OSError:
        pass
    icon = base + ".jpeg"
    return name, icon if os.path.isfile(icon) and os.path.getsize(icon) > 0 else None


def _files(folder, deep):
    if deep:
        for root, subdirs, files in os.walk(folder):
            subdirs[:] = sorted(d for d in subdirs if not d.startswith("."))
            for name in sorted(files):
                yield os.path.join(root, name)
    else:
        for name in sorted(os.listdir(folder)):
            yield os.path.join(folder, name)


def cached_identify(identify, known):
    """
    Wrap identify() with a cache keyed by path, size and modification time,
    so a library is read once and later launches only stat() the files.

    Args:
        known (dict): {path: [size, mtime, title_id, kind]}, updated in place.
    """
    def lookup(path):
        try:
            stat = os.stat(path)
        except OSError:
            return None, None
        signature = [stat.st_size, int(stat.st_mtime)]
        entry = known.get(path)
        if entry and entry[:2] == signature:
            return entry[2], entry[3]
        title_id, kind = identify(path)
        known[path] = signature + [title_id, kind]
        return title_id, kind
    return lookup


def find_games(folders, header_key=None, cache_dir=None, identify=None, titledb=None, known=None):
    """
    Scan folders for games.

    Args:
        folders    (list[tuple[str, bool]]): (folder, deep_scan) pairs.
        header_key (bytes | None):  prod.keys header_key, to read title IDs.
        cache_dir  (str | None):    Eden's cache folder (names and icons).
        identify   (callable):      path -> (title_id, kind); injectable for tests.
        titledb    (TitleDb|None):  eShop facts: players, name, icon/banner URLs.
        known      (dict | None):   identify() cache, see cached_identify().
                                    Entries of files that are gone are dropped.

    Returns:
        list[dict]: one entry per game, sorted by title:
                    {"title", "path", "title_id", "image", "players",
                     "icon_url", "banner_url"}
                    Updates and DLC are dropped; when the same game exists in
                    several files the first one is kept.
    """
    if identify is None:
        from .Switch import identify as read_ids
        identify = lambda path: read_ids(path, header_key)  # noqa: E731
    if known is not None:
        identify = cached_identify(identify, known)

    games = []
    seen_files = set()
    seen_titles = {}
    for folder, deep in folders:
        if not os.path.isdir(folder):
            log("WARNING", "Game folder not found", folder)
            continue
        try:
            for path in _files(folder, deep):
                name = os.path.basename(path)
                if not name.lower().endswith(GAME_EXTENSIONS) or not os.path.isfile(path):
                    continue
                key = os.path.normcase(os.path.abspath(path))
                if key in seen_files:
                    continue
                seen_files.add(key)

                title_id, kind = identify(path) if not name.lower().endswith(".nro") else (None, None)
                if kind is None and looks_like_extra(name):
                    kind = "extra"
                if kind not in (None, "base"):
                    log("INFO", f"Skipping {kind}", name)
                    continue
                if title_id and title_id in seen_titles:
                    log("INFO", "Skipping duplicate of", f"{name} -> {seen_titles[title_id]}")
                    continue
                if title_id:
                    seen_titles[title_id] = name

                cached_name, icon = eden_cached(cache_dir, title_id)
                info = (titledb.get(title_id) if titledb and title_id else None) or {}
                games.append({
                    "title": cached_name or info.get("name") or clean_title(name),
                    "path": path,
                    "title_id": title_id,
                    "image": icon,
                    "players": info.get("players"),
                    "icon_url": info.get("icon_url"),
                    "banner_url": info.get("banner_url"),
                })
        except OSError as e:
            log("WARNING", "Could not scan game folder", f"{folder}: {e}")

    if known is not None:
        for path in [p for p in known if os.path.normcase(os.path.abspath(p)) not in seen_files]:
            del known[path]

    # Same title in several formats: tell them apart
    counts = {}
    for game in games:
        counts[game["title"].lower()] = counts.get(game["title"].lower(), 0) + 1
    for game in games:
        if counts[game["title"].lower()] > 1:
            game["title"] += f" ({os.path.splitext(game['path'])[1][1:].upper()})"

    games.sort(key=lambda g: g["title"].lower())
    return games
