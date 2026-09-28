"""
Core/Backup.py
Save-data backups, taken right before a game starts.

    <backup_dir>/<Game title> [<TITLEID>]/2026-09-28_21-04-10.zip

Each zip holds the save folders relative to the emulator's save root
(Emulator.save_folders), so restoring is "unzip into that folder". Only the
newest `keep` zips per game are kept, and nothing is written when the saves
have not changed since the newest zip: a fingerprint of every file (path,
size, modification time) is stored as the zip comment.
"""

import hashlib
import os
import time
import zipfile

from .Art import safe_name
from .Log import log

FINGERPRINT_PREFIX = b"saves:"


def _files(root, folders):
    """[(absolute path, archive name)] for every file under folders, sorted."""
    found = []
    for folder in folders:
        for current, subdirs, files in os.walk(folder):
            subdirs.sort()
            for name in sorted(files):
                path = os.path.join(current, name)
                found.append((path, os.path.relpath(path, root).replace(os.sep, "/")))
    return found


def fingerprint(files):
    digest = hashlib.sha1()
    for path, arcname in files:
        stat = os.stat(path)
        digest.update(f"{arcname}|{stat.st_size}|{stat.st_mtime_ns}\n".encode("utf-8"))
    return FINGERPRINT_PREFIX + digest.hexdigest().encode("ascii")


def _existing(folder):
    try:
        return sorted(f for f in os.listdir(folder) if f.endswith(".zip"))
    except OSError:
        return []


def backup_saves(game, root, folders, backup_dir, keep=10, now=None):
    """
    Zip the save folders of `game` unless the newest backup already matches.

    Args:
        game       (dict): {"title", "title_id"?} - names the backup folder.
        root       (str):  save root; archive paths are relative to it.
        folders    (list): save folders inside root.
        backup_dir (str):  where the per-game folders go.
        keep       (int):  zips kept per game (oldest deleted first).

    Returns:
        str | None: the new zip, or None (nothing to save, unchanged, error).
    """
    if not folders or keep <= 0:
        return None
    started = time.perf_counter()
    tmp = None
    try:
        files = _files(root, folders)
        if not files:
            return None
        stamp = fingerprint(files)

        name = safe_name(game["title"])
        if game.get("title_id"):
            name += f" [{game['title_id']}]"
        folder = os.path.join(backup_dir, name)
        zips = _existing(folder)
        if zips:
            try:
                with zipfile.ZipFile(os.path.join(folder, zips[-1])) as newest:
                    if newest.comment == stamp:
                        log("INFO", "Saves unchanged since the last backup", zips[-1])
                        return None
            except (OSError, zipfile.BadZipFile):
                pass

        os.makedirs(folder, exist_ok=True)
        base = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime(now))
        path = os.path.join(folder, base + ".zip")
        n = 1
        while os.path.exists(path):
            n += 1
            path = os.path.join(folder, f"{base}_{n}.zip")
        tmp = path + ".part"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for file_path, arcname in files:
                archive.write(file_path, arcname)
            archive.comment = stamp
        os.replace(tmp, path)

        for old in _existing(folder)[:-keep]:
            try:
                os.remove(os.path.join(folder, old))
            except OSError as e:
                log("WARNING", "Could not delete old backup", f"{old}: {e}")
        log("INFO", "Saves backed up", f"{path} ({len(files)} files, "
                                       f"{(time.perf_counter() - started) * 1000:.0f} ms)")
        return path
    except Exception as e:
        log("WARNING", "Save backup failed", f"{game['title']}: {e}")
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        return None
