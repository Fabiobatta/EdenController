"""
Eden/Config.py
Everything that understands Eden's qt-config.ini input format.

Eden inherited its input system from yuzu. Each player's bindings live in the
[Controls] section as one key per Switch input:

    player_0_button_a\\default=false
    player_0_button_a="button:1,engine:sdl,guid:0300000000000000..,port:0"
    player_0_lstick\\default=false
    player_0_lstick="axis_x:0,axis_y:1,deadzone:0.150000,engine:sdl,guid:..,port:0,.."

A physical pad is identified by two fields inside every SDL binding:

    guid  SDL joystick GUID with the name-CRC bytes (2..3) zeroed
          (input_common/drivers/sdl_driver.cpp, GetGUID)
    port  index of the pad among the connected pads that share that GUID,
          in SDL enumeration order (SDLDriver::InitJoystick)

When several identical pads are connected (e.g. the virtual Xbox 360 pads that
Sunshine/Vibeshine creates for every Moonlight client) they all share one GUID
and only "port" tells them apart. That port shifts whenever the pads connect
in a different order - which is exactly the problem this launcher solves.

Three jobs:
    1. load_template()  - reuse Player 1's SDL mapping as the default profile
    2. load_profiles()  - discover Eden input profiles (config/input/*.ini)
    3. write_input()    - rewrite the player_N_* keys for a launch

Core never looks inside any of this; profile payloads are opaque to it.
"""

import glob
import os

from Core.Log import log

from .Ini import IniFile, quote, unquote

SECTION = "Controls"
MAX_PLAYERS = 8
DEFAULT_PROFILE = "ED Default"

# Settings::ControllerType::ProController
PRO_CONTROLLER = 0

# common/settings_input.cpp - NativeButton / NativeAnalog / NativeMotion::mapping
BUTTONS = (
    "button_a", "button_b", "button_x", "button_y", "button_lstick",
    "button_rstick", "button_l", "button_r", "button_zl", "button_zr",
    "button_plus", "button_minus", "button_dleft", "button_dup", "button_dright",
    "button_ddown", "button_slleft", "button_srleft", "button_home", "button_screenshot",
    "button_slright", "button_srright",
)
ANALOGS = ("lstick", "rstick")
MOTIONS = ("motionleft", "motionright")
MAPPING_KEYS = BUTTONS + ANALOGS + MOTIONS


# ============================================================================
# PARAM PACKAGES  (common/param_package.cpp)
# ============================================================================
def parse_params(text):
    """"a:1,b:2" -> [["a", "1"], ["b", "2"]], order preserved."""
    pairs = []
    for part in text.split(","):
        if not part:
            continue
        key, _, value = part.partition(":")
        pairs.append([key, value])
    return pairs


def serialize_params(pairs):
    return ",".join(f"{k}:{v}" for k, v in pairs)


def build_params(**values):
    """
    Serialize like Common::ParamPackage, whose std::map keeps keys sorted.
    Used only for the built-in fallback mapping.
    """
    return ",".join(f"{k}:{values[k]}" for k in sorted(values))


def retarget(param_text, guid, port):
    """
    Point one SDL binding at a different physical pad.

    Only "engine:sdl" bindings are touched: keyboard, mouse, UDP (cemuhook)
    or TAS bindings do not belong to a pad and are returned unchanged.
    "guid2" (the second half of a dual Joy-Con pair) collapses onto the same
    pad, since a launcher slot is always a single controller.
    """
    pairs = parse_params(param_text)
    if ["engine", "sdl"] not in pairs:
        return param_text
    for pair in pairs:
        if pair[0] in ("guid", "guid2"):
            pair[1] = guid
        elif pair[0] == "port":
            pair[1] = str(port)
    return serialize_params(pairs)


def is_sdl_mapping(mapping):
    return any(["engine", "sdl"] in parse_params(v) for v in mapping.values())


# ============================================================================
# GUID FORMATTING
# ============================================================================
def eden_guid(raw_hex):
    """
    Convert SDL's raw 32-char GUID string into Eden's pad GUID.

    SDLDriver's GetGUID() copies the 16 GUID bytes and zeroes bytes 2..3
    (the CRC of the device name), then Common::UUID::RawString() prints them
    as lowercase hex in byte order. Same rule for SDL2 and SDL3 builds.
    """
    raw_hex = raw_hex.lower()
    if len(raw_hex) < 32:
        return raw_hex
    return raw_hex[:4] + "0000" + raw_hex[8:32]


def enumerate_pads(hardware):
    """
    Attach Eden's (guid, port) to every pad of a fresh SDL scan.

    Port numbering mirrors SDLDriver::InitJoystick: the n-th pad seen with a
    given GUID gets port n. It counts across every connected pad, not only
    the assigned ones.

    Returns:
        list[dict]: {"path", "name", "guid", "port"} in enumeration order.
    """
    counters = {}
    pads = []
    for hw in hardware:
        guid = eden_guid(hw["guid"])
        port = counters.get(guid, 0)
        counters[guid] = port + 1
        pads.append({"path": hw["path"], "name": hw["name"], "guid": guid, "port": port})
    return pads


# ============================================================================
# FALLBACK MAPPING
# ============================================================================
def fallback_template(guid="0" * 32, port=0):
    """
    Built-in mapping for an XInput (Xbox 360 / Xbox One) pad on Windows, used
    only when Player 1 in qt-config.ini has no SDL mapping to copy.

    Raw joystick indices of SDL's XInput driver: buttons A B X Y LB RB Back
    Start LS RS Guide = 0..10, axes LX LY LT RX RY RT = 0..5, D-pad = hat 0.
    Like Eden's auto-map, Switch buttons follow the physical position
    (Switch A = east = Xbox B).

    Other drivers number their inputs differently, which is why mapping
    Player 1 once inside Eden is the recommended setup.
    """
    common = {"engine": "sdl", "guid": guid, "port": port}

    def button(index):
        return build_params(button=index, **common)

    def hat(direction):
        return build_params(hat=0, direction=direction, **common)

    def trigger(axis):
        return build_params(axis=axis, threshold="0.5", invert="+", **common)

    def stick(axis_x, axis_y):
        return build_params(axis_x=axis_x, axis_y=axis_y, deadzone="0.150000",
                            range="1.000000", offset_x="0.000000", offset_y="0.000000",
                            invert_x="+", invert_y="+", **common)

    motion = build_params(motion=0, **common)

    return {
        "button_a": button(1), "button_b": button(0),
        "button_x": button(3), "button_y": button(2),
        "button_lstick": button(8), "button_rstick": button(9),
        "button_l": button(4), "button_r": button(5),
        "button_zl": trigger(2), "button_zr": trigger(5),
        "button_plus": button(7), "button_minus": button(6),
        "button_dleft": hat("left"), "button_dup": hat("up"),
        "button_dright": hat("right"), "button_ddown": hat("down"),
        "button_slleft": button(4), "button_srleft": button(5),
        "button_home": button(10), "button_screenshot": "[empty]",
        "button_slright": button(4), "button_srright": button(5),
        "lstick": stick(0, 1), "rstick": stick(3, 4),
        "motionleft": motion, "motionright": motion,
    }


# ============================================================================
# TEMPLATE
# ============================================================================
def _read_mapping(items, prefix):
    """
    Collect one player's (or one profile's) bindings.

    A key whose "\\default" flag is true is ignored by Eden in favour of its
    keyboard default, so it is not part of the user's mapping.
    """
    mapping = {}
    for key in MAPPING_KEYS:
        full = prefix + key
        if items.get(full + "\\default", "true").strip().lower() == "true":
            continue
        if full in items:
            mapping[key] = unquote(items[full])
    return mapping


def load_template(ini):
    """
    Build the default profile from Player 1 of qt-config.ini.

    Reuses Player 1's bindings when they point at an SDL pad, so the user's
    own button layout, deadzones and motion settings survive. Otherwise the
    built-in XInput fallback is used.
    """
    items = ini.items(SECTION) if ini else {}
    mapping = _read_mapping(items, "player_0_")
    if mapping and is_sdl_mapping(mapping):
        log("INFO", "Default profile", "Player 1 mapping from qt-config.ini")
        return mapping

    log("WARNING", "Player 1 has no SDL mapping in qt-config.ini - using the built-in XInput layout")
    return fallback_template()


# ============================================================================
# PROFILES
# ============================================================================
def load_profiles(profiles_dir, template):
    """
    Load Eden input profiles (<config>/input/*.ini).

    Profiles are saved from Eden's Controls dialog; their [Controls] section
    holds the same keys as qt-config.ini without the "player_N_" prefix.
    Only SDL profiles are offered, since the launcher assigns physical pads.
    A profile file named "ED Default.ini" overrides the built-in default.

    Returns:
        dict: {"display name": mapping dict}, default first.
    """
    profiles = {DEFAULT_PROFILE: dict(template)}

    if os.path.isdir(profiles_dir):
        for filepath in sorted(glob.glob(os.path.join(profiles_dir, "*.ini"))):
            name = os.path.splitext(os.path.basename(filepath))[0]
            try:
                mapping = _read_mapping(IniFile.load(filepath).items(SECTION), "")
            except Exception as e:
                log("WARNING", "Skipping unreadable profile", filepath)
                log("EXCEPTION", "Profile load exception", e)
                continue
            if not is_sdl_mapping(mapping):
                log("INFO", "Skipping non-SDL profile", name)
                continue
            # Keys the profile does not set fall back to the default layout
            profiles[name] = {**template, **mapping}

    log("INFO", "Profiles loaded", str(len(profiles)))
    return profiles


# ============================================================================
# WRITING THE LAUNCH CONFIG
# ============================================================================
def _bool(value):
    return "true" if value else "false"


def build_player_values(assignments, hardware, profiles):
    """
    Compute every [Controls] key the launch needs to change.

    Players are packed in assignment order (Player 1, 2, ...). Slots beyond
    the assigned ones are disconnected so a stale mapping can never grab a
    pad. Unrelated keys (vibration, colors, hotkeys ...) are left alone.

    Returns:
        dict: {ini key: raw ini value}
    """
    pads = enumerate_pads(hardware)
    values = {}
    player = 0

    for assignment in assignments:
        if player >= MAX_PLAYERS:
            break
        pad = next((p for p in pads if p["path"] == assignment["path"]), None)
        if not pad:
            log("WARNING", "Assigned controller vanished before launch", assignment["name"])
            continue

        profile = profiles.get(assignment["profile_key"]) or profiles[DEFAULT_PROFILE]
        log("INFO", f"Saving -> Player {player + 1} | {pad['name']} | "
                    f"guid {pad['guid']} port {pad['port']} | Profile: {assignment['profile_key']}")

        prefix = f"player_{player}_"
        values[prefix + "connected\\default"] = _bool(player == 0)
        values[prefix + "connected"] = "true"
        values[prefix + "type\\default"] = "true"
        values[prefix + "type"] = str(PRO_CONTROLLER)
        for key in MAPPING_KEYS:
            if key not in profile:
                continue
            values[prefix + key + "\\default"] = "false"
            values[prefix + key] = quote(retarget(profile[key], pad["guid"], pad["port"]))
        player += 1

    for unused in range(player, MAX_PLAYERS):
        prefix = f"player_{unused}_"
        # The built-in default is "connected" for Player 1 only
        values[prefix + "connected\\default"] = _bool(unused != 0)
        values[prefix + "connected"] = "false"

    return values, player


def write_input(config_file, assignments, hardware, profiles):
    """
    Rewrite the player_N_* keys of qt-config.ini for this launch.

    Only those keys are replaced - every other line of the user's settings
    is preserved as-is.
    """
    if not assignments:
        log("INFO", "No controllers assigned - leaving qt-config.ini untouched")
        return

    try:
        ini = IniFile.load(config_file) if os.path.exists(config_file) else IniFile()
    except Exception as e:
        log("EXCEPTION", "Could not read qt-config.ini, Eden will use the old config", e)
        return

    values, written = build_player_values(assignments, hardware, profiles)
    if written == 0:
        log("WARNING", "No assigned controller is connected - leaving qt-config.ini untouched")
        return

    ini.set_many(SECTION, values)
    try:
        os.makedirs(os.path.dirname(config_file), exist_ok=True)
        ini.save(config_file)
        log("INFO", "qt-config.ini updated", f"{written} player(s)")
    except Exception as e:
        log("EXCEPTION", "Config write failed, Eden will use the old config", e)
