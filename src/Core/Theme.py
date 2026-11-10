"""
Core/Theme.py
The colours of the interface, switchable at runtime.

    dark    the default: light text over the game's blurred, darkened art
    oled    pure black: no background picture at all (nothing to burn in,
            and the lightest to draw)
    light   dark text over the art washed out towards white

COLOR is one dict that every drawing module reads at draw time, so
apply_theme() changes all of them at once; the UI then throws away its
cached pictures and redraws (LauncherUi.set_theme).

Besides plain colours ("#RRGGBB") a theme has translucent fills as RGBA
tuples (panels, dialogs, overlays) and one colour per player number.
"""

THEMES = {
    "dark": {
        "BG": "#0B0D12",
        "TEXT": "#F4F5F7",
        "TEXT_DIM": "#A9AFBB",
        "TEXT_FAINT": "#6B7280",
        "ACCENT": "#F5D90A",
        "OK": "#3FB950",
        "BAD": "#E5534B",
        "NEON_RED": "#FF6B61",
        "WARN": "#F5BE28",
        "LINE": "#3A3F4B",
        "LINE_FAINT": "#2E323B",
        "LOCKED": "#4B5060",
        "DISC_TEXT": "#111111",
        "PANEL": (10, 12, 18, 196),             # player cards
        "PANEL_EMPTY": (255, 255, 255, 16),
        "DIALOG": (22, 24, 31, 248),
        "PILL": (14, 16, 22, 235),              # toasts, chips
        "CHIP": (0, 0, 0, 140),
        "ROW": (255, 255, 255, 12),             # menu rows, stat tiles
        "ROW_SELECTED": (255, 214, 10, 34),
        "TROPHY": (18, 16, 8, 245),
        "TROPHY_ROW": (255, 214, 10, 22),
        "OVERLAY": (0, 0, 0, 175),              # behind dialogs
        "SCREEN": (8, 9, 13, 255),              # full-screen lists over the content
        "SPIN": (6, 7, 10, 222),                # behind the roulette
        "FADE": (0, 0, 0),                      # footer band, screen fades
        "PLAYERS": ("#4FA3FF", "#FF5C5C", "#5CD65C", "#FFD23F",
                    "#B57BFF", "#FF9A3C", "#3DDCCB", "#FF6FB5"),
    },
}
THEMES["oled"] = {
    **THEMES["dark"],
    "BG": "#000000",
    "PANEL": (0, 0, 0, 255),
    "PANEL_EMPTY": (255, 255, 255, 10),
    "DIALOG": (10, 10, 12, 255),
    "PILL": (0, 0, 0, 255),
    "SCREEN": (0, 0, 0, 255),
    "SPIN": (0, 0, 0, 240),
    "OVERLAY": (0, 0, 0, 200),
}
THEMES["light"] = {
    **THEMES["dark"],
    "BG": "#EEF0F4",
    "TEXT": "#14161B",
    "TEXT_DIM": "#4A5060",
    "TEXT_FAINT": "#858B9A",
    "ACCENT": "#B87400",
    "OK": "#1F8A34",
    "BAD": "#CC3A32",
    "NEON_RED": "#D8362C",
    "WARN": "#B87400",
    "LINE": "#B9BFCB",
    "LINE_FAINT": "#D2D6DE",
    "LOCKED": "#A3A9B6",
    "DISC_TEXT": "#FFFFFF",
    "PANEL": (255, 255, 255, 214),
    "PANEL_EMPTY": (255, 255, 255, 96),
    "DIALOG": (250, 251, 253, 252),
    "PILL": (255, 255, 255, 244),
    "CHIP": (255, 255, 255, 190),
    "ROW": (0, 0, 0, 14),
    "ROW_SELECTED": (184, 116, 0, 40),
    "TROPHY": (255, 250, 235, 250),
    "TROPHY_ROW": (184, 116, 0, 30),
    "OVERLAY": (30, 34, 44, 120),
    "SCREEN": (240, 242, 246, 255),
    "SPIN": (240, 242, 246, 232),
    "FADE": (238, 240, 244),
    "PLAYERS": ("#1F6FE0", "#D93A3A", "#22943A", "#B98A00",
                "#8544D6", "#D96A0A", "#0E9E8E", "#D6408C"),
}
THEME_NAMES = ("dark", "oled", "light")

COLOR = dict(THEMES["dark"])
_current = "dark"


def apply_theme(name):
    """Switch every colour to theme `name` (unknown names: dark). Returns the name applied."""
    global _current
    _current = name if name in THEMES else "dark"
    COLOR.clear()
    COLOR.update(THEMES[_current])
    return _current


def current_theme():
    return _current


def player_color(index):
    """Colour of player index + 1 (P1 = 0) in the current theme."""
    colors = COLOR["PLAYERS"]
    return colors[index % len(colors)]
