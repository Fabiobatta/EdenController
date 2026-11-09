"""
Unit tests for the Eden adapter. Run from the repository root:

    python -m unittest discover -s tests
"""

import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

# Core.Log imports tkinter for its error dialogs; CI images may lack Tk
try:
    import tkinter  # noqa: F401
except ImportError:
    tk_stub = types.ModuleType("tkinter")
    tk_stub.messagebox = types.SimpleNamespace(showerror=lambda *a, **k: None)
    sys.modules["tkinter"] = tk_stub
    sys.modules["tkinter.messagebox"] = tk_stub.messagebox

HAS_TK = hasattr(sys.modules.get("tkinter"), "Tk")

from Eden import Config  # noqa: E402
from Eden.Ini import IniFile, quote, read_bool, unquote  # noqa: E402

# Two identical virtual Xbox 360 pads, as Sunshine/Vibeshine creates them
RAW_GUID = "030003f05e0400008e02000000007801"
EDEN_GUID = "030000005e0400008e02000000007801"
OTHER_RAW = "0300aaaa4c050000cc09000000006800"   # a DualShock 4
OTHER_EDEN = "030000004c050000cc09000000006800"


def sdl_button(button, port, guid=EDEN_GUID):
    return f'"button:{button},engine:sdl,guid:{guid},port:{port}"'


QT_CONFIG = "\r\n".join([
    "[UI]",
    "theme=default",
    "fullscreen\\default=false",
    "fullscreen=true",
    "",
    "[Controls]",
    "player_0_type\\default=true",
    "player_0_type=0",
    "player_0_connected\\default=true",
    "player_0_connected=true",
    "player_0_button_a\\default=false",
    "player_0_button_a=" + sdl_button(1, 0),
    "player_0_button_b\\default=false",
    "player_0_button_b=" + sdl_button(0, 0),
    "player_0_lstick\\default=false",
    'player_0_lstick="axis_x:0,axis_y:1,deadzone:0.200000,engine:sdl,guid:'
    + EDEN_GUID + ',invert_x:+,invert_y:+,offset_x:0.000000,offset_y:0.000000,port:0,range:1.000000"',
    "player_0_vibration_enabled\\default=true",
    "player_0_vibration_enabled=true",
    "player_1_connected\\default=false",
    "player_1_connected=true",
    "player_1_button_a\\default=false",
    "player_1_button_a=" + sdl_button(1, 1),
    "enable_raw_input\\default=true",
    "enable_raw_input=false",
    "",
    "[Renderer]",
    "resolution_setup\\default=false",
    "resolution_setup=3",
    "",
])


def hardware(*pads):
    return [{"path": path, "guid": guid, "name": "Controller"} for path, guid in pads]


class GuidTests(unittest.TestCase):
    def test_crc_bytes_are_zeroed(self):
        self.assertEqual(Config.eden_guid(RAW_GUID), EDEN_GUID)
        self.assertEqual(Config.eden_guid(RAW_GUID.upper()), EDEN_GUID)

    def test_ports_count_per_guid_in_enumeration_order(self):
        pads = Config.enumerate_pads(hardware(
            ("p1", RAW_GUID), ("ds4", OTHER_RAW), ("p2", RAW_GUID), ("p3", RAW_GUID)))
        self.assertEqual([(p["path"], p["guid"], p["port"]) for p in pads], [
            ("p1", EDEN_GUID, 0), ("ds4", OTHER_EDEN, 0), ("p2", EDEN_GUID, 1), ("p3", EDEN_GUID, 2)])


class ParamTests(unittest.TestCase):
    def test_retarget_rewrites_sdl_binding(self):
        text = f"button:1,engine:sdl,guid:{EDEN_GUID},port:0"
        self.assertEqual(Config.retarget(text, OTHER_EDEN, 3),
                         f"button:1,engine:sdl,guid:{OTHER_EDEN},port:3")

    def test_retarget_collapses_guid2(self):
        text = f"button:1,engine:sdl,guid:{EDEN_GUID},guid2:{OTHER_EDEN},port:0"
        self.assertEqual(Config.retarget(text, EDEN_GUID, 2),
                         f"button:1,engine:sdl,guid:{EDEN_GUID},guid2:{EDEN_GUID},port:2")

    def test_retarget_leaves_other_engines_alone(self):
        for text in ("code:67,engine:keyboard,toggle:0", "[empty]", "engine:cemuhookudp,motion:0,pad:0,port:26760"):
            self.assertEqual(Config.retarget(text, EDEN_GUID, 5), text)

    def test_fallback_is_complete_and_sdl(self):
        mapping = Config.fallback_template()
        self.assertEqual(set(mapping), set(Config.MAPPING_KEYS))
        self.assertTrue(Config.is_sdl_mapping(mapping))
        self.assertIn("engine:sdl", mapping["button_zl"])
        self.assertTrue(mapping["button_a"].startswith("button:1,"))  # keys sorted like ParamPackage


class IniTests(unittest.TestCase):
    def test_roundtrip_is_lossless(self):
        for text in (QT_CONFIG, "﻿" + QT_CONFIG, QT_CONFIG.replace("\r\n", "\n"), "[A]\nx=1"):
            self.assertEqual(IniFile(text).dumps(), text)

    def test_set_many_replaces_and_appends_inside_section(self):
        ini = IniFile(QT_CONFIG)
        ini.set_many("Controls", {"player_1_connected": "false", "player_2_connected": "true"})
        controls = ini.items("Controls")
        self.assertEqual(controls["player_1_connected"], "false")
        self.assertEqual(controls["player_2_connected"], "true")
        # appended before the blank separator, not inside [Renderer]
        self.assertNotIn("player_2_connected", ini.items("Renderer"))
        self.assertEqual(ini.items("UI"), IniFile(QT_CONFIG).items("UI"))

    def test_missing_section_is_created(self):
        ini = IniFile("[UI]\ntheme=default\n")
        ini.set_many("Controls", {"a": "1"})
        self.assertEqual(ini.dumps(), "[UI]\ntheme=default\n\n[Controls]\na=1\n")

    def test_quote_matches_adjust_output_string(self):
        self.assertEqual(quote("button:1,engine:sdl"), '"button:1,engine:sdl"')
        self.assertEqual(quote("true"), "true")
        self.assertEqual(unquote('"a,b"'), "a,b")

    def test_read_bool_respects_default_flag(self):
        items = {"x\\default": "true", "x": "true", "y\\default": "false", "y": "true"}
        self.assertFalse(read_bool(items, "x", False))
        self.assertTrue(read_bool(items, "y", False))
        self.assertTrue(read_bool(items, "missing", True))


class ProfileTests(unittest.TestCase):
    def test_template_comes_from_player_1(self):
        template = Config.load_template(IniFile(QT_CONFIG))
        self.assertEqual(set(template), {"button_a", "button_b", "lstick"})
        self.assertIn("deadzone:0.200000", template["lstick"])

    def build(self, template_config, profile_key, face=None):
        profiles = Config.load_profiles("/nonexistent", Config.load_template(IniFile(template_config)))
        hw = [{"path": "p1", "guid": RAW_GUID, "name": "Pad", "face_buttons": face}]
        values, _ = Config.build_player_values(
            [{"path": "p1", "name": "p1", "profile_key": profile_key}], hw, profiles)
        return {k: unquote(v) for k, v in values.items()}

    def test_xbox_is_default_and_follows_labels(self):
        profiles = Config.load_profiles("/nonexistent", Config.load_template(IniFile(QT_CONFIG)))
        self.assertEqual(list(profiles)[:2], [Config.XBOX_PROFILE, Config.NINTENDO_PROFILE])
        face = {"south": 0, "east": 1, "west": 2, "north": 3}
        xbox = self.build(QT_CONFIG, Config.XBOX_PROFILE, face)
        self.assertEqual(xbox["player_0_button_a"], f"button:0,engine:sdl,guid:{EDEN_GUID},port:0")
        self.assertEqual(xbox["player_0_button_b"], f"button:1,engine:sdl,guid:{EDEN_GUID},port:0")
        self.assertEqual(xbox["player_0_button_x"], f"button:2,engine:sdl,guid:{EDEN_GUID},port:0")
        self.assertEqual(xbox["player_0_button_y"], f"button:3,engine:sdl,guid:{EDEN_GUID},port:0")
        nintendo = self.build(QT_CONFIG, Config.NINTENDO_PROFILE, face)
        self.assertTrue(nintendo["player_0_button_a"].startswith("button:1,"))
        self.assertTrue(nintendo["player_0_button_x"].startswith("button:3,"))
        self.assertEqual(xbox["player_0_lstick"], nintendo["player_0_lstick"])   # rest untouched

    def test_layout_is_stable_when_eden_saved_it_back(self):
        # Eden rewrites qt-config.ini on exit, so Player 1 may already hold the
        # Xbox layout the launcher wrote: the next launch must not swap it back
        face = {"south": 0, "east": 1, "west": 2, "north": 3}
        saved_back = QT_CONFIG.replace(sdl_button(1, 0), "TMP").replace(sdl_button(0, 0), sdl_button(1, 0)) \
                              .replace("TMP", sdl_button(0, 0))
        first = self.build(QT_CONFIG, Config.XBOX_PROFILE, face)
        second = self.build(saved_back, Config.XBOX_PROFILE, face)
        for key in ("button_a", "button_b", "button_x", "button_y"):
            self.assertEqual(first["player_0_" + key], second["player_0_" + key])

    def test_face_buttons_follow_the_pad_bindings(self):
        # A driver that numbers the face buttons differently
        face = {"south": 1, "east": 2, "west": 0, "north": 3}
        xbox = self.build(QT_CONFIG, Config.XBOX_PROFILE, face)
        self.assertTrue(xbox["player_0_button_a"].startswith("button:1,"))
        self.assertTrue(xbox["player_0_button_x"].startswith("button:0,"))
        # No binding information: SDL's XInput order
        guess = self.build(QT_CONFIG, Config.XBOX_PROFILE, None)
        self.assertTrue(guess["player_0_button_a"].startswith("button:0,"))

    def test_keyboard_player_1_falls_back(self):
        ini = IniFile("[Controls]\nplayer_0_button_a\\default=false\nplayer_0_button_a=\"code:67,engine:keyboard\"\n")
        self.assertEqual(Config.load_template(ini), Config.fallback_template())
        self.assertEqual(Config.load_template(None), Config.fallback_template())

    def test_profiles_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "Swapped.ini"), "w", encoding="utf-8") as f:
                f.write("[Controls]\ntype\\default=true\ntype=0\n"
                        "button_a\\default=false\nbutton_a=" + sdl_button(0, 0) + "\n")
            with open(os.path.join(tmp, "Keyboard.ini"), "w", encoding="utf-8") as f:
                f.write("[Controls]\nbutton_a\\default=false\nbutton_a=\"code:67,engine:keyboard\"\n")
            template = Config.load_template(IniFile(QT_CONFIG))
            profiles = Config.load_profiles(tmp, template)

        self.assertEqual(list(profiles), [Config.XBOX_PROFILE, Config.NINTENDO_PROFILE, "Swapped"])
        self.assertIn("button:0,", profiles["Swapped"]["button_a"])
        self.assertEqual(profiles["Swapped"]["button_b"], template["button_b"])  # inherited


class WriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "config", "qt-config.ini")
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w", encoding="utf-8", newline="") as f:
            f.write(QT_CONFIG)
        self.profiles = Config.load_profiles(
            os.path.join(self.tmp.name, "none"), Config.load_template(IniFile.load(self.path)))

    def tearDown(self):
        self.tmp.cleanup()

    def assignments(self, *paths):
        return [{"path": p, "name": p, "profile_key": Config.DEFAULT_PROFILE} for p in paths]

    def test_original_config_is_kept_once(self):
        hw = hardware(("p1", RAW_GUID), ("p2", RAW_GUID))
        Config.write_input(self.path, self.assignments("p1"), hw, self.profiles)
        backup = self.path + Config.ORIGINAL_SUFFIX
        with open(backup, encoding="utf-8", newline="") as f:
            self.assertEqual(f.read(), QT_CONFIG)
        Config.write_input(self.path, self.assignments("p2", "p1"), hw, self.profiles)
        with open(backup, encoding="utf-8", newline="") as f:
            self.assertEqual(f.read(), QT_CONFIG)             # never overwritten

    def test_players_follow_assignment_order_not_connection_order(self):
        # Pads connected as p1, p2, p3 but the players picked p3 then p1
        hw = hardware(("p1", RAW_GUID), ("p2", RAW_GUID), ("p3", RAW_GUID))
        Config.write_input(self.path, self.assignments("p3", "p1"), hw, self.profiles)

        with open(self.path, encoding="utf-8", newline="") as f:
            text = f.read()
        controls = IniFile(text).items("Controls")

        # Default Xbox layout: Switch A = raw button 0, the Xbox A (bottom) button
        self.assertEqual(unquote(controls["player_0_button_a"]), f"button:0,engine:sdl,guid:{EDEN_GUID},port:2")
        self.assertEqual(unquote(controls["player_1_button_a"]), f"button:0,engine:sdl,guid:{EDEN_GUID},port:0")
        self.assertEqual(unquote(controls["player_1_button_b"]), f"button:1,engine:sdl,guid:{EDEN_GUID},port:0")
        self.assertIn(f"port:2", controls["player_0_lstick"])
        self.assertIn("deadzone:0.200000", controls["player_1_lstick"])
        self.assertEqual(controls["player_1_button_b\\default"], "false")
        self.assertEqual(controls["player_1_connected"], "true")
        for p in range(2, 8):
            self.assertEqual(controls[f"player_{p}_connected"], "false")
            self.assertEqual(controls[f"player_{p}_connected\\default"], "true")

        # Everything outside the player keys is untouched, CRLF kept
        self.assertIn("\r\n", text)
        self.assertEqual(IniFile(text).items("UI"), IniFile(QT_CONFIG).items("UI"))
        self.assertEqual(IniFile(text).items("Renderer"), IniFile(QT_CONFIG).items("Renderer"))
        self.assertEqual(controls["enable_raw_input"], "false")
        self.assertEqual(controls["player_0_vibration_enabled"], "true")

    def test_no_assignment_leaves_file_untouched(self):
        Config.write_input(self.path, [], hardware(("p1", RAW_GUID)), self.profiles)
        with open(self.path, encoding="utf-8", newline="") as f:
            self.assertEqual(f.read(), QT_CONFIG)

    def test_single_player_disconnects_player_1_default_correctly(self):
        values, written = Config.build_player_values(
            self.assignments("p2"), hardware(("p1", RAW_GUID), ("p2", RAW_GUID)), self.profiles)
        self.assertEqual(written, 1)
        self.assertEqual(values["player_0_connected\\default"], "true")
        self.assertEqual(values["player_1_connected\\default"], "true")
        self.assertEqual(values["player_1_connected"], "false")
        self.assertIn("port:1", values["player_0_button_a"])


@unittest.skipIf(sys.platform in ("win32", "darwin"), "exercises the Linux path layout")
class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved_env = dict(os.environ)
        self.saved_cwd = os.getcwd()
        os.chdir(self.tmp.name)  # no portable "user" folder here

        self.eden_dir = os.path.join(self.tmp.name, "eden")
        os.makedirs(self.eden_dir)
        open(os.path.join(self.eden_dir, "eden"), "w").close()

        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.tmp.name, "cfg")
        os.environ["XDG_DATA_HOME"] = os.path.join(self.tmp.name, "data")
        os.environ.pop("EDEN_LAUNCHER_USER_DIR", None)
        os.environ.pop("EDEN_LAUNCHER_SDL", None)
        config_dir = os.path.join(self.tmp.name, "cfg", "eden")
        os.makedirs(config_dir)
        with open(os.path.join(config_dir, "qt-config.ini"), "w", encoding="utf-8") as f:
            f.write("[Controls]\nenable_raw_input\\default=false\nenable_raw_input=true\n")

    def tearDown(self):
        os.chdir(self.saved_cwd)
        os.environ.clear()
        os.environ.update(self.saved_env)
        self.tmp.cleanup()

    def test_locate_and_prepare(self):
        from Eden import Eden
        emu = Eden()
        emu.locate(self.eden_dir)
        self.assertEqual(emu.exe, os.path.join(self.eden_dir, "eden"))
        self.assertEqual(emu.config_path, os.path.join(self.tmp.name, "cfg", "eden", "qt-config.ini"))
        self.assertEqual(emu.log_dir(), os.path.join(self.tmp.name, "data", "eden", "log"))

        emu.prepare()
        self.assertEqual(os.environ["SDL_JOYSTICK_RAWINPUT"], "1")      # read from qt-config.ini
        self.assertEqual(os.environ["SDL_JOYSTICK_HIDAPI_XBOX"], "0")
        # The hints are for the launcher's SDL only - Eden sets its own
        self.assertNotIn("SDL_JOYSTICK_HIDAPI_XBOX", emu.env)
        self.assertEqual(emu.command(["launcher", "-f", "-g", "game.nsp"]),
                         [emu.exe, "-f", "-g", "game.nsp"])

    def test_layout_setting_orders_profiles(self):
        from Core.Settings import Settings
        from Eden import Eden
        emu = Eden()
        emu.locate(self.eden_dir)
        emu.settings = Settings({"eden": {"layout": "nintendo"}})
        self.assertEqual(list(emu.load_profiles())[0], Config.NINTENDO_PROFILE)
        emu.settings = Settings({"eden": {"layout": "does-not-exist"}})
        self.assertEqual(list(emu.load_profiles())[0], Config.XBOX_PROFILE)

    def test_game_command_and_docked_policy(self):
        from Core.Settings import Settings
        from Eden import Eden
        emu = Eden()
        emu.locate(self.eden_dir)
        emu.settings = Settings({"eden": {"fullscreen": "true"}})
        self.assertEqual(emu.game_command("g.nsp"), [emu.exe, "-f", "-g", "g.nsp"])
        emu.settings = Settings({"eden": {"fullscreen": "false"}})
        self.assertEqual(emu.game_command("g.nsp"), [emu.exe, "-g", "g.nsp"])

    def test_eglantine_binary(self):
        from Eden import Eden
        os.remove(os.path.join(self.eden_dir, "eden"))
        open(os.path.join(self.eden_dir, "eglantine"), "w").close()
        emu = Eden()
        emu.locate(self.eden_dir)
        self.assertEqual((emu.display_name, emu.exe), ("Eglantine", os.path.join(self.eden_dir, "eglantine")))
        # Same data folder and config file as Eden
        self.assertEqual(emu.config_path, os.path.join(self.tmp.name, "cfg", "eden", "qt-config.ini"))
        open(os.path.join(self.eden_dir, "eden"), "w").close()       # both: Eden first
        emu = Eden()
        emu.locate(self.eden_dir)
        self.assertEqual((emu.display_name, os.path.basename(emu.exe)), ("Eden", "eden"))

    def test_lichii_and_other_forks(self):
        from Core.Settings import Settings
        from Eden import Eden
        from Eden.Eden import _find_fork, fork_name
        os.remove(os.path.join(self.eden_dir, "eden"))
        open(os.path.join(self.eden_dir, "lichii"), "w").close()
        emu = Eden()
        emu.locate(self.eden_dir)
        self.assertEqual((emu.display_name, emu.exe), ("Lichii", os.path.join(self.eden_dir, "lichii")))
        self.assertEqual(emu.config_path, os.path.join(self.tmp.name, "cfg", "eden", "qt-config.ini"))

        # Named in the settings, with its own data folder once it has a config
        own = os.path.join(self.tmp.name, "cfg", "citron")
        os.makedirs(own)
        open(os.path.join(own, "qt-config.ini"), "w").close()
        open(os.path.join(self.eden_dir, "citron"), "w").close()
        emu = Eden()
        emu.settings = Settings({"eden": {"emulator_exe": "citron"}})
        emu.locate(self.eden_dir)
        self.assertEqual((emu.display_name, os.path.basename(emu.exe)), ("Citron", "citron"))
        self.assertEqual(emu.config_path, os.path.join(own, "qt-config.ini"))

        # Unknown name in the folder: the binary with the Qt config file name in it
        folder = os.path.join(self.tmp.name, "forks")
        os.makedirs(folder)
        for name, data in (("aaa-cli.exe", b"qt-config.ini"), ("EdenLauncher.exe", b"qt-config.ini"),
                           ("helper.exe", b"nothing"), ("Mystery.exe", b"..qt-config.ini..")):
            with open(os.path.join(folder, name), "wb") as f:
                f.write(data)
        found = _find_fork(folder)
        self.assertEqual(os.path.basename(found), "Mystery.exe")
        self.assertEqual(fork_name(found), "Mystery")
        self.assertEqual(fork_name("x/LICHII.EXE"), "Lichii")

    def test_missing_binary_names_both(self):
        import importlib
        adapter = importlib.import_module("Eden.Eden")
        os.remove(os.path.join(self.eden_dir, "eden"))
        calls = []
        saved, adapter.fatal = adapter.fatal, lambda *a: calls.append(a)
        try:
            adapter.Eden().locate(self.eden_dir)
        finally:
            adapter.fatal = saved
        self.assertIn("eden / eglantine", calls[0][1])

    def test_applet_scheme_from_the_binary(self):
        import importlib
        adapter = importlib.import_module("Eden.Eden")
        from Eden import Eden
        cache = {}
        settings = types.SimpleNamespace(load_cache=lambda name: dict(cache),
                                         save_cache=lambda name, data: cache.update(data))
        saved = adapter.PROBE_CHUNK
        adapter.PROBE_CHUNK = 16               # the key straddles two reads
        try:
            for content, scheme in ((b"x" * 30 + b"disableControllerApplet" + b"controller_applet_mode", "ui"),
                                    (b"y" * 13 + b"controller_applet_mode" + b"z" * 40, "mode"),
                                    (b"nothing to see here", "ui")):
                exe = os.path.join(self.eden_dir, "eden")
                with open(exe, "wb") as f:
                    f.write(content)
                os.utime(exe, (1, len(content)))
                cache.clear()
                emu = Eden()
                emu.locate(self.eden_dir)
                emu.settings = settings
                self.assertEqual(emu.applet_scheme(), scheme)
                self.assertEqual(cache[exe][2], scheme)
            # Remembered: a cached answer for the same size and date is reused
            cache[exe][2] = "mode"
            self.assertEqual(emu.applet_scheme(), "mode")
        finally:
            adapter.PROBE_CHUNK = saved

    def test_portable_user_folder_wins(self):
        from Eden import Eden
        os.makedirs(os.path.join(self.tmp.name, "user", "config"))
        emu = Eden()
        emu.locate(self.eden_dir)
        self.assertEqual(emu.config_path, os.path.join(self.tmp.name, "user", "config", "qt-config.ini"))


class ReconcileTests(unittest.TestCase):
    # Same Xbox 360 model (vendor 045e, product 028e) seen through two SDL drivers
    LAUNCHER_RAW = "030003f05e0400008e02000000007200"   # e.g. XInput ('x' = 0x78 would differ)
    EDEN = "030000005e0400008e02000000007801"

    def test_guid_mismatch_uses_edens_guid_and_ports(self):
        pads = Config.enumerate_pads(
            hardware(("p1", self.LAUNCHER_RAW), ("p2", self.LAUNCHER_RAW)), {self.EDEN})
        self.assertEqual([(p["guid"], p["port"]) for p in pads], [(self.EDEN, 0), (self.EDEN, 1)])

    def test_matching_or_ambiguous_guids_are_kept(self):
        mine = Config.eden_guid(self.LAUNCHER_RAW)
        self.assertEqual(Config.reconcile_guid(mine, {mine, self.EDEN}), mine)
        other = "030000005e0400008e02000000009999"
        self.assertEqual(Config.reconcile_guid(mine, {self.EDEN, other}), mine)   # two candidates
        self.assertEqual(Config.reconcile_guid(mine, {OTHER_EDEN}), mine)          # other model
        self.assertEqual(Config.reconcile_guid(mine, set()), mine)

    def test_write_input_uses_guids_from_qt_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "qt-config.ini")
            with open(path, "w", encoding="utf-8") as f:
                f.write(QT_CONFIG)       # Eden's bindings use EDEN_GUID
            profiles = Config.load_profiles(tmp, Config.load_template(IniFile.load(path)))
            assignments = [{"path": "p1", "name": "p1", "profile_key": Config.DEFAULT_PROFILE}]
            Config.write_input(path, assignments, hardware(("p1", self.LAUNCHER_RAW)), profiles)
            controls = IniFile.load(path).items("Controls")
        self.assertIn(f"guid:{EDEN_GUID}", controls["player_0_button_a"])


class AppletTests(unittest.TestCase):
    def test_controller_applet_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "qt-config.ini")
            with open(path, "w", encoding="utf-8") as f:
                f.write(QT_CONFIG)
            profiles = Config.load_profiles(tmp, Config.load_template(IniFile.load(path)))
            args = ([{"path": "p1", "name": "p1", "profile_key": Config.DEFAULT_PROFILE}],
                    hardware(("p1", RAW_GUID)), profiles)

            Config.write_input(path, *args, disable_controller_applet=None)
            self.assertNotIn("disableControllerApplet", IniFile.load(path).items("UI"))
            Config.write_input(path, *args, disable_controller_applet=True)
            ui = IniFile.load(path).items("UI")
            self.assertEqual((ui["disableControllerApplet"], ui["disableControllerApplet\\default"]),
                             ("true", "false"))
            self.assertEqual(ui["theme"], "default")   # rest of [UI] untouched

    def test_controller_applet_mode_on_newer_builds(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "qt-config.ini")
            with open(path, "w", encoding="utf-8") as f:
                f.write(QT_CONFIG)
            profiles = Config.load_profiles(tmp, Config.load_template(IniFile.load(path)))
            args = ([{"path": "p1", "name": "p1", "profile_key": Config.DEFAULT_PROFILE}],
                    hardware(("p1", RAW_GUID)), profiles)
            mode = lambda: IniFile.load(path).items("LibraryApplet")              # noqa: E731

            Config.write_input(path, *args, disable_controller_applet=True, applet_scheme=Config.APPLET_MODE)
            self.assertEqual((mode()["controller_applet_mode"], mode()["controller_applet_mode\\default"]),
                             ("2", "false"))
            self.assertNotIn("disableControllerApplet", IniFile.load(path).items("UI"))
            # "on" undoes only the launcher's own Disabled ...
            Config.write_input(path, *args, disable_controller_applet=False, applet_scheme=Config.APPLET_MODE)
            self.assertEqual((mode()["controller_applet_mode"], mode()["controller_applet_mode\\default"]),
                             ("0", "true"))
            # ... and leaves a user's LLE choice alone
            ini = IniFile.load(path)
            ini.set_many("LibraryApplet", {"controller_applet_mode\\default": "false", "controller_applet_mode": "1"})
            ini.save(path)
            Config.write_input(path, *args, disable_controller_applet=False, applet_scheme=Config.APPLET_MODE)
            self.assertEqual(mode()["controller_applet_mode"], "1")


def _pfs0(files, magic=b"PFS0", entry_size=0x18):
    """Build a PFS0/HFS0 container from [(name, data)]."""
    import struct
    names = b"".join(n.encode() + b"\0" for n, _ in files)
    entries, offset, name_offset = b"", 0, 0
    for name, data in files:
        entry = struct.pack("<QQI", offset, len(data), name_offset)
        entries += entry + b"\0" * (entry_size - len(entry))
        offset += len(data)
        name_offset += len(name) + 1
    header = magic + struct.pack("<III", len(files), len(names), 0)
    return header + entries + names + b"".join(d for _, d in files)


def _nca(key, content_type, program_id):
    """A fake NCA: a real, encrypted 0xC00 header and nothing else."""
    import struct
    from Eden.Switch import xts_encrypt
    header = bytearray(0xC00)
    header[0x200:0x204] = b"NCA3"
    header[0x205] = content_type
    struct.pack_into("<Q", header, 0x210, int(program_id, 16))
    return xts_encrypt(key, bytes(header))


def _xci(secure_files):
    import struct
    secure = _pfs0(secure_files, b"HFS0", 0x40)
    root = _pfs0([("update", b""), ("secure", secure)], b"HFS0", 0x40)
    header = bytearray(0x200)
    header[0x100:0x104] = b"HEAD"
    struct.pack_into("<Q", header, 0x130, 0x200)
    return bytes(header) + root


class SwitchTests(unittest.TestCase):
    KEY = bytes(range(32))

    def test_xts_matches_ieee_1619_vector(self):
        from Eden.Switch import xts_decrypt, xts_encrypt
        # IEEE 1619-2007 vector 2 (little-endian data unit number, as the standard defines it)
        key = bytes([0x11] * 16 + [0x22] * 16)
        plain = bytes([0x44] * 32)
        cipher = bytes.fromhex("c454185e6a16936e39334038acef838bfb186fff7480adc4289382ecd6d394f0")
        self.assertEqual(xts_encrypt(key, plain, 0x3333333333, tweak_order="little"), cipher)
        self.assertEqual(xts_decrypt(key, cipher, 0x3333333333, tweak_order="little"), plain)

    def test_title_kinds(self):
        from Eden.Switch import BASE, DLC, UPDATE, base_title_id, title_kind
        self.assertEqual(title_kind("0100152000022000"), BASE)
        self.assertEqual(title_kind("0100152000022800"), UPDATE)
        self.assertEqual(title_kind("0100152000023001"), DLC)
        self.assertEqual(base_title_id("0100152000022800"), "0100152000022000")
        self.assertEqual(base_title_id("0100152000023001"), "0100152000022000")

    def test_identify_nsp_and_xci(self):
        from Eden.Switch import identify, load_header_key
        with tempfile.TemporaryDirectory() as tmp:
            def write(name, data):
                path = os.path.join(tmp, name)
                with open(path, "wb") as f:
                    f.write(data)
                return path

            keys = write("prod.keys", ("header_key = " + self.KEY.hex() + "\n").encode())
            key = load_header_key(keys)
            self.assertEqual(key, self.KEY)

            # Ticket name: no key needed
            tik = write("a.nsp", _pfs0([("0100152000022800" + "0" * 16 + ".tik", b"x"),
                                        ("abc.nca", b"y" * 0x10)]))
            self.assertEqual(identify(tik), ("0100152000022800", "update"))

            # CNMT XML
            xml = write("b.nsp", _pfs0([("abc.cnmt.xml", b"<Id>0x0100152000022000</Id>")]))
            self.assertEqual(identify(xml), ("0100152000022000", "base"))

            # Encrypted NCA headers: the Meta NCA carries the content's ID
            update = write("c.nsp", _pfs0([
                ("p.nca", _nca(self.KEY, 0, "0100152000022000")),   # patch program: base ID
                ("m.cnmt.nca", _nca(self.KEY, 1, "0100152000022800")),
            ]))
            self.assertEqual(identify(update, key), ("0100152000022800", "update"))
            self.assertEqual(identify(update, None), (None, None))           # no keys
            self.assertEqual(identify(update, bytes(32)), (None, None))      # wrong keys

            xci = write("d.xci", _xci([("m.cnmt.nca", _nca(self.KEY, 1, "01006A800016E000")),
                                       ("p.nca", _nca(self.KEY, 0, "01006A800016E000"))]))
            self.assertEqual(identify(xci, key), ("01006A800016E000", "base"))
            self.assertEqual(identify(write("e.nsp", b"garbage")), (None, None))

            # Compressed forms: NCZ files keep the encrypted NCA header
            nsz = write("f.nsz", _pfs0([("m.cnmt.nca", _nca(self.KEY, 1, "0100152000022000")),
                                        ("p.ncz", _nca(self.KEY, 0, "0100152000022000"))]))
            self.assertEqual(identify(nsz, key), ("0100152000022000", "base"))
            xcz = write("g.xcz", _xci([("m.cnmt.nca", _nca(self.KEY, 1, "01006A800016E000"))]))
            self.assertEqual(identify(xcz, key), ("01006A800016E000", "base"))


def _npdm(program_id):
    """A minimal NPDM: META header pointing at an ACI0 with the program ID."""
    import struct
    data = bytearray(0xC0)
    data[0:4] = b"META"
    struct.pack_into("<II", data, 0x70, 0x80, 0x40)
    data[0x80:0x84] = b"ACI0"
    struct.pack_into("<Q", data, 0x90, int(program_id, 16))
    return bytes(data)


def _nup(packages):
    """
    A NUP archive (Eglantine's layout) from [(title_id, kind, version, has_exefs, {tlv type: bytes})].
    Only the superblock, TOC header, package records and meta TLVs are real.
    """
    import struct
    from Eden import Nx2
    count = len(packages)
    toc_size = Nx2.TOC_HEADER.size + count * Nx2.PACKAGE_RECORD.size
    meta_start = Nx2.NUP_SUPERBLOCK_SIZE + toc_size
    metas, records, offset = b"", b"", meta_start
    for title_id, kind, version, has_exefs, meta in packages:
        block = b"".join(Nx2.TLV_HEADER.pack(t, 0, len(v)) + v for t, v in meta.items())
        records += Nx2.PACKAGE_RECORD.pack(int(title_id, 16), 0, 1 if has_exefs else Nx2.NO_DIR, Nx2.NO_DIR,
                                           version, kind, 0, 0, 0, offset if block else 0, len(block), bytes(20))
        metas += block
        offset += len(block)
    toc = Nx2.TOC_HEADER.pack(Nx2.TOC_MAGIC, count, 1, 0, toc_size, 0, 0) + records
    size = meta_start + len(metas)
    superblock = Nx2.NUP_SUPERBLOCK.pack(Nx2.NUP_MAGIC, 0x80, 1, Nx2.NUP_SUPERBLOCK_SIZE, toc_size, bytes(32),
                                         size, size, count, 0)
    return superblock + bytes(Nx2.NUP_SUPERBLOCK_SIZE - len(superblock)) + toc + metas


class Nx2Tests(unittest.TestCase):
    PNG = b"\x89PNG\r\n\x1a\nfake-icon"

    def test_read_nup(self):
        from Eden import Nx2
        base, update = "0100B3F000BE2000", "0100B3F000BE2800"
        cases = [
            # Base + update: the update's name/version/icon win, the base's ID is the game's
            ([(base, Nx2.KIND_BASE, 0, True, {Nx2.META_TITLE_NAME: b"Old name"}),
              (update, Nx2.KIND_UPDATE, 917504, True, {Nx2.META_TITLE_NAME: b"Mario Kart World",
                                                       Nx2.META_DISPLAY_VERSION: b"1.8.0",
                                                       Nx2.META_ICON: self.PNG, 0x000A: b"x" * 50})],
             {"title_id": base, "kind": "base", "name": "Mario Kart World", "version": "1.8.0", "icon": self.PNG}),
            # An update that carries its executable boots on its own
            ([(update, Nx2.KIND_UPDATE, 1, True, {})], {"title_id": base, "kind": "base"}),
            ([(update, Nx2.KIND_UPDATE, 1, False, {})], {"title_id": base, "kind": "update"}),
            ([("0100B3F000BE3001", Nx2.KIND_ADDON, 0, False, {})], {"kind": "extra"}),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "game.nup")
            for packages, expected in cases:
                with open(path, "wb") as f:
                    f.write(_nup(packages))
                info = Nx2.read_nup(path)
                self.assertEqual({k: info[k] for k in expected}, expected)
            with open(path, "wb") as f:
                f.write(b"NUP\x01" + b"\0" * 20)
            self.assertIsNone(Nx2.read_nup(path))
        self.assertEqual(Nx2.npdm_program_id(_npdm("0100B3F000BE2000")), "0100B3F000BE2000")
        self.assertIsNone(Nx2.npdm_program_id(b"nope" * 40))

    def _dump(self, root, parts, program_id="0100A1B2C3D4E000"):
        for part in parts:
            exefs = os.path.join(root, *part, "exefs")
            os.makedirs(exefs)
            open(os.path.join(exefs, "main"), "wb").close()
            with open(os.path.join(exefs, "main.npdm"), "wb") as f:
                f.write(_npdm(program_id))
            os.makedirs(os.path.join(root, *part, "romfs"))

    def test_loose_dumps(self):
        from Eden import Nx2
        with tempfile.TemporaryDirectory() as tmp:
            game = os.path.join(tmp, "Pokemon Pokopia v1.2")
            self._dump(game, [("base",), ("updates", "65536"), ("updates", "655360")])
            main = Nx2.loose_dump(game)
            self.assertEqual(main, os.path.join(game, "updates", "655360", "exefs", "main"))  # newest update
            self.assertEqual(Nx2.dump_root(main), game)
            self.assertEqual(Nx2.dump_title_id(main), "0100A1B2C3D4E000")
            bare = os.path.join(tmp, "Bare")
            self._dump(bare, [()])
            self.assertEqual(Nx2.loose_dump(bare), os.path.join(bare, "exefs", "main"))
            self.assertIsNone(Nx2.loose_dump(tmp))

    def test_game_list_finds_switch2_games(self):
        from Eden.Games import find_games
        from Eden import Nx2
        with tempfile.TemporaryDirectory() as tmp:
            games_dir = os.path.join(tmp, "games")
            os.makedirs(games_dir)
            with open(os.path.join(games_dir, "mkw.nup"), "wb") as f:
                f.write(_nup([("0100B3F000BE2000", Nx2.KIND_BASE, 0, True,
                               {Nx2.META_TITLE_NAME: b"Mario Kart World", Nx2.META_ICON: self.PNG})]))
            self._dump(os.path.join(games_dir, "Pokemon Pokopia v1.2"), [("base",)])
            # A file inside a dump's RomFS is game data, never a game
            open(os.path.join(games_dir, "Pokemon Pokopia v1.2", "base", "romfs", "data.nsp"), "wb").close()
            icons = os.path.join(tmp, "icons")
            for deep in (False, True):          # dumps are found even without "scan subfolders"
                games = find_games([(games_dir, deep)], identify=lambda p: (None, None), icon_dir=icons)
                self.assertEqual([(g["title"], os.path.basename(g["path"]), g["title_id"]) for g in games],
                                 [("Mario Kart World", "mkw.nup", "0100B3F000BE2000"),
                                  ("Pokemon Pokopia v1.2", "main", "0100A1B2C3D4E000")])
            with open(games[0]["image"], "rb") as f:
                self.assertEqual(f.read(), self.PNG)
            self.assertTrue(games[0]["image"].endswith("0100B3F000BE2000.png"))


class SettingsTests(unittest.TestCase):
    def test_defaults_are_created_and_user_values_win(self):
        from Core.Settings import load_settings
        with tempfile.TemporaryDirectory() as tmp:
            settings = load_settings(tmp, "TestLauncher", "[Eden]\nlayout = Xbox\ndocked = auto\n")
            self.assertTrue(os.path.exists(os.path.join(tmp, "TestLauncher.ini")))
            self.assertEqual(settings.get("Launcher", "language"), "auto")
            self.assertTrue(settings.get_bool("Launcher", "rumble"))

            with open(os.path.join(tmp, "TestLauncher.ini"), "w", encoding="utf-8") as f:
                f.write("[Launcher]\nlanguage = it\nrumble = false\n"
                        "[Eden]\ngame_dirs = D:\\Giochi ; E:\\Altri\n")
            settings = load_settings(tmp, "TestLauncher", "[Eden]\nlayout = Xbox\n")
            self.assertEqual(settings.get("launcher", "LANGUAGE"), "it")
            self.assertFalse(settings.get_bool("Launcher", "rumble", True))
            self.assertEqual(settings.get("Eden", "layout"), "Xbox")      # missing -> default
            self.assertEqual(settings.get_list("Eden", "game_dirs"), ["D:\\Giochi", "E:\\Altri"])

            settings.save_state(last_game="x.nsp")
            self.assertEqual(load_settings(tmp, "TestLauncher").state, {"last_game": "x.nsp"})

    def test_new_file_in_the_windows_language(self):
        from Core.Settings import LAUNCHER_DEFAULTS, _parse, load_settings
        from Eden.Eden import DEFAULT_SETTINGS
        # Both languages define exactly the same options
        self.assertEqual({s: set(v) for s, v in _parse(LAUNCHER_DEFAULTS["en"]).items()},
                         {s: set(v) for s, v in _parse(LAUNCHER_DEFAULTS["it"]).items()})
        self.assertEqual(_parse(DEFAULT_SETTINGS["en"]), _parse(DEFAULT_SETTINGS["it"]))
        for language, marker in (("en", "; Interface language"), ("it", "; Lingua dell'interfaccia")):
            with tempfile.TemporaryDirectory() as tmp:
                settings = load_settings(tmp, "TestLauncher", DEFAULT_SETTINGS, language=language)
                with open(os.path.join(tmp, "TestLauncher.ini"), encoding="utf-8") as f:
                    text = f.read()
                self.assertIn(marker, text)
                self.assertIn("%USERPROFILE%\\OneDrive\\", text)     # single backslashes in the file
                self.assertEqual(settings.get("Eden", "docked"), "auto")


class I18nTests(unittest.TestCase):
    def test_languages_have_the_same_keys(self):
        import Eden.Eden  # noqa: F401  (registers the Eden strings)
        from Core.I18n import STRINGS
        self.assertEqual(set(STRINGS["en"]), set(STRINGS["it"]))

    def test_selection_and_fallback(self):
        from Core import I18n
        try:
            self.assertEqual(I18n.set_language("it"), "it")
            self.assertEqual(I18n.t("title_players"), "Chi gioca?")
            self.assertEqual(I18n.players_label(1), "1 giocatore")
            self.assertEqual(I18n.players_label(4), "1–4 giocatori")
            self.assertEqual(I18n.set_language("xx"), "en")
            self.assertEqual(I18n.t("missing_key"), "missing_key")
        finally:
            I18n.set_language("en")


class ComboTests(unittest.TestCase):
    def test_parse_combo(self):
        from Core.Settings import BUTTON_NAMES, parse_combo
        sdl = types.SimpleNamespace(**{name: i for i, name in enumerate(sorted(set(BUTTON_NAMES.values())))})
        self.assertEqual(parse_combo("Back + Start", sdl),
                         [getattr(sdl, BUTTON_NAMES["back"]), getattr(sdl, BUTTON_NAMES["start"])])
        default = parse_combo("back+lb+rb", sdl)
        self.assertEqual(parse_combo("back+nope", sdl), default)
        self.assertEqual(parse_combo("", sdl), default)


class GamesTests(unittest.TestCase):
    def test_eden_game_dirs(self):
        from Eden.Games import eden_game_dirs
        items = {
            "Paths\\gamedirs\\size": "3",
            "Paths\\gamedirs\\1\\path": "SDMC",
            "Paths\\gamedirs\\2\\path": '"D:/Giochi Switch"',
            "Paths\\gamedirs\\2\\deep_scan\\default": "false",
            "Paths\\gamedirs\\2\\deep_scan": "true",
            "Paths\\gamedirs\\3\\path": "E:/Altri",
        }
        self.assertEqual(eden_game_dirs(items), [
            (os.path.normpath("D:/Giochi Switch"), True), (os.path.normpath("E:/Altri"), False)])

    def test_find_games_by_title_id_with_eden_cache(self):
        from Eden.Games import find_games
        ids = {
            "Dont Starve Together.nsp": ("0100AAAA00000000", "base"),
            "Dont Starve Together v1.28.0.nsp": ("0100AAAA00000800", "update"),
            "Strikers.xci": ("0100BBBB00000000", "base"),
            "Strikers Update 1.3.2.nsp": ("0100BBBB00000800", "update"),
            "Zz Strikers copy.nsp": ("0100BBBB00000000", "base"),   # same game twice
            "Unknown Game.nsp": (None, None),
            "Unknown Game v1.0.3.nsp": (None, None),                # unreadable: guessed by name
        }
        with tempfile.TemporaryDirectory() as tmp:
            games_dir = os.path.join(tmp, "games")
            cache = os.path.join(tmp, "cache", "game_list")
            os.makedirs(games_dir)
            os.makedirs(cache)
            for name in ids:
                open(os.path.join(games_dir, name), "w").close()
            with open(os.path.join(cache, "0100AAAA00000000.appname.txt"), "w", encoding="utf-8") as f:
                f.write("Don't Starve Together")
            with open(os.path.join(cache, "0100AAAA00000000.jpeg"), "wb") as f:
                f.write(b"jpeg")
            games = find_games([(games_dir, False)], cache_dir=os.path.join(tmp, "cache"),
                               identify=lambda path: ids[os.path.basename(path)])
        self.assertEqual([g["title"] for g in games], ["Don't Starve Together", "Strikers", "Unknown Game"])
        self.assertTrue(games[0]["image"].endswith("0100AAAA00000000.jpeg"))
        self.assertIsNone(games[1]["image"])
        self.assertEqual(games[1]["title_id"], "0100BBBB00000000")

    def test_find_games_hides_updates_and_dlc(self):
        from Eden.Games import clean_title, find_games
        self.assertEqual(clean_title("Super Game [0100ABCD12340000][v0] (USA).nsp"), "Super Game (USA)")
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "sub"))
            for name in ("Zelda [01007EF00011E000][v0].nsp", "Zelda [01007EF00011E800][v5].nsp",
                         "Zelda [01007EF00011F001][v0].nsp", "Kart.xci", "Kart.nsp",
                         "readme.txt", "sub/Deep Game.nsp"):
                open(os.path.join(tmp, name), "w").close()
            no_id = lambda path: (None, None)  # noqa: E731
            shallow = [g["title"] for g in find_games([(tmp, False)], identify=no_id)]
            deep = [g["title"] for g in find_games([(tmp, True)], identify=no_id)]
        self.assertEqual(shallow, ["Kart (NSP)", "Kart (XCI)", "Zelda"])
        self.assertEqual(deep, ["Deep Game", "Kart (NSP)", "Kart (XCI)", "Zelda"])


class DockedTests(unittest.TestCase):
    def test_force_docked_is_written_to_system(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "qt-config.ini")
            with open(path, "w", encoding="utf-8") as f:
                f.write(QT_CONFIG)
            profiles = Config.load_profiles(tmp, Config.load_template(IniFile.load(path)))
            hw = hardware(("p1", RAW_GUID), ("p2", RAW_GUID))
            assignments = [{"path": p, "name": p, "profile_key": Config.DEFAULT_PROFILE} for p in ("p1", "p2")]

            Config.write_input(path, assignments, hw, profiles, force_docked=False)
            self.assertFalse(IniFile.load(path).has_section("System"))

            Config.write_input(path, assignments, hw, profiles, force_docked=True)
            system = IniFile.load(path).items("System")
        self.assertEqual(system["use_docked_mode"], "1")
        self.assertEqual(system["use_docked_mode\\default"], "true")


class TitleDbTests(unittest.TestCase):
    def test_lookup(self):
        import gzip
        import json
        from Eden.TitleDb import CDN, TitleDb
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "titledb.json.gz")
            with gzip.open(path, "wt", encoding="utf-8") as f:
                json.dump({"v": 1, "games": {"0100152000022000": [4, "Mario Kart 8 Deluxe", "i.jpg", "b.jpg"],
                                             "0100AAAA00000000": [None, "No Count", None, None]}}, f)
            db = TitleDb.load(path)
        self.assertEqual(db.get("0100152000022000"), {"players": 4, "name": "Mario Kart 8 Deluxe",
                                                      "icon_url": CDN + "i.jpg", "banner_url": CDN + "b.jpg"})
        self.assertIsNone(db.get("0100AAAA00000000")["players"])
        self.assertIsNone(db.get("0100BBBB00000000"))
        self.assertEqual(TitleDb.load(os.path.join("nope", "missing.gz")).games, {})

    def test_index_builder_keeps_base_games_only(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        from build_titledb_index import build
        cdn = "https://img-eshop.cdn.nintendo.net/i/" + "a" * 64 + ".jpg"
        us = {"1": {"id": "0100152000022000", "name": "Mario Kart™ 8 Deluxe", "numberOfPlayers": 4,
                    "iconUrl": cdn, "bannerUrl": None},
              "2": {"id": "0100152000022800", "name": "Update", "numberOfPlayers": 4},
              "3": {"id": "0100AAAA00000000", "name": "Demo", "isDemo": True, "numberOfPlayers": 2}}
        gb = {"1": {"id": "0100152000022000", "name": "Other name", "numberOfPlayers": 8,
                    "bannerUrl": cdn}}
        self.assertEqual(build([us, gb]), {"0100152000022000": [4, "Mario Kart 8 Deluxe", "a" * 64 + ".jpg",
                                                                 "a" * 64 + ".jpg"]})


class LibraryCacheTests(unittest.TestCase):
    def test_identify_runs_once_per_file_version(self):
        from Eden.Games import cached_identify
        calls = []
        identify = cached_identify(lambda p: calls.append(p) or ("0100152000022000", "base"), known := {})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "game.nsp")
            with open(path, "w") as f:
                f.write("x")
            self.assertEqual(identify(path), ("0100152000022000", "base"))
            self.assertEqual(identify(path), ("0100152000022000", "base"))
            self.assertEqual(len(calls), 1)
            with open(path, "w") as f:
                f.write("changed")                       # size changes -> read again
            identify(path)
            self.assertEqual(len(calls), 2)
        self.assertIn(path, known)

    def test_settings_cache_roundtrip(self):
        from Core.Settings import load_settings
        with tempfile.TemporaryDirectory() as tmp:
            settings = load_settings(tmp, "TestLauncher")
            self.assertEqual(settings.load_cache("library"), {})
            settings.save_cache("library", {"a.nsp": [1, 2, "0100152000022000", "base"]})
            self.assertEqual(load_settings(tmp, "TestLauncher").load_cache("library"),
                             {"a.nsp": [1, 2, "0100152000022000", "base"]})


class ArtTests(unittest.TestCase):
    def test_resolve_prefers_local_files(self):
        from Core.Art import ArtLibrary
        with tempfile.TemporaryDirectory() as tmp:
            art = ArtLibrary(tmp, eshop=True)
            os.makedirs(os.path.join(tmp, "backgrounds"))
            os.makedirs(os.path.join(tmp, "eshop"))
            open(os.path.join(tmp, "0100152000022000.png"), "w").close()
            open(os.path.join(tmp, "eshop", "icon123.jpg"), "w").close()
            game = {"title": "Mario Kart 8 Deluxe", "title_id": "0100152000022000", "image": None,
                    "icon_url": "https://img-eshop.cdn.nintendo.net/i/icon123.jpg",
                    "banner_url": "https://img-eshop.cdn.nintendo.net/i/banner456.jpg"}
            art.resolve(game)
            self.assertTrue(game["cover"].endswith("0100152000022000.png"))
            self.assertTrue(game["image"].endswith(os.path.join("eshop", "icon123.jpg")))  # cached download
            self.assertIsNone(game["background"])                                       # not downloaded yet
            jobs = art._jobs([game])
            self.assertEqual([key for _, key, _ in jobs], ["background"])

            open(os.path.join(tmp, "backgrounds", "Mario Kart 8 Deluxe.jpg"), "w").close()
            art.resolve(game)
            self.assertTrue(game["background"].endswith("Mario Kart 8 Deluxe.jpg"))   # user file by title

    def test_downloads_run_in_background(self):
        import http.server
        import threading
        from Core.Art import ArtLibrary
        with tempfile.TemporaryDirectory() as tmp:
            served = os.path.join(tmp, "www")
            os.makedirs(served)
            with open(os.path.join(served, "banner.jpg"), "wb") as f:
                f.write(b"jpegdata")
            handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=served, **k)  # noqa: E731
            http.server.SimpleHTTPRequestHandler.log_message = lambda *a: None
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                art = ArtLibrary(os.path.join(tmp, "covers"))
                game = {"title": "Game", "title_id": "0100AAAA00000000", "image": None,
                        "banner_url": f"http://127.0.0.1:{server.server_port}/banner.jpg"}
                art.resolve(game)
                art.start([game])
                for _ in range(100):
                    done = art.take_finished()
                    if done:
                        break
                    threading.Event().wait(0.05)
            finally:
                server.shutdown()
            self.assertEqual(done, [game])
            with open(game["background"], "rb") as f:
                self.assertEqual(f.read(), b"jpegdata")


class ChildEnvTests(unittest.TestCase):
    def test_launcher_bundle_is_removed(self):
        from Core.Process import clean_child_env
        bundle = os.path.join(tempfile.gettempdir(), "_MEI12345")
        env = {"PATH": os.pathsep.join([os.path.join(bundle, "bin"), "/usr/bin"]),
               "LD_LIBRARY_PATH": bundle, "LD_LIBRARY_PATH_ORIG": "/opt/lib",
               "TCL_LIBRARY": os.path.join(bundle, "_tcl_data"), "_PYI_ARCHIVE_FILE": "x",
               "HOME": "/home/me"}
        clean = clean_child_env(env, bundle)
        self.assertEqual(clean["PATH"], "/usr/bin")
        self.assertEqual(clean["LD_LIBRARY_PATH"], "/opt/lib")
        for name in ("LD_LIBRARY_PATH_ORIG", "TCL_LIBRARY", "_PYI_ARCHIVE_FILE"):
            self.assertNotIn(name, clean)
        self.assertEqual(clean["HOME"], "/home/me")
        self.assertIn("TCL_LIBRARY", env)                       # the input is not changed

    def test_no_library_path_without_original(self):
        from Core.Process import clean_child_env
        clean = clean_child_env({"LD_LIBRARY_PATH": "/tmp/_MEIabc", "LD_LIBRARY_PATH_ORIG": ""}, "/tmp/_MEIabc")
        self.assertNotIn("LD_LIBRARY_PATH", clean)


class GlyphTests(unittest.TestCase):
    def test_every_glyph_renders(self):
        from Core import Glyphs
        for name in ("a", "b", "x", "y", "lb", "rb", "ls", "rs", "start", "back", "dpad"):
            image = Glyphs.button(name, 24)
            self.assertEqual(image.height, 24)
            self.assertEqual(image.mode, "RGBA")
        self.assertEqual(Glyphs.people(2, 20, "#FFFFFF").height, 20)
        self.assertEqual(Glyphs.star(20, "#F5D90A").size, (20, 20))
        self.assertEqual(Glyphs.clock(18, "#FFFFFF").size, (18, 18))
        self.assertEqual(Glyphs.trophy(30, "#F5D90A").size, (30, 30))
        self.assertEqual(Glyphs.button("lt", 24).size, Glyphs.button("lb", 24).size)
        self.assertEqual(Glyphs.panel(100, 40, 8, (0, 0, 0, 128), glow=("#FF0000", 6)).size, (112, 52))
        with self.assertRaises(ValueError):
            Glyphs.button("nope", 24)

    def test_split_screen_picture(self):
        from Core import Glyphs
        regions = ((0, 0, 1, 0.5), (0, 0.5, 1, 1))
        image = Glyphs.split_screen(56, 32, regions, 1, "#FF8A65")
        self.assertEqual(image.size, (56, 32))
        # The highlighted half is drawn in the player's colour, the other one is not
        self.assertEqual(image.getpixel((28, 24))[:3], (0xFF, 0x8A, 0x65))
        self.assertNotEqual(image.getpixel((28, 8))[:3], (0xFF, 0x8A, 0x65))


@unittest.skipUnless(HAS_TK, "Tk not available")
class SplitLayoutTests(unittest.TestCase):
    def test_positions_follow_join_order(self):
        from Core.Ui import split_layout
        self.assertEqual([name for _, name in split_layout(1)], ["split_full"])
        self.assertEqual([name for _, name in split_layout(2)], ["split_top", "split_bottom"])
        self.assertEqual([name for _, name in split_layout(3)], ["split_tl", "split_tr", "split_bl"])
        self.assertEqual([name for _, name in split_layout(4)],
                         ["split_tl", "split_tr", "split_bl", "split_br"])
        self.assertIsNone(split_layout(5))              # shared screen
        self.assertEqual(split_layout(0), split_layout(1))


class BackupTests(unittest.TestCase):
    def _saves(self, tmp):
        root = os.path.join(tmp, "save")
        folder = os.path.join(root, "0000000000000000", "A" * 32, "0100152000022000")
        os.makedirs(os.path.join(folder, "sub"))
        with open(os.path.join(folder, "save.bin"), "wb") as f:
            f.write(b"progress")
        with open(os.path.join(folder, "sub", "extra.dat"), "wb") as f:
            f.write(b"more")
        return root, folder

    def test_backup_skip_unchanged_and_rotate(self):
        import zipfile
        from Core.Backup import backup_saves
        game = {"title": "Mario Kart: 8", "title_id": "0100152000022000"}
        with tempfile.TemporaryDirectory() as tmp:
            root, folder = self._saves(tmp)
            out = os.path.join(tmp, "backups")
            first = backup_saves(game, root, [folder], out, keep=2, now=1_000_000)
            self.assertTrue(first and os.path.isfile(first))
            self.assertEqual(os.path.basename(os.path.dirname(first)), "Mario Kart 8 [0100152000022000]")
            with zipfile.ZipFile(first) as z:
                self.assertEqual(sorted(z.namelist()), [
                    "0000000000000000/" + "A" * 32 + "/0100152000022000/save.bin",
                    "0000000000000000/" + "A" * 32 + "/0100152000022000/sub/extra.dat"])
            # Nothing changed: no new zip
            self.assertIsNone(backup_saves(game, root, [folder], out, keep=2, now=1_000_100))
            # Changed three times: only the newest two are kept
            for i in range(3):
                with open(os.path.join(folder, "save.bin"), "wb") as f:
                    f.write(b"progress" * (i + 2))
                self.assertIsNotNone(backup_saves(game, root, [folder], out, keep=2, now=1_000_200 + i * 60))
            zips = sorted(os.listdir(os.path.dirname(first)))
            self.assertEqual(len(zips), 2)
            self.assertNotIn(os.path.basename(first), zips)

    def test_nothing_to_back_up(self):
        from Core.Backup import backup_saves
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(backup_saves({"title": "X"}, tmp, [], tmp))
            root, folder = self._saves(tmp)
            self.assertIsNone(backup_saves({"title": "X"}, root, [folder], tmp, keep=0))

    def test_eden_save_folders(self):
        from Eden import Eden
        with tempfile.TemporaryDirectory() as tmp:
            eden = Eden()
            eden.user_dir = tmp
            root, folder = self._saves(os.path.join(tmp, "nand", "user"))
            os.makedirs(os.path.join(root, "0000000000000000", "B" * 32, "0100152000022000"))
            os.makedirs(os.path.join(root, "0000000000000000", "B" * 32, "0100000000010000"))
            found_root, folders = eden.save_folders({"title_id": "0100152000022000"})
            self.assertEqual(found_root, root)
            self.assertEqual(len(folders), 2)
            self.assertEqual(eden.save_folders({"title_id": None}), (None, []))


class RouletteTests(unittest.TestCase):
    def test_candidates_take_everyone(self):
        from Core.Roulette import ease_out, pick_candidates
        games = [{"title": "Solo", "players": 1}, {"title": "Duo", "players": 2},
                 {"title": "Party", "players": 4}, {"title": "Big", "players": 8}, {"title": "Unknown"}]
        self.assertEqual([g["title"] for g in pick_candidates(games, 4)], ["Party", "Big"])
        self.assertEqual([g["title"] for g in pick_candidates(games, 2)], ["Duo", "Party", "Big"])
        self.assertEqual(len(pick_candidates(games, 1)), 5)
        self.assertEqual(len(pick_candidates(games, 0)), 5)
        self.assertEqual(pick_candidates(games, 9), [])
        self.assertEqual((ease_out(0), ease_out(1)), (0, 1))


class AchievementTests(unittest.TestCase):
    def _tracker(self):
        from Core.Achievements import Achievements
        state = {}
        settings = types.SimpleNamespace(state=state, save_state=lambda **v: state.update(v))
        return Achievements(settings), state

    def test_unlocks_once(self):
        import time
        tracker, state = self._tracker()
        game = {"title": "Mario Kart 8 Deluxe"}
        noon = time.mktime((2026, 9, 28, 12, 0, 0, 0, 0, -1))
        self.assertEqual(tracker.game_started(game, "MK", 4, now=noon), ["first_game", "party2", "party4"])
        self.assertEqual(tracker.game_started(game, "MK", 4, now=noon), [])       # already unlocked
        self.assertEqual(state["achievements"]["party4"]["game"], "Mario Kart 8 Deluxe")
        self.assertEqual(tracker.game_started(game, "MK", 8, from_roulette=True, now=noon), ["party8", "roulette"])
        night = time.mktime((2026, 9, 29, 2, 30, 0, 0, 0, -1))
        self.assertEqual(tracker.game_started({"title": "B"}, "B", 1, now=night), ["night_owl"])
        self.assertEqual(tracker.game_started({"title": "C"}, "C", 1, now=noon), ["variety"])

    def test_play_time_and_counters(self):
        import time
        from Core.Achievements import ACHIEVEMENTS
        tracker, state = self._tracker()
        noon = time.mktime((2026, 9, 28, 12, 0, 0, 0, 0, -1))
        state["play"] = {"MK": [11 * 3600, 0], "X": [90 * 3600, 0]}
        self.assertEqual(tracker.game_ended({"title": "MK"}, "MK", 3 * 3600 + 5, now=noon),
                         ["marathon", "devoted", "century"])
        self.assertEqual(tracker.roulette_picked({"title": "MK"}, "MK"), [])
        tracker.roulette_picked({"title": "MK"}, "MK")
        self.assertEqual(tracker.roulette_picked({"title": "MK"}, "MK"), ["destiny"])
        self.assertEqual(tracker.favorites_changed(4), [])
        self.assertEqual(tracker.favorites_changed(5), ["favorites"])
        self.assertTrue(set(state["achievements"]) <= set(ACHIEVEMENTS))

    def test_every_achievement_has_text(self):
        from Core.Achievements import ACHIEVEMENTS
        from Core.I18n import STRINGS
        for language in ("en", "it"):
            for achievement in ACHIEVEMENTS:
                self.assertIn(f"ach_{achievement}", STRINGS[language])
                self.assertIn(f"ach_{achievement}_desc", STRINGS[language])


class ColorTests(unittest.TestCase):
    def test_vivid_color(self):
        from PIL import Image, ImageDraw
        from Core.Glyphs import vivid_color
        self.assertEqual(vivid_color(Image.new("RGB", (100, 100), (128, 128, 128)), "#F5D90A"), "#F5D90A")
        image = Image.new("RGB", (100, 100), (120, 120, 120))
        ImageDraw.Draw(image).rectangle((0, 0, 60, 60), fill=(200, 30, 30))    # red wins over grey
        color = vivid_color(image, "#F5D90A")
        r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
        self.assertTrue(r > 200 and g < 80 and b < 80, color)


class SoundTests(unittest.TestCase):
    def test_every_sound_is_a_valid_wav(self):
        import io
        import wave
        from Core.Sound import RATE, SOUNDS, synthesize, wav
        for name in ("join3", "win", "move"):
            data = wav(synthesize(SOUNDS[name], 0.5))
            with wave.open(io.BytesIO(data)) as w:
                self.assertEqual((w.getframerate(), w.getnchannels(), w.getsampwidth()), (RATE, 1, 2))
                self.assertGreater(w.getnframes(), RATE // 100)


class MixerTests(unittest.TestCase):
    def test_sounds_overlap_instead_of_cutting(self):
        from array import array
        from Core.Sound import Mixer
        mixer = Mixer()
        self.assertEqual(mixer.render(4), bytes(8))               # silence when idle
        mixer.add(array("h", [100] * 6))
        mixer.add(array("h", [30000] * 3))
        out = array("h")
        out.frombytes(mixer.render(4))
        self.assertEqual(list(out), [30100, 30100, 30100, 100])
        out = array("h")
        out.frombytes(mixer.render(4))
        self.assertEqual(list(out), [100, 100, 0, 0])             # the first one plays to its end
        self.assertFalse(mixer.busy)

    def test_mix_is_clipped_not_wrapped(self):
        from array import array
        from Core.Sound import Mixer
        mixer = Mixer()
        mixer.add(array("h", [30000, -30000]))
        mixer.add(array("h", [30000, -30000]))
        out = array("h")
        out.frombytes(mixer.render(2))
        self.assertEqual(list(out), [32767, -32768])

    def test_oldest_voice_makes_room(self):
        from array import array
        from Core.Sound import Mixer
        mixer = Mixer(max_voices=2)
        for value in (1, 10, 100):
            mixer.add(array("h", [value]))
        out = array("h")
        out.frombytes(mixer.render(1))
        self.assertEqual(out[0], 110)

    def test_wav_files_load_at_the_mixer_rate(self):
        import wave
        from Core.Sound import RATE, SOUNDS, load_pcm, synthesize, wav
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "join1.wav")
            with open(path, "wb") as f:
                f.write(wav(synthesize(SOUNDS["join1"], 0.5)))
            pcm = load_pcm(path)
            with wave.open(path) as w:
                self.assertEqual(len(pcm), w.getnframes())
            stereo = os.path.join(tmp, "custom.wav")              # 44.1 kHz stereo override
            with wave.open(stereo, "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(2 * RATE)
                w.writeframes(b"\x10\x00\x30\x00" * 400)
            pcm = load_pcm(stereo)
            self.assertEqual(len(pcm), 200)
            self.assertEqual(pcm[0], 0x20)                        # channels averaged
            self.assertIsNone(load_pcm(os.path.join(tmp, "missing.wav")))


class TimeLabelTests(unittest.TestCase):
    def test_labels(self):
        import time
        from Core.I18n import last_played_label, playtime_label, set_language
        set_language("it")
        try:
            self.assertEqual(playtime_label(45 * 60), "45 min")
            self.assertEqual(playtime_label(3 * 3600 + 5 * 60 + 30), "3 h 5 min")
            now = time.mktime((2026, 9, 28, 12, 0, 0, 0, 0, -1))
            self.assertEqual(last_played_label(now - 3600, now), "oggi")
            self.assertEqual(last_played_label(now - 86400, now), "ieri")
            self.assertEqual(last_played_label(now - 5 * 86400, now), "5 giorni fa")
            self.assertEqual(last_played_label(now - 90 * 86400, now), "30/06/2026")
        finally:
            set_language("en")


@unittest.skipUnless(HAS_TK, "needs Tk")
class ReconnectTests(unittest.TestCase):
    PAD = "030000005e0400008e02000000007801"

    def _app(self, mode="GAMES", process=None):
        from Core.App import LauncherApp
        app = LauncherApp.__new__(LauncherApp)
        toasts, confirmed = [], []
        app.ui = types.SimpleNamespace(set_pads=lambda n: None, show_toast=lambda m, c=None: toasts.append(m))
        app.toasts, app.confirmed = toasts, confirmed
        app.confirm_player_number = lambda instance_id, n: confirmed.append((instance_id, n))
        app.refresh_grid = app.update_hints = lambda: None
        app.mode, app.process = mode, process
        app.hid_colors, app.hid_profiles, app.color_pool = {}, {}, ["#111111", "#222222", "#333333"]
        app.pads = {1: {"path": "XInput#0", "guid": self.PAD, "name": "Pad"},
                    2: {"path": "XInput#1", "guid": self.PAD, "name": "Pad"}}
        app.assignments = [{"path": "XInput#0", "guid": self.PAD, "name": "Pad", "profile_key": "Xbox"},
                           {"path": "XInput#1", "guid": self.PAD, "name": "Pad", "profile_key": "Nintendo"}]
        for a in app.assignments:
            app.get_assigned_color(a["path"])
            app.hid_profiles[a["path"]] = a["profile_key"]
        return app

    def test_slot_waits_during_a_game_and_comes_back(self):
        from Core.I18n import set_language
        set_language("en")
        app = self._app(mode="GAMES", process=object())
        color = app.hid_colors["XInput#1"]
        del app.pads[2]                                     # P2's pad drops mid-game
        app.on_pads_changed()
        self.assertEqual(len(app.assignments), 2)
        self.assertTrue(app.assignments[1]["waiting"])
        self.assertEqual(app.toasts[-1], "P2 disconnected · the slot waits for it")
        app.pads[3] = {"path": "XInput#2", "guid": self.PAD, "name": "Pad"}    # back in another slot
        app.on_pads_changed()
        p2 = app.assignments[1]
        self.assertFalse(p2["waiting"])
        self.assertEqual(p2["path"], "XInput#2")
        self.assertEqual(app.hid_colors["XInput#2"], color)                     # same colour, same layout
        self.assertEqual(app.hid_profiles["XInput#2"], "Nintendo")
        self.assertEqual(app.confirmed, [])                                     # no rumble while Eden runs
        self.assertEqual(app.toasts[-1], "P2 is back")

    def test_player_cards_free_the_slot(self):
        app = self._app(mode="PLAYERS")
        del app.pads[2]
        app.on_pads_changed()
        self.assertEqual([a["path"] for a in app.assignments], ["XInput#0"])
        self.assertNotIn("XInput#1", app.hid_colors)

    def test_waiting_slots_are_freed_on_the_player_cards(self):
        app = self._app(mode="GAMES")
        del app.pads[2]
        app.on_pads_changed()
        app.drop_waiting()
        self.assertEqual(len(app.assignments), 1)

    def test_pathless_keys_follow_eden_ports(self):
        from Core.App import LauncherApp
        g = self.PAD
        new = lambda: {"raw_path": "", "guid": g}                               # noqa: E731
        pads = {1: new(), 2: new(), 3: new()}
        LauncherApp._assign_keys(pads, [1, 2, 3])
        self.assertEqual([pads[i]["path"][-2:] for i in (1, 2, 3)], ["_0", "_1", "_2"])
        del pads[2]                                     # the middle pad drops: the others keep their keys
        LauncherApp._assign_keys(pads, [1, 3])
        self.assertEqual([pads[i]["path"][-2:] for i in (1, 3)], ["_0", "_2"])
        pads[4] = new()                                 # a pad comes back: it takes the freed number,
        LauncherApp._assign_keys(pads, [1, 3, 4])       # like Eden reuses the freed port
        self.assertEqual(pads[4]["path"][-2:], "_1")
        # After an SDL re-initialisation the same pads get their old keys back, in order
        fresh = {7: new(), 8: new()}
        LauncherApp._assign_keys(fresh, [7, 8], {g: [pads[1]["path"], pads[3]["path"]]})
        self.assertEqual([fresh[i]["path"][-2:] for i in (7, 8)], ["_0", "_2"])
        withpath = {9: {"raw_path": "XInput#0", "guid": g}}
        LauncherApp._assign_keys(withpath, [9])
        self.assertEqual(withpath[9]["path"], "XInput#0")

    def test_held_buttons_report(self):
        from Core.App import LauncherApp
        app = LauncherApp.__new__(LauncherApp)
        held = {"A", "RB", "UP"}
        app.sdl = types.SimpleNamespace(
            SDL_GameControllerGetButton=lambda ctrl, button: button in held,
            get_triggers=lambda ctrl: (0.9, 0.1),
            **{f"SDL_CONTROLLER_BUTTON_{n}": n for n in ("A", "B", "X", "Y", "BACK", "START", "LEFT_STICK",
                                                         "RIGHT_STICK", "DPAD_LEFT", "DPAD_RIGHT", "DPAD_DOWN")},
            SDL_CONTROLLER_BUTTON_LEFT_SHOULDER="LB", SDL_CONTROLLER_BUTTON_RIGHT_SHOULDER="RB",
            SDL_CONTROLLER_BUTTON_DPAD_UP="UP")
        self.assertEqual(app.held_buttons(None), ["a", "rb", "lt", "dpad"])

    def test_match_prefers_the_same_path_then_the_oldest_slot(self):
        from Core.App import match_reconnects
        g = self.PAD
        waiting = [{"path": "XInput#1", "guid": g}, {"path": "XInput#3", "guid": g}]
        pads = [{"path": "XInput#3", "guid": g}, {"path": "XInput#0", "guid": g}, {"path": "X", "guid": "other"}]
        pairs = match_reconnects(waiting, pads)
        self.assertEqual([(s["path"], p["path"]) for s, p in pairs], [("XInput#3", "XInput#3"),
                                                                      ("XInput#1", "XInput#0")])
        self.assertEqual(match_reconnects(waiting, [{"path": "X", "guid": "other"}]), [])


@unittest.skipUnless(HAS_TK, "needs Tk")
class OrderTests(unittest.TestCase):
    def test_favourites_first_then_order(self):
        from Core.App import LauncherApp
        games = [{"title": t, "path": t, "title_id": None} for t in ("Alpha", "Bravo", "Charlie", "Delta")]
        app = types.SimpleNamespace(
            games=games, sort_mode="recent", game_key=LauncherApp.game_key,
            emu=types.SimpleNamespace(settings=types.SimpleNamespace(state={
                "play": {"Bravo": [600, 100], "Charlie": [9000, 50], "Delta": [60, 300]},
                "favorites": ["Charlie"]})))
        LauncherApp.order_games(app)
        self.assertEqual([g["title"] for g in app.games], ["Charlie", "Delta", "Bravo", "Alpha"])
        self.assertTrue(app.games[0]["favorite"])
        app.sort_mode = "most"
        LauncherApp.order_games(app)
        self.assertEqual([g["title"] for g in app.games], ["Charlie", "Bravo", "Delta", "Alpha"])
        app.sort_mode = "az"
        LauncherApp.order_games(app)
        self.assertEqual([g["title"] for g in app.games], ["Charlie", "Alpha", "Bravo", "Delta"])
        self.assertEqual(app.games[1]["playtime"], 0)


@unittest.skipUnless(HAS_TK, "needs Tk")
class BadgeTests(unittest.TestCase):
    def test_badge_colours(self):
        from Core.GameGrid import badge_state
        self.assertEqual(badge_state(4, 3), "ok")
        self.assertEqual(badge_state(2, 3), "bad")
        self.assertEqual(badge_state(4, 1), "neutral")       # one player: nothing to compare
        self.assertEqual(badge_state(None, 3), "neutral")    # unknown count


if __name__ == "__main__":
    unittest.main()
