"""
Core/GameGrid.py
The game picker: a grid of portrait tiles over the selected game's blurred
art, drawn on the UI canvas (tag "games").

Every tile carries a players badge - how many can play together on one
console - coloured against the number of controllers assigned:

    green   the game takes everyone who joined
    red     fewer players than joined
    grey    nothing to compare (unknown count, or a single player)

Favourites carry a star in the top-right corner. The header shows the
selected game's name, players, play time and when it was last played.

Speed: tile pictures are composed once per (game, size, state) and cached;
the rows just outside the view are prepared while idle, so scrolling only
re-places cached images. The background is debounced while scrolling.
"""

import hashlib
from collections import OrderedDict

from PIL import Image, ImageDraw, ImageFilter, ImageTk

from . import Glyphs
from .I18n import last_played_label, players_label, playtime_label, t

TILE_W, TILE_H = 150, 225       # 720p baseline
GAP = 24
TOP = 124                       # first row
BOTTOM = 76                     # space kept for the footer
RADIUS = 10
BORDER = 4
SELECTED_GROW = 1.08
TILE_CACHE = 160                # composed tiles kept (a few pages)
BACKGROUND_DELAY_MS = 140

BADGE = {                       # fill, text
    "ok": ((46, 160, 67, 240), (255, 255, 255)),
    "bad": ((210, 58, 48, 240), (255, 255, 255)),
    "neutral": ((12, 14, 20, 200), (240, 240, 240)),
}
ACCENT = "#F5D90A"
TEXT = "#F4F5F7"
TEXT_DIM = "#A9AFBB"
OK = "#3FB950"
BAD = "#E5534B"


def badge_state(game_players, joined):
    """Badge colour for a game supporting `game_players` when `joined` pads are assigned."""
    if not game_players or joined < 2:
        return "neutral"
    return "ok" if game_players >= joined else "bad"


def _placeholder_color(title):
    digest = hashlib.md5(title.encode("utf-8")).digest()
    return tuple(40 + b % 110 for b in digest[:3])


def _wrap(draw, text, font, width):
    lines, line = [], ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if draw.textlength(candidate, font=font) <= width or not line:
            line = candidate
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


class GameGrid:
    def __init__(self, ui):
        self.ui = ui
        self.games = []
        self.index = 0
        self.players = 0
        self.filter_players = 0
        self.loading = False
        self.first_row = 0
        self.columns = 1
        self.rows = 1
        self._tiles = OrderedDict()
        self._anim_job = None
        self._preload_job = None
        self._items = {}                # game index -> canvas item (normal tile)
        self._selected_items = None     # (halo, enlarged tile)
        self._layout = None             # what the items were built for
        self._shown_index = None

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    def set(self, games, index, players, filter_players):
        if games is not self.games:
            self.first_row = 0
        self.games, self.index = games, index
        self.players, self.filter_players = players, filter_players
        self.loading = False

    def set_loading(self):
        self.loading = True
        self.games = []

    def reset(self):
        """Screen size changed: every cached tile has the wrong size."""
        self._tiles.clear()
        self._layout = None

    def forget(self, game):
        for key in [k for k in self._tiles if k[0] == game["path"]]:
            del self._tiles[key]
        self._layout = None             # next draw rebuilds with the new picture

    def page_size(self):
        return self.columns * self.rows

    # ------------------------------------------------------------------
    # Tiles
    # ------------------------------------------------------------------
    def _compose(self, game, width, height, selected, badge):
        ui = self.ui
        cover = ui.load_art(game.get("cover"))
        icon = ui.load_art(game.get("image"))
        if cover is not None:
            tile = Glyphs.cover_crop(cover, width, height)
        elif icon is not None:
            # Square icon over a blurred, darkened stretch of itself
            small = Glyphs.cover_crop(icon, 24, max(1, round(24 * height / width))).filter(ImageFilter.GaussianBlur(2))
            tile = small.resize((width, height), Image.BILINEAR)
            tile = Image.blend(tile, Image.new("RGB", tile.size, (0, 0, 0)), 0.35)
            tile.paste(icon.resize((width, width), Image.LANCZOS), (0, (height - width) // 2))
        else:
            tile = Image.new("RGB", (width, height), _placeholder_color(game["title"]))
            draw = ImageDraw.Draw(tile)
            font = Glyphs.font(max(10, width // 8))
            lines = _wrap(draw, game["title"], font, width - width // 6)[:5]
            line_h = font.size * 1.25
            y = (height - line_h * len(lines)) / 2
            for line in lines:
                draw.text((width / 2, y), line, font=font, fill=TEXT, anchor="ma")
                y += line_h

        tile = tile.convert("RGBA")
        radius = max(2, ui.px(RADIUS))
        mask = Image.new("L", (width, height), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=255)
        tile.putalpha(mask)

        players = game.get("players")
        if players:
            self._draw_badge(tile, players, badge)
        if game.get("favorite"):
            self._draw_star(tile)
        if selected:
            ImageDraw.Draw(tile).rounded_rectangle(
                (0, 0, width - 1, height - 1), radius=radius, outline=ACCENT, width=max(2, ui.px(BORDER)))
        return tile

    def _draw_badge(self, tile, players, state):
        """Players pill in the bottom-left corner: [people icon] 1-4."""
        ui = self.ui
        h = max(12, ui.px(24))
        margin = ui.px(8)
        fill, text_color = BADGE[state]
        font = Glyphs.font(h * 0.6)
        label = str(players) if players <= 1 else f"1-{players}"
        icon = Glyphs.people(1 if players <= 1 else 2, int(h * 0.62), "#%02X%02X%02X" % text_color)
        text_w = int(ImageDraw.Draw(tile).textlength(label, font=font))
        w = h // 3 + icon.width + h // 5 + text_w + h // 3
        pill = Glyphs.panel(w, h, h // 2, fill)
        x, y = margin, tile.height - margin - h
        tile.alpha_composite(pill, (x, y))
        tile.alpha_composite(icon, (x + h // 3, y + (h - icon.height) // 2))
        ImageDraw.Draw(tile).text((x + h // 3 + icon.width + h // 5, y + h / 2), label, font=font,
                                  fill=text_color, anchor="lm")

    def _draw_star(self, tile):
        ui = self.ui
        d = max(14, ui.px(28))
        margin = ui.px(7)
        disc = Glyphs.panel(d, d, d // 2, (12, 14, 20, 210))
        icon = Glyphs.star(int(d * 0.66), ACCENT)
        x = tile.width - margin - d
        tile.alpha_composite(disc, (x, margin))
        tile.alpha_composite(icon, (x + (d - icon.width) // 2, margin + (d - icon.height) // 2))

    def tile(self, game, width, height, selected):
        """PhotoImage of a game's tile (cached), also used by the roulette."""
        return self._tile(game, width, height, selected)

    def _tile(self, game, width, height, selected):
        badge = badge_state(game.get("players"), self.players)
        key = (game["path"], width, height, selected, badge, bool(game.get("cover")), bool(game.get("image")),
               bool(game.get("favorite")))
        photo = self._tiles.get(key)
        if photo is None:
            photo = ImageTk.PhotoImage(self._compose(game, width, height, selected, badge))
            self._tiles[key] = photo
            if len(self._tiles) > TILE_CACHE:
                self._tiles.popitem(last=False)
        else:
            self._tiles.move_to_end(key)
        return photo

    def _preload(self, sizes):
        """Compose the rows just above and below the view while idle."""
        self._preload_job = None
        tile_w, tile_h = sizes
        start = max(0, (self.first_row - 1) * self.columns)
        end = min(len(self.games), (self.first_row + self.rows + 1) * self.columns)
        for i in range(start, end):
            self._tile(self.games[i], tile_w, tile_h, False)

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------
    def _header(self, selected, visible_count):
        ui, c = self.ui, self.ui.canvas
        left = ui.px(64)
        title_font = ui.font(30)
        tags = ("games", "games-header")
        title_x = left
        if selected.get("favorite"):
            star_h = ui.px(26)
            star = ui.photo(("star", star_h), lambda: Glyphs.star(star_h, ACCENT))
            c.create_image(left, ui.px(46), image=star, anchor="w", tags=tags)
            title_x += star_h + ui.px(12)
        c.create_text(title_x, ui.px(46), anchor="w", fill=TEXT, font=title_font, tags=tags,
                      text=ui.truncate(selected["title"], title_font, ui.width - title_x - ui.px(300)))
        x, y = left, ui.px(90)
        players = selected.get("players")
        if players:
            icon_h = ui.px(20)
            icon = ui.photo(("people", 1 if players <= 1 else 2, icon_h),
                            lambda: Glyphs.people(1 if players <= 1 else 2, icon_h, TEXT))
            c.create_image(x, y, image=icon, anchor="w", tags=tags)
            x += icon.width() + ui.px(8)
            item = c.create_text(x, y, anchor="w", fill=TEXT, font=ui.font(16),
                                 text=players_label(players), tags=tags)
            x = c.bbox(item)[2] + ui.px(16)
            if self.players >= 2:
                fits = players >= self.players
                text = ("✓  " + t("players_fit", n=self.players)) if fits else \
                       ("✕  " + t("players_too_many", max=players, n=self.players))
                x += self._chip(x, y, text, OK if fits else BAD) + ui.px(16)
        if self.filter_players:
            x += self._chip(x, y, t("games_filter", n=self.filter_players), ACCENT) + ui.px(16)
        item = c.create_text(x, y, anchor="w", fill=TEXT_DIM, font=ui.font(14, False),
                             text=f"{self.index + 1} / {visible_count}", tags=tags)
        seconds = selected.get("playtime") or 0
        if seconds >= 60:
            x = c.bbox(item)[2] + ui.px(24)
            icon_h = ui.px(17)
            icon = ui.photo(("clock", icon_h), lambda: Glyphs.clock(icon_h, TEXT_DIM))
            c.create_image(x, y, image=icon, anchor="w", tags=tags)
            text = playtime_label(seconds)
            if selected.get("last_played"):
                text += "  ·  " + last_played_label(selected["last_played"])
            c.create_text(x + icon.width() + ui.px(8), y, anchor="w", fill=TEXT_DIM, font=ui.font(14, False),
                          text=text, tags=tags)

    def _chip(self, x, y, text, color):
        """Outlined pill with coloured text; returns its width."""
        ui, c = self.ui, self.ui.canvas
        font = ui.font(13)
        w, h = font.measure(text) + ui.px(24), ui.px(26)
        pill = ui.photo(("chip", w, h, color), lambda: Glyphs.panel(
            w, h, h // 2, (0, 0, 0, 140), outline=(color, max(1, ui.px(1.5)))))
        c.create_image(x, y, image=pill, anchor="w", tags=("games", "games-header"))
        c.create_text(x + w // 2, y, text=text, fill=color, font=font, tags=("games", "games-header"))
        return w

    def draw(self, full=False):
        """
        Draw the grid. When only the selection moved within the same page,
        just the two affected tiles and the header change (fast path), so
        Tk repaints a small area instead of the whole grid.
        """
        ui, c = self.ui, self.ui.canvas
        if not ui.width:
            return
        if self.loading or not self.games:
            self._clear()
            self._draw_placeholder()
            return

        tile_w, tile_h, gap = ui.px(TILE_W), ui.px(TILE_H), ui.px(GAP)
        top = ui.px(TOP)
        self.columns = max(1, (ui.width - 2 * ui.px(64) + gap) // (tile_w + gap))
        self.rows = max(1, (ui.height - top - ui.px(BOTTOM) + gap) // (tile_h + gap))
        count = len(self.games)
        self.index = max(0, min(self.index, count - 1))

        # Scroll only when the selection leaves the visible rows
        previous_first = self.first_row
        row = self.index // self.columns
        if row < self.first_row:
            self.first_row = row
        elif row >= self.first_row + self.rows:
            self.first_row = row - self.rows + 1
        last_row = (count - 1) // self.columns
        self.first_row = max(0, min(self.first_row, max(0, last_row - self.rows + 1)))

        selected = self.games[self.index]
        ui.set_backdrop(selected, BACKGROUND_DELAY_MS)

        layout = (id(self.games), count, self.first_row, self.columns, self.rows,
                  ui.width, ui.height, self.players, self.filter_players)
        grid_w = self.columns * tile_w + (self.columns - 1) * gap
        left = (ui.width - grid_w) // 2
        first = self.first_row * self.columns
        position = lambda i: (left + ((i - first) % self.columns) * (tile_w + gap),  # noqa: E731
                              top + ((i - first) // self.columns) * (tile_h + gap))
        grow_w, grow_h = int(tile_w * SELECTED_GROW), int(tile_h * SELECTED_GROW)

        if not full and layout == self._layout and self._items and self._anim_job is None:
            # Fast path: same page, only the highlight moved
            old = self._shown_index
            if old in self._items:
                c.itemconfigure(self._items[old], state="normal")
            c.itemconfigure(self._items[self.index], state="hidden")
            x, y = position(self.index)
            halo, tile = self._selected_items
            c.coords(halo, x + tile_w // 2, y + tile_h // 2)
            c.coords(tile, x + tile_w // 2, y + tile_h // 2)
            c.itemconfigure(tile, image=self._tile(selected, grow_w, grow_h, True))
            c.delete("games-header")
            self._header(selected, count)
            self._shown_index = self.index
            return

        # Full rebuild
        self._clear()
        self._header(selected, count)
        self._items = {}
        for item in range(first, min(count, first + self.columns * self.rows)):
            x, y = position(item)
            self._items[item] = c.create_image(
                x, y, image=self._tile(self.games[item], tile_w, tile_h, False), anchor="nw",
                state="hidden" if item == self.index else "normal", tags=("games", "tiles"))

        x, y = position(self.index)
        glow = ui.px(18)
        halo = ui.photo(("halo", grow_w, grow_h), lambda: Glyphs.panel(
            grow_w, grow_h, ui.px(RADIUS), (0, 0, 0, 0), glow=(ACCENT, glow)))
        self._selected_items = (
            c.create_image(x + tile_w // 2, y + tile_h // 2, image=halo, tags=("games", "tiles")),
            c.create_image(x + tile_w // 2, y + tile_h // 2, image=self._tile(selected, grow_w, grow_h, True),
                           tags=("games", "tiles")),
        )

        # More rows hint
        arrow_font = ui.font(14)
        if self.first_row > 0:
            c.create_text(ui.width // 2, top - ui.px(16), text="▲", fill=TEXT_DIM, font=arrow_font, tags="games")
        if self.first_row + self.rows <= last_row:
            c.create_text(ui.width // 2, ui.height - ui.px(BOTTOM) + ui.px(4), text="▼", fill=TEXT_DIM,
                          font=arrow_font, tags="games")

        self._layout = layout
        self._shown_index = self.index
        if self.first_row != previous_first and previous_first is not None:
            self._slide((self.first_row - previous_first) * (tile_h + gap))
        if self._preload_job is None:
            self._preload_job = ui.root.after_idle(self._preload, (tile_w, tile_h))

    def _clear(self):
        if self._anim_job:
            self.ui.root.after_cancel(self._anim_job)
            self._anim_job = None
        self.ui.canvas.delete("games")
        self._items = {}
        self._layout = None

    def _draw_placeholder(self):
        ui, c = self.ui, self.ui.canvas
        if self.loading:
            c.create_text(ui.px(64), ui.px(46), anchor="w", fill=TEXT, font=ui.font(30),
                          text=t("games_loading"), tags="games")
        else:
            c.create_text(ui.width // 2, ui.height // 2 - ui.px(20), fill=TEXT, font=ui.font(24),
                          text=t("games_none_for", n=self.filter_players), tags="games")
            ui.rich(ui.width // 2, ui.height // 2 + ui.px(24), f"[y] {t('hint_filter_off')}", 16,
                    TEXT_DIM, anchor="center", tags="games")

    def _slide(self, distance):
        """Ease the tiles into place after a scroll (about 100 ms)."""
        c = self.ui.canvas
        distance = max(-self.ui.px(TILE_H), min(self.ui.px(TILE_H), distance))
        steps = [0.45, 0.25, 0.14, 0.08, 0.05, 0.03]
        c.move("tiles", 0, distance)

        def step(i=0):
            if i >= len(steps):
                self._anim_job = None
                return
            c.move("tiles", 0, -int(round(distance * steps[i])) if i < len(steps) - 1
                   else -(distance - sum(int(round(distance * s)) for s in steps[:-1])))
            self._anim_job = self.ui.root.after(16, step, i + 1)
        step()
