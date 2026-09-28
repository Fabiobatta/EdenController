# License

Eden Launcher is licensed under the
**Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)**
license: https://creativecommons.org/licenses/by-nc/4.0/

You are free to share and adapt this material for **non-commercial** purposes,
as long as you give appropriate credit, provide a link to the license, and
indicate if changes were made.

## Attribution

This project is an adaptation of
[Ryujinx Launcher](https://github.com/Artomos-dev/RyujinxLauncher)
by **Artomos**, licensed under CC BY-NC 4.0.

* `src/Core/` started from Ryujinx Launcher (commit `e9caa6c`), as did
  `build.sh` and `build.bat`. The engine structure (Emulator contract, SDL
  wrappers, process handling, logging, startup sequence, assignment logic)
  comes from there. Changed or added here: the whole UI (`Ui.py`,
  `GameGrid.py`, `Glyphs.py`: one Tk canvas instead of customtkinter),
  translations (`I18n.py`), the settings file (`Settings.py`), pictures
  (`Art.py`), the game picker, the roulette (`Roulette.py`), save backups
  (`Backup.py`), interface sounds (`Sound.py`), confirmation rumble, a
  configurable kill combo,
  incremental controller detection and stable keys for controllers without a
  device path, face-button bindings, and the build scripts' Eden defaults.
* `src/Eden/`, `src/EdenLauncher.py`, `tools/`, `tests/`, the assets and the CI
  workflow were written for this project.
* Player counts, names and eShop picture addresses in the generated
  `assets/titledb.json.gz` come from [blawar/titledb](https://github.com/blawar/titledb).

No warranties are given. This project is not affiliated with the Eden
emulator project, the Ryujinx team or Nintendo.
