"""
Core/Backup.py
Save-data backups, taken right before a game starts.

    <backup_dir>/<Game title> [<TITLEID>]/2026-09-28_21-04-10.zip

Each zip holds the save folders relative to the emulator's save root
(Emulator.save_folders), so restoring is "unzip into that folder". Only the
newest `keep` zips per game are kept, and nothing is written when the saves
have not changed since the newest zip: a fingerprint of every file (path,
size, modification time) is stored as the zip comment.

list_backups() and restore() are what a game's menu uses to put a backup
back from the launcher itself.
"""

import hashlib
import os
import shutil
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


def backup_folder(game, backup_dir):
    """The folder holding the backups of `game`."""
    name = safe_name(game["title"])
    if game.get("title_id"):
        name += f" [{game['title_id']}]"
    return os.path.join(backup_dir, name)


def list_backups(game, backup_dir):
    """
    The backups of `game`, newest first.

    Returns:
        list[dict]: {"path", "time" (epoch, from the file name), "size" (bytes)}.
    """
    folder = backup_folder(game, backup_dir)
    backups = []
    for name in reversed(_existing(folder)):
        path = os.path.join(folder, name)
        try:
            when = time.mktime(time.strptime(name[:19], "%Y-%m-%d_%H-%M-%S"))
        except ValueError:
            when = os.path.getmtime(path)
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        backups.append({"path": path, "time": when, "size": size})
    return backups


def restore(zip_path, root, folders):
    """
    Put a backup back: the game's current save folders are removed and the
    zip is extracted into `root` (take a backup of the current saves first).

    Args:
        zip_path (str):  a zip made by backup_saves().
        root     (str):  the emulator's save root (Emulator.save_folders).
        folders  (list): the game's current save folders, deleted first.

    Returns:
        int: files restored. Raises ValueError for a zip that would write
             outside root, OSError / zipfile.BadZipFile when it cannot be read.
    """
    base = os.path.abspath(root)
    with zipfile.ZipFile(zip_path) as archive:
        members = [m for m in archive.infolist() if not m.is_dir()]
        if not members:
            raise ValueError("empty backup")
        for member in members:
            target = os.path.abspath(os.path.join(base, member.filename))
            if os.path.isabs(member.filename) or os.path.commonpath([base, target]) != base:
                raise ValueError(f"unsafe path in backup: {member.filename}")
        for folder in folders:
            if os.path.commonpath([base, os.path.abspath(folder)]) == base and os.path.isdir(folder):
                shutil.rmtree(folder)
        for member in members:
            archive.extract(member, base)
    log("INFO", "Saves restored", f"{zip_path} -> {base} ({len(members)} files)")
    return len(members)


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

        folder = backup_folder(game, backup_dir)
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
