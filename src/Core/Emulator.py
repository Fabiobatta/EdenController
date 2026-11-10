"""
Core/Emulator.py
The contract every emulator package implements.

Core defines this interface; emulator packages (Ryujinx/, ...)
implement it. Core never imports an emulator package - the entry script
picks one and hands the instance to Core.Bootstrap.run().

Lifecycle, driven by Core/Bootstrap.py in this exact order:

    0. <settings load>  "<Name>Launcher.ini" is read into self.settings
    1. locate(base)     resolve dir / exe / config_path (mount AppImage).
                        Runs before logging, because log_dir() depends on it.
    2. <logger starts>
    3. prepare()        detect version, choose SDL backend, set process env
                        vars, snapshot the child environment.
                        Runs before the SDL wrapper is imported.
    4. <SDL loads>
    5. load_profiles()  discover selectable controller profiles.
    6. <UI runs>
    7. write_input_config(...)  called on every launch.
    8. cleanup()        called on every exit path.

Attributes an implementation must set during locate():

    dir          str   emulator directory (AppImage mount point if mounted)
    exe          str   binary the launcher will start
    config_path  str   the emulator's config file
    is_appimage  bool  purely informational, used in the startup banner

and may set:

    display_name str   what the user sees ("Close {name}"), when the binary
                       found is a fork with its own name; defaults to `name`

and during prepare():

    env          dict  environment handed to the emulator process
"""


class Emulator:
    """Base class - subclass this in <Name>/<Name>.py."""

    name = "Emulator"   # branding: window title, "<Name>Path.config", log filename

    # Emulator-specific part of "<Name>Launcher.ini", appended to the generic
    # [Launcher] section when the file is first created: a string, or
    # {"en": ..., "it": ...} to follow the language of the new file
    default_settings = ""

    def __init__(self):
        self.display_name = self.name
        self.dir = None
        self.exe = None
        self.config_path = None
        self.is_appimage = False
        self.env = None
        self.profiles = {}
        self.settings = None    # Core.Settings.Settings, set by Bootstrap
        self.variants = [self]  # every emulator this launcher can start, the main one first

    # ------------------------------------------------------------------
    # 1. Where everything lives
    # ------------------------------------------------------------------
    def locate(self, base):
        """
        Resolve dir / exe / config_path from the launcher's base directory.
        Mount an AppImage if that is how this emulator ships.
        Must fail loudly (Core.Log.fatal) if the binary cannot be found.
        """
        raise NotImplementedError

    def log_dir(self):
        """Directory for launcher log files."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # 2. Runtime setup - runs before the SDL wrapper is imported
    # ------------------------------------------------------------------
    def prepare(self):
        """
        Detect the emulator version, decide the SDL backend, set any process
        environment variables the emulator needs, and snapshot self.env for
        the child process.

        The snapshot must be taken here, before load_sdl() publishes its own
        SDL_* loader variables, so those never leak into the emulator.
        """
        raise NotImplementedError

    def sdl_backend(self):
        """Return "SDL2" or "SDL3"."""
        raise NotImplementedError

    def sdl_dir(self):
        """Directory containing the SDL shared library."""
        return self.dir

    # ------------------------------------------------------------------
    # 3. Controller profiles - payload is opaque to Core
    # ------------------------------------------------------------------
    def load_profiles(self):
        """
        Return {"display name": <payload>, ...}, insertion-ordered.

        Core only ever uses the keys: it shows them in the profile selector,
        lets the D-pad cycle them, and hands the chosen key back through
        write_input_config(). The payload is never inspected by Core, so it
        can be a dict, an INI section name, or None.

        The first entry is the default for newly assigned controllers.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # 4. Writing the launch configuration
    # ------------------------------------------------------------------
    def write_input_config(self, assignments, hardware):
        """
        Persist the controller mapping in whatever format this emulator uses.

        Args:
            assignments (list[dict]): Player order, one entry per assigned pad:
                {"path": hid path, "name": display name, "profile_key": str}
            hardware    (list[dict]): Fresh SDL enumeration, in OS order:
                {"path": hid path, "guid": raw 32-char hex, "name": SDL name,
                 "face_buttons": {"south", "east", "west", "north"} raw button
                                 indices, or None if SDL cannot tell}

        Match the two by "path". Read the existing config and replace only the
        input section - never regenerate the whole file, or unrelated user
        settings are lost.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # 5. Launching and teardown
    # ------------------------------------------------------------------
    def command(self, argv):
        """Full command line. Frontend arguments (Playnite, etc.) pass through."""
        return [self.exe] + argv[1:]

    def cleanup(self):
        """Release anything locate() acquired. Called on every exit path."""
        pass

    # ------------------------------------------------------------------
    # 6. Game picker (optional)
    # ------------------------------------------------------------------
    def game_picker_enabled(self):
        """True if list_games() may offer games (START then opens the game grid)."""
        return False

    def list_games(self):
        """
        Games offered by the launcher's game picker when no game was passed
        on the command line. Runs on a background thread: it must not touch
        the UI.

        Returns:
            list[dict]: in display order, with at least {"title", "path"}.
                        Optional: "title_id", "image" (square icon path),
                        "players" (max players on one console), "icon_url",
                        "banner_url" (downloaded by Core/Art.py).
                        An empty list disables the picker.
        """
        return []

    def game_command(self, path):
        """Full command line that boots the game at `path`."""
        return [self.exe, path]

    # ------------------------------------------------------------------
    # 7. Save data (optional)
    # ------------------------------------------------------------------
    def save_folders(self, game):
        """
        Folders holding the save data of `game` (a list_games() entry), backed
        up by Core/Backup.py before each launch.

        Returns:
            (root, [folder, ...]): folders are inside root; the backup stores
                                   paths relative to root, so unzipping the
                                   backup into root restores it. (None, [])
                                   when there is nothing to back up.
        """
        return None, []

    # ------------------------------------------------------------------
    # 8. More than one emulator, diagnostics, settings screen (optional)
    # ------------------------------------------------------------------
    def emulator_for(self, game, choice=None):
        """
        The emulator (one of self.variants) that starts `game`; `choice` is
        the display name the user picked for that game, if any.
        """
        return self

    def supports_nx2(self):
        """True if this emulator runs Switch 2 games."""
        return False

    def add_variant(self, path):
        """Add the emulator whose executable is `path`; None if this launcher cannot (or it is not one)."""
        return None

    def remove_variant(self, variant):
        """Remove an emulator added with add_variant(); False if it cannot be removed."""
        return False

    def ensure_ready(self, main):
        """Called on the emulator about to start a game (profiles, environment)."""

    def last_errors(self, limit=3):
        """The last error lines of the emulator's own log, newest last."""
        return []

    def needs_firmware(self, game):
        """True if `game` will not start because the emulator lacks the console firmware it requires."""
        return False

    def window_names(self):
        """Words in the titles of the emulator's main windows (its other windows are dialogs)."""
        return []

    def game_folders(self):
        """((folder, deep scan) pairs, True if they come from the launcher's settings)."""
        return [], False

    # The emulator's own options offered on the settings screen:
    # (settings key, (values...), default), labelled by the I18n strings
    # "opt_<key>" and "val_<value>". The section is settings_section.
    settings_section = None
    setting_choices = ()

    def set_game_folders(self, folders):
        """Store the launcher's own list of game folders (empty: the emulator's)."""
