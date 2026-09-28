"""
Eden Launcher
Controller-first launcher for the Eden Nintendo Switch emulator.

Pick which physical controller is Player 1..8 with the controller itself,
right before the game starts. Built for game streaming (Sunshine / Apollo /
Vibeshine host + Moonlight client), where every client pad appears on the
host as an identical virtual Xbox controller and Eden's saved mapping would
otherwise bind players to whichever pad happened to connect first.

Structure:
    Core/   emulator-agnostic engine (UI, controllers, process, logging),
            from Ryujinx Launcher by Artomos - CC BY-NC 4.0
    Eden/   everything Eden-specific (paths, SDL hints, qt-config.ini)

License: CC BY-NC 4.0 (Attribution-NonCommercial), see LICENSE.md
"""

from Core.Bootstrap import run
from Eden import Eden

if __name__ == "__main__":
    run(Eden())
