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


class I18nTests(unittest.TestCase):
    def test_languages_have_the_same_keys(self):
        import Eden.Eden  # noqa: F401  (registers the Eden strings)
        from Core.I18n import STRINGS
        self.assertEqual(set(STRINGS["en"]), set(STRINGS["it"]))

    def test_selection_and_fallback(self):
        from Core import I18n
        try:
            self.assertEqual(I18n.set_language("it"), "it")
            self.assertEqual(I18n.t("slot_profile", name="Xbox"), "◄   Profilo: Xbox   ►")
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

    def test_find_games_hides_updates_and_dlc(self):
        from Eden.Games import clean_title, find_games
        self.assertEqual(clean_title("Super Game [0100ABCD12340000][v0] (USA).nsp"), "Super Game (USA)")
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "sub"))
            for name in ("Zelda [01007EF00011E000][v0].nsp", "Zelda [01007EF00011E800][v5].nsp",
                         "Zelda [01007EF00011F001][v0].nsp", "Kart.xci", "Kart.nsp",
                         "readme.txt", "sub/Deep Game.nsp"):
                open(os.path.join(tmp, name), "w").close()
            shallow = [g["title"] for g in find_games([(tmp, False)])]
            deep = [g["title"] for g in find_games([(tmp, True)])]
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


if __name__ == "__main__":
    unittest.main()
