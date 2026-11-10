"""
Core/Ui.py
Everything on screen, drawn on one full-window tk.Canvas.

Layers, bottom to top (canvas tags):
    bg        blurred, darkened art (the selected / last played game)
    players   the 8 player cards            } one of the two
    games     the game grid (Core/GameGrid) }
    header    title, clock, connected controllers
    footer    button hints ("[a] Play  [b] Back")
    roulette  the "tonight we play" spinner (Core/Roulette.py)
    trophies  the achievements list, the statistics
    menu      list menus: settings, a game's menu (Core/Menu.py)
    splash    the "let's go" screen before a game starts
    toast     transient message
    trophy    "achievement unlocked" banner
    alert     modal dialog
    fade      the two-step fade between screens
    dim       idle dimming (nearly black, only the clock)

Colours come from Core/Theme.py (dark, oled, light) and are read at draw
time; set_theme() drops the cached pictures and redraws.

Pictures come from Pillow (Core/Glyphs.py) and are cached per size, so a
redraw only re-places cached images. Layout is authored for 1280x720 and
scaled uniformly (px()). Emulator-agnostic: Core/App.py feeds plain
view models and never touches widgets.
"""

import ctypes
import os
import math
import re
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from collections import OrderedDict

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageTk

from . import Glyphs
from .I18n import t
from .Paths import resource_path
from .Theme import COLOR, THEMES, apply_theme, current_accent, current_theme, player_color  # noqa: F401

# ============================================================================
# HI-DPI: physical pixels on Windows (must run before the window exists)
# ============================================================================
if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)      # Windows 8.1+
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()       # Vista-8
        except Exception:
            pass

BASE_W, BASE_H = 1280, 720
FONT_FAMILY = "Segoe UI" if sys.platform == "win32" else "DejaVu Sans"
# Lighter / heavier cuts of the UI font, used when installed
FONT_WEIGHTS = {"semibold": "Segoe UI Semibold", "light": "Segoe UI Light"} if sys.platform == "win32" else {}
MARGIN = 64
FOOTER_H = 64
DOCK_Y = 42             # centre of the hint dock above the bottom edge
SPLASH_SHINE_MS = 760   # the light sweeping over the cover on the launch screen
ART_MAX = 720           # art is kept in memory at most this big
ART_CACHE = 48          # pictures kept decoded
BG_CACHE = 8            # blurred backgrounds kept (screen-sized each)
BG_SAMPLE_W = 96        # backdrop detail: the art is shrunk to this width...
BG_BLUR = 2.2           # ...blurred, and stretched back to the screen
DRIFT = 16              # px (720p) the backdrop may wander from its place
DRIFT_PERIODS = (97.0, 71.0)    # seconds for one horizontal / vertical swing
DRIFT_TICK_MS = 250     # at most 4 moves a second (~2 at this speed)
DRIFT_QUIET_S = 3.0     # the backdrop holds still this long after any input or change
BG_COMPOSE_W = 480      # the backdrop is composed about this wide, then enlarged (LauncherUi._bg_zoom)
# Every change of the backdrop repaints the whole window (tens of ms at
# 1080p), so a cross-fade is one blended picture between the two, no more
BG_FADE = (0.5,)
BG_FADE_MS = 60
POP_DISTANCE = 46       # px (720p) a player's row slides in from the right when joining
POP_MS = 240
HOP_MS = 560            # a player's icon jumping when they join or pick another one
HOP_HEIGHT = 13
TV_MORPH_MS = 300       # the split-screen map dividing anew when a player joins or leaves
SPLASH_EXIT_MS = 460    # the cover growing into a dark screen as the game opens
EXIT_FRAMES = 12

# Colours live in Core/Theme.py (COLOR is the current theme's)
PLAYER_COLORS = THEMES["dark"]["PLAYERS"]

_TOKEN = re.compile(r"\[(\w+)\]")


# Where each player's view goes in a local co-op split screen, by the number
# of players: (region as x0, y0, x1, y1 fractions, I18n label key).
SPLIT = {
    "full": ((0, 0, 1, 1), "split_full"),
    "top": ((0, 0, 1, 0.5), "split_top"),
    "bottom": ((0, 0.5, 1, 1), "split_bottom"),
    "left": ((0, 0, 0.5, 1), "split_left"),
    "right": ((0.5, 0, 1, 1), "split_right"),
    "tl": ((0, 0, 0.5, 0.5), "split_tl"),
    "tr": ((0.5, 0, 1, 0.5), "split_tr"),
    "bl": ((0, 0.5, 0.5, 1), "split_bl"),
    "br": ((0.5, 0.5, 1, 1), "split_br"),
}
# How a game splits the screen: most Switch games put two players one above
# the other ("rows"), some side by side ("columns"), and many never split
# ("shared"). Three and four players get the quadrants either way; with five
# or more the screen is shared. "auto" is "rows".
SPLIT_MODES = ("auto", "rows", "columns", "shared")
_QUADRANTS = {3: ("tl", "tr", "bl"), 4: ("tl", "tr", "bl", "br")}
SPLIT_LAYOUTS = {
    "rows": {1: ("full",), 2: ("top", "bottom"), **_QUADRANTS},
    "columns": {1: ("full",), 2: ("left", "right"), **_QUADRANTS},
    "shared": {1: ("full",)},
}
# Games known to differ from "rows" (title ID -> mode), used while a game's
# own setting is "auto": Mario Kart 8 Deluxe plays two players side by side
KNOWN_SPLIT = {"0100152000022000": "columns"}


def split_mode(game, chosen=None):
    """The split mode for `game`: the user's choice, else what is known, else "rows"."""
    if chosen in SPLIT_MODES and chosen != "auto":
        return chosen
    return KNOWN_SPLIT.get(((game or {}).get("title_id") or "").upper(), "rows")


def split_layout(players, mode="rows"):
    """
    [(region, label key)] per player, in player order, or None when the
    screen is shared (five players or more, or a game that never splits).
    """
    names = SPLIT_LAYOUTS.get(mode if mode in SPLIT_LAYOUTS else "rows", {}).get(max(1, players))
    return [SPLIT[name] for name in names] if names else None


def art_of(game):
    """The picture a game's backdrop starts from, or None."""
    if not game:
        return None
    return game.get("background") or game.get("cover") or game.get("image")


def load_picture(path, limit=ART_MAX):
    """Decoded picture (RGB, at most `limit` px) or None. Thread-safe (no cache)."""
    if not path:
        return None
    try:
        with Image.open(path) as image:
            image.draft("RGB", (limit, limit))              # fast JPEG downscale
            picture = image.convert("RGB")
            picture.thumbnail((limit, limit), Image.LANCZOS)
        return picture
    except Exception:
        return None


def background_sample(art, width, height):
    """The backdrop of `art` at thumbnail size: blurred, a few dozen pixels wide."""
    if art is not None:
        sample = BG_SAMPLE_W
        small = Glyphs.cover_crop(art, sample, max(1, round(sample * height / width)))
        return small.filter(ImageFilter.GaussianBlur(BG_BLUR))
    # Default backdrop: deep blue with a soft violet glow
    image = Image.new("RGB", (64, 36), (10, 12, 24))
    draw = ImageDraw.Draw(image)
    draw.ellipse((-12, -20, 40, 26), fill=(40, 36, 110))
    draw.ellipse((34, 14, 80, 50), fill=(14, 54, 84))
    return image.filter(ImageFilter.GaussianBlur(9)).resize((BG_SAMPLE_W, max(1, round(BG_SAMPLE_W * height / width))))


def compose_background(sample, width, height, shade):
    """Screen-sized backdrop from a background_sample(): stretched and shaded."""
    return ImageChops.multiply(sample.resize((width, height), Image.BILINEAR), shade)


def ease_out(x):
    """Fast start, soft landing (cubic)."""
    return 1 - (1 - x) ** 3


def smoothstep(x):
    """Slow start, slow end."""
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def hop_pose(progress, height):
    """
    A joining player's icon at `progress` (0-1) of its hop: (lift in px, squashed).
    One jump, a squash on landing, a small second bounce.
    """
    import math
    if progress < 0.4:
        return -height * math.sin(math.pi * progress / 0.4), False
    if progress < 0.52:
        return 0.0, True
    if progress < 0.82:
        return -height * 0.3 * math.sin(math.pi * (progress - 0.52) / 0.3), False
    return 0.0, False


def rounded_points(x0, y0, x1, y1, r):
    """Polygon points of a rounded rectangle (draw with smooth=True)."""
    r = max(0.0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    return (x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1,
            x0, y1, x0, y1 - r, x0, y0 + r, x0, y0)


class Animator:
    """
    Every running animation on one 60 fps timer.

        animator.run("shelf", 110, step, done)

    step(progress) is called with the eased progress, ending with exactly
    1.0; done() follows. Starting an animation under a key that is still
    running finishes the old one first (it jumps to its end), so a held
    direction never piles animations up. Nothing runs while idle.
    """

    FRAME_MS = 16

    def __init__(self, root):
        self.root = root
        self._running = {}              # key -> [start, duration s, step, done]
        self._job = None

    def run(self, key, ms, step, done=None):
        self.finish(key)
        self._running[key] = [time.monotonic(), max(0.001, ms / 1000), step, done]
        if self._job is None:
            self._job = self.root.after(self.FRAME_MS, self._frame)

    def finish(self, key):
        """Jump the animation `key` to its end now."""
        entry = self._running.pop(key, None)
        if entry:
            self._end(entry)

    def finish_all(self):
        for key in list(self._running):
            self.finish(key)

    def active(self, key=None):
        """Is the animation `key` running (any animation, with no key)?"""
        return bool(self._running) if key is None else key in self._running

    @staticmethod
    def _end(entry):
        try:
            entry[2](1.0)
            if entry[3]:
                entry[3]()
        except tk.TclError:
            pass                        # its canvas items are gone

    def _frame(self):
        self._job = None
        now = time.monotonic()
        for key, entry in list(self._running.items()):
            progress = (now - entry[0]) / entry[1]
            if progress >= 1.0:
                if self._running.get(key) is entry:
                    del self._running[key]
                self._end(entry)
            else:
                try:
                    entry[2](ease_out(progress))
                except tk.TclError:
                    self._running.pop(key, None)
        if self._running and self._job is None:
            # Aim at a steady 60 fps: the time this frame took comes off the wait
            spent = (time.monotonic() - now) * 1000
            self._job = self.root.after(max(1, int(self.FRAME_MS - spent)), self._frame)


class LauncherUi:
    """
    Owns the canvas. Core/App.py drives it through:

        refresh(slots) / flash_slot(i)        player cards
        set_hints([...]) / set_tip(markup)    footer hints, tip line
        set_pads(n)                           connected controllers
        set_backdrop(game, delay_ms)          blurred, slowly drifting backdrop
        show_games(...) / show_games_loading() / hide_games()
        show_toast(msg, color)
        show_trophy(name, desc) / trophy_showing
        show_trophies(items) / hide_trophies()
        show_alert(mode) / close_alert() / alert_mode
    """

    def __init__(self, root, emulator_name, display_name=None):
        self.root = root
        self.emulator_name = emulator_name          # branding: window title, icons, footer
        self.display_name = display_name or emulator_name   # the emulator in messages

        root.title(f"{emulator_name} Launcher")
        root.configure(bg=COLOR["BG"])
        try:
            root.configure(cursor="none")       # controller-only UI: hide the mouse
        except tk.TclError:
            pass
        root.attributes("-fullscreen", True)
        self._load_icon()

        self.canvas = tk.Canvas(root, bg=COLOR["BG"], highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self.width = self.height = 0
        self.scale = 1.0
        self.alert_mode = None
        self.alert_detail = None
        self.alert_since = 0.0
        self.view = "players"
        self.slots = []
        self.flashing = set()
        self._flash_until = {}
        self.hints = []
        self.tip = ""
        self.pads = 0
        self.batteries = []                 # [(colour, avatar, (percent, charging))] next to the clock

        self._fonts = {}
        self._photos = {}
        self._art = OrderedDict()
        self._backgrounds = OrderedDict()
        self._bg_wanted = None
        self._bg_owner = None
        self._bg_job = None
        self.motion = True                  # background drift, see BACKGROUND MOTION
        self.dynamic_colors = True          # selection colour from the game's art (GameGrid.accent)
        self._drift = (0, 0)
        self._motion_quiet_until = 0.0
        self.busy_overlay = False           # something animated on top (roulette): hold the backdrop still
        self._toast_job = None
        self._toast = None
        self._trophy = None                 # (name, desc) on screen
        self._trophy_job = None
        self._trophies = None               # list screen items, None = closed
        self._stats = None                  # statistics screen data, None = closed
        self._splash = None                 # "let's go" screen data, None = closed
        self._splash_cover = None           # (left, top, size) of the cover on the launch screen
        self.animations = True              # fades, cards popping in
        self.brand_note = ""                # after the name in the footer ("build 21")
        self.alert_buttons = None           # replaces an alert's default buttons
        self.alert_lines = ()               # extra lines under an alert's text
        self.alert_name = None
        self._fade_job = None
        self._fade_switch = None
        self._hold_draw = False             # a fade is hiding the screen: content waits
        self._pop = {}                      # player card index -> px still to travel
        self._hop = {}                      # player card index -> (lift, squashed) of its icon
        self._discs = {}                    # player card index -> (canvas item, resting y, avatar, size, colour)
        self._tv_shown = None               # (regions, colours) the split-screen map last showed
        self._pop_job = None
        self.dimmed = False
        self.menu = None                    # Core.Menu.Menu, set by the App

        self.animator = Animator(root)
        self._bg_samples = OrderedDict()    # path -> thumbnail-size backdrop (for cross-fades)
        self._bg_shown = None               # path of the backdrop on screen
        self._bg_fade = None                # (job, temporary picture) of a running cross-fade
        self.splash_ms = 0                  # how long the "let's go" screen lasts (its progress bar)
        self.resume = None                  # the game "continue" offers on the player screen
        self.quick = ()                     # hints shown next to the clock: [(button, label)]

        from .GameGrid import GameGrid
        self.grid = GameGrid(self)

        self.canvas.bind("<Configure>", self._on_configure)
        self._tick_clock()
        self._drift_tick()

    def _load_icon(self):
        """Window/taskbar icon: .ico on Windows, .png elsewhere."""
        ico = resource_path(os.path.join("assets", f"{self.emulator_name}LauncherIcon.ico"))
        png = resource_path(os.path.join("assets", f"{self.emulator_name}LauncherPNG.png"))
        try:
            if sys.platform == "win32" and os.path.exists(ico):
                self.root.iconbitmap(default=ico)
            elif os.path.exists(png):
                self._icon = tk.PhotoImage(file=png)
                self.root.iconphoto(True, self._icon)
        except Exception:
            pass

    # ========================================================================
    # SIZE / SCALE
    # ========================================================================
    def _on_configure(self, event):
        if event.widget is not self.canvas or (event.width, event.height) == (self.width, self.height):
            return
        self.width, self.height = event.width, event.height
        self.scale = max(0.3, min(self.width / BASE_W, self.height / BASE_H))
        # Everything sized in pixels must be made again
        self._fonts.clear()
        self._photos.clear()
        self._backgrounds.clear()
        self._bg_samples.clear()
        self.grid.reset()
        self.redraw()

    def px(self, value):
        return max(1, int(round(value * self.scale)))

    def set_theme(self, name=None, accent=None):
        """Switch colours (theme: dark / oled; accent: Theme.ACCENTS) and redraw everything with them."""
        apply_theme(name, accent)
        self.root.configure(bg=COLOR["BG"])
        self.canvas.configure(bg=COLOR["BG"])
        self._photos.clear()
        self._backgrounds.clear()
        self.grid.reset()
        self.redraw()

    def font(self, size, weight=True):
        """
        UI font `size` px tall (720p baseline). weight: True / "bold", False /
        "normal", or "semibold" / "light" where the system has them (titles,
        large numbers) - plain bold / normal otherwise.
        """
        weight = {True: "bold", False: "normal"}.get(weight, weight)
        key = (self.px(size), weight)
        if key not in self._fonts:
            family, tk_weight = FONT_FAMILY, "bold" if weight in ("bold", "semibold") else "normal"
            special = FONT_WEIGHTS.get(weight)
            if special and special in self._families():
                family, tk_weight = special, "normal"
            self._fonts[key] = tkfont.Font(family=family, size=-key[0], weight=tk_weight)
        return self._fonts[key]

    def _families(self):
        if not hasattr(self, "_font_families"):
            try:
                self._font_families = set(tkfont.families(self.root))
            except tk.TclError:
                self._font_families = set()
        return self._font_families

    def quiet(self):
        """True while idle animations should hold still: a dialog, a menu, a game running..."""
        if self.alert_mode or self.busy_overlay or self.dimmed or self._splash is not None \
                or self._trophies is not None or self._stats is not None \
                or (self.menu is not None and self.menu.active):
            return True
        try:
            return self.root.state() == "withdrawn"
        except tk.TclError:
            return True

    def photo(self, key, make):
        """PhotoImage cached under `key`; make() returns a PIL image."""
        image = self._photos.get(key)
        if image is None:
            image = self._photos[key] = ImageTk.PhotoImage(make())
        return image

    def has_photo(self, key):
        return key in self._photos

    def load_art(self, path):
        """Decoded picture (RGB, at most ART_MAX px), LRU-cached; None if unreadable."""
        if not path:
            return None
        if path in self._art:
            self._art.move_to_end(path)
            return self._art[path]
        art = load_picture(path)
        self._art[path] = art
        if len(self._art) > ART_CACHE:
            self._art.popitem(last=False)
        return art

    def forget_art(self, path):
        self._art.pop(path, None)
        self._bg_samples.pop(path, None)
        for key in [k for k in self._backgrounds if k[0] == path]:
            del self._backgrounds[key]

    # ========================================================================
    # TEXT HELPERS
    # ========================================================================
    def truncate(self, text, font, width):
        if font.measure(text) <= width:
            return text
        while text and font.measure(text + "…") > width:
            text = text[:-1]
        return text.rstrip() + "…"

    def _rich_parts(self, markup, size, bold):
        font = self.font(size, bold)
        glyph_h = int(self.px(size) * 1.5)
        gap = self.px(size * 0.3)
        parts = []
        for i, piece in enumerate(_TOKEN.split(markup)):
            if i % 2:
                image = self.photo(("glyph", piece, glyph_h), lambda p=piece: Glyphs.button(p, glyph_h))
                parts.append(("image", image, image.width()))
            elif piece:
                parts.append(("text", piece, font.measure(piece)))
        total = sum(w for _, _, w in parts) + gap * sum(1 for p in parts if p[0] == "image") * 2
        return parts, total, gap, font

    def rich_width(self, markup, size, bold=True):
        """Width in pixels rich() would use for `markup`."""
        return self._rich_parts(markup, size, bold)[1]

    def rich(self, x, y, markup, size, fill, bold=True, anchor="w", tags=()):
        """
        Draw text with inline button pictures ("Press [a] to join").

        Args:
            anchor: "w", "center" or "e" - horizontal alignment; y is the
                    vertical centre.
        Returns:
            int: total width in pixels.
        """
        parts, total, gap, font = self._rich_parts(markup, size, bold)
        cx = {"w": x, "center": x - total // 2, "e": x - total}[anchor]
        for kind, value, w in parts:
            if kind == "image":
                cx += gap
                self.canvas.create_image(cx, y, image=value, anchor="w", tags=tags)
                cx += w + gap
            else:
                self.canvas.create_text(cx, y, text=value, anchor="w", fill=fill, font=font, tags=tags)
                cx += w
        return total

    def glass(self, width, height, radius, fill="PANEL", edge="EDGE", glow=None):
        """A cached translucent panel with the light rim of the theme (PhotoImage)."""
        width, height = int(width), int(height)
        fill_rgba = COLOR[fill] if isinstance(fill, str) else fill
        edge_rgba = COLOR[edge] if isinstance(edge, str) and edge in COLOR else edge
        return self.photo(("glass", width, height, radius, fill_rgba, edge_rgba, glow), lambda: Glyphs.panel(
            width, height, radius, fill_rgba, outline=(edge_rgba, max(1, self.px(1.2))) if edge_rgba else None,
            glow=glow))

    # ========================================================================
    # WHOLE-SCREEN REDRAW
    # ========================================================================
    def redraw(self):
        if not self.width:
            return
        self._apply_background()
        self._draw_content()
        self._draw_header()
        self._draw_footer()
        if self._toast:
            self._draw_toast(*self._toast)
        if self._trophies is not None:
            self._draw_trophies()
        if self._stats is not None:
            self._draw_stats()
        if self.menu is not None and self.menu.active:
            self.menu.draw()
        if self._splash is not None:
            self._draw_splash()
        if self._trophy:
            self._draw_trophy(*self._trophy)
        if self.alert_mode:
            self._draw_alert(self.alert_mode)
        self._draw_dim()

    def _draw_content(self):
        if self.view == "players":
            self.grid.clear()
            self._draw_players()
        else:
            self.animator.finish("tv")
            self.canvas.delete("players", "tv-morph")
            self.grid.draw(full=True)

    def _raise_overlays(self):
        for tag in ("header", "footer", "roulette", "trophies", "menu", "splash", "toast", "trophy",
                    "alert", "fade", "dim"):
            self.canvas.tag_raise(tag)

    raise_overlays = _raise_overlays

    # ========================================================================
    # FADES
    # A change of screen goes through two quick steps of darkness instead of
    # jumping: darker, darker, (the new screen is drawn), lighter, gone -
    # about a tenth of a second. Four cached pictures, nothing else.
    # ========================================================================
    FADE_STEPS = (120, 215)
    FADE_MS = 22

    def fade(self, switch):
        """
        Change screen: switch() draws the new one, whose content then rises
        a little into place. (A full-screen fade looked softer but every
        step repainted and blended the whole window: the most expensive
        thing Tk can be asked to do, and the stutter users noticed.)
        """
        self._finish_fade()
        switch()
        if self.animations and self.width and self.view == "players":
            self._rise("players")

    def _rise(self, tag, distance=18, ms=200):
        c = self.canvas
        distance = self.px(distance)
        c.move(tag, 0, distance)
        offset = [1.0]

        def step(progress):
            c.move(tag, 0, distance * (1.0 - progress - offset[0]))
            offset[0] = 1.0 - progress
        self.animator.run(f"rise-{tag}", ms, step)

    def _fade_overlay(self, alpha):
        shade = self.photo(("fade", self.width, self.height, alpha), lambda: Image.new(
            "RGBA", (self.width, self.height), tuple(COLOR["FADE"]) + (alpha,)))
        item = self.canvas.find_withtag("fade")
        if item:
            self.canvas.itemconfigure(item[0], image=shade)
        else:
            self.canvas.create_image(0, 0, image=shade, anchor="nw", tags="fade")
        self.canvas.tag_raise("fade")
        self.canvas.tag_raise("dim")

    def _fade_step(self, step):
        self._fade_job = None
        down = len(self.FADE_STEPS)
        if step < down:
            self._fade_overlay(self.FADE_STEPS[step])
        elif step == down:
            switch, self._fade_switch = self._fade_switch, None
            self._hold_draw = False
            if switch:
                switch()
            self._fade_overlay(self.FADE_STEPS[-1])
        elif step <= 2 * down - 1:
            self._fade_overlay(self.FADE_STEPS[2 * down - 1 - step])
        else:
            self.canvas.delete("fade")
            return
        self._fade_job = self.root.after(self.FADE_MS, self._fade_step, step + 1)

    def _finish_fade(self):
        if self._fade_job:
            self.root.after_cancel(self._fade_job)
            self._fade_job = None
        switch, self._fade_switch = self._fade_switch, None
        self._hold_draw = False
        if switch:
            switch()
        self.canvas.delete("fade")

    # ========================================================================
    # IDLE DIM
    # ========================================================================
    def set_dim(self, on):
        """Nearly black screen with only the clock, moved now and then (idle TV)."""
        if on == self.dimmed:
            return
        self.dimmed = on
        self._draw_dim()

    def _draw_dim(self):
        c = self.canvas
        c.delete("dim")
        if not self.dimmed or not self.width:
            return
        shade = self.photo(("dim", self.width, self.height),
                           lambda: Image.new("RGBA", (self.width, self.height), (0, 0, 0, 238)))
        c.create_image(0, 0, image=shade, anchor="nw", tags="dim")
        # A new place every minute, so nothing stays lit on the same pixels
        minute = int(time.time() // 60)
        x = self.px(160) + (minute * 197) % max(1, self.width - self.px(320))
        y = self.px(120) + (minute * 113) % max(1, self.height - self.px(240))
        c.create_text(x, y, text=time.strftime("%H:%M"), fill="#5A5F6B", font=self.font(40), tags="dim")
        c.tag_raise("dim")

    # ========================================================================
    # BACKGROUND
    # ========================================================================
    def set_backdrop(self, game, delay_ms=0, fade=True):
        """
        Blurred art of `game` behind everything (None: the default backdrop).
        delay_ms debounces fast scrolling; fade=False swaps it at once (the
        caller is changing the screen in the same moment anyway).
        """
        self.hold_motion()
        self._bg_owner = game
        path = art_of(game)
        if path == self._bg_wanted and self.canvas.find_withtag("bg"):
            return
        self._bg_wanted = path
        self._bg_crossfade = fade
        if self._bg_job:
            self.root.after_cancel(self._bg_job)
            self._bg_job = None
        if delay_ms:
            self._bg_job = self.root.after(delay_ms, self._apply_background)
        else:
            self._apply_background()

    def _shade(self, width, height):
        """Brightness map: dim overall, darker at the edges and at the bottom."""
        key = ("shade", width, height)
        if key not in self._photos:           # stored as a PIL image
            w, h = 160, 90
            mask = Image.new("L", (w, h), 0)
            ImageDraw.Draw(mask).ellipse((-w * 0.25, -h * 0.35, w * 1.25, h * 1.2), fill=255)
            mask = mask.filter(ImageFilter.GaussianBlur(18))
            vignette = mask.point(lambda v: int(38 + v * 0.36))            # 38..130
            bottom = Image.linear_gradient("L").resize((w, h)).point(lambda v: 255 - int(v * 0.62))
            shade = ImageChops.multiply(vignette, bottom).resize((width, height), Image.BILINEAR)
            self._photos[key] = Image.merge("RGB", (shade, shade, shade))
        return self._photos[key]

    def _margin(self):
        """How far the backdrop may drift: it is drawn this much larger on every side."""
        return self.px(DRIFT) if self.motion else 0

    def _sample(self, path):
        sample = self._bg_samples.get(path)
        if sample is None:
            m = self._margin()
            sample = self._bg_samples[path] = background_sample(
                self.load_art(path), self.width + 2 * m, self.height + 2 * m)
            if len(self._bg_samples) > 40:
                self._bg_samples.popitem(last=False)
        return sample

    def _bg_zoom(self):
        """
        The backdrop is composed small and enlarged by whole pixels inside
        Tk: it is a blurred picture a few dozen pixels wide to begin with,
        so nothing is lost, and a new one costs a few milliseconds instead
        of tens (which is what makes cross-fading it affordable).
        """
        return max(1, round(self.width / BG_COMPOSE_W))

    def _enlarge(self, sample):
        """Screen-sized backdrop picture (a Tk image) from a thumbnail-size sample."""
        m, zoom = self._margin(), self._bg_zoom()
        width = -(-(self.width + 2 * m) // zoom)
        height = -(-(self.height + 2 * m) // zoom)
        small = ImageTk.PhotoImage(compose_background(sample, width, height, self._shade(width, height)))
        if zoom == 1:
            return small
        big = tk.PhotoImage(master=self.root)
        self.root.tk.call(big.name, "copy", str(small), "-zoom", zoom, zoom)
        return big

    def _background_photo(self, path):
        key = (path, self.width, self.height, self._margin())
        if key in self._backgrounds:
            self._backgrounds.move_to_end(key)
            return self._backgrounds[key]
        photo = self._enlarge(self._sample(path))
        self._backgrounds[key] = photo
        if len(self._backgrounds) > BG_CACHE:
            self._backgrounds.popitem(last=False)
        return photo

    def _apply_background(self):
        self._bg_job = None
        if not self.width:
            return
        self._stop_bg_fade()
        if current_theme() == "oled":           # pure black: no picture at all
            self.canvas.delete("bg")
            self._bg_shown = None
            return
        m = self._margin()
        x, y = -m + self._drift[0], -m + self._drift[1]
        item = self.canvas.find_withtag("bg")
        if not item:
            self.canvas.create_image(x, y, image=self._background_photo(self._bg_wanted), anchor="nw", tags="bg")
            self.canvas.tag_lower("bg")
            self._bg_shown = self._bg_wanted
            return
        self.canvas.coords(item[0], x, y)
        old, new = self._bg_shown, self._bg_wanted
        self._bg_shown = new
        if not self.animations or old == new or self.quiet() or not getattr(self, "_bg_crossfade", True):
            self.canvas.itemconfigure(item[0], image=self._background_photo(new))
            return
        # Cross-fade: blend the two thumbnails (cheap), enlarge each step
        before, after = self._sample(old), self._sample(new)
        if before.size != after.size:
            before = before.resize(after.size)

        def step(n=0):
            if n < len(BG_FADE):
                photo = self._enlarge(Image.blend(before, after, BG_FADE[n]))
                self.canvas.itemconfigure("bg", image=photo)
                self._bg_fade = (self.root.after(BG_FADE_MS, step, n + 1), photo)
            else:
                self._bg_fade = None
                self.canvas.itemconfigure("bg", image=self._background_photo(new))
        step()

    def _stop_bg_fade(self):
        if self._bg_fade:
            self.root.after_cancel(self._bg_fade[0])
            self._bg_fade = None

    # ========================================================================
    # BACKGROUND MOTION
    # The blurred backdrop drifts slowly around its centre, one pixel at a
    # time. Moving it repaints the whole window, so it moves rarely (about
    # twice a second - invisible steps on a blurred picture), never while
    # the grid is being browsed, and not at all while a game runs, a dialog
    # is open or the roulette spins.
    # ========================================================================
    def hold_motion(self, seconds=DRIFT_QUIET_S):
        self._motion_quiet_until = time.monotonic() + seconds

    def _drift_tick(self):
        self.root.after(DRIFT_TICK_MS, self._drift_tick)
        if not self.motion or not self.width or self.alert_mode or self.busy_overlay or self.animator.active() \
                or self._trophies is not None or self._stats is not None or self._splash is not None \
                or self.dimmed or (self.menu is not None and self.menu.active):
            return
        now = time.monotonic()
        if now < self._motion_quiet_until:
            return
        try:
            if self.root.state() == "withdrawn":
                return
        except tk.TclError:
            return
        amplitude = self._margin() * 0.9
        drift = (round(amplitude * math.sin(2 * math.pi * now / DRIFT_PERIODS[0])),
                 round(amplitude * math.sin(2 * math.pi * now / DRIFT_PERIODS[1] + 1.3)))
        if drift == self._drift:
            return
        self._drift = drift
        item = self.canvas.find_withtag("bg")
        if item:
            m = self._margin()
            self.canvas.coords(item[0], -m + drift[0], -m + drift[1])

    # ========================================================================
    # HEADER
    # ========================================================================
    def _tick_clock(self):
        if self.width:
            self.canvas.itemconfigure("clock", text=time.strftime("%H:%M"))
            if self.dimmed:
                self._draw_dim()
        self.root.after(15000, self._tick_clock)

    def set_pads(self, count):
        if count != self.pads:
            self.pads = count
            self._draw_header()

    def set_batteries(self, entries):
        """Controller batteries next to the clock: [(player colour, avatar, (percent, charging))]."""
        if list(entries) != self.batteries:
            self.batteries = list(entries)
            self._draw_header()

    def set_quick(self, hints):
        """Shortcuts shown next to the clock: [(button, label)] (settings, achievements)."""
        if list(hints) != list(self.quick):
            self.quick = list(hints)
            self._draw_header()

    def _draw_header(self):
        c = self.canvas
        c.delete("header")
        if not self.width:
            return
        right = self.width - self.px(MARGIN)
        y = self.px(52)
        # Status pill: connected controllers and the time
        clock_font, count_font = self.font(17, "semibold"), self.font(15)
        pad_h = self.px(17)
        clock_w = clock_font.measure("00:00")
        count_w = count_font.measure(str(self.pads))
        w = self.px(18) + pad_h * 2 + self.px(8) + count_w + self.px(18) + clock_w + self.px(18)
        # One avatar + battery per player whose controller reports a battery
        icon_h, cell_h = self.px(16), self.px(13)
        cells = [(color, avatar, battery, self.px(5) + icon_h + self.px(5) + int(cell_h * 1.9) + self.px(8))
                 for color, avatar, battery in self.batteries]
        w += sum(cell[3] for cell in cells)
        h = self.px(40)
        c.create_image(right, y, image=self.glass(w, h, h // 2, "PILL"), anchor="e", tags="header")
        x = right - w + self.px(14)
        for color, avatar, (level, charging), cell_w in cells:
            mark = self.photo(("avatar", avatar, icon_h, color), lambda: Glyphs.avatar(avatar, icon_h, color))
            c.create_image(x + self.px(5), y, image=mark, anchor="w", tags="header")
            gauge = self.photo(("battery", cell_h, level, COLOR["TEXT_DIM"], charging),
                               lambda: Glyphs.battery(cell_h, level, COLOR["TEXT_DIM"], charging))
            c.create_image(x + self.px(10) + icon_h, y, image=gauge, anchor="w", tags="header")
            x += cell_w
        c.create_text(right - self.px(18), y, text=time.strftime("%H:%M"), anchor="e", fill=COLOR["TEXT"],
                      font=clock_font, tags=("header", "clock"))
        x = right - self.px(18) - clock_w - self.px(18)
        c.create_text(x, y, text=str(self.pads), anchor="e", font=count_font, tags="header",
                      fill=COLOR["TEXT"] if self.pads else COLOR["TEXT_FAINT"])
        icon = self.photo(("pad", pad_h, COLOR["TEXT_DIM"]), lambda: Glyphs.gamepad(pad_h, COLOR["TEXT_DIM"]))
        c.create_image(x - count_w - self.px(8), y, image=icon, anchor="e", tags="header")

        # Shortcuts to the left of it
        x = right - w - self.px(22)
        for button, label in reversed(self.quick):
            x -= self.rich(x, y, f"[{button}] {label}", 13, COLOR["TEXT_DIM"], anchor="e", tags="header") + self.px(22)

        if self.view == "players":
            left = self.px(MARGIN)
            c.create_text(left, self.px(58), text=t("title_players"), anchor="w", fill=COLOR["TEXT"],
                          font=self.font(36, "semibold"), tags="header")
            self.rich(left, self.px(102), t("subtitle_players"), 15, COLOR["TEXT_DIM"],
                      bold=False, tags="header")
        self._raise_overlays()

    # ========================================================================
    # FOOTER
    # ========================================================================
    def set_brand(self, note):
        """Text after the launcher's name in the footer: the build, a newer one."""
        if note != self.brand_note:
            self.brand_note = note
            self._draw_footer()

    def set_hints(self, hints):
        """hints: [(button or (buttons...), label)], drawn right to left from the edge."""
        if hints != self.hints:
            self.hints = list(hints)
            self._draw_footer()

    def _draw_footer(self):
        """The hint dock: a glass pill centred at the bottom, the launcher's name in the corner."""
        c = self.canvas
        c.delete("footer")
        if not self.width:
            return
        y = self.height - self.px(DOCK_Y)
        hints = []
        for buttons, label in self.hints:
            if isinstance(buttons, str):
                buttons = (buttons,)
            hints.append("".join(f"[{b}]" for b in buttons) + " " + label)
        dock_left = self.width
        if hints:
            room = self.width - 2 * self.px(36)
            for size, gap in ((15, 30), (14, 24), (13, 18), (12, 14)):
                gap = self.px(gap)
                widths = [self.rich_width(markup, size) for markup in hints]
                total = sum(widths) + gap * (len(hints) - 1) + 2 * self.px(26)
                if total <= room:
                    break
            h = self.px(48)
            c.create_image(self.width // 2, y, image=self.glass(total, h, h // 2, "PILL"), tags="footer")
            x = dock_left = self.width // 2 - total // 2 + self.px(26)
            for markup, width in zip(hints, widths):
                self.rich(x, y, markup, size, COLOR["TEXT"], tags="footer")
                x += width + gap
            dock_left -= self.px(26)
        brand = f"{self.emulator_name.upper()} LAUNCHER" + (f"  ·  {self.brand_note}" if self.brand_note else "")
        brand_font = self.font(11)
        if self.px(MARGIN) + brand_font.measure(brand) + self.px(20) < dock_left:    # only where the dock leaves room
            c.create_text(self.px(MARGIN), y, text=brand, anchor="w",
                          fill=COLOR["TEXT_FAINT"], font=brand_font, tags="footer")
        self._raise_overlays()

    # ========================================================================
    # PLAYER CARDS
    # ========================================================================
    def set_tip(self, markup):
        self.tip = markup
        if self.view == "players":
            self._draw_players()

    def set_resume(self, game):
        """The game the "continue" card on the player screen offers (None: no card)."""
        if game is not self.resume:
            self.resume = game
            if self.view == "players":
                self._draw_players()

    def refresh(self, slots):
        """
        Args:
            slots (list[dict]): assigned controllers in player order, at most 8:
                {"name", "color", "profile", "editing"}
        """
        self.slots = slots
        if self.view == "players":
            self._draw_players()

    def flash_slot(self, index, duration_ms=700, pop=False):
        """
        Glow a player's row briefly (a controller just joined, or its owner
        pressed a button); with pop=True the row also slides into place.
        """
        self.flashing.add(index)
        self._flash_until[index] = time.monotonic() + duration_ms / 1000
        if pop and self.animations:
            start = self.px(POP_DISTANCE)
            self._pop[index] = start

            def step(progress):
                left = int(round(start * (1.0 - progress)))
                moved = self._pop.get(index, 0) - left
                self._pop[index] = left
                if moved and self.view == "players" and not self._hold_draw:
                    self.canvas.move(f"slot{index}", -moved, 0)

            def done():
                self._pop.pop(index, None)
            self.refresh(self.slots)
            self.animator.run(f"pop{index}", POP_MS, step, done)
        else:
            self.refresh(self.slots)

        def stop():
            if time.monotonic() + 0.02 >= self._flash_until.get(index, 0):  # no newer flash running
                self.flashing.discard(index)
                self.refresh(self.slots)
        self.root.after(duration_ms, stop)

    def hop(self, index, delay_ms=0):
        """Player index + 1's icon jumps (they joined, or picked another icon)."""
        if not self.animations:
            return

        def start():
            started = time.monotonic()

            def step(progress):
                p = 1.0 if progress >= 1.0 else min(1.0, (time.monotonic() - started) * 1000 / HOP_MS)
                self._hop[index] = hop_pose(p, self.px(HOP_HEIGHT))
                self._place_disc(index)

            def done():
                self._hop.pop(index, None)
                self._place_disc(index)
            self.animator.run(f"hop{index}", HOP_MS, step, done)
        if delay_ms:
            self.root.after(delay_ms, start)
        else:
            start()

    def _place_disc(self, index):
        entry = self._discs.get(index)
        if not entry or self.view != "players":
            return
        item, middle, avatar, size, color = entry
        lift, squashed = self._hop.get(index, (0.0, False))
        c = self.canvas
        if not c.type(item):
            return
        if squashed:
            image = self.photo(("disc-squash", avatar, size, color), lambda: Glyphs.avatar_disc(
                avatar, size, color, COLOR["DISC_TEXT"]).resize((int(size * 1.2), int(size * 0.8)), Image.LANCZOS))
            y = middle + size * 0.1
        else:
            image = self.photo(("disc", avatar, size, color), lambda: Glyphs.avatar_disc(avatar, size, color,
                                                                                       COLOR["DISC_TEXT"]))
            y = middle + lift
        c.itemconfigure(item, image=image)
        c.coords(item, c.coords(item)[0], y)

    def _morph_tv(self, left, top, width, height, before, after):
        """The split-screen map divides anew: each player's part glides from its old place to its new one."""
        c = self.canvas
        c.delete("tv-morph")
        self.animator.finish("tv")
        tags = ("tv-morph",)
        empty = self.photo(("tv-empty", width, height), lambda: Glyphs.split_map(width, height, None, ()))
        c.create_image(left, top, image=empty, anchor="nw", tags=tags)
        # The map's geometry, as Glyphs.split_map draws it
        inset = (max(2, 3) + int(2 * height * 0.035)) / 2
        inner_w, inner_h = width - 2 * inset, height - 2 * inset
        gap = max(2, int(2 * height * 0.012)) / 2
        radius = height * 0.085 * 0.62

        def box(region):
            x0, y0, x1, y1 = region
            return (left + inset + x0 * inner_w + gap, top + inset + y0 * inner_h + gap,
                    left + inset + x1 * inner_w - gap, top + inset + y1 * inner_h - gap)

        def collapsed(rect):
            cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
            return (cx, cy, cx, cy)
        old_regions, old_colors = before
        new_regions, new_colors = after
        parts = []
        for i in range(max(len(old_regions), len(new_regions))):
            start = box(old_regions[i]) if i < len(old_regions) else None
            end = box(new_regions[i]) if i < len(new_regions) else None
            start = start or collapsed(end)
            end = end or collapsed(start)
            color = new_colors[i] if i < len(new_colors) else old_colors[i] if i < len(old_colors) else "#888888"
            item = c.create_polygon(rounded_points(*start, radius), smooth=True, fill=color, outline="", tags=tags)
            parts.append((item, start, end))
        c.tag_raise("tv-morph")
        started = time.monotonic()

        def step(progress):
            p = 1.0 if progress >= 1.0 else smoothstep((time.monotonic() - started) * 1000 / TV_MORPH_MS)
            for item, (a0, b0, a1, b1), (c0, d0, c1, d1) in parts:
                rect = (a0 + (c0 - a0) * p, b0 + (d0 - b0) * p, a1 + (c1 - a1) * p, b1 + (d1 - b1) * p)
                c.coords(item, *rounded_points(*rect, radius))
        self.animator.run("tv", TV_MORPH_MS, step, lambda: c.delete("tv-morph"))

    def _draw_players(self):
        """
        Left: a TV showing who plays in which part of the screen, the
        "continue" card, the tip. Right: one row per player who joined, and
        a faint row inviting the next one.
        """
        c = self.canvas
        if self._hold_draw:
            return
        c.delete("players")
        if not self.width:
            return
        left, top = self.px(MARGIN), self.px(150)
        slots = self.slots
        count = len(slots)

        # ---- the screen ------------------------------------------------
        tv_w, tv_h = self.px(480), self.px(270)
        layout = split_layout(count) if count else None
        regions = tuple(region for region, _ in layout) if layout else None
        colors = tuple(slot["color"] for slot in slots)
        icons = tuple(slot.get("avatar") for slot in slots)
        screen = self.photo(("tv", tv_w, tv_h, regions, colors, COLOR["DISC_TEXT"], icons),
                            lambda: Glyphs.split_map(tv_w, tv_h, regions, colors, COLOR["DISC_TEXT"], icons))
        c.create_image(left, top, image=screen, anchor="nw", tags="players")
        shown, self._tv_shown = self._tv_shown, (regions, colors)
        if shown and shown[0] and regions and shown[0] != regions and self.animations:
            self._morph_tv(left, top, tv_w, tv_h, shown, (regions, colors))
        if not count:
            self.rich(left + tv_w // 2, top + tv_h // 2, t("slot_join"), 17, COLOR["TEXT_DIM"],
                      anchor="center", tags="players")
        caption = t("split_shared") if count and not layout else t("players_tv_caption")
        c.create_text(left + self.px(4), top + tv_h + self.px(24), text=caption, anchor="w",
                      fill=COLOR["TEXT_FAINT"], font=self.font(13, False), tags="players")

        y = top + tv_h + self.px(52)
        if self.resume:
            card_h, icon = self.px(84), self.px(60)
            c.create_image(left, y, image=self.glass(tv_w, card_h, self.px(18)), anchor="nw", tags="players")
            c.create_image(left + self.px(12), y + card_h // 2, anchor="w", tags="players",
                           image=self.grid.tile(self.resume, icon, icon))
            text_x = left + self.px(12) + icon + self.px(16)
            hint_w = self.rich(left + tv_w - self.px(18), y + card_h // 2, "[y]", 17, COLOR["TEXT"],
                               anchor="e", tags="players")
            c.create_text(text_x, y + self.px(28), text=t("resume_caption").upper(), anchor="w",
                          fill=COLOR["ACCENT"], font=self.font(11), tags="players")
            title_font = self.font(17, "semibold")
            c.create_text(text_x, y + self.px(54), anchor="w", fill=COLOR["TEXT"], font=title_font, tags="players",
                          text=self.truncate(self.resume["title"], title_font,
                                             left + tv_w - self.px(30) - hint_w - text_x))
            y += card_h + self.px(26)
        if self.tip:
            self.rich(left + self.px(4), y, self.tip, 12, COLOR["TEXT_FAINT"], bold=False, tags="players")

        # ---- the players -----------------------------------------------
        x0 = left + tv_w + self.px(56)
        row_w = self.width - self.px(MARGIN) - x0
        rows = min(8, count + 1)
        row_h = self.px(62 if rows <= 6 else 53)
        gap = self.px(10 if rows <= 6 else 8)
        radius = self.px(18)
        name_font, small = self.font(16, "semibold"), self.font(12, False)
        for i in range(rows):
            y = top + i * (row_h + gap)
            middle = y + row_h // 2
            if i >= count:                      # the next free place
                c.create_image(x0, y, image=self.glass(row_w, row_h, radius, "PANEL_EMPTY"), anchor="nw",
                               tags="players")
                c.create_text(x0 + self.px(34), middle, text=f"P{i + 1}", fill=COLOR["TEXT_FAINT"],
                              font=self.font(15), tags="players")
                self.rich(x0 + self.px(70), middle, t("slot_join"), 14, COLOR["TEXT_FAINT"], tags="players")
                continue

            slot = slots[i]
            tags = ("players", f"slot{i}")
            x = x0 + self._pop.get(i, 0)        # still sliding into place
            color = slot["color"]
            glow = i in self.flashing
            pad = self.px(16) if glow else 0
            if glow:
                image = self.photo(("row-glow", row_w, row_h, color), lambda: Glyphs.panel(
                    row_w, row_h, radius, COLOR["PANEL"], outline=(color, self.px(2)), glow=(color, pad)))
            else:
                image = self.glass(row_w, row_h, radius)
            c.create_image(x - pad, y - pad, image=image, anchor="nw", tags=tags)

            size = self.px(40)
            avatar = slot.get("avatar") or f"P{i + 1}"
            disc = self.photo(("disc", avatar, size, color), lambda: Glyphs.avatar_disc(avatar, size, color,
                                                                                       COLOR["DISC_TEXT"]))
            item = c.create_image(x + self.px(14) + size // 2, middle, image=disc, tags=tags)
            self._discs[i] = (item, middle, avatar, size, color)
            if i in self._hop:
                self._place_disc(i)

            # Right: where this player plays, then the button layout
            right = x + row_w - self.px(14)
            if slot["editing"]:
                text = f"◀   {slot['profile']}   ▶"
                chip_w = name_font.measure(text) + self.px(30)
                chip = self.photo(("chip-edit", chip_w, self.px(34), COLOR["ACCENT"]), lambda: Glyphs.panel(
                    chip_w, self.px(34), self.px(17), COLOR["ROW_SELECTED"], outline=(COLOR["ACCENT"], self.px(2))))
                c.create_image(right, middle, image=chip, anchor="e", tags=tags)
                c.create_text(right - chip_w // 2, middle, text=text, fill=COLOR["TEXT"], font=name_font, tags=tags)
                right -= chip_w + self.px(16)
            else:
                chip_font = self.font(13)
                chip_w = chip_font.measure(slot["profile"]) + self.px(26)
                c.create_image(right, middle, image=self.glass(chip_w, self.px(30), self.px(15), "CHIP"),
                               anchor="e", tags=tags)
                c.create_text(right - chip_w // 2, middle, text=slot["profile"], fill=COLOR["TEXT_DIM"],
                              font=chip_font, tags=tags)
                right -= chip_w + self.px(16)
                where = t(layout[i][1]) if layout else t("split_shared")
                where_font = self.font(14)
                c.create_text(right, middle, text=where, anchor="e", fill=color, font=where_font, tags=tags)
                right -= where_font.measure(where) + self.px(16)

            text_x = x + self.px(14) + size + self.px(14)
            room = max(self.px(40), right - text_x)
            c.create_text(text_x, middle - self.px(10), anchor="w", fill=COLOR["TEXT"], font=name_font,
                          text=self.truncate(t("player_n", n=i + 1), name_font, room), tags=tags)
            if slot["editing"]:
                self.rich(text_x, middle + self.px(12), t("slot_editing_hints"), 11, COLOR["TEXT_DIM"], tags=tags)
            else:
                sub_x = text_x
                if slot.get("battery"):
                    level, charging = slot["battery"]
                    gauge_h = self.px(11)
                    gauge = self.photo(("battery", gauge_h, level, COLOR["TEXT_FAINT"], charging),
                                       lambda: Glyphs.battery(gauge_h, level, COLOR["TEXT_FAINT"], charging))
                    c.create_image(sub_x, middle + self.px(12), image=gauge, anchor="w", tags=tags)
                    sub_x += gauge.width() + self.px(6)
                    label = f"{level}%"
                    c.create_text(sub_x, middle + self.px(12), anchor="w", font=small, text=label, tags=tags,
                                  fill=COLOR["NEON_RED"] if level < 15 else COLOR["TEXT_FAINT"])
                    sub_x += small.measure(label) + self.px(10)
                c.create_text(sub_x, middle + self.px(12), anchor="w", fill=COLOR["TEXT_FAINT"], font=small,
                              text=self.truncate(slot["name"], small, max(self.px(40), right - sub_x)), tags=tags)
        c.tag_raise("tv-morph")
        self._raise_overlays()

    # ========================================================================
    # GAME GRID
    # ========================================================================
    def show_games(self, games, index, players=0, filter_players=0, sort_label=None, pinned=0, tabs=None,
                   tab=None):
        """
        Show (or update) the game grid; see Core/GameGrid.py.

        Args:
            pinned (int): how many games at the start form the "Continue playing" row.
            tabs (list): [(key, label, count)] over the grid, tab the current key.
        """
        entering = self.view != "games"
        self.view = "games"
        if sort_label is not None:
            self.grid.sort_label = sort_label
        self.grid.set(games, index, players, filter_players, pinned, tabs, tab)
        if entering:
            self.fade(self.redraw)
        elif not self._hold_draw:
            self.grid.draw()
            self._raise_overlays()

    def show_games_loading(self):
        self.view = "games"
        self.grid.set_loading()
        self.redraw()

    def hide_games(self):
        """Back to the player cards."""
        if self.view == "players":
            return
        self.view = "players"
        self.fade(self.redraw)

    def games_columns(self):
        return self.grid.columns

    def games_page(self):
        return self.grid.page_size()

    def games_art_changed(self, game):
        """A picture arrived for `game`: redraw what shows it."""
        for key in ("cover", "image", "background"):
            self.forget_art(game.get(key))
        self.grid.forget(game)
        if self.view == "games" and not self._hold_draw:
            self.grid.draw()
            self._raise_overlays()
        if game is self._bg_owner:
            self.set_backdrop(game)             # its backdrop picture may have changed

    # ========================================================================
    # ACHIEVEMENTS
    # ========================================================================
    @property
    def trophy_showing(self):
        return self._trophy is not None

    def show_trophy(self, name, desc, duration_ms=4500):
        """ "Achievement unlocked" banner at the top of the screen."""
        self._trophy = (name, desc)
        self._draw_trophy(name, desc)
        if self._trophy_job:
            self.root.after_cancel(self._trophy_job)
        self._trophy_job = self.root.after(duration_ms, self._hide_trophy)

    def _hide_trophy(self):
        self._trophy = None
        self._trophy_job = None
        self.canvas.delete("trophy")

    def _draw_trophy(self, name, desc):
        c = self.canvas
        c.delete("trophy")
        if not self.width:
            return
        w, h = self.px(580), self.px(92)
        x, y = self.width // 2, self.px(22) + h // 2
        box = self.photo(("trophy-box", w, h), lambda: Glyphs.panel(
            w, h, self.px(18), COLOR["TROPHY"], outline=(COLOR["GOLD"], self.px(2)),
            glow=(COLOR["GOLD"], self.px(14))))
        c.create_image(x, y, image=box, tags="trophy")
        icon_h = self.px(52)
        icon = self.photo(("trophy", icon_h, COLOR["GOLD"]), lambda: Glyphs.trophy(icon_h, COLOR["GOLD"]))
        left = x - w // 2 + self.px(24)
        c.create_image(left, y, image=icon, anchor="w", tags="trophy")
        text_x = left + icon_h + self.px(18)
        width = w - (text_x - (x - w // 2)) - self.px(20)
        c.create_text(text_x, y - self.px(24), text=t("trophy_unlocked"), anchor="w", fill=COLOR["GOLD"],
                      font=self.font(12), tags="trophy")
        c.create_text(text_x, y, text=self.truncate(name, self.font(21), width), anchor="w",
                      fill=COLOR["TEXT"], font=self.font(21), tags="trophy")
        c.create_text(text_x, y + self.px(25), text=self.truncate(desc, self.font(13, False), width), anchor="w",
                      fill=COLOR["TEXT_DIM"], font=self.font(13, False), tags="trophy")
        self._raise_overlays()

    def show_trophies(self, items, count_text):
        """
        The achievements list over the current screen.

        Args:
            items (list[tuple]): (name, desc, unlocked, detail) in display order.
        """
        self._trophies = (items, count_text)
        self._draw_trophies()

    def hide_trophies(self):
        self._trophies = None
        self.canvas.delete("trophies")

    def _draw_trophies(self):
        c = self.canvas
        c.delete("trophies")
        if not self.width or self._trophies is None:
            return
        items, count_text = self._trophies
        shade = self.photo(("trophies-overlay", self.width, self.height),
                           lambda: Image.new("RGBA", (self.width, self.height), COLOR["SCREEN"]))
        c.create_image(0, 0, image=shade, anchor="nw", tags="trophies")
        left = self.px(MARGIN)
        c.create_text(left, self.px(46), text=t("trophies_title"), anchor="w", fill=COLOR["TEXT"],
                      font=self.font(32), tags="trophies")
        c.create_text(left, self.px(86), text=count_text, anchor="w", fill=COLOR["GOLD"],
                      font=self.font(15), tags="trophies")

        columns = 2
        rows = (len(items) + columns - 1) // columns
        gap = self.px(12)
        top = self.px(116)
        card_w = (self.width - 2 * left - gap) // columns
        card_h = max(self.px(40), min(self.px(66), (self.height - top - self.px(84)) // max(1, rows) - gap))
        icon_h = int(card_h * 0.62)
        for i, (name, desc, unlocked, detail) in enumerate(items):
            x = left + (i // rows) * (card_w + gap)
            y = top + (i % rows) * (card_h + gap)
            color = COLOR["GOLD"] if unlocked else COLOR["LOCKED"]
            card = self.photo(("trophy-card", card_w, card_h, unlocked), lambda: Glyphs.panel(
                card_w, card_h, self.px(12), COLOR["TROPHY_ROW"] if unlocked else COLOR["ROW"],
                outline=(COLOR["GOLD"] if unlocked else COLOR["LINE_FAINT"], max(1, self.px(1.5)))))
            c.create_image(x, y, image=card, anchor="nw", tags="trophies")
            icon = self.photo(("trophy", icon_h, color), lambda: Glyphs.trophy(icon_h, color))
            c.create_image(x + self.px(14), y + card_h // 2, image=icon, anchor="w", tags="trophies")
            text_x = x + self.px(14) + icon_h + self.px(14)
            detail_w = self.font(12, False).measure(detail) + self.px(16) if detail else 0
            width = x + card_w - text_x - self.px(14) - detail_w
            c.create_text(text_x, y + card_h * 0.34, anchor="w", font=self.font(16),
                          text=self.truncate(name, self.font(16), width),
                          fill=COLOR["TEXT"] if unlocked else COLOR["TEXT_FAINT"], tags="trophies")
            c.create_text(text_x, y + card_h * 0.7, anchor="w", font=self.font(12, False),
                          text=self.truncate(desc, self.font(12, False), width),
                          fill=COLOR["TEXT_DIM"] if unlocked else COLOR["TEXT_FAINT"], tags="trophies")
            if detail:
                c.create_text(x + card_w - self.px(14), y + card_h // 2, anchor="e", text=detail,
                              fill=COLOR["TEXT_DIM"], font=self.font(12, False), tags="trophies")
        self.rich(self.width - left, self.height - self.px(FOOTER_H / 2), t("trophies_close"), 15,
                  COLOR["TEXT"], anchor="e", tags="trophies")
        self._raise_overlays()

    # ========================================================================
    # STATISTICS
    # ========================================================================
    def show_stats(self, data):
        """
        The statistics screen over the current one.

        Args:
            data (dict): {"tiles": [(label, value, detail)],        up to 4
                          "days":  [(label, seconds)],              oldest first
                          "games": [(title, seconds)]}              most played first
        """
        self._stats = data
        self._draw_stats()

    def hide_stats(self):
        self._stats = None
        self.canvas.delete("stats")

    def _draw_stats(self):
        from .I18n import playtime_label
        c = self.canvas
        c.delete("stats")
        if not self.width or self._stats is None:
            return
        data = self._stats
        tags = ("stats", "trophies")            # same layer as the achievements list
        shade = self.photo(("trophies-overlay", self.width, self.height),
                           lambda: Image.new("RGBA", (self.width, self.height), COLOR["SCREEN"]))
        c.create_image(0, 0, image=shade, anchor="nw", tags=tags)
        left = self.px(MARGIN)
        c.create_text(left, self.px(46), text=t("stats_title"), anchor="w", fill=COLOR["TEXT"],
                      font=self.font(32), tags=tags)
        c.create_text(left, self.px(86), text=t("stats_subtitle"), anchor="w", fill=COLOR["TEXT_DIM"],
                      font=self.font(15, False), tags=tags)

        gap = self.px(14)
        width = self.width - 2 * left
        tiles = data.get("tiles") or []
        tile_w, tile_h = (width - gap * 3) // 4, self.px(104)
        top = self.px(120)
        for i, (label, value, detail) in enumerate(tiles[:4]):
            x = left + i * (tile_w + gap)
            box = self.photo(("stat-tile", tile_w, tile_h), lambda: Glyphs.panel(
                tile_w, tile_h, self.px(14), COLOR["ROW"], outline=(COLOR["LINE_FAINT"], max(1, self.px(1.5)))))
            c.create_image(x, top, image=box, anchor="nw", tags=tags)
            inner = tile_w - self.px(36)
            c.create_text(x + self.px(18), top + self.px(22), text=label.upper(), anchor="w",
                          fill=COLOR["TEXT_FAINT"], font=self.font(11), tags=tags)
            c.create_text(x + self.px(18), top + self.px(54), anchor="w", fill=COLOR["ACCENT"],
                          text=self.truncate(value, self.font(24), inner), font=self.font(24), tags=tags)
            c.create_text(x + self.px(18), top + self.px(84), anchor="w", fill=COLOR["TEXT_DIM"],
                          text=self.truncate(detail, self.font(12, False), inner), font=self.font(12, False),
                          tags=tags)

        # Two panels: the last days as bars, the most played games
        panel_top = top + tile_h + gap
        panel_h = self.height - panel_top - self.px(FOOTER_H + 12)
        panel_w = (width - gap) // 2
        for n, caption in enumerate((t("stats_days"), t("stats_games"))):
            x = left + n * (panel_w + gap)
            box = self.photo(("stat-panel", panel_w, panel_h), lambda: Glyphs.panel(
                panel_w, panel_h, self.px(14), COLOR["ROW"], outline=(COLOR["LINE_FAINT"], max(1, self.px(1.5)))))
            c.create_image(x, panel_top, image=box, anchor="nw", tags=tags)
            c.create_text(x + self.px(18), panel_top + self.px(24), text=caption.upper(), anchor="w",
                          fill=COLOR["TEXT_FAINT"], font=self.font(11), tags=tags)

        days = data.get("days") or []
        if days:
            peak = max(seconds for _, seconds in days) or 1
            area_top, area_bottom = panel_top + self.px(64), panel_top + panel_h - self.px(40)
            slot_w = (panel_w - self.px(36)) / len(days)
            bar_w = int(min(self.px(46), slot_w * 0.6))
            for i, (label, seconds) in enumerate(days):
                cx = left + self.px(18) + int(slot_w * (i + 0.5))
                height = int((area_bottom - area_top) * seconds / peak)
                if seconds:
                    c.create_rectangle(cx - bar_w // 2, area_bottom - max(self.px(3), height), cx + bar_w // 2,
                                       area_bottom, fill=COLOR["ACCENT"], width=0, tags=tags)
                    c.create_text(cx, area_bottom - max(self.px(3), height) - self.px(12),
                                  text=playtime_label(seconds), fill=COLOR["TEXT_DIM"],
                                  font=self.font(10, False), tags=tags)
                else:
                    c.create_rectangle(cx - bar_w // 2, area_bottom - self.px(2), cx + bar_w // 2, area_bottom,
                                       fill=COLOR["LINE"], width=0, tags=tags)
                c.create_text(cx, area_bottom + self.px(18), text=label, fill=COLOR["TEXT_DIM"],
                              font=self.font(12), tags=tags)

        games = (data.get("games") or [])[:6]
        x = left + panel_w + gap + self.px(18)
        inner = panel_w - self.px(36)
        if not games:
            c.create_text(x, panel_top + self.px(70), text=t("stats_empty"), anchor="w", fill=COLOR["TEXT_DIM"],
                          font=self.font(14, False), tags=tags)
        else:
            peak = games[0][1] or 1
            row_h = min(self.px(56), (panel_h - self.px(60)) // len(games))
            for i, (title, seconds) in enumerate(games):
                y = panel_top + self.px(54) + i * row_h
                time_text = playtime_label(seconds)
                time_w = self.font(13).measure(time_text)
                c.create_text(x, y, anchor="w", fill=COLOR["TEXT"], font=self.font(14),
                              text=self.truncate(title, self.font(14), inner - time_w - self.px(16)), tags=tags)
                c.create_text(x + inner, y, anchor="e", fill=COLOR["TEXT_DIM"], font=self.font(13),
                              text=time_text, tags=tags)
                c.create_rectangle(x, y + self.px(14), x + inner, y + self.px(19), fill=COLOR["LINE_FAINT"],
                                   width=0, tags=tags)
                c.create_rectangle(x, y + self.px(14), x + max(self.px(4), int(inner * seconds / peak)),
                                   y + self.px(19), fill=player_color(i), width=0, tags=tags)
        self.rich(self.width - left, self.height - self.px(FOOTER_H / 2), t("stats_close"), 15,
                  COLOR["TEXT"], anchor="e", tags=tags)
        self._raise_overlays()

    # ========================================================================
    # "LET'S GO" SCREEN
    # ========================================================================
    def show_splash(self, game, players, layout, emulator=None, icons=None, starting=None):
        """
        The moment between choosing a game and the emulator appearing: the
        cover, the title, and who plays where on the split screen.

        Args:
            players (list[str]): the players' colours, in player order.
            layout  (list | None): split_layout() for them (None = shared screen).
            icons   (list[str]): the players' avatars, in player order.
            starting (str): the emulator's name, for "Starting ...".
        """
        self._splash = (game, tuple(players), layout, emulator, tuple(icons or ()), starting)
        self._draw_splash(entering=True)

    def hide_splash(self):
        self._splash = None
        for key in ("splash", "splash-in", "splash-shine", "splash-exit"):
            self.animator.finish(key)
        self.canvas.delete("splash")

    def _draw_splash(self, entering=False):
        from .GameGrid import SHINE_FRAMES, shine_frame
        from .I18n import players_label
        c = self.canvas
        c.delete("splash")
        for key in ("splash", "splash-in", "splash-shine"):
            self.animator.finish(key)
        if not self.width or self._splash is None:
            return
        game, players, layout, emulator, icons, starting = self._splash
        shade = self.photo(("splash-overlay", self.width, self.height),
                           lambda: Image.new("RGBA", (self.width, self.height), COLOR["SCREEN"]))
        c.create_image(0, 0, image=shade, anchor="nw", tags="splash")
        # The game's banner on the right, as in the game browser
        frames = self.grid._hero_frames(game["background"]) if game.get("background") else None
        if frames:
            c.create_image(self.width, 0, image=frames[-1], anchor="ne", tags="splash")

        left = self.px(96)
        tile = self.px(196) // 2 * 2
        map_w, map_h = self.px(338), self.px(190)
        block_h = tile + (self.px(64) + map_h if players else 0) + self.px(70)
        top = max(self.px(40), (self.height - block_h) // 2 - self.px(16))
        accent = self.grid.accent(game)
        self._splash_cover = (left, top, tile)
        cover = ("splash", "splash-cover")
        words = ("splash", "splash-words")
        lower = ("splash", "splash-lower")
        c.create_image(left + tile // 2, top + tile // 2, image=self.grid.halo(game, tile, tile), tags=cover)
        c.create_image(left, top, image=self.grid.tile(game, tile, tile), anchor="nw", tags=cover)
        tx = left + tile + self.px(38)
        width = int(self.width * 0.58) - tx
        c.create_text(tx, top + tile // 2 - self.px(62), text=t("splash_title").upper(), anchor="w", fill=accent,
                      font=self.font(14), tags=words)
        title_font = next((f for f in (self.font(44, "semibold"), self.font(36, "semibold"))
                           if f.measure(game["title"]) <= width), self.font(30, "semibold"))
        c.create_text(tx, top + tile // 2 - self.px(14), text=self.truncate(game["title"], title_font, width),
                      anchor="w", fill=COLOR["TEXT"], font=title_font, tags=words)
        details = [text for text in (players_label(game["players"]) if game.get("players") else "", emulator) if text]
        c.create_text(tx, top + tile // 2 + self.px(34), text="   ·   ".join(details), anchor="w",
                      fill=COLOR["TEXT_DIM"], font=self.font(16, False), tags=words)

        y = top + tile + self.px(34)
        if players:
            regions = tuple(region for region, _ in layout) if layout else None
            picture = self.photo(("tv", map_w, map_h, regions, players, COLOR["DISC_TEXT"], icons),
                                 lambda: Glyphs.split_map(map_w, map_h, regions, players, COLOR["DISC_TEXT"], icons))
            caption = t("split_shared") if not layout else t("splash_split")
            c.create_text(left + self.px(2), y + self.px(10), text=caption.upper(), anchor="w",
                          fill=COLOR["TEXT_FAINT"], font=self.font(11), tags=lower)
            c.create_image(left, y + self.px(30), image=picture, anchor="nw", tags=lower)
            y += self.px(30) + map_h + self.px(28)
        # "Starting Eden..." over a thin bar filling up until the game starts
        bar_w = map_w
        status = t("splash_starting", name=starting) if starting else ""
        status_item = c.create_text(left + self.px(2), y, text=status, anchor="w", fill=COLOR["TEXT_DIM"],
                                    font=self.font(13, False), tags=lower)
        bar_y = y + self.px(18)
        line = max(2, self.px(4))
        c.create_rectangle(left, bar_y, left + bar_w, bar_y + line, fill=COLOR["LINE"], width=0, tags=lower)
        bar = c.create_rectangle(left, bar_y, left, bar_y + line, fill=accent, width=0, tags=lower)
        self.rich(self.width // 2, self.height - self.px(DOCK_Y), t("splash_hints"), 14, COLOR["TEXT_DIM"],
                  anchor="center", tags="splash")
        self._raise_overlays()
        if not (self.animations and self.splash_ms):
            return
        started = time.monotonic()
        bar_ms = max(1, self.splash_ms - SPLASH_EXIT_MS)    # full when the cover starts growing

        def step(_progress):                    # linear: the bar is a clock, not a gesture
            elapsed = time.monotonic() - started
            done = min(1.0, elapsed * 1000 / bar_ms)
            c.coords(bar, left, bar_y, left + int(bar_w * done), bar_y + line)
            if status:
                c.itemconfigure(status_item, text=status + "." * (1 + int(elapsed * 4) % 3))
        self.animator.run("splash", self.splash_ms, step)

        if entering:
            # Cover from the left, words and the map rising after it
            moves = (("splash-cover", -self.px(40), 0, 0.0), ("splash-words", 0, self.px(22), 0.12),
                     ("splash-lower", 0, self.px(30), 0.24))
            for tag, dx, dy, _ in moves:
                c.move(tag, dx, dy)
            placed = {tag: 0.0 for tag, *_ in moves}

            def enter(progress):
                for tag, dx, dy, delay in moves:
                    p = max(0.0, min(1.0, (progress - delay) / (1.0 - delay)))
                    c.move(tag, -dx * (p - placed[tag]), -dy * (p - placed[tag]))
                    placed[tag] = p

            def settle():
                enter(1.0)
                self._splash_shine(left + tile // 2, top + tile // 2, tile, shine_frame, SHINE_FRAMES)
            self.animator.run("splash-in", 380, enter, settle)

    def splash_exit(self):
        """
        The game is about to open: the cover glides to the middle, a dark
        screen grows out of it over everything, and the cover sinks into it.
        """
        if self._splash is None or not self.animations or not self.width:
            return
        c = self.canvas
        for key in ("splash-in", "splash-shine"):
            self.animator.finish(key)
        game = self._splash[0]
        left, top, tile = self._splash_cover
        _, master = self.grid._master(game)
        tags = ("splash", "splash-exit")
        screen = COLOR["SCREEN"]
        dark = screen if isinstance(screen, str) else "#%02X%02X%02X" % tuple(screen[:3])
        veil = c.create_rectangle(left, top, left + tile, top + tile, fill=dark, width=0, tags=tags)
        cover = c.create_image(left + tile / 2, top + tile / 2, tags=tags)
        c.tag_raise("splash-exit")

        def frame(n):
            def make():
                from PIL import ImageEnhance
                scale = 1 + 0.55 * n / (EXIT_FRAMES - 1)
                light = 1 - max(0.0, (n / (EXIT_FRAMES - 1) - 0.35) / 0.65)
                size = int(tile * scale)
                image = master.resize((size, size), Image.BILINEAR)
                rgb = ImageEnhance.Brightness(image.convert("RGB")).enhance(light)
                rgb.putalpha(image.getchannel("A"))
                return rgb
            return self.photo(("splash-exit", game["path"], tile, n), make)
        start_x, start_y = left + tile / 2, top + tile / 2
        end_x, end_y = self.width / 2, self.height / 2
        started, shown = time.monotonic(), [None]

        def step(progress):
            p = 1.0 if progress >= 1.0 else min(1.0, (time.monotonic() - started) * 1000 / SPLASH_EXIT_MS)
            grow = smoothstep(p / 0.75)
            c.coords(veil, left * (1 - grow), top * (1 - grow), left + tile + (self.width - left - tile) * grow,
                     top + tile + (self.height - top - tile) * grow)
            glide = smoothstep(p / 0.8)
            c.coords(cover, start_x + (end_x - start_x) * glide, start_y + (end_y - start_y) * glide)
            n = min(EXIT_FRAMES - 1, int(p * EXIT_FRAMES))
            if n != shown[0]:
                shown[0] = n
                c.itemconfigure(cover, image=frame(n))
        step(0.0)
        self.animator.run("splash-exit", SPLASH_EXIT_MS, step)

    def _splash_shine(self, x, y, size, frame, count):
        """The light sweeps once over the cover, as on the selected tile in the grid."""
        c = self.canvas
        if self._splash is None:
            return
        item = c.create_image(x, y, image=frame(self, size, 0), tags=("splash", "splash-cover"))
        shown = [0]

        started = time.monotonic()

        def step(_progress):                # the sweep keeps an even pace (smoothstep), not the eased one
            linear = min(1.0, (time.monotonic() - started) * 1000 / SPLASH_SHINE_MS)
            progress = linear * linear * (3 - 2 * linear)
            n = min(count - 1, int(progress * count))
            if n != shown[0]:
                shown[0] = n
                c.itemconfigure(item, image=frame(self, size, n))
        self.animator.run("splash-shine", SPLASH_SHINE_MS, step, lambda: c.delete(item))

    # ========================================================================
    # TOAST
    # ========================================================================
    def show_toast(self, message, color=None):
        """Transient message above the footer (2.5 s)."""
        self._toast = (message, color or COLOR["NEON_RED"])
        self.animator.finish("toast")
        self._draw_toast(*self._toast, rise=True)
        if self._toast_job:
            self.root.after_cancel(self._toast_job)
        self._toast_job = self.root.after(2500, self._hide_toast)

    def _hide_toast(self):
        self._toast = None
        self._toast_job = None
        self.animator.finish("toast")
        self.canvas.delete("toast")

    def _draw_toast(self, message, color, rise=False):
        c = self.canvas
        c.delete("toast")
        if not self.width:
            return
        font = self.font(15)
        dot = self.px(10)
        w, h = font.measure(message) + dot + self.px(58), self.px(44)
        x, y = self.width // 2, self.height - self.px(DOCK_Y + 62)
        c.create_image(x, y, image=self.glass(w, h, h // 2, "PILL", "EDGE_STRONG"), tags="toast")
        left = x - w // 2 + self.px(22)
        mark = self.photo(("dot", dot, color), lambda: Glyphs.disc("", dot, color))
        c.create_image(left, y, image=mark, anchor="w", tags="toast")
        c.create_text(left + dot + self.px(12), y, text=message, anchor="w", fill=COLOR["TEXT"], font=font,
                      tags="toast")
        self._raise_overlays()
        if rise and self.animations:
            distance = self.px(16)
            c.move("toast", 0, distance)
            offset = [1.0]

            def step(progress):
                c.move("toast", 0, distance * (1.0 - progress - offset[0]))
                offset[0] = 1.0 - progress
            self.animator.run("toast", 150, step)

    # ========================================================================
    # ALERTS
    # ========================================================================
    ALERTS = {
        "LAUNCH": ("alert_no_pads_title", "alert_no_pads_text", "WARN",
                   (("a", "alert_continue"), ("b", "alert_back"))),
        "EXIT": ("alert_exit_title", "alert_exit_text", "TEXT",
                 (("a", "alert_yes"), ("b", "alert_no"))),
        "KILL_CONFIRM": ("alert_kill_title", "alert_kill_text", "TEXT",
                         (("a", "alert_kill_launcher"), ("y", "alert_kill_desktop"), ("b", "alert_kill_cancel"))),
        "GAME_FAILED": ("alert_failed_title", "alert_failed_text", "WARN", (("a", "alert_ok"),)),
        "CONFIRM": ("alert_confirm_title", "alert_confirm_text", "TEXT",
                    (("a", "alert_yes"), ("b", "alert_no"))),
    }

    def show_alert(self, mode, buttons=None, lines=(), name=None, title=None, text=None):
        """
        Modal dialog: "LAUNCH", "EXIT", "KILL_CONFIRM", "GAME_FAILED" or "CONFIRM".

        Args:
            buttons (tuple | None): ((glyph, label key), ...) instead of the mode's own.
            lines   (list[str]):    extra lines under the text (an emulator's errors).
            name    (str | None):   the emulator named in the text, when not the main one.
            title, text (str):      ready-made texts instead of the mode's own.
        """
        self.alert_mode = mode
        self.alert_buttons = buttons
        self.alert_lines = tuple(lines)
        self.alert_name = name
        self.alert_texts = (title, text)
        self.alert_since = time.monotonic()
        self._draw_alert(mode)

    def close_alert(self):
        self.alert_mode = None
        self.alert_detail = None
        self.alert_buttons = None
        self.alert_lines = ()
        self.alert_name = None
        self.alert_texts = (None, None)
        self.canvas.delete("alert")

    def set_alert_detail(self, rows):
        """
        Live "held buttons" lines under the close menu's choices.

        Args:
            rows (list[tuple]): (label, color, held) per player; held is a
                tuple of glyph names, empty when nothing is pressed, None
                when the controller is disconnected.
        """
        self.alert_detail = rows
        if self.alert_mode:
            self._draw_alert(self.alert_mode)

    def _draw_alert(self, mode):
        c = self.canvas
        c.delete("alert")
        if not self.width:
            return
        title, text, color, buttons = self.ALERTS[mode]
        buttons = self.alert_buttons or buttons
        own_title, own_text = getattr(self, "alert_texts", (None, None))
        name = self.alert_name or self.display_name
        shade = self.photo(("overlay", self.width, self.height),
                           lambda: Image.new("RGBA", (self.width, self.height), COLOR["OVERLAY"]))
        c.create_image(0, 0, image=shade, anchor="nw", tags="alert")
        detail = self.alert_detail if mode == "KILL_CONFIRM" else None
        per_row = 4
        rows = (len(detail) + per_row - 1) // per_row if detail else 0
        lines = self.alert_lines
        w = self.px(760 if rows or lines or len(buttons) > 3 else 600)
        line_h = self.px(24)
        extra = len(lines) * line_h + (self.px(16) if lines else 0)
        h = self.px(270) + extra + (self.px(48 + 36 * rows) if rows else 0)
        cx, cy = self.width // 2, self.height // 2
        top = cy - h // 2
        c.create_image(cx, cy, image=self.glass(w, h, self.px(26), "DIALOG", "EDGE_STRONG"), tags="alert")
        c.create_text(cx, top + self.px(63), text=own_title or t(title, name=name), fill=COLOR[color],
                      font=self.font(24, "semibold"), tags="alert")
        c.create_text(cx, top + self.px(115), text=own_text or t(text, name=name), fill=COLOR["TEXT_DIM"],
                      font=self.font(16, False), width=w - self.px(80), justify="center", tags="alert")
        small = self.font(13, False)
        for n, line in enumerate(lines):
            c.create_text(cx, top + self.px(168) + n * line_h, text=self.truncate(line, small, w - self.px(70)),
                          fill=COLOR["TEXT"], font=small, tags="alert")
        markup = "      ".join(f"[{b}] {t(label)}" for b, label in buttons)
        self.rich(cx, top + self.px(205) + extra, markup, 18 if len(buttons) <= 3 else 16, COLOR["TEXT"],
                  anchor="center", tags="alert")
        if rows:
            self._draw_held(detail, cx - w // 2, top + self.px(262) + extra, w, per_row)
        c.tag_raise("alert")
        c.tag_raise("fade")
        c.tag_raise("dim")

    def _draw_held(self, detail, left, y, width, per_row):
        """The held-buttons report: a caption, then "P1 [a] [lb]" cells, four per line."""
        c = self.canvas
        pad = self.px(40)
        c.create_line(left + pad, y - self.px(18), left + width - pad, y - self.px(18),
                      fill=COLOR["LINE"], width=max(1, self.px(1)), tags="alert")
        c.create_text(left + pad, y, text=t("held_title"), anchor="w", fill=COLOR["TEXT_FAINT"],
                      font=self.font(12, False), tags="alert")
        cell = (width - 2 * pad) // per_row
        label_font = self.font(14)
        for i, (label, color, held) in enumerate(detail):
            x = left + pad + (i % per_row) * cell
            row_y = y + self.px(34) + (i // per_row) * self.px(36)
            item = c.create_text(x, row_y, text=label, anchor="w", fill=color, font=label_font, tags="alert")
            x = c.bbox(item)[2] + self.px(6)
            if held is None:
                c.create_text(x, row_y, text=t("held_disconnected"), anchor="w", fill=COLOR["BAD"],
                              font=self.font(12, False), tags="alert")
            elif held:
                self.rich(x, row_y, "".join(f"[{name}]" for name in held), 13, COLOR["TEXT"],
                          tags="alert")
            else:
                c.create_text(x + self.px(4), row_y, text="—", anchor="w", fill=COLOR["TEXT_FAINT"],
                              font=label_font, tags="alert")
