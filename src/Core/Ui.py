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
    toast     transient message
    alert     modal dialog

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
MARGIN = 64
FOOTER_H = 64
ART_MAX = 720           # art is kept in memory at most this big
ART_CACHE = 48          # pictures kept decoded
BG_CACHE = 8            # blurred backgrounds kept (screen-sized each)
BG_SAMPLE_W = 96        # backdrop detail: the art is shrunk to this width...
BG_BLUR = 2.2           # ...blurred, and stretched back to the screen
DRIFT = 16              # px (720p) the backdrop may wander from its place
DRIFT_PERIODS = (97.0, 71.0)    # seconds for one horizontal / vertical swing
DRIFT_TICK_MS = 250     # at most 4 moves a second (~2 at this speed)
DRIFT_QUIET_S = 0.6     # no motion right after the screen changed

COLOR = {
    "BG": "#0B0D12",
    "TEXT": "#F4F5F7",
    "TEXT_DIM": "#A9AFBB",
    "TEXT_FAINT": "#6B7280",
    "ACCENT": "#F5D90A",
    "OK": "#3FB950",
    "BAD": "#E5534B",
    "NEON_RED": "#FF6B61",
    "WARN": "#F5BE28",
}

# Controller colours (Material 300 palette: bright, distinct, easy on the eyes)
COLOR_POOL = [
    "#4FC3F7", "#FF8A65", "#AED581", "#BA68C8", "#FFD54F", "#4DB6AC", "#F06292",
    "#7986CB", "#E57373", "#81C784", "#64B5F6", "#FFB74D", "#9575CD", "#4DD0E1",
    "#DCE775", "#F48FB1", "#FFF176", "#80CBC4", "#A1887F", "#90A4AE",
]

_TOKEN = re.compile(r"\[(\w+)\]")


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


def compose_background(art, width, height, shade):
    """Screen-sized backdrop: the art blurred and shaded (default glow without art)."""
    if art is not None:
        sample = BG_SAMPLE_W
        small = Glyphs.cover_crop(art, sample, max(1, round(sample * height / width)))
        image = small.filter(ImageFilter.GaussianBlur(BG_BLUR)).resize((width, height), Image.BILINEAR)
    else:
        # Default backdrop: deep blue with a soft teal glow
        image = Image.new("RGB", (64, 36), (13, 17, 30))
        ImageDraw.Draw(image).ellipse((-10, -18, 44, 30), fill=(24, 70, 92))
        image = image.filter(ImageFilter.GaussianBlur(9)).resize((width, height), Image.BILINEAR)
    return ImageChops.multiply(image, shade)


class LauncherUi:
    """
    Owns the canvas. Core/App.py drives it through:

        refresh(slots) / flash_slot(i)        player cards
        set_hints([...]) / set_tip(markup)    footer hints, tip line
        set_pads(n)                           connected controllers
        set_backdrop(game, delay_ms)          blurred, slowly drifting backdrop
        show_games(...) / show_games_loading() / hide_games()
        show_toast(msg, color)
        show_alert(mode) / close_alert() / alert_mode
    """

    def __init__(self, root, emulator_name):
        self.root = root
        self.emulator_name = emulator_name

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
        self.alert_since = 0.0
        self.view = "players"
        self.slots = []
        self.flashing = set()
        self.hints = []
        self.tip = ""
        self.pads = 0

        self._fonts = {}
        self._photos = {}
        self._art = OrderedDict()
        self._backgrounds = OrderedDict()
        self._bg_wanted = None
        self._bg_owner = None
        self._bg_job = None
        self.motion = True                  # background drift, see BACKGROUND MOTION
        self._drift = (0, 0)
        self._motion_quiet_until = 0.0
        self.busy_overlay = False           # something animated on top (roulette): hold the backdrop still
        self._toast_job = None
        self._toast = None

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
        self.grid.reset()
        self.redraw()

    def px(self, value):
        return max(1, int(round(value * self.scale)))

    def font(self, size, bold=True):
        key = (self.px(size), bold)
        if key not in self._fonts:
            self._fonts[key] = tkfont.Font(family=FONT_FAMILY, size=-key[0],
                                           weight="bold" if bold else "normal")
        return self._fonts[key]

    def photo(self, key, make):
        """PhotoImage cached under `key`; make() returns a PIL image."""
        image = self._photos.get(key)
        if image is None:
            image = self._photos[key] = ImageTk.PhotoImage(make())
        return image

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

    def rich(self, x, y, markup, size, fill, bold=True, anchor="w", tags=()):
        """
        Draw text with inline button pictures ("Press [a] to join").

        Args:
            anchor: "w", "center" or "e" - horizontal alignment; y is the
                    vertical centre.
        Returns:
            int: total width in pixels.
        """
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
        if self.alert_mode:
            self._draw_alert(self.alert_mode)

    def _draw_content(self):
        if self.view == "players":
            self.canvas.delete("games")
            self._draw_players()
        else:
            self.canvas.delete("players")
            self.grid.draw(full=True)

    def _raise_overlays(self):
        for tag in ("header", "footer", "roulette", "toast", "alert"):
            self.canvas.tag_raise(tag)

    # ========================================================================
    # BACKGROUND
    # ========================================================================
    def set_backdrop(self, game, delay_ms=0):
        """
        Blurred art of `game` behind everything (None: the default backdrop).
        delay_ms debounces fast scrolling.
        """
        self.hold_motion()
        self._bg_owner = game
        path = art_of(game)
        if path == self._bg_wanted and self.canvas.find_withtag("bg"):
            return
        self._bg_wanted = path
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
            vignette = mask.point(lambda v: int(55 + v * 0.42))            # 55..162
            bottom = Image.linear_gradient("L").resize((w, h)).point(lambda v: 255 - int(v * 0.45))
            shade = ImageChops.multiply(vignette, bottom).resize((width, height), Image.BILINEAR)
            self._photos[key] = Image.merge("RGB", (shade, shade, shade))
        return self._photos[key]

    def _margin(self):
        """How far the backdrop may drift: it is drawn this much larger on every side."""
        return self.px(DRIFT) if self.motion else 0

    def _background_photo(self, path):
        m = self._margin()
        size = (self.width + 2 * m, self.height + 2 * m)
        key = (path, *size)
        if key in self._backgrounds:
            self._backgrounds.move_to_end(key)
            return self._backgrounds[key]
        photo = ImageTk.PhotoImage(compose_background(self.load_art(path), *size, self._shade(*size)))
        self._backgrounds[key] = photo
        if len(self._backgrounds) > BG_CACHE:
            self._backgrounds.popitem(last=False)
        return photo

    def _apply_background(self):
        self._bg_job = None
        if not self.width:
            return
        photo = self._background_photo(self._bg_wanted)
        m = self._margin()
        x, y = -m + self._drift[0], -m + self._drift[1]
        item = self.canvas.find_withtag("bg")
        if item:
            self.canvas.itemconfigure(item[0], image=photo)
            self.canvas.coords(item[0], x, y)
        else:
            self.canvas.create_image(x, y, image=photo, anchor="nw", tags="bg")
            self.canvas.tag_lower("bg")

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
        if not self.motion or not self.width or self.alert_mode or self.busy_overlay:
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
        self.root.after(15000, self._tick_clock)

    def set_pads(self, count):
        if count != self.pads:
            self.pads = count
            self._draw_header()

    def _draw_header(self):
        c = self.canvas
        c.delete("header")
        if not self.width:
            return
        right = self.width - self.px(MARGIN)
        y = self.px(46)
        c.create_text(right, y, text=time.strftime("%H:%M"), anchor="e", fill=COLOR["TEXT"],
                      font=self.font(22), tags=("header", "clock"))
        x = right - self.font(22).measure("00:00") - self.px(34)
        count = c.create_text(x, y, text=str(self.pads), anchor="e", fill=COLOR["TEXT_DIM"],
                              font=self.font(18), tags="header")
        pad_h = self.px(20)
        icon = self.photo(("pad", pad_h), lambda: Glyphs.gamepad(pad_h, COLOR["TEXT_DIM"]))
        c.create_image(c.bbox(count)[0] - self.px(8), y, image=icon, anchor="e", tags="header")

        if self.view == "players":
            left = self.px(MARGIN)
            c.create_text(left, y, text=t("title_players"), anchor="w", fill=COLOR["TEXT"],
                          font=self.font(32), tags="header")
            self.rich(left, self.px(88), t("subtitle_players"), 15, COLOR["TEXT_DIM"],
                      bold=False, tags="header")
        self._raise_overlays()

    # ========================================================================
    # FOOTER
    # ========================================================================
    def set_hints(self, hints):
        """hints: [(button or (buttons...), label)], drawn right to left from the edge."""
        if hints != self.hints:
            self.hints = list(hints)
            self._draw_footer()

    def _draw_footer(self):
        c = self.canvas
        c.delete("footer")
        if not self.width:
            return
        band_h = self.px(FOOTER_H + 40)
        band = self.photo(("fade", self.width, band_h),
                          lambda: Glyphs.vertical_fade(self.width, band_h, 0, 215))
        c.create_image(0, self.height, image=band, anchor="sw", tags="footer")
        y = self.height - self.px(FOOTER_H / 2)
        x = self.width - self.px(MARGIN)
        for buttons, label in reversed(self.hints):
            if isinstance(buttons, str):
                buttons = (buttons,)
            markup = "".join(f"[{b}]" for b in buttons) + " " + label
            x -= self.rich(x, y, markup, 15, COLOR["TEXT"], anchor="e", tags="footer") + self.px(30)
        brand = f"{self.emulator_name.upper()} LAUNCHER"
        if self.px(MARGIN) + self.font(12).measure(brand) < x:     # only where the hints leave room
            c.create_text(self.px(MARGIN), y, text=brand, anchor="w",
                          fill=COLOR["TEXT_FAINT"], font=self.font(12), tags="footer")
        self._raise_overlays()

    # ========================================================================
    # PLAYER CARDS
    # ========================================================================
    def set_tip(self, markup):
        self.tip = markup
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

    def flash_slot(self, index, duration_ms=700):
        """Glow a card briefly (a controller just joined)."""
        self.flashing.add(index)
        self.refresh(self.slots)

        def stop():
            self.flashing.discard(index)
            self.refresh(self.slots)
        self.root.after(duration_ms, stop)

    def _draw_players(self):
        c = self.canvas
        c.delete("players")
        if not self.width:
            return
        cw, ch, gap = self.px(262), self.px(150), self.px(20)
        left = (self.width - (4 * cw + 3 * gap)) // 2
        top = self.px(132)
        radius = self.px(16)
        for i in range(8):
            x = left + (i % 4) * (cw + gap)
            y = top + (i // 4) * (ch + gap)
            slot = self.slots[i] if i < len(self.slots) else None
            if slot is None:
                image = self.photo(("card-empty", cw, ch), lambda: Glyphs.panel(
                    cw, ch, radius, (255, 255, 255, 16), outline=("#3A3F4B", max(1, self.px(1.5)))))
                c.create_image(x, y, image=image, anchor="nw", tags="players")
                c.create_text(x + cw // 2, y + self.px(56), text=f"P{i + 1}", fill=COLOR["TEXT_FAINT"],
                              font=self.font(30), tags="players")
                self.rich(x + cw // 2, y + self.px(104), t("slot_empty"), 14, COLOR["TEXT_FAINT"],
                          anchor="center", tags="players")
                continue

            color = slot["color"]
            glow = i in self.flashing
            pad = self.px(16) if glow else 0
            image = self.photo(("card", cw, ch, color, glow), lambda: Glyphs.panel(
                cw, ch, radius, (10, 12, 18, 196), outline=(color, self.px(3)),
                glow=(color, pad) if glow else None))
            c.create_image(x - pad, y - pad, image=image, anchor="nw", tags="players")

            if slot["editing"]:
                c.create_text(x + cw // 2, y + self.px(34), text=f"P{i + 1}", fill=color,
                              font=self.font(15), tags="players")
                c.create_text(x + cw // 2, y + self.px(72), text=f"◀   {slot['profile']}   ▶",
                              fill=color, font=self.font(22), tags="players")
                self.rich(x + cw // 2, y + ch - self.px(26), t("slot_editing_hints"), 11,
                          COLOR["TEXT_DIM"], anchor="center", tags="players")
                continue

            size = self.px(52)
            disc = self.photo(("disc", i, size, color), lambda: Glyphs.disc(f"P{i + 1}", size, color))
            c.create_image(x + self.px(18), y + self.px(18), image=disc, anchor="nw", tags="players")
            text_x = x + self.px(84)
            name_font = self.font(15)
            c.create_text(text_x, y + self.px(34), anchor="w", fill=COLOR["TEXT"], font=name_font,
                          text=self.truncate(slot["name"], name_font, cw - self.px(98)), tags="players")
            c.create_text(text_x, y + self.px(58), anchor="w", fill=color, font=self.font(13, False),
                          text=t("slot_profile", name=slot["profile"]), tags="players")
            self.rich(x + self.px(18), y + ch - self.px(26), t("slot_hints"), 11, COLOR["TEXT_DIM"],
                      tags="players")

        if self.tip:
            self.rich(self.width // 2, top + 2 * ch + gap + self.px(58), self.tip, 14,
                      COLOR["TEXT_DIM"], bold=False, anchor="center", tags="players")
        self._raise_overlays()

    # ========================================================================
    # GAME GRID
    # ========================================================================
    def show_games(self, games, index, players=0, filter_players=0):
        """Show (or update) the game grid; see Core/GameGrid.py."""
        entering = self.view != "games"
        self.view = "games"
        self.grid.set(games, index, players, filter_players)
        if entering:
            self.redraw()
        else:
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
        self.redraw()

    def games_columns(self):
        return self.grid.columns

    def games_page(self):
        return self.grid.page_size()

    def games_art_changed(self, game):
        """A picture arrived for `game`: redraw what shows it."""
        for key in ("cover", "image", "background"):
            self.forget_art(game.get(key))
        self.grid.forget(game)
        if self.view == "games":
            self.grid.draw()
            self._raise_overlays()
        if game is self._bg_owner:
            self.set_backdrop(game)             # its backdrop picture may have changed

    # ========================================================================
    # TOAST
    # ========================================================================
    def show_toast(self, message, color=None):
        """Transient message above the footer (2.5 s)."""
        self._toast = (message, color or COLOR["NEON_RED"])
        self._draw_toast(*self._toast)
        if self._toast_job:
            self.root.after_cancel(self._toast_job)
        self._toast_job = self.root.after(2500, self._hide_toast)

    def _hide_toast(self):
        self._toast = None
        self._toast_job = None
        self.canvas.delete("toast")

    def _draw_toast(self, message, color):
        c = self.canvas
        c.delete("toast")
        if not self.width:
            return
        font = self.font(16)
        w, h = font.measure(message) + self.px(56), self.px(46)
        x, y = self.width // 2, self.height - self.px(FOOTER_H + 44)
        pill = self.photo(("toast", w, h, color), lambda: Glyphs.panel(
            w, h, h // 2, (14, 16, 22, 235), outline=(color, self.px(2))))
        c.create_image(x, y, image=pill, tags="toast")
        c.create_text(x, y, text=message, fill=color, font=font, tags="toast")
        self._raise_overlays()

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
    }

    def show_alert(self, mode):
        """Modal dialog: "LAUNCH", "EXIT", or "KILL_CONFIRM"."""
        self.alert_mode = mode
        self.alert_since = time.monotonic()
        self._draw_alert(mode)

    def close_alert(self):
        self.alert_mode = None
        self.canvas.delete("alert")

    def _draw_alert(self, mode):
        c = self.canvas
        c.delete("alert")
        if not self.width:
            return
        title, text, color, buttons = self.ALERTS[mode]
        shade = self.photo(("overlay", self.width, self.height),
                           lambda: Image.new("RGBA", (self.width, self.height), (0, 0, 0, 175)))
        c.create_image(0, 0, image=shade, anchor="nw", tags="alert")
        w, h = self.px(600), self.px(270)
        cx, cy = self.width // 2, self.height // 2
        box = self.photo(("dialog", w, h), lambda: Glyphs.panel(
            w, h, self.px(20), (22, 24, 31, 248), outline=("#3A3F4B", max(1, self.px(1.5)))))
        c.create_image(cx, cy, image=box, tags="alert")
        c.create_text(cx, cy - self.px(72), text=t(title), fill=COLOR[color], font=self.font(26),
                      tags="alert")
        c.create_text(cx, cy - self.px(20), text=t(text, name=self.emulator_name), fill=COLOR["TEXT_DIM"],
                      font=self.font(16, False), width=w - self.px(80), justify="center", tags="alert")
        markup = "      ".join(f"[{b}] {t(label)}" for b, label in buttons)
        self.rich(cx, cy + self.px(70), markup, 18, COLOR["TEXT"], anchor="center", tags="alert")
        c.tag_raise("alert")
