"""
Eden/Switch.py
Read the title ID and content type of an NSP or XCI without Eden.

The game list must tell base games from updates and DLC even when the file
name says nothing ("Game v1.28.0.nsp"), and needs the title ID to find the
name and icon Eden cached. The sources, cheapest first:

    NSP  ticket file name   "<rights id>.tik": the first 16 hex digits are
                            the title ID (titlekey-encrypted dumps)
         CNMT XML           "<id>.cnmt.xml" with <Id> and <Type>
         NCA headers        decrypted with header_key from prod.keys; the
                            Meta NCA's program ID is the content's title ID
    XCI  NCA headers of the "secure" partition

Title ID conventions: base games end in 000, updates in 800, DLC in
anything else (base + 0x1000 + index).

Containers (switchbrew.org): PFS0 (NSP), HFS0 (XCI partitions). NCA headers
are AES-128-XTS encrypted with the 32-byte header_key, 0x200-byte sectors and
a big-endian sector number as tweak (hactool's convention).
"""

import os
import re
import struct

from Core.Log import log

# NCA content types (header offset 0x205)
CONTENT_PROGRAM = 0
CONTENT_META = 1

BASE, UPDATE, DLC = "base", "update", "dlc"

_HEADER_SIZE = 0xC00
_SECTOR = 0x200


# ============================================================================
# TITLE ID HELPERS
# ============================================================================
def title_kind(title_id):
    """"0100152000022000" -> "base", "...800" -> "update", else "dlc"."""
    suffix = int(title_id, 16) & 0xFFF
    if suffix == 0:
        return BASE
    if suffix == 0x800:
        return UPDATE
    return DLC


def base_title_id(title_id):
    """Title ID of the base game an update or DLC belongs to."""
    value = int(title_id, 16)
    if value & 0xFFF == 0x800:
        value &= ~0xFFF
    elif value & 0xFFF:
        # DLC: base + 0x1000 + index
        value = (value - 0x1000) & ~0xFFF
    return f"{value:016X}"


# ============================================================================
# KEYS
# ============================================================================
def load_header_key(keys_file):
    """header_key from a prod.keys file, as 32 bytes, or None."""
    try:
        with open(keys_file, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                name, _, value = line.partition("=")
                if name.strip().lower() == "header_key":
                    key = bytes.fromhex(value.strip())
                    return key if len(key) == 32 else None
    except OSError:
        pass
    return None


# ============================================================================
# AES-128-XTS (decrypt only, Nintendo tweak)
# ============================================================================
def _gf_double(tweak):
    """Multiply the XTS tweak by x in GF(2^128), little-endian convention."""
    value = int.from_bytes(tweak, "little") << 1
    if value >> 128:
        value = (value & ((1 << 128) - 1)) ^ 0x87
    return value.to_bytes(16, "little")


def _xor(a, b):
    return bytes(x ^ y for x, y in zip(a, b))


def xts_decrypt(key, data, sector=0, sector_size=_SECTOR, tweak_order="big"):
    """AES-128-XTS decrypt. Nintendo's tweak is the big-endian sector number;
    IEEE 1619 (the standard) uses little-endian."""
    from Crypto.Cipher import AES  # pycryptodome

    data_cipher = AES.new(key[:16], AES.MODE_ECB)
    tweak_cipher = AES.new(key[16:], AES.MODE_ECB)
    out = bytearray()
    for start in range(0, len(data), sector_size):
        tweak = tweak_cipher.encrypt(sector.to_bytes(16, tweak_order))
        chunk = data[start:start + sector_size]
        for block in range(0, len(chunk), 16):
            out += _xor(data_cipher.decrypt(_xor(chunk[block:block + 16], tweak)), tweak)
            tweak = _gf_double(tweak)
        sector += 1
    return bytes(out)


def xts_encrypt(key, data, sector=0, sector_size=_SECTOR, tweak_order="big"):
    """Inverse of xts_decrypt, used by the tests to build fake NCAs."""
    from Crypto.Cipher import AES

    data_cipher = AES.new(key[:16], AES.MODE_ECB)
    tweak_cipher = AES.new(key[16:], AES.MODE_ECB)
    out = bytearray()
    for start in range(0, len(data), sector_size):
        tweak = tweak_cipher.encrypt(sector.to_bytes(16, tweak_order))
        chunk = data[start:start + sector_size]
        for block in range(0, len(chunk), 16):
            out += _xor(data_cipher.encrypt(_xor(chunk[block:block + 16], tweak)), tweak)
            tweak = _gf_double(tweak)
        sector += 1
    return bytes(out)


def nca_header_info(raw_header, header_key):
    """
    (content_type, program_id) from the first 0x400 encrypted bytes of an
    NCA, or None if it does not decrypt to an NCA2/NCA3 header.
    """
    header = xts_decrypt(header_key, raw_header[:0x400])
    if header[0x200:0x204] not in (b"NCA3", b"NCA2"):
        return None
    content_type = header[0x205]
    program_id = struct.unpack_from("<Q", header, 0x210)[0]
    return content_type, f"{program_id:016X}"


# ============================================================================
# CONTAINERS
# ============================================================================
def _read_partition(f, offset, magic, entry_size):
    """
    Parse a PFS0/HFS0 header at `offset`.

    Returns:
        list[tuple[str, int, int]]: (name, absolute offset, size)
    """
    f.seek(offset)
    head = f.read(0x10)
    if len(head) < 0x10 or head[:4] != magic:
        return []
    count, strtab_size = struct.unpack_from("<II", head, 4)
    if count > 10000:
        return []
    entries = f.read(entry_size * count)
    strtab = f.read(strtab_size)
    data_start = offset + 0x10 + entry_size * count + strtab_size
    files = []
    for i in range(count):
        file_offset, size, name_offset = struct.unpack_from("<QQI", entries, i * entry_size)
        end = strtab.find(b"\0", name_offset)
        name = strtab[name_offset:end if end >= 0 else None].decode("utf-8", "replace")
        files.append((name, data_start + file_offset, size))
    return files


def _meta_ids_from_ncas(f, ncas, header_key):
    """Title IDs of the Meta NCAs (one per content: game, update, DLC)."""
    ids = []
    for name, offset, size in ncas:
        if not name.lower().endswith((".nca", ".ncz")) or size < _HEADER_SIZE:
            continue
        f.seek(offset)
        info = nca_header_info(f.read(0x400), header_key)
        if info and info[0] == CONTENT_META:
            ids.append(info[1])
    return ids


def _nsp_ids(f, header_key):
    files = _read_partition(f, 0, b"PFS0", 0x18)
    if not files:
        return []

    # 1. Tickets: "<16 hex title id><16 hex>.tik"
    ids = [n[:16].upper() for n, _, _ in files
           if re.fullmatch(r"[0-9a-fA-F]{32}\.tik", n)]
    if ids:
        return ids

    # 2. CNMT XML (some dumps include it)
    for name, offset, size in files:
        if name.lower().endswith(".cnmt.xml") and size < 1 << 20:
            f.seek(offset)
            match = re.search(rb"<Id>0x([0-9a-fA-F]{16})</Id>", f.read(size))
            if match:
                return [match.group(1).decode().upper()]

    # 3. Encrypted NCA headers
    if header_key:
        return _meta_ids_from_ncas(f, files, header_key)
    return []


def _xci_ids(f, header_key):
    if not header_key:
        return []
    f.seek(0x100)
    if f.read(4) != b"HEAD":
        return []
    f.seek(0x130)
    root_offset = struct.unpack("<Q", f.read(8))[0]
    root = _read_partition(f, root_offset, b"HFS0", 0x40)
    secure = next((p for p in root if p[0] == "secure"), None)
    if not secure:
        return []
    return _meta_ids_from_ncas(f, _read_partition(f, secure[1], b"HFS0", 0x40), header_key)


def read_title_ids(path, header_key=None):
    """
    Every title ID an NSP/XCI contains (a bundle may hold game + update +
    DLC). Empty when the file cannot be identified.
    """
    ext = os.path.splitext(path)[1].lower()
    try:
        with open(path, "rb") as f:
            if ext == ".nsp":
                return _nsp_ids(f, header_key)
            if ext == ".xci":
                return _xci_ids(f, header_key)
    except Exception as e:
        log("WARNING", "Could not read title ID", f"{os.path.basename(path)}: {e}")
    return []


def identify(path, header_key=None):
    """
    (title_id, kind) of the main content of a file: the base game if the
    file contains one, else its update/DLC. (None, None) if unknown.
    """
    ids = read_title_ids(path, header_key)
    if not ids:
        return None, None
    for title_id in ids:
        if title_kind(title_id) == BASE:
            return title_id, BASE
    title_id = sorted(ids)[0]
    return title_id, title_kind(title_id)
