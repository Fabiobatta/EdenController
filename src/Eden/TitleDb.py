"""
Eden/TitleDb.py
eShop facts about a game, from the index bundled with the launcher
(assets/titledb.json.gz, built by tools/build_titledb_index.py).

    info = TitleDb.load().get("0100152000022000")
    info -> {"players": 4, "name": "Mario Kart 8 Deluxe",
             "icon_url": "https://img-eshop.cdn.nintendo.net/i/....jpg",
             "banner_url": "https://img-eshop.cdn.nintendo.net/i/....jpg"}

"players" is the most players one console supports (eShop "number of
players"), i.e. local co-op/versus. Missing index or title -> None.
"""

import gzip
import json
import os

from Core.Log import log
from Core.Paths import resource_path

CDN = "https://img-eshop.cdn.nintendo.net/i/"
INDEX = os.path.join("assets", "titledb.json.gz")


class TitleDb:
    def __init__(self, games=None):
        self.games = games or {}

    @classmethod
    def load(cls, path=None):
        path = path or resource_path(INDEX)
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                games = json.load(f).get("games", {})
            log("INFO", "Title database", f"{len(games)} games")
            return cls(games)
        except FileNotFoundError:
            log("WARNING", "No title database bundled - no player counts or eShop art", path)
        except Exception as e:
            log("EXCEPTION", "Title database unreadable", e)
        return cls()

    def get(self, title_id):
        row = self.games.get((title_id or "").upper())
        if not row:
            return None
        players, name, icon, banner = (list(row) + [None] * 4)[:4]
        return {
            "players": players,
            "name": name,
            "icon_url": CDN + icon if icon else None,
            "banner_url": CDN + banner if banner else None,
        }
