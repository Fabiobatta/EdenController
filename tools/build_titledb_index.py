"""
tools/build_titledb_index.py
Build assets/titledb.json.gz: for every Switch base game, the number of
players on one console, the eShop name, the eShop icon/banner and a few
eShop screenshots (the launcher's slideshow background).

Source: blawar/titledb (US and GB English databases, ~90 MB each). The CI
runs this before packaging so the launcher ships a ~1-2 MB index and never
downloads the full database itself. Without the index the launcher still
works, just without player badges and eShop art.

    python tools/build_titledb_index.py [output]

Format:
    {"v": 1, "games": {"<TITLEID>": [players, "name", "icon", "banner", ["screenshot", ...]]}}
icon/banner/screenshots are "<sha256>.<ext>" names under
https://img-eshop.cdn.nintendo.net/i/
"""

import gzip
import json
import os
import re
import sys
import time
import urllib.request

SOURCE = "https://raw.githubusercontent.com/blawar/titledb/master/{}.json"
REGIONS = ("US.en", "GB.en")
CDN = re.compile(r"^https://img-eshop\.cdn\.nintendo\.net/i/([0-9a-f]{64}\.(?:jpg|png))$")
TRADEMARKS = re.compile(r"[™®©]")
SCREENSHOTS = 3         # per game; each one adds ~40 KB to the gzipped index per 1000 games


def download(region, attempts=4):
    request = urllib.request.Request(SOURCE.format(region), headers={"Accept-Encoding": "gzip"})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                data = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
            return json.loads(data)
        except Exception as e:     # truncated transfer, timeout, bad gzip...
            if attempt == attempts:
                raise
            print(f"  attempt {attempt} failed ({e}), retrying...", flush=True)
            time.sleep(2 ** attempt)


def build(databases):
    games = {}
    for database in databases:
        for entry in database.values():
            title_id = (entry.get("id") or "").upper()
            if len(title_id) != 16 or not title_id.endswith("000") or entry.get("isDemo"):
                continue
            row = games.setdefault(title_id, [None, None, None, None, None])
            players = entry.get("numberOfPlayers")
            name = TRADEMARKS.sub("", entry.get("name") or "").strip() or None
            icon = CDN.match(entry.get("iconUrl") or "")
            banner = CDN.match(entry.get("bannerUrl") or "")
            screens = [m.group(1) for m in (CDN.match(u or "") for u in entry.get("screenshots") or []) if m]
            for i, value in enumerate((players if isinstance(players, int) and players > 0 else None,
                                       name, icon and icon.group(1), banner and banner.group(1),
                                       screens[:SCREENSHOTS] or None)):
                if row[i] is None and value:
                    row[i] = value
    return games


def main():
    output = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "titledb.json.gz")
    databases = []
    for region in REGIONS:
        print(f"Downloading {region}...", flush=True)
        databases.append(download(region))
    games = build(databases)
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with gzip.open(output, "wt", encoding="utf-8", compresslevel=9) as f:
        json.dump({"v": 1, "games": games}, f, ensure_ascii=False, separators=(",", ":"))
    with_players = sum(1 for row in games.values() if row[0])
    with_screens = sum(1 for row in games.values() if row[4])
    print(f"{output}: {len(games)} games, {with_players} with player count, {with_screens} with screenshots, "
          f"{os.path.getsize(output) // 1024} KB")


if __name__ == "__main__":
    main()
