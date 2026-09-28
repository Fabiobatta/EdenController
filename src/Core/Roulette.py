"""
Core/Roulette.py
"Tonight we play...": a strip of covers that spins and slows down until it
stops on a random game, drawn on the UI canvas (tag "roulette") over a
dimmed screen.

Light by design: at most VISIBLE + 1 canvas images, moved ~30 times a
second; the tiles come from the game grid's cache and the few games on the
strip are composed before the spin starts, so no picture is made mid-spin.

    roulette = Roulette(ui, on_tick=..., on_done=...)
    roulette.start(candidates, chosen, subtitle)
    roulette.close()
"""

import random
import time

from PIL import Image

from . import Glyphs
from .I18n import players_label, t

STRIP_GAMES = 12            # different games shown on the strip
SPIN_TILES = 38             # tiles that pass before the chosen one stops
SPIN_SECONDS = 4.2
FRAME_MS = 33
VISIBLE = 7
TILE_W, TILE_H = 170, 255   # 720p baseline
GAP = 26
ACCENT = "#F5D90A"
TEXT = "#F4F5F7"
TEXT_DIM = "#A9AFBB"


def pick_candidates(games, joined):
    """
    Games the roulette may choose: every game when one player (or none)
    joined, else only games whose player count takes everyone - a game for
    1-2 players is never drawn for four.
    """
    if joined <= 1:
        return list(games)
    return [g for g in games if (g.get("players") or 0) >= joined]


def ease_out(x):
    """Fast start, long slow finish (quartic)."""
    return 1 - (1 - x) ** 4


class Roulette:
    def __init__(self, ui, on_tick=None, on_done=None):
        self.ui = ui
        self.on_tick = on_tick          # a tile passed the marker
        self.on_done = on_done          # the strip stopped
        self.active = False
        self.spinning = False
        self.chosen = None
        self._strip = []
        self._items = []
        self._job = None
        self._start = 0.0
        self._last_tile = 0

    # ------------------------------------------------------------------
    def start(self, candidates, chosen, subtitle):
        self.close()
        ui = self.ui
        if not ui.width:
            return
        self.active = self.spinning = True
        ui.busy_overlay = True
        self.chosen = chosen
        self.subtitle = subtitle

        others = [g for g in candidates if g is not chosen]
        pool = random.sample(others, min(len(others), STRIP_GAMES - 1)) + [chosen]
        # A random sequence that never shows the same game twice in a row,
        # ending on the chosen one with a few tiles after it
        strip, previous = [], None
        for _ in range(SPIN_TILES + VISIBLE):
            options = [g for g in pool if g is not previous] or pool
            previous = random.choice(options)
            strip.append(previous)
        strip[SPIN_TILES] = chosen
        for i in (SPIN_TILES - 1, SPIN_TILES + 1):
            if strip[i] is chosen and len(pool) > 1:
                strip[i] = next(g for g in pool if g is not chosen)
        self._strip = strip

        self.tile_w, self.tile_h = ui.px(TILE_W), ui.px(TILE_H)
        self.step = self.tile_w + ui.px(GAP)
        for game in pool:                       # compose before the spin
            ui.grid.tile(game, self.tile_w, self.tile_h, False)

        self._draw_frame_static()
        self._items = [ui.canvas.create_image(-1000, 0, anchor="center", tags=("roulette", "roulette-tiles"))
                       for _ in range(VISIBLE + 2)]
        ui.canvas.tag_raise("roulette-marker")
        self._start = time.monotonic()
        self._last_tile = 0
        self._frame()

    def _draw_frame_static(self):
        ui, c = self.ui, self.ui.canvas
        shade = ui.photo(("roulette-overlay", ui.width, ui.height),
                         lambda: Image.new("RGBA", (ui.width, ui.height), (6, 7, 10, 222)))
        c.create_image(0, 0, image=shade, anchor="nw", tags="roulette")
        cx, cy = ui.width // 2, ui.height // 2
        c.create_text(cx, cy - self.tile_h // 2 - ui.px(78), text=t("roulette_title"), fill=TEXT,
                      font=ui.font(34), tags="roulette")
        c.create_text(cx, cy - self.tile_h // 2 - ui.px(38), text=self.subtitle, fill=TEXT_DIM,
                      font=ui.font(15, False), tags=("roulette", "roulette-subtitle"))
        # Marker: glowing frame around the centre slot, triangles above and below
        w, h = int(self.tile_w * 1.08), int(self.tile_h * 1.08)
        glow = ui.px(20)
        frame = ui.photo(("roulette-frame", w, h), lambda: Glyphs.panel(
            w, h, ui.px(12), (0, 0, 0, 0), outline=(ACCENT, ui.px(4)), glow=(ACCENT, glow)))
        c.create_image(cx, cy, image=frame, tags=("roulette", "roulette-marker"))
        a = ui.px(14)
        top, bottom = cy - h // 2 - ui.px(10), cy + h // 2 + ui.px(10)
        c.create_polygon(cx - a, top - a, cx + a, top - a, cx, top, fill=ACCENT, tags=("roulette", "roulette-marker"))
        c.create_polygon(cx - a, bottom + a, cx + a, bottom + a, cx, bottom, fill=ACCENT,
                         tags=("roulette", "roulette-marker"))

    def _frame(self):
        self._job = None
        ui, c = self.ui, self.ui.canvas
        progress = min(1.0, (time.monotonic() - self._start) / SPIN_SECONDS)
        position = ease_out(progress) * SPIN_TILES            # tile under the marker (float)
        cx, cy = ui.width // 2, ui.height // 2
        first = int(position) - VISIBLE // 2 - 1
        for slot, item in enumerate(self._items):
            index = first + slot
            if 0 <= index < len(self._strip):
                x = cx + (index - position) * self.step
                c.coords(item, x, cy)
                c.itemconfigure(item, image=ui.grid.tile(self._strip[index], self.tile_w, self.tile_h, False),
                                state="normal")
            else:
                c.itemconfigure(item, state="hidden")

        passed = int(position + 0.5)
        if passed != self._last_tile:
            self._last_tile = passed
            if self.on_tick:
                self.on_tick()
        if progress < 1.0:
            self._job = ui.root.after(FRAME_MS, self._frame)
        else:
            self._finish()

    def _finish(self):
        ui, c = self.ui, self.ui.canvas
        self.spinning = False
        game = self.chosen
        cx, cy = ui.width // 2, ui.height // 2
        w, h = int(self.tile_w * 1.08), int(self.tile_h * 1.08)
        big = ui.grid.tile(game, w, h, True)
        c.create_image(cx, cy, image=big, tags="roulette")
        c.delete("roulette-subtitle")
        title_font = ui.font(26)
        y = cy + h // 2 + ui.px(46)
        c.create_text(cx, y, text=ui.truncate(game["title"], title_font, ui.width - ui.px(160)), fill=TEXT,
                      font=title_font, tags="roulette")
        if game.get("players"):
            c.create_text(cx, y + ui.px(34), text=players_label(game["players"]), fill=TEXT_DIM,
                          font=ui.font(15, False), tags="roulette")
        ui.rich(cx, y + ui.px(78), t("roulette_hints"), 17, TEXT, anchor="center", tags="roulette")
        if self.on_done:
            self.on_done(game)

    def close(self):
        if self._job:
            self.ui.root.after_cancel(self._job)
            self._job = None
        self.ui.canvas.delete("roulette")
        self._items = []
        self.active = self.spinning = False
        self.ui.busy_overlay = False
