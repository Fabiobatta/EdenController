"""
Core/Art.py
Pictures for the game grid and the backgrounds.

Per game (keys of the game dict), best source first:

    cover       portrait art for the tile
                    covers/<id or title>.<png|jpg|jpeg|webp>   (user files)
                    SteamGridDB, if steamgriddb_api_key is set  (downloaded)
    background  landscape art, blurred behind the UI
                    covers/backgrounds/<id or title>.<ext>      (user files)
                    game["banner_url"] (eShop banner)           (downloaded)
    image       square icon (the emulator's own, else game["icon_url"])

Downloads run on one background thread, once per picture: files land in
the covers folder and are reused from then on. Tk is not thread-safe, so
the thread only writes files and queues the games it updated; the UI loop
collects them with take_finished().
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
MAX_DOWNLOAD = 15 * 1024 * 1024
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(title):
    """A title usable as a file name on every OS."""
    return _UNSAFE.sub("", title).strip().rstrip(".") or "game"


def _stems(game):
    return [s for s in (game.get("title_id"), safe_name(game["title"])) if s]


def find_image(folder, game):
    """Picture named after the game (ID first, then title) in folder, or None."""
    if not folder or not os.path.isdir(folder):
        return None
    for stem in _stems(game):
        for ext in IMAGE_EXTENSIONS:
            path = os.path.join(folder, stem + ext)
            if os.path.isfile(path):
                return path
    return None


def _cached_download(url, folder):
    """Where a downloaded URL is stored (its file name is a content hash)."""
    name = os.path.basename(urllib.parse.urlparse(url).path)
    return os.path.join(folder, name) if name else None


class ArtLibrary:
    """Finds local pictures and fetches missing ones in the background."""

    def __init__(self, covers_dir, eshop=True, sgdb_key=None, sgdb_missing=()):
        self.covers_dir = covers_dir
        self.backgrounds_dir = os.path.join(covers_dir, "backgrounds")
        self.eshop_dir = os.path.join(covers_dir, "eshop")
        self.eshop = eshop
        self.sgdb_key = sgdb_key
        self.sgdb_missing = set(sgdb_missing)   # searched before, nothing found
        self._finished = []
        self._lock = threading.Lock()
        self.busy = False

    # ------------------------------------------------------------------
    # Local files
    # ------------------------------------------------------------------
    def resolve(self, game):
        """Fill cover/background/image from files already on disk."""
        game["cover"] = find_image(self.covers_dir, game)
        game["background"] = find_image(self.backgrounds_dir, game)
        if not game["background"] and game.get("banner_url"):
            path = _cached_download(game["banner_url"], self.eshop_dir)
            game["background"] = path if path and os.path.isfile(path) else None
        if not game.get("image") and game.get("icon_url"):
            path = _cached_download(game["icon_url"], self.eshop_dir)
            game["image"] = path if path and os.path.isfile(path) else None

    # ------------------------------------------------------------------
    # Downloads
    # ------------------------------------------------------------------
    def _get(self, url, headers=None):
        request = urllib.request.Request(url, headers={"User-Agent": "EdenLauncher", **(headers or {})})
        with urllib.request.urlopen(request, timeout=20) as response:
            data = response.read(MAX_DOWNLOAD + 1)
        if len(data) > MAX_DOWNLOAD:
            raise ValueError("file too large")
        return data

    def _save(self, data, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        return path

    def _eshop(self, url):
        path = _cached_download(url, self.eshop_dir)
        return self._save(self._get(url), path)

    def _sgdb(self, game):
        auth = {"Authorization": f"Bearer {self.sgdb_key}"}
        term = urllib.parse.quote(game["title"])
        found = json.loads(self._get(f"{SGDB_API}/search/autocomplete/{term}", auth))
        results = found.get("data") or []
        if not results:
            return None
        grids = json.loads(self._get(
            f"{SGDB_API}/grids/game/{results[0]['id']}?dimensions=600x900&types=static", auth))
        images = grids.get("data") or []
        if not images:
            return None
        url = images[0]["url"]
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
        ext = ext if ext in IMAGE_EXTENSIONS else ".png"
        return self._save(self._get(url), os.path.join(self.covers_dir, _stems(game)[0] + ext))

    def _jobs(self, games):
        """(game, key, fetch) in order of visual impact: backgrounds, icons, covers."""
        jobs = []
        if self.eshop:
            jobs += [(g, "background", lambda g=g: self._eshop(g["banner_url"]))
                     for g in games if not g.get("background") and g.get("banner_url")]
            jobs += [(g, "image", lambda g=g: self._eshop(g["icon_url"]))
                     for g in games if not g.get("image") and g.get("icon_url")]
        if self.sgdb_key:
            jobs += [(g, "cover", lambda g=g: self._sgdb(g)) for g in games
                     if not g.get("cover") and (g.get("title_id") or g["title"]) not in self.sgdb_missing]
        return jobs

    def _run(self, jobs):
        try:
            for game, key, fetch in jobs:
                try:
                    path = fetch()
                except Exception as e:
                    log("WARNING", f"Download failed ({key})", f"{game['title']}: {e}")
                    continue        # network trouble: try again next launch
                if path:
                    game[key] = path
                    with self._lock:
                        if game not in self._finished:
                            self._finished.append(game)
                elif key == "cover":
                    log("INFO", "No cover on SteamGridDB", game["title"])
                    self.sgdb_missing.add(game.get("title_id") or game["title"])
        finally:
            self.busy = False

    def start(self, games):
        jobs = self._jobs(games)
        if not jobs:
            return
        log("INFO", "Downloading pictures", f"{len(jobs)} file(s)")
        self.busy = True
        threading.Thread(target=self._run, args=(jobs,), daemon=True).start()

    def take_finished(self):
        """Games whose pictures arrived since the last call."""
        with self._lock:
            done, self._finished = self._finished, []
        return done
