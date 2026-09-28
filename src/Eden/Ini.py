"""
Eden/Ini.py
A minimal, lossless editor for Eden's qt-config.ini.

Eden writes its configuration with SimpleIni, not QSettings, and the file is
full of keys that Python's configparser would mangle: backslashes in key
names ("player_0_button_a\\default"), '%' characters, case-sensitive keys and
quoted values. So the file is handled as plain lines:

    - every line that is not touched is written back byte-for-byte
    - a key that exists is replaced in place
    - a key that does not exist is appended at the end of its section

Only one section ([Controls]) is ever edited by the launcher, but the class
is generic.
"""

import os
import tempfile

_BOM = "﻿"


class IniFile:
    """Line-preserving view of a SimpleIni file."""

    def __init__(self, text=""):
        self.bom = text.startswith(_BOM)
        if self.bom:
            text = text[len(_BOM):]
        self.newline = "\r\n" if "\r\n" in text else "\n"
        self.trailing_newline = text.endswith(("\n", "\r"))
        self.lines = text.splitlines()

    # ------------------------------------------------------------------
    # Loading / saving
    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path):
        with open(path, "r", encoding="utf-8", newline="") as f:
            return cls(f.read())

    def dumps(self):
        text = self.newline.join(self.lines)
        if self.trailing_newline or not self.lines:
            text += self.newline
        return (_BOM if self.bom else "") + text

    def save(self, path):
        """Atomic write: a crash mid-save never leaves a truncated config."""
        directory = os.path.dirname(os.path.abspath(path))
        fd, tmp = tempfile.mkstemp(prefix=".qt-config.", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(self.dumps())
            os.replace(tmp, path)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------
    @staticmethod
    def _section_name(line):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            return stripped[1:-1].strip()
        return None

    def _section_range(self, section):
        """(first line after the header, end index) or None if absent."""
        start = None
        for i, line in enumerate(self.lines):
            name = self._section_name(line)
            if name is None:
                continue
            if start is not None:
                return start, i
            if name == section:
                start = i + 1
        if start is not None:
            return start, len(self.lines)
        return None

    def has_section(self, section):
        return self._section_range(section) is not None

    # ------------------------------------------------------------------
    # Keys
    # ------------------------------------------------------------------
    @staticmethod
    def _split(line):
        """(key, raw value) for a "key=value" line, else None."""
        stripped = line.strip()
        if not stripped or stripped[0] in ";#" or "=" not in stripped:
            return None
        key, value = stripped.split("=", 1)
        return key.strip(), value.strip()

    def items(self, section):
        """{key: raw value} of a section, in file order."""
        result = {}
        rng = self._section_range(section)
        if rng is None:
            return result
        for line in self.lines[rng[0]:rng[1]]:
            kv = self._split(line)
            if kv:
                result[kv[0]] = kv[1]
        return result

    def get(self, section, key, default=None):
        return self.items(section).get(key, default)

    def set_many(self, section, values):
        """
        Set several keys of one section in a single pass.

        Args:
            section (str):  Section name without brackets.
            values  (dict): {key: raw value}. Values are written verbatim, so
                            quoting is the caller's job (see quote()).
        """
        rng = self._section_range(section)
        if rng is None:
            if self.lines and self.lines[-1].strip():
                self.lines.append("")
            self.lines.append(f"[{section}]")
            rng = (len(self.lines), len(self.lines))

        pending = dict(values)
        start, end = rng
        for i in range(start, end):
            kv = self._split(self.lines[i])
            if kv and kv[0] in pending:
                self.lines[i] = f"{kv[0]}={pending.pop(kv[0])}"

        if not pending:
            return

        # Append before any blank lines that separate this section from the next
        insert_at = end
        while insert_at > start and not self.lines[insert_at - 1].strip():
            insert_at -= 1
        new_lines = [f"{k}={v}" for k, v in pending.items()]
        self.lines[insert_at:insert_at] = new_lines


# ============================================================================
# VALUE HELPERS (mirror frontend_common/config.cpp)
# ============================================================================
# Config::AdjustOutputString quotes any value containing one of these
_SPECIAL_CHARACTERS = set("!#$%^&*|;'\",<>?`~=")


def unquote(raw):
    """Config::ReadStringSetting strips every double quote."""
    return (raw or "").replace('"', "")


def quote(value):
    """Quote a value exactly like Config::AdjustOutputString would."""
    if any(c in _SPECIAL_CHARACTERS for c in value):
        return f'"{value}"'
    return value


def read_bool(items, key, default):
    """
    Config::ReadSettingGeneric semantics, used for Settings::values entries
    such as enable_raw_input: "<key>\\default=true" (or a missing flag) means
    the built-in default is used and the stored value is ignored.
    """
    if items.get(key + "\\default", "true").strip().lower() == "true":
        return default
    value = items.get(key)
    if value is None:
        return default
    return unquote(value).strip().lower() in ("true", "1")
