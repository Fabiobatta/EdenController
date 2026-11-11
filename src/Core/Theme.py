"""
Core/Theme.py
The colours of the interface, switchable at runtime.

    dark    the default: glass panels over the game's blurred, darkened art
    oled    pure black: no background picture at all (nothing to burn in,
            and the lightest to draw)

plus an accent colour (the focus glow of games without art, selected
menu rows, values, bars), one of ACCENTS.

COLOR is one dict that every drawing module reads at draw time, so
apply_theme() changes all of them at once; the UI then throws away its
cached pictures and redraws (LauncherUi.set_theme).

Besides plain colours ("#RRGGBB") a theme has translucent fills as RGBA
tuples (panels, dialogs, overlays) and one colour per player number.
"""

ACCENTS = {
    "violet": "#8E7BFF",
    "azure": "#4CC2FF",
    "mint": "#3DDCB4",
    "amber": "#FFB648",
    "rose": "#FF6FA8",
}
ACCENT_NAMES = tuple(ACCENTS)

THEMES = {
    "dark": {
        "BG": "#07080C",
        "TEXT": "#F5F6F8",
        "TEXT_DIM": "#A6AEBD",
        "TEXT_FAINT": "#646C7B",
        "ACCENT": ACCENTS["violet"],
        "GOLD": "#F6C445",                      # favourites, achievements
        "OK": "#3DDC84",
        "BAD": "#FF5D5D",
        "NEON_RED": "#FF6B61",
        "WARN": "#FFB84D",
        "LINE": "#2C303C",
        "LINE_FAINT": "#20232D",
        "LOCKED": "#4B5060",
        "DISC_TEXT": "#0B0C10",
        "EDGE": (255, 255, 255, 34),            # the light rim of glass panels
        "EDGE_STRONG": (255, 255, 255, 80),
        "PANEL": (20, 23, 32, 208),             # cards
        "PANEL_EMPTY": (255, 255, 255, 9),
        "DIALOG": (17, 19, 27, 255),
        "DRAWER": (13, 15, 22, 255),            # side menus
        "PILL": (18, 20, 28, 196),              # toasts, the hint dock, the clock (the backdrop shows through)
        "CHIP": (255, 255, 255, 22),
        "ROW": (255, 255, 255, 13),             # menu rows, stat tiles
        "ROW_SELECTED": (142, 123, 255, 60),    # follows the accent (apply_theme)
        "TROPHY": (22, 19, 10, 246),
        "TROPHY_ROW": (246, 196, 69, 24),
        "OVERLAY": (3, 4, 7, 178),              # behind dialogs and drawers
        "SCREEN": (8, 9, 14, 255),              # full-screen lists over the content
        "SPIN": (4, 5, 9, 246),                 # behind the roulette
        "FADE": (0, 0, 0),                      # screen fades
        "PLAYERS": ("#4FA3FF", "#FF5C5C", "#5CD65C", "#FFD23F",
                    "#B57BFF", "#FF9A3C", "#3DDCCB", "#FF6FB5"),
    },
}
THEMES["oled"] = {
    **THEMES["dark"],
    "BG": "#000000",
    "PANEL": (10, 11, 15, 255),
    "PANEL_EMPTY": (255, 255, 255, 8),
    "DIALOG": (8, 9, 12, 255),
    "DRAWER": (5, 6, 9, 255),
    "PILL": (8, 9, 12, 255),
    "SCREEN": (0, 0, 0, 255),
    "SPIN": (0, 0, 0, 242),
    "OVERLAY": (0, 0, 0, 205),
}
THEME_NAMES = ("dark", "oled")

COLOR = dict(THEMES["dark"])
_current = "dark"
_accent = "violet"


def _rgb(color):
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def apply_theme(name=None, accent=None):
    """
    Switch every colour to theme `name` and accent `accent` (None keeps the
    current one; unknown names fall back to dark / violet). Returns the
    theme name applied.
    """
    global _current, _accent
    if name is not None:
        _current = name if name in THEMES else "dark"
    if accent is not None:
        _accent = accent if accent in ACCENTS else "violet"
    COLOR.clear()
    COLOR.update(THEMES[_current])
    COLOR["ACCENT"] = ACCENTS[_accent]
    COLOR["ROW_SELECTED"] = _rgb(ACCENTS[_accent]) + (60,)
    return _current


def current_theme():
    return _current


def current_accent():
    return _accent


def player_color(index):
    """Colour of player index + 1 (P1 = 0) in the current theme."""
    colors = COLOR["PLAYERS"]
    return colors[index % len(colors)]
