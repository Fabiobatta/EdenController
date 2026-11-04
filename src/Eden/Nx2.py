"""
Eden/Nx2.py
Switch 2 games the way Eglantine (Eden's Switch 2 fork) runs them, read
without the emulator.

    NUP archive   one file packing a game's ExeFS/RomFS trees, its updates
                  and DLC, with the title name and icon in per-package
                  metadata (core/file_sys/nup_container.cpp)
    Loose dump    a folder with the extracted executable, in one of
                      <game>/exefs/main
                      <game>/base/exefs/main
                      <game>/updates/<version>/exefs/main
                  next to main.npdm, plus a romfs folder (core/file_sys/
                  nup_writer.cpp, IsLooseDump / DumpRootFromMain)

Eglantine boots both with "-g <path>": the .nup file, or the "main" file of
a loose dump. A dump with updates boots from its newest update, which
carries the matching executable (deconstructed_rom_directory.cpp refuses a
base executable with a newer update's RomFS).

Eglantine's own game list only finds loose dumps when the game folder has
"scan subfolders" on; the launcher also looks one folder down, so they show
up in the launcher either way.
"""

import os
import struct

from Core.Log import log

NUP_MAGIC = b"NUP\x01"
NUP_SUPERBLOCK = struct.Struct("<4sHHQQ32sQQII")       # first 0x50 bytes of the 0x80 superblock
NUP_SUPERBLOCK_SIZE = 0x80
TOC_HEADER = struct.Struct("<IIQQQQQ")                 # 0x30
TOC_MAGIC = 0x01434F54                                 # "TOC\x01"
PACKAGE_RECORD = struct.Struct("<QIIIIBBHIQI20s")      # 0x40
TLV_HEADER = struct.Struct("<HHI")
MAX_PACKAGES = 4096

# PackageKind
KIND_BASE, KIND_UPDATE, KIND_ADDON = 0, 1, 2
NO_DIR = 0xFFFFFFFF

# Meta TLV types
META_NPDM = 0x0001
META_ICON = 0x0003
META_DISPLAY_VERSION = 0x0004
META_TITLE_NAME = 0x0005

# What a NUP entry needs; everything else (localized icons...) is skipped
_WANTED_META = {META_NPDM, META_ICON, META_DISPLAY_VERSION, META_TITLE_NAME}
MAX_META_ITEM = 8 * 1024 * 1024


# ============================================================================
# NPDM
# ============================================================================
def npdm_program_id(data):
    """
    Program (title) ID in an NPDM: "META" header, ACI0 offset at 0x70, the
    ACI0's program ID at +0x10. None if the data is not an NPDM.
    """
    if len(data) < 0x80 or data[:4] != b"META":
        return None
    aci_offset, aci_size = struct.unpack_from("<II", data, 0x70)
    if aci_size < 0x18 or aci_offset + 0x18 > len(data) or data[aci_offset:aci_offset + 4] != b"ACI0":
        return None
    program_id = struct.unpack_from("<Q", data, aci_offset + 0x10)[0]
    return f"{program_id:016X}" if program_id else None


def base_title_id(title_id):
    """The base game's ID for an update or the game itself (last three digits 000)."""
    return title_id[:13] + "000" if title_id else title_id


# ============================================================================
# NUP
# ============================================================================
def _read_meta(f, offset, size):
    """{type: payload} of the TLVs this module uses, reading only those."""
    meta = {}
    end = offset + size
    position = offset
    while position + TLV_HEADER.size <= end:
        f.seek(position)
        kind, _, length = TLV_HEADER.unpack(f.read(TLV_HEADER.size))
        position += TLV_HEADER.size
        if position + length > end:
            break
        if kind in _WANTED_META and kind not in meta and length <= MAX_META_ITEM:
            meta[kind] = f.read(length)
        position += length
    return meta


def read_nup(path):
    """
    Read a NUP archive's packages and metadata.

    Returns:
        dict | None: {"title_id", "kind" ("base" | "update" | "extra"),
                      "name", "version", "icon" (image bytes or None)}, or
                      None when the file is not a readable NUP.
    """
    try:
        with open(path, "rb") as f:
            raw = f.read(NUP_SUPERBLOCK_SIZE)
            if len(raw) < NUP_SUPERBLOCK_SIZE:
                return None
            magic, header_size, _, toc_offset, toc_size, _, blob_offset, file_size, count, flags = \
                NUP_SUPERBLOCK.unpack_from(raw)
            if magic != NUP_MAGIC or header_size < NUP_SUPERBLOCK_SIZE or flags != 0:
                return None
            if not 0 < count <= MAX_PACKAGES or toc_size < TOC_HEADER.size + count * PACKAGE_RECORD.size:
                return None
            f.seek(toc_offset)
            toc = f.read(TOC_HEADER.size + count * PACKAGE_RECORD.size)
            if len(toc) < TOC_HEADER.size + count * PACKAGE_RECORD.size:
                return None
            toc_magic, toc_count = TOC_HEADER.unpack_from(toc)[:2]
            if toc_magic != TOC_MAGIC or toc_count != count:
                return None

            packages = []
            for i in range(count):
                title_id, _, exefs_dir, _, version, kind, _, _, _, meta_offset, meta_size, _ = \
                    PACKAGE_RECORD.unpack_from(toc, TOC_HEADER.size + i * PACKAGE_RECORD.size)
                packages.append({"title_id": title_id, "version": version, "kind": kind,
                                 "exefs": exefs_dir != NO_DIR, "meta": (meta_offset, meta_size)})

            base = next((p for p in packages if p["kind"] == KIND_BASE), None)
            updates = sorted((p for p in packages if p["kind"] == KIND_UPDATE),
                             key=lambda p: p["version"], reverse=True)
            main = base or (updates[0] if updates else None)
            if main is None:
                return {"title_id": f"{packages[0]['title_id']:016X}", "kind": "extra",
                        "name": None, "version": None, "icon": None}

            # Bootable like Eglantine's IsBootableGameContainer: a base, or an
            # update that carries its executable. Name, version and icon: the
            # newest update's when it has them, else the base's.
            info = {"title_id": base_title_id(f"{main['title_id']:016X}"),
                    "kind": "base" if base or main["exefs"] else "update",
                    "name": None, "version": None, "icon": None}
            for package in updates + ([base] if base else []):
                offset, size = package["meta"]
                if not offset or not size or offset + size > file_size:
                    continue
                meta = _read_meta(f, offset, size)
                if info["name"] is None and meta.get(META_TITLE_NAME):
                    info["name"] = meta[META_TITLE_NAME].decode("utf-8", "replace").strip("\0 ") or None
                if info["version"] is None and meta.get(META_DISPLAY_VERSION):
                    info["version"] = meta[META_DISPLAY_VERSION].decode("utf-8", "replace").strip("\0 ") or None
                if info["icon"] is None and meta.get(META_ICON):
                    info["icon"] = meta[META_ICON]
                if not main["title_id"] and meta.get(META_NPDM):
                    info["title_id"] = base_title_id(npdm_program_id(meta[META_NPDM]))
            return info
    except (OSError, struct.error) as e:
        log("WARNING", "Could not read NUP", f"{os.path.basename(path)}: {e}")
        return None


def icon_extension(data):
    return ".png" if data[:4] == b"\x89PNG" else ".jpg"


# ============================================================================
# LOOSE DUMPS
# ============================================================================
def _child(directory, name, entries=None):
    """Path of `directory`'s child called `name`, ignoring case (Linux), or None."""
    try:
        entries = os.listdir(directory) if entries is None else entries
    except OSError:
        return None
    lowered = name.lower()
    match = next((e for e in entries if e.lower() == lowered), None)
    return os.path.join(directory, match) if match else None


def _exefs_main(directory):
    """<directory>/exefs/main if it is there, else None."""
    exefs = _child(directory, "exefs")
    if not exefs or not os.path.isdir(exefs):
        return None
    main = _child(exefs, "main")
    return main if main and os.path.isfile(main) else None


def loose_dump(directory, entries=None):
    """
    The executable to boot when `directory` is a loose dump's root, else None.

    Returns:
        str | None: path of the "main" to pass to -g - the newest update's
                    when it carries one, else the base's.
    """
    try:
        entries = os.listdir(directory) if entries is None else entries
    except OSError:
        return None
    lowered = {e.lower() for e in entries}
    if not lowered & {"exefs", "base", "updates"}:
        return None                                 # cheap exit for ordinary folders

    updates = _child(directory, "updates", entries)
    if updates and os.path.isdir(updates):
        try:
            # Newest version first (decimal folder names), then any other folder
            versions = sorted(os.listdir(updates), reverse=True,
                              key=lambda v: (v.isdigit(), int(v) if v.isdigit() else 0, v))
        except OSError:
            versions = []
        for version in versions:
            main = _exefs_main(os.path.join(updates, version))
            if main:
                return main
    base = _child(directory, "base", entries)
    if base and os.path.isdir(base):
        main = _exefs_main(base)
        if main:
            return main
    return _exefs_main(directory)


def dump_title_id(main):
    """Title ID of a loose dump from the main.npdm next to its executable."""
    npdm = _child(os.path.dirname(main), "main.npdm")
    if not npdm:
        return None
    try:
        with open(npdm, "rb") as f:
            return base_title_id(npdm_program_id(f.read(64 * 1024)))
    except OSError:
        return None


def dump_root(main):
    """The dump folder a loose dump's exefs/main belongs to."""
    root = os.path.dirname(os.path.dirname(main))           # .../exefs/main -> parent of exefs
    name = os.path.basename(root).lower()
    if name == "base":
        return os.path.dirname(root)
    if name.isdigit() and os.path.basename(os.path.dirname(root)).lower() == "updates":
        return os.path.dirname(os.path.dirname(root))
    return root
