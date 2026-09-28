"""
Core/Covers.py
Portrait cover art for the game grid.

Covers live in a "covers" folder next to the launcher, named after the
game's ID or title:

    covers/0100152000022000.png
    covers/Mario Kart 8 Deluxe.jpg

Users can drop their own images there (any size, 2:3 looks best). With a
SteamGridDB API key (free, https://www.steamgriddb.com/profile/preferences/api)
missing covers are downloaded in the background, once per game.
"""

import json
import os
import re
import threading
import urllib.parse
import urllib.request

from .Log import log

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
SGDB_API = "https://www.steamgriddb.com/api/v2"
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(title):
    """A title usable as a file name on every OS."""
    return _UNSAFE.sub("", title).strip().rstrip(".") or "game"


def find_cover(covers_dir, game):
    """Path of the cover for a game (by ID first, then title), or None."""
    if not covers_dir or not os.path.isdir(covers_dir):
        return None
    stems = [s for s in (game.get("title_id"), safe_name(game["title"])) if s]
    for stem in stems:
        for ext in IMAGE_EXTENSIONS:
            path = os.path.join(covers_dir, stem + ext)
            if os.path.isfile(path):
                return path
    return None


class CoverDownloader:
    """
    Fetch missing covers from SteamGridDB on a background thread.

    Tk is not thread-safe, so the thread only writes files and appends to
    `self.finished`; the UI loop picks results up with take_finished().
    Games already looked up without success are remembered in `tried` (the
    launcher state) so they are not searched again on every start.
    """

    def __init__(self, api_key, covers_dir, tried=None):
        self.api_key = api_key
        self.covers_dir = covers_dir
        self.tried = set(tried or [])
        self.finished = []          # games whose cover just arrived
        self._lock = threading.Lock()

    def _get(self, url):
        request = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "EdenLauncher",
        })
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.read()

    def _fetch(self, game):
        term = urllib.parse.quote(game["title"])
        found = json.loads(self._get(f"{SGDB_API}/search/autocomplete/{term}"))
        results = found.get("data") or []
        if not results:
            return None
        grids = json.loads(self._get(
            f"{SGDB_API}/grids/game/{results[0]['id']}?dimensions=600x900&types=static"))
        images = grids.get("data") or []
        if not images:
            return None
        url = images[0]["url"]
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
        ext = ext if ext in IMAGE_EXTENSIONS else ".png"
        path = os.path.join(self.covers_dir, (game.get("title_id") or safe_name(game["title"])) + ext)
        data = self._get(url)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def _run(self, games):
        os.makedirs(self.covers_dir, exist_ok=True)
        for game in games:
            key = game.get("title_id") or game["title"]
            try:
                path = self._fetch(game)
            except Exception as e:
                log("WARNING", "Cover download failed", f"{game['title']}: {e}")
                continue    # network error: try again next time
            if path:
                log("INFO", "Cover downloaded", f"{game['title']} -> {os.path.basename(path)}")
                with self._lock:
                    game["cover"] = path
                    self.finished.append(game)
            else:
                log("INFO", "No cover on SteamGridDB", game["title"])
                self.tried.add(key)

    def start(self, games):
        missing = [g for g in games if not g.get("cover")
                   and (g.get("title_id") or g["title"]) not in self.tried]
        if not missing:
            return
        log("INFO", "Downloading covers", f"{len(missing)} game(s)")
        threading.Thread(target=self._run, args=(missing,), daemon=True).start()

    def take_finished(self):
        with self._lock:
            done, self.finished = self.finished, []
        return done
