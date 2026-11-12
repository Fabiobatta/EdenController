"""
Core/Update.py
"A newer build is out": asks GitHub for the latest release of the launcher,
on a background thread, at most once a day.

    check = UpdateCheck(settings, build=21)
    check.start()
    check.newer         # None, or the newer build number once known

and, asked from the settings, installs it (Updater): the release's exe is
downloaded next to the running one, checked against the SHA-256 GitHub
publishes for it, swapped in (a running exe can be renamed on Windows,
not overwritten) and started; the old one is deleted at the next start.

    updater = Updater(23, sys.executable)
    updater.start()
    updater.state       # "downloading" (updater.progress 0-1), "ready", "failed" (updater.error)
    updater.install()   # once ready: the new exe in place, the old one kept as <exe>.old
"""

import hashlib
import json
import os
import re
import threading
import time
import urllib.request

from .Log import log

RELEASE_URL = "https://api.github.com/repos/Fabiobatta/EdenController/releases/latest"
TAG_URL = "https://api.github.com/repos/Fabiobatta/EdenController/releases/tags/build-{n}"
ASSET_NAME = "EdenLauncher.exe"
MIN_EXE_BYTES = 1024 * 1024         # anything smaller is an error page, not the launcher
CHUNK = 256 * 1024
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


# ============================================================================
# INSTALLING A NEWER BUILD
# ============================================================================
def _get_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "EdenLauncher",
                                                  "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def release_asset(build, fetch_json=_get_json):
    """(download URL, size, SHA-256 hex or None) of the launcher's exe in release build-N."""
    release = fetch_json(TAG_URL.format(n=build))
    for asset in release.get("assets") or ():
        if asset.get("name") == ASSET_NAME:
            digest = asset.get("digest") or ""
            sha = digest.split(":", 1)[1] if digest.startswith("sha256:") else None
            return asset["browser_download_url"], asset.get("size"), sha
    raise ValueError(f"no {ASSET_NAME} in build-{build}")


def download(url, dest, size=None, sha256=None, progress=None, opener=None):
    """
    Download `url` to `dest`, through dest + ".part" so that a broken
    transfer never leaves a half file under the real name. Checks it is an
    exe (MZ), its size and its SHA-256 when given. progress(done, total).
    """
    opener = opener or (lambda u: urllib.request.urlopen(
        urllib.request.Request(u, headers={"User-Agent": "EdenLauncher"}), timeout=30))
    part = dest + ".part"
    digest = hashlib.sha256()
    done = 0
    try:
        with opener(url) as response, open(part, "wb") as f:
            total = size or int(response.headers.get("Content-Length") or 0) or None
            while True:
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                if done == 0 and not chunk.startswith(b"MZ"):
                    raise ValueError("the download is not a Windows program")
                f.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        if done < MIN_EXE_BYTES or (size and done != size):
            raise ValueError(f"incomplete download ({done} bytes)")
        if sha256 and digest.hexdigest().lower() != sha256.lower():
            raise ValueError("the download does not match the published checksum")
        os.replace(part, dest)
    except BaseException:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    return dest


def install(exe, new):
    """
    Put `new` in place of `exe`: the running exe is renamed to <exe>.old
    (Windows lets a running program be renamed, not overwritten), then the
    new one takes its name. On failure the old one is put back.
    """
    old = exe + ".old"
    cleanup_old(exe)
    os.replace(exe, old)
    try:
        os.replace(new, exe)
    except OSError:
        os.replace(old, exe)
        raise
    return old


def cleanup_old(exe):
    """Delete the <exe>.old an update left behind (it may still be running: then next time)."""
    try:
        os.remove(exe + ".old")
    except OSError:
        pass


class Updater:
    """Downloads build N of the launcher on a thread; install() swaps it in."""

    def __init__(self, build, exe, asset=release_asset, fetch=download):
        self.build, self.exe = build, exe
        self._asset, self._download = asset, fetch
        self.state = "idle"
        self.progress = 0.0
        self.error = None
        self.path = exe + ".new"

    def start(self):
        self.state = "downloading"
        threading.Thread(target=self._run, name="update", daemon=True).start()

    def _run(self):
        try:
            url, size, sha = self._asset(self.build)
            log("INFO", "Downloading the update", f"build {self.build}: {url}")

            def progress(done, total):
                self.progress = done / total if total else 0.0
            self._download(url, self.path, size, sha, progress)
            self.progress = 1.0
            self.state = "ready"
            log("INFO", "Update downloaded", self.path)
        except Exception as e:
            log("WARNING", "Update download failed", e)
            self.error = str(e)
            self.state = "failed"

    def install(self):
        return install(self.exe, self.path)
