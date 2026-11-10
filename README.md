# <img src="assets/EdenLauncherPNG.png" alt="Eden Launcher" width="28"> Eden Launcher

**Pick which controller is Player 1, 2, 3… for the [Eden](https://eden-emu.dev) Switch emulator, using only your controllers, right before the game starts.**

Made for game streaming (**Sunshine / Vibeshine / Apollo** host + **Moonlight** client), and just as useful on a couch PC. Also works with **[Eglantine](https://github.com/Djihads80/Eglantine)** (Eden's experimental Switch 2 fork), **Lichii** and other Eden forks.

**[⬇ Download EdenLauncher.exe](https://github.com/Fabiobatta/EdenController/releases/latest)** (Windows)

---

## Why

Eden stores each player's controller as a GUID plus a *connection order*. When you stream, every Moonlight controller shows up on the host as an identical virtual Xbox pad, so the only thing that tells Player 1 from Player 2 is which pad connected first. One controller waking up late and your players are swapped or dead, and fixing it means mouse and keyboard in Eden's settings.

## How it works

1. Start `EdenLauncher.exe` instead of Eden. A fullscreen, controller-only screen opens.
2. Everyone presses **A** on their controller: first press = Player 1, second = Player 2, and so on (up to 8). Your pad rumbles once for P1, twice for P2… Each card shows where that player's view will be in split screen (2 players: top / bottom; 3–4: the quadrants, starting top left), and pressing any button lights up your own card, so you can tell which one is yours.
3. Press **Start**: pick a game from the grid (or it launches the game passed by your streaming app).
4. The launcher rewrites only the controller part of Eden's config, turns on docked (TV) mode for 2+ players, and starts Eden.

Besides the players, the launcher sets three Eden options, each of which can be left alone in the settings: docked mode, the games' mouse-only "controllers" window (skipped), and the "are you sure you want to close?" question (off, so the launcher can close Eden cleanly). Nothing else in your Eden setup is touched. The interface is in English or Italian (follows Windows).

## Features

- **Xbox button layout**: the A button (bottom) is Switch A, so you accelerate in Mario Kart with the button labelled A. A Nintendo (positional) layout and your own Eden profiles are available per player.
- **Game grid**: every game in rows and columns, each with a big local co-op badge, the selected one framed with a glow in the colour of its art; title and facts on top, the game's blurred art behind. Updates and DLC are hidden (title IDs are read from the files). **LB / RB** switch between tabs (All · Favourites · Recent · For N players · Switch 2), **LT / RT** jump to the next letter.
- **Continue playing**: the games of the last 30 days sit in their own row at the top of the grid.
- **Instant start**: the list of the last scan shows up at once, while the folders are checked again in the background.
- **Player count on every cover**: green if the game supports everyone who joined, red if not. **Y** shows only games that fit your group.
- **Roulette** (**X**): a spinning strip that picks a random game, only among games that support all joined players.
- **A menu for every game** (**Start**): favourite, hide, sort order, split-screen layout, which emulator starts it, and **restore a save backup** without leaving the couch.
- **Settings on screen** (**LB** on the player screen): language, theme (dark / OLED black), accent colour, sounds, rumble, game folders, emulators, docked mode… no file to edit.
- **Several emulators, one list**: Eden, Eglantine, Lichii and other forks together. Switch 2 games start with the emulator that runs them, the rest with your main one. Add one from Settings → Emulators.
- **Who plays where**: the player screen shows a TV with each player's part of the split screen, and so does the "Here we go" screen before a game starts.
- **Smooth, light animations**: a gliding focus frame, rows sliding in, cross-fading backgrounds, a breathing glow, a settings drawer. They can be turned off.
- **Sounds**: soft synthesised bells and wooden ticks, a different note for every player number. Replace any of them with your own `.wav` in a `sounds` folder.
- **Play time, statistics, achievements**: recent / A–Z / most played order, last 7 days, most played games, 14 small rewards ("Party of four", "Night owl"…), shown with **RB**.
- **Save backups**: your Eden saves are zipped before each launch (last 10 kept per game, skipped if unchanged).
- **Close a game from the controller**: hold **Back + LB + RB**. Eden is asked to close properly (and killed only if it does not answer), then you pick: change players, another game, or the desktop.
- **Tells you when a game did not start**: if Eden closes right after a launch, the last errors of its log are on screen.
- **Controller dropped mid-game?** Its player slot waits for it and goes back to it when it reconnects.
- Rumble, interface sounds, a selection colour taken from each game's cover, idle dimming for the TV, a note when a newer build is out.

## Controls

| Where | Button | Action |
| :--- | :--- | :--- |
| Players | **A** / **B** | Join / leave |
| Players | **X** | Change your button layout |
| Players | **Y** | Resume the last game |
| Players | **LB** / **RB** | Settings / achievements and statistics |
| Players | **Start** | Choose a game |
| Games | **A** / **B** | Play / back |
| Games | **X** | Roulette |
| Games | **Y** | Only games for all players (the "For N players" tab) |
| Games | **Start** / **Back** | The game's menu / favourite |
| Games | **LB RB** / **LT RT** | Tabs / next letter |
| In game | **Back + LB + RB** | Close menu (change players, another game, desktop, or resume) |

## Setup

1. Put `EdenLauncher.exe` **in the same folder as `eden.exe`** (or `eglantine.exe`, `lichii.exe`, or another Eden fork's exe: it is found by itself, or set `emulator_exe` in `EdenLauncher.ini`). Or anywhere else, with an `EdenPath.config` file next to it containing that folder's path.
2. In Eden, set up **Player 1** once with an Xbox controller (Configure → Controls → Auto-map) and save. The launcher copies that mapping to every player.
3. In Sunshine / Vibeshine, add an application:
   - **Command**: `"C:\Emulators\Eden\EdenLauncher.exe"`. Game grid included.
   - Or one app per game: `"C:\...\EdenLauncher.exe" -f -g "D:\Switch\Game.nsp"`. Arguments go straight to Eden.
   - **Working directory**: Eden's folder.
4. Connect all controllers in Moonlight, start the app, press A in the order you want.

**Tips**

- **More than 4 players**: Windows XInput stops at 4 pads. Set the host's gamepad emulation to **DS4** for up to 8.
- **Eglantine** keeps Eden's data folder (`%APPDATA%\eden`): installed normally, both share config and saves. Make Eglantine portable (an empty `user` folder next to `eglantine.exe`) to keep them apart. The game grid lists Switch 2 games both as `.nup` files (with their own name and icon) and as extracted dump folders (`<game>/exefs/main`, `<game>/base/...`, `<game>/updates/<version>/...`), even when Eglantine's own list misses them because "Scan subfolders" is off for that folder. They have no player count yet: add them under `[Players]` (e.g. `Mario Kart World = 4`) so the roulette can pick them.
- **Per-game input profiles** set in Eden (custom game config) override the launcher. Keep game controls on the global config.
- **A button stuck in game?** Open the close menu (**Back + LB + RB**) and let go of the controllers: it lists what each player's controller is holding right now. If something shows up there, the stuck input comes from the streaming client/host, not from Eden or the game.
- **More emulators**: other Eden-family emulators in the launcher's own folder are found by themselves. For one in another folder: Settings (**LB**) → Emulators → "Add an emulator…" and pick its exe (or add a line under `[Emulators]` in `EdenLauncher.ini`, e.g. `Eglantine = D:\Emulators\Eglantine`). Switch 2 games (`.nup` files and dump folders) then start with the first emulator marked "Switch 2"; each game's menu (**Start**) can pick another one.
- **Split-screen layout**: the launcher assumes two players play one above the other; set "side by side" or "shared screen" in a game's menu (Mario Kart 8 Deluxe is already known).
- **A game with its own controls in Eden** (per-game configuration with an input profile) ignores the launcher's players: it is marked with a **!** in the grid.
- **Logs**: `%APPDATA%\eden\log\EdenLauncher_*.log`, or `<Eden>\user\log` for portable Eden. If the launcher itself fails to start, the reason is in `EdenLauncher.crash.log` next to the exe.
- **Your original Eden settings**: the first time the launcher changes `qt-config.ini` it keeps a copy next to it, `qt-config.ini.before-EdenLauncher`. Rename it back to `qt-config.ini` to undo everything the launcher did.

## Settings

Most options are on the settings screen (**LB**). They live in `EdenLauncher.ini`, created next to the exe on first run; the main ones:

| Option | Default | What it does |
| :--- | :--- | :--- |
| `language` | `auto` | `auto`, `en` or `it` |
| `layout` | `Xbox` | `Xbox`, `Nintendo` or the name of an Eden input profile |
| `docked` | `auto` | Docked mode: `auto` (2+ players), `always` or `never` |
| `controller_applet` | `off` | Skip the mouse-only "connect controllers" window some games open |
| `game_dirs` | *(empty)* | Game folders separated by `;`. Empty means Eden's own folders |
| `emulator_exe` | *(empty)* | The emulator to start, e.g. `lichii.exe`. Empty finds Eden or a fork by itself |
| `kill_combo` | `back+lb+rb` | Buttons that open the close menu in game |
| `backup_saves` / `backup_dir` / `backup_keep` | `true` / `saves_backup` / `10` | Save backups |
| `sounds` / `rumble` | `true` | Interface sounds and rumble |
| `theme` / `accent` | `dark` / `violet` | `dark` or `oled` (pure black); accent `violet`, `azure`, `mint`, `amber` or `rose` |
| `animations` / `idle_dim_minutes` | `true` / `5` | Screen fades; dim the screen when idle (`0` = never) |
| `confirm_close` | `off` | `off` turns off Eden's "are you sure?" on closing, `keep` leaves it |
| `check_updates` | `true` | Say when a newer build is on GitHub (once a day, nothing is downloaded) |
| `steamgriddb_api_key` | *(empty)* | Free key to auto-download missing covers from SteamGridDB |

Custom pictures: a square icon in `covers\<Title ID or game name>.png` for the grid, a wide banner in `covers\backgrounds\` for the background and the launch screen. Wrong player count? Add `[Players]` with `Game name = 4`.

**Restoring a save backup**: in the game grid press **Start** on the game, then "Restore a save…" (the current save is backed up first). By hand: close Eden, open the zip from `saves_backup` and extract its `0000000000000000` folder into `%APPDATA%\eden\nand\user\save\` (portable Eden: `<Eden>\user\nand\user\save\`).

## Building from source

Python 3.10+. Run `build.bat Eden` (Windows) or `./build.sh Eden` (Linux). The exe ends up in `dist\`. Tests: `python -m unittest discover -s tests`.

## Credits and license

Based on [RyujinxLauncher](https://github.com/Artomos-dev/RyujinxLauncher) by **Artomos**. Player counts and eShop art come from [blawar/titledb](https://github.com/blawar/titledb). Licensed **CC BY-NC 4.0** (non-commercial, with attribution), see [LICENSE.md](LICENSE.md).

Not affiliated with Eden, Ryujinx or Nintendo. Use only with games you legally own.
