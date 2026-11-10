"""
Core/Update.py
"A newer build is out": asks GitHub for the latest release of the launcher,
on a background thread, at most once a day.

    check = UpdateCheck(settings, build=21)
    check.start()
    check.newer         # None, or the newer build number once known

Nothing is downloaded or installed; the launcher only tells.
"""

import json
import re
import threading
import time
import urllib.request

from .Log import log

RELEASE_URL = "https://api.github.com/repos/Fabiobatta/EdenController/releases/latest"
DOWNLOAD_PAGE = "github.com/Fabiobatta/EdenController/releases"
CHECK_EVERY_S = 24 * 3600
_TAG = re.compile(r"build-(\d+)$")


def build_of(tag):
    """Build number of a release tag ("build-21" -> 21), else None."""
    match = _TAG.match(tag or "")
    return int(match.group(1)) if match else None


class UpdateCheck:
    def __init__(self, settings, build, enabled=True, fetch=None):
        self.settings = settings
        self.build = build
        self.enabled = enabled and build > 0        # a build from source has nothing to compare
        self._fetch = fetch or self._download
        state = settings.state or {}
        latest = state.get("update_latest") or 0
        self.newer = latest if self.enabled and latest > build else None

    @staticmethod
    def _download():
        request = urllib.request.Request(RELEASE_URL, headers={
            "User-Agent": "EdenLauncher", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8")).get("tag_name")

    def due(self, now=None):
        now = time.time() if now is None else now
        last = (self.settings.state or {}).get("update_checked") or 0
        return self.enabled and now - last >= CHECK_EVERY_S

    def start(self):
        if self.due():
            threading.Thread(target=self.run, daemon=True).start()

    def run(self, now=None):
        """Ask once; network trouble is not worth a word to the user."""
        now = time.time() if now is None else now
        try:
            latest = build_of(self._fetch())
        except Exception as e:
            log("INFO", "Update check failed", e)
            return
        if latest is None:
            return
        self.settings.save_state(update_checked=int(now), update_latest=latest)
        if latest > self.build:
            log("INFO", "Newer build available", latest)
            self.newer = latest
