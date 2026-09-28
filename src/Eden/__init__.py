"""
Eden emulator package.

Implements the Core.Emulator contract for Eden:
    Eden.py    - the adapter Core talks to (paths, SDL backend and hints)
    Config.py  - qt-config.ini template, input profiles, GUID/port, writer
    Ini.py     - lossless line-based editor for SimpleIni files
"""

from .Eden import Eden

__all__ = ["Eden"]
