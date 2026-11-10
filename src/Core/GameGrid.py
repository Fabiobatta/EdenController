"""
Core/GameGrid.py
The game picker: a grid of square game tiles - rows and columns - under
the selected game's title and facts, over its blurred art (canvas tag
"games").

Every tile carries a big players badge - how many can play together on
one console - coloured against the number of controllers assigned:

    green   the game takes everyone who joined
    red     fewer players than joined
    grey    nothing to compare (unknown count, or a single player)

The focused tile is a little larger, framed in white with a glow in the
colour of the game's art that breathes slowly. Favourites carry a star.

Motion, and why it stays cheap (Tk repaints whatever area changes, so the
area matters more than the number of items):
    - moving within the visible rows changes only two tiles and the frame:
      the frame glides to the new tile while the old tile shrinks back and
      the new one grows, in three cached sizes
    - a new row scrolling into view slides the grid by one row
    - the backdrop follows the selection only once it has rested, in one
      blended step (Core/Ui.py)
    - tiles about to scroll into view are composed ahead while idle
Tiles are composed once per game (a master at the focused size) and cached.
"""

from collections import OrderedDict

from PIL import Image, ImageDraw, ImageFilter, ImageTk

from . import Glyphs
from .I18n import last_played_label, players_label, playtime_label, t
from .Theme import COLOR

TILE = 128                      # 720p baseline, square
GROW = 1.12                     # the focused tile
GAP = 18
TOP = 182                       # first row
SECTION = 26                    # extra space under the "Continue playing" row, for its caption
BOTTOM = 86                     # space kept for the hint dock
SIDE = 64
RADIUS = 14
BADGE_H = 0.235                 # players badge height, of the tile
MASTER_CACHE = 160              # composed tiles kept (PIL, focused size)
PHOTO_CACHE = 480               # tile pictures kept (every size shown)
HERO_CACHE = 4                  # banners kept for the launch screen
HERO_FADE = ()
BACKGROUND_DELAY_MS = 220       # the selection rests this long before the backdrop follows
MOVE_MS = 120
SCROLL_MS = 150
ENTER_MS = 200
SIZE_STEPS = 3                  # sizes a tile passes through between normal and focused
PULSE = (0.5, 0.6, 0.72, 0.84, 0.94, 1.0)
PULSE_MS = 120
PRELOAD_MS = 40
SQUARE_MIN = 0.9                # width / height from which a tile counts as square

BADGE = {                       # fill, text
    "ok": ((40, 168, 74, 245), (255, 255, 255)),
    "bad": ((214, 60, 52, 245), (255, 255, 255)),
    "neutral": ((10, 12, 18, 215), (245, 245, 245)),
}


def badge_state(game_players, joined):
    """Badge colour for a game supporting `game_players` when `joined` pads are assigned."""
    if not game_players or joined < 2:
        return "neutral"
    return "ok" if game_players >= joined else "bad"


def _placeholder_color(title):
    import hashlib
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


def grid_shape(width, height, tile, gap, top, bottom, side):
    """(columns, rows) of tiles that fit the screen."""
    columns = max(1, (width - 2 * side + gap) // (tile + gap))
    rows = max(1, (height - top - bottom + gap) // (tile + gap))
    return columns, rows


def scroll_rows(row, rows, first_row, total_rows):
    """
    The top row on screen once `row` must be visible: it changes only when
    the row is off screen, and never shows empty rows past the end.
    """
    if row < first_row:
        first_row = row
    elif row >= first_row + rows:
        first_row = row - rows + 1
    return max(0, min(first_row, max(0, total_rows - rows)))


def first_visible_row(index, columns, rows, first_row, count):
    """scroll_rows() for a plain grid of `count` games, `columns` per row."""
    return scroll_rows(index // columns, rows, first_row, (count - 1) // columns + 1)


class Layout:
    """
    Where each game sits: an optional first row of `pinned` games ("Continue
    playing"), then every other game, `columns` per row.
    """

    def __init__(self, count, columns, pinned=0):
        self.count, self.columns = count, max(1, columns)
        self.pinned = pinned if 0 < pinned < count else 0

    def cell(self, index):
        """(row, column) of a game."""
        if index < self.pinned:
            return 0, index
        rest = index - self.pinned
        return (1 if self.pinned else 0) + rest // self.columns, rest % self.columns

    @property
    def rows(self):
        rest = self.count - self.pinned
        return (1 if self.pinned else 0) + (rest - 1) // self.columns + 1 if rest > 0 else 1

    def row_length(self, row):
        if self.pinned and row == 0:
            return self.pinned
        first = self.pinned + (row - (1 if self.pinned else 0)) * self.columns
        return max(0, min(self.columns, self.count - first))

    def index(self, row, column):
        """The game at (row, column), the column clamped to that row's length."""
        column = max(0, min(column, self.row_length(row) - 1))
        if self.pinned and row == 0:
            return column
        return self.pinned + (row - (1 if self.pinned else 0)) * self.columns + column

    def step(self, index, action):
        """
        The game reached from `index` with "left" / "right" (the next game,
        wrapping around the list) or "up" / "down" (the row above / below,
        same column, or the last game of a shorter row).
        """
        if action == "left":
            return (index - 1) % self.count
        if action == "right":
            return (index + 1) % self.count
        row, column = self.cell(index)
        if action == "up" and row > 0:
            return self.index(row - 1, column)
        if action == "down" and row < self.rows - 1:
            return self.index(row + 1, column)
        return index


class GameGrid:
    def __init__(self, ui):
        self.ui = ui
        self.games = []
        self.index = 0
        self.players = 0
        self.filter_players = 0
        self.loading = False
        self.columns = 1
        self.rows = 1
        self.first_row = 0
        self.pinned = 0                 # games in the "Continue playing" row
        self.layout = Layout(0, 1)
        self.tabs = []                  # [(key, label, count)] over the grid
        self.tab = "all"
        self.sort_label = ""
        self.nav = None                 # (label, colour) of the player browsing
        self._masters = OrderedDict()   # tile key -> PIL image at the focused size
        self._photos = OrderedDict()    # (tile key, px) -> PhotoImage
        self._heroes = OrderedDict()    # banners for the launch screen
        self._items = {}                # game index -> canvas item
        self._shown = None              # (layout, index) the items are for
        self._ring = None
        self._ring_key = None
        self._pulse = len(PULSE) - 1
        self._beat = 0
        self._preload_job = None
        self._letter_job = None
        self._pulse_tick()

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    def set(self, games, index, players, filter_players, pinned=0, tabs=None, tab=None):
        if games is not self.games:
            self.first_row = 0
        self.games, self.index = games, index
        self.players, self.filter_players = players, filter_players
        self.pinned = pinned
        if tabs is not None:
            self.tabs, self.tab = tabs, tab
        self.layout = Layout(len(games), self.columns, pinned)
        self.loading = False

    def step(self, index, action):
        """The game reached from `index` by a direction (see Layout.step)."""
        return self.layout.step(index, action) if self.games else index

    def set_loading(self):
        self.loading = True
        self.games = []

    def reset(self):
        """Screen size or colours changed: every cached picture is stale."""
        self._masters.clear()
        self._photos.clear()
        self._heroes.clear()
        self._shown = None
        self._ring_key = None

    def forget(self, game):
        for cache in (self._masters, self._photos):
            for key in [k for k in cache if (k[0] if cache is self._masters else k[0][0]) == game["path"]]:
                del cache[key]
        for key in [k for k in self._heroes if k[0] in (game.get("background"), game.get("cover"))]:
            del self._heroes[key]
        self._shown = None              # next draw rebuilds with the new picture

    def page_size(self):
        return max(1, self.columns * self.rows)

    def _grown(self):
        return int(self.ui.px(TILE) * GROW) // 2 * 2

    def _sizes(self):
        """Tile sizes from normal to focused, the steps of the grow animation."""
        tile, grown = self.ui.px(TILE), self._grown()
        return [int(tile + (grown - tile) * n / SIZE_STEPS) // 2 * 2 for n in range(SIZE_STEPS + 1)]

    # ------------------------------------------------------------------
    # Tiles
    # ------------------------------------------------------------------
    def accent(self, game):
        """Glow colour for `game`: from its art, computed once per picture."""
        if not self.ui.dynamic_colors:
            return COLOR["ACCENT"]
        path = game.get("image") or game.get("cover") or game.get("background")
        cached = game.get("_accent")
        if cached and cached[0] == (path, COLOR["ACCENT"]):
            return cached[1]
        art = self.ui.load_art(path)
        color = Glyphs.vivid_color(art, COLOR["ACCENT"]) if art is not None else COLOR["ACCENT"]
        game["_accent"] = ((path, COLOR["ACCENT"]), color)
        return color

    def _compose(self, game, width, height, badge):
        """A tile of any shape: art (or a titled placeholder), rounded, with its badges."""
        ui = self.ui
        square = width / height >= SQUARE_MIN
        cover = ui.load_art(game.get("cover"))
        if square and cover is not None and cover.width / cover.height < SQUARE_MIN:
            # A portrait cover would lose its top and bottom in a square tile:
            # the square icon first, the cover only when there is nothing else
            order = ("image", "cover", "background")
        else:
            order = ("cover", "image", "background")    # the user's own picture wins when it fits
        art = next((a for a in (ui.load_art(game.get(k)) for k in order) if a is not None), None)
        if art is not None:
            tile = Glyphs.cover_crop(art, width, height)
        else:
            base = _placeholder_color(game["title"])
            tile = Image.new("RGB", (width, height), base)
            # A soft diagonal light so the plain colour does not look flat
            light = Image.linear_gradient("L").rotate(35, expand=True).resize((width, height))
            tile = Image.composite(Image.new("RGB", tile.size, tuple(min(255, c + 46) for c in base)), tile,
                                   light.point(lambda v: int(v * 0.55)))
            draw = ImageDraw.Draw(tile)
            font = Glyphs.font(max(10, width // 9))
            lines = _wrap(draw, game["title"], font, width - width // 5)[:4]
            line_h = font.size * 1.22
            y = (height - line_h * len(lines)) / 2
            for line in lines:
                draw.text((width / 2, y), line, font=font, fill="#F4F5F7", anchor="ma")
                y += line_h

        tile = Glyphs.rounded(tile, max(2, int(ui.px(RADIUS) * min(width, height) / ui.px(TILE))))
        scale = min(width, height) / ui.px(TILE)
        if game.get("hidden"):
            # Shown only while "show hidden games" is on: clearly set apart
            dark = Image.new("RGBA", tile.size, (0, 0, 0, 255))
            dark.putalpha(tile.getchannel("A"))
            tile = Image.blend(tile, dark, 0.62)
        if game.get("players"):
            self._draw_badge(tile, game["players"], badge, scale)
        if game.get("favorite"):
            self._draw_corner(tile, None, COLOR["GOLD"], scale, right=True)
        if game.get("custom_input"):
            self._draw_corner(tile, "!", COLOR["WARN"], scale, right=True, bottom=True)
        return tile

    def _draw_badge(self, tile, players, state, scale):
        """
        Players pill in the bottom-left corner: [people icon] 1-4, large
        enough to read from the couch, green / red against the players who
        joined.
        """
        h = max(14, int(min(tile.size) * BADGE_H))
        margin = max(3, int(min(tile.size) * 0.06))
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

    def _draw_corner(self, tile, text, color, scale, right=False, bottom=False):
        """A small disc in a corner: the favourite star (text None) or a one-letter mark."""
        d = max(14, int(min(tile.size) * 0.2))
        margin = max(3, int(min(tile.size) * 0.06))
        x = tile.width - margin - d if right else margin
        y = tile.height - margin - d if bottom else margin
        tile.alpha_composite(Glyphs.panel(d, d, d // 2, (10, 12, 18, 225)), (x, y))
        if text is None:
            icon = Glyphs.star(int(d * 0.64), color)
        else:
            icon = Glyphs.disc(text, int(d * 0.78), color, "#111111")
        tile.alpha_composite(icon, (x + (d - icon.width) // 2, y + (d - icon.height) // 2))

    def _key(self, game, shape):
        return (game["path"], shape, badge_state(game.get("players"), self.players), game.get("players"),
                game.get("image"), game.get("cover"), bool(game.get("favorite")), bool(game.get("hidden")),
                bool(game.get("custom_input")))

    def _master(self, game):
        key = self._key(game, "square")
        master = self._masters.get(key)
        if master is None:
            size = self._grown()
            master = self._masters[key] = self._compose(game, size, size, key[2])
            if len(self._masters) > MASTER_CACHE:
                self._masters.popitem(last=False)
        else:
            self._masters.move_to_end(key)
        return key, master

    def _square(self, game, px):
        """PhotoImage of a game's square tile, `px` wide."""
        key, master = self._master(game)
        photo = self._photos.get((key, px))
        if photo is None:
            image = master if px == master.width else master.resize((px, px), Image.BILINEAR, reducing_gap=2.0)
            photo = self._photos[(key, px)] = ImageTk.PhotoImage(image)
            if len(self._photos) > PHOTO_CACHE:
                self._photos.popitem(last=False)
        else:
            self._photos.move_to_end((key, px))
        return photo

    def tile(self, game, width, height, selected=False):
        """PhotoImage of a game's tile (cached), also used by the roulette and the menus."""
        width, height = int(width), int(height)
        if width == height:
            return self._square(game, width)
        key = (self._key(game, (width, height)), width)
        photo = self._photos.get(key)
        if photo is None:
            photo = self._photos[key] = ImageTk.PhotoImage(self._compose(game, width, height, key[0][2]))
        return photo

    def halo(self, game, width, height):
        """The focus frame around a width x height tile, in the game's colour (centre it on the tile)."""
        return self._frame(int(width), int(height), self.accent(game), len(PULSE) - 1)

    def _ring_parts(self, width, height):
        """
        What every focus frame of one size shares, made once: the white rim
        and the glow's shape at each breathing level. A frame in a game's
        colour is then just the glow tinted and the rim on top.
        """
        ui = self.ui
        key = ("ring-parts", width, height)
        parts = ui._photos.get(key)                 # PIL images, kept with the other cached pictures
        if parts is None:
            pad = ui.px(24)
            radius = ui.px(RADIUS) * min(width, height) // ui.px(TILE)
            line = max(2, ui.px(3))
            size = (width + 2 * pad, height + 2 * pad)
            grow = int(line * 1.5)
            box = (pad, pad, pad + width - 1, pad + height - 1)
            outer = (box[0] - grow, box[1] - grow, box[2] + grow, box[3] + grow)
            shape = Image.new("L", size, 0)
            ImageDraw.Draw(shape).rounded_rectangle(outer, radius=radius + grow, fill=255)
            shape = shape.filter(ImageFilter.GaussianBlur(pad * 0.34))
            ImageDraw.Draw(shape).rounded_rectangle(box, radius=radius, fill=0)     # nothing over the tile
            glows = [shape.point(lambda v, k=level: int(v * k)) for level in PULSE]
            rim = Image.new("RGBA", (size[0] * 2, size[1] * 2), (0, 0, 0, 0))
            ImageDraw.Draw(rim).rounded_rectangle(tuple(v * 2 for v in outer), radius=(radius + grow) * 2,
                                                  outline=(255, 255, 255, 255), width=line * 2)
            parts = ui._photos[key] = (glows, rim.resize(size, Image.LANCZOS))
        return parts

    def _frame(self, width, height, color, level):
        ui = self.ui

        def make():
            glows, rim = self._ring_parts(width, height)
            image = Image.new("RGBA", rim.size, Glyphs._rgb(color) + (0,))
            image.putalpha(glows[level])
            return Image.alpha_composite(image, rim)
        return ui.photo(("ring", width, height, color, level), make)

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------
    def _metrics(self):
        ui = self.ui
        tile, gap = ui.px(TILE), ui.px(GAP)
        section = ui.px(SECTION) if self.pinned else 0
        self.columns, self.rows = grid_shape(ui.width, ui.height - section, tile, gap, ui.px(TOP),
                                             ui.px(BOTTOM), ui.px(SIDE))
        self.layout = Layout(len(self.games), self.columns, self.pinned)
        grid_w = self.columns * tile + (self.columns - 1) * gap
        return tile, gap, (ui.width - grid_w) // 2, ui.px(TOP)

    def _row_y(self, row, metrics, first_row=None):
        """Top of `row` on screen with `first_row` at the top (default: the current one)."""
        tile, gap, _, top = metrics
        first_row = self.first_row if first_row is None else first_row
        y = top + (row - first_row) * (tile + gap)
        if self.layout.pinned and first_row == 0 and row >= 1:
            y += self.ui.px(SECTION)            # room for the "All games" caption
        return y

    def _center(self, index, metrics, first_row=None):
        """Centre of the tile at `index` with `first_row` (default: the current one) at the top."""
        tile, gap, left, _ = metrics
        row, column = self.layout.cell(index)
        return left + column * (tile + gap) + tile / 2, self._row_y(row, metrics, first_row) + tile / 2

    def _visible(self, first_row):
        rows = range(first_row, first_row + self.rows)
        return [i for i in range(len(self.games)) if self.layout.cell(i)[0] in rows]

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------
    def draw(self, full=False):
        """
        Show the games with self.index focused. A move within the visible
        rows changes two tiles; a move to another row slides the grid; any
        other change (other games, other size) rebuilds it.
        """
        ui = self.ui
        if not ui.width or ui._hold_draw:
            return
        if self.loading or not self.games:
            self._clear()
            self._draw_placeholder()
            return
        metrics = self._metrics()
        count = len(self.games)
        self.index = max(0, min(self.index, count - 1))
        selected = self.games[self.index]
        previous_first = self.first_row
        self.first_row = scroll_rows(self.layout.cell(self.index)[0], self.rows, self.first_row, self.layout.rows)

        layout = (id(self.games), count, ui.width, ui.height, self.players, self.columns, self.rows, self.pinned)
        shown = self._shown
        ui.animator.finish("grid")
        same = shown is not None and shown[0] == layout and self._items and not full
        if same and self.first_row == previous_first:
            self._move(metrics, shown[1])
        elif same and abs(self.first_row - previous_first) == 1 and ui.animations:
            row = self.layout.cell(self.index)[0]
            distance = self._row_y(row, metrics, previous_first) - self._row_y(row, metrics)
            self._build(metrics)
            self._slide(metrics, distance)
        else:
            self._build(metrics)
            if full or shown is None or shown[0] != layout:
                self._enter(metrics)
        self._shown = (layout, self.index)
        self._draw_header(selected, count)
        self._draw_scrollbar(metrics, count)
        ui.set_backdrop(selected, BACKGROUND_DELAY_MS)
        if self._preload_job is None:
            self._preload_job = ui.root.after(PRELOAD_MS, self._preload)

    def _build(self, metrics):
        """Every visible tile, the focused one large inside its frame."""
        c = self.ui.canvas
        c.delete("tiles")
        self._items = {}
        sizes = self._sizes()
        for i in self._visible(self.first_row):
            x, y = self._center(i, metrics)
            size = sizes[-1] if i == self.index else sizes[0]
            self._items[i] = c.create_image(x, y, image=self._square(self.games[i], size),
                                            tags=("games", "tiles", f"tile{i}"))
        if self.layout.pinned and self.first_row == 0:
            # The two sections of the "All" tab, captioned
            ui, left = self.ui, metrics[2]
            tile, gap, _, top = metrics
            font = ui.font(11)
            if self.columns - self.layout.pinned >= 2:
                # beside the row, in the room its few games leave free
                x = left + self.layout.pinned * (tile + gap) + gap
                y = top + tile // 2
                c.create_rectangle(x, y - ui.px(22), x + ui.px(4), y + ui.px(22), fill=COLOR["ACCENT"], width=0,
                                   tags=("games", "tiles"))
                c.create_text(x + ui.px(18), y - ui.px(10), text=t("games_continue"), anchor="w",
                              fill=COLOR["TEXT"], font=ui.font(17, "bold"), tags=("games", "tiles"))
                c.create_text(x + ui.px(18), y + ui.px(14), text=t("games_continue_hint"), anchor="w",
                              fill=COLOR["TEXT_DIM"], font=ui.font(12, False), tags=("games", "tiles"))
            else:
                c.create_text(left + ui.px(2), top - ui.px(14), text=t("games_continue").upper(), anchor="w",
                              fill=COLOR["TEXT_FAINT"], font=font, tags=("games", "tiles"))
            y = self._row_y(1, metrics) - ui.px(GAP) // 2 - ui.px(SECTION) // 2
            c.create_text(left + ui.px(2), y, text=t("games_all").upper(), anchor="w",
                          fill=COLOR["TEXT_FAINT"], font=font, tags=("games", "tiles"))
            c.tag_lower("tiles")
            c.tag_lower("bg")
        self._place_ring(metrics)

    def _move(self, metrics, old):
        """Within the visible rows: the frame glides over, the old tile shrinks, the new one grows."""
        ui, c = self.ui, self.ui.canvas
        new, games = self.index, self.games
        if old == new or old not in self._items or new not in self._items:
            self._build(metrics)
            return
        sizes = self._sizes()
        c.tag_raise(self._items[new])
        self._place_ring(metrics, glide_from=self._center(old, metrics))
        if not ui.animations:
            c.itemconfigure(self._items[old], image=self._square(games[old], sizes[0]))
            c.itemconfigure(self._items[new], image=self._square(games[new], sizes[-1]))
            return
        last = [None]

        def step(progress):
            n = min(SIZE_STEPS, int(progress * SIZE_STEPS + 0.5))
            if n == last[0]:
                return
            last[0] = n
            c.itemconfigure(self._items[old], image=self._square(games[old], sizes[SIZE_STEPS - n]))
            c.itemconfigure(self._items[new], image=self._square(games[new], sizes[n]))
        ui.animator.run("grid-size", MOVE_MS, step)

    def _slide(self, metrics, distance):
        """A new row came into view: the tiles start one row away and slide into place."""
        ui, c = self.ui, self.ui.canvas
        c.move("tiles", 0, distance)
        c.move("games-ring", 0, distance)
        c.move("games-nav", 0, distance)
        offset = [1.0]

        def step(progress):
            delta = distance * (1.0 - progress - offset[0])
            for tag in ("tiles", "games-ring", "games-nav"):
                c.move(tag, 0, delta)
            offset[0] = 1.0 - progress
        ui.animator.run("grid", SCROLL_MS, step)

    def _enter(self, metrics):
        """The grid appears: the tiles rise a little into place."""
        ui, c = self.ui, self.ui.canvas
        if not ui.animations:
            return
        distance = ui.px(22)
        for tag in ("tiles", "games-ring", "games-nav"):
            c.move(tag, 0, distance)
        offset = [1.0]

        def step(progress):
            delta = distance * (1.0 - progress - offset[0])
            for tag in ("tiles", "games-ring", "games-nav"):
                c.move(tag, 0, delta)
            offset[0] = 1.0 - progress
        ui.animator.run("grid", ENTER_MS, step)

    def _preload(self):
        """
        Compose the tiles (and the focus frames) the next moves will need,
        a couple per call, while nothing else is happening.
        """
        ui = self.ui
        self._preload_job = None
        if ui.view != "games" or not self.games:
            return
        if ui.animator.active("grid") or ui.animator.active("grid-size") or ui.animator.active("ring"):
            self._preload_job = ui.root.after(PRELOAD_MS, self._preload)
            return
        sizes = self._sizes()
        rows = range(self.first_row - 1, self.first_row + self.rows + 1)
        near = [i for i in range(len(self.games)) if self.layout.cell(i)[0] in rows]
        order = sorted(near, key=lambda i: abs(i - self.index))
        made = 0
        for i in order:
            game = self.games[i]
            key = self._key(game, "square")
            if key not in self._masters or (key, sizes[0]) not in self._photos:
                self._square(game, sizes[0])
                made += 1
            elif abs(i - self.index) <= self.columns and (key, sizes[1]) not in self._photos:
                for size in sizes:                      # the grow animation's steps
                    self._square(game, size)
                made += 1
            elif abs(i - self.index) <= 1 and ("ring", sizes[-1], sizes[-1], self.accent(game), 0) not in ui._photos:
                for level in range(len(PULSE)):         # the frame in this game's colour
                    self._frame(sizes[-1], sizes[-1], self.accent(game), level)
                made += 1
            if made >= 2:
                self._preload_job = ui.root.after(PRELOAD_MS, self._preload)
                return

    # ------------------------------------------------------------------
    # The focus frame
    # ------------------------------------------------------------------
    def _place_ring(self, metrics, glide_from=None):
        ui, c = self.ui, self.ui.canvas
        selected = self.games[self.index]
        size = self._grown()
        x, y = self._center(self.index, metrics)
        self._ring_key = (size, self.accent(selected))
        image = self._frame(size, size, self._ring_key[1], self._pulse if ui.animations else len(PULSE) - 1)
        if self._ring and c.type(self._ring):
            c.itemconfigure(self._ring, image=image)
        else:
            self._ring = c.create_image(x, y, image=image, tags=("games", "games-ring"))
        c.tag_raise("games-ring")
        if glide_from and ui.animations:
            sx, sy = glide_from
            c.coords(self._ring, sx, sy)

            moved = [0.0]

            def step(progress):
                c.coords(self._ring, sx + (x - sx) * progress, sy + (y - sy) * progress)
                c.move("games-nav", (x - sx) * (progress - moved[0]), (y - sy) * (progress - moved[0]))
                moved[0] = progress
            ui.animator.run("ring", MOVE_MS, step)
        else:
            ui.animator.finish("ring")
            c.coords(self._ring, x, y)
        # Who is browsing: that player's number on the corner of the frame
        c.delete("games-nav")
        if self.nav:
            d = ui.px(30)
            disc = ui.photo(("nav", self.nav, d), lambda: Glyphs.disc(self.nav[0], d, self.nav[1], COLOR["DISC_TEXT"]))
            back = ui.photo(("nav-back", d), lambda: Glyphs.panel(d + 6, d + 6, (d + 6) // 2, (8, 9, 14, 255)))
            cx, cy = (glide_from or (x, y))
            cx, cy = cx - size / 2 + ui.px(3), cy - size / 2 + ui.px(3)
            c.create_image(cx, cy, image=back, tags=("games", "games-nav"))
            c.create_image(cx, cy, image=disc, tags=("games", "games-nav"))

    def _pulse_tick(self):
        """The glow of the focus frame breathes: six cached pictures, there and back."""
        ui = self.ui
        self._pulse_job = ui.root.after(PULSE_MS, self._pulse_tick)
        if not self._ring_key or not ui.animations or ui.view != "games" or ui.quiet() \
                or ui.animator.active("ring") or ui.animator.active("grid"):
            return
        steps = len(PULSE)
        self._beat = (self._beat + 1) % (2 * steps - 2)
        self._pulse = self._beat if self._beat < steps else 2 * steps - 2 - self._beat
        if self._ring and ui.canvas.type(self._ring):
            size, color = self._ring_key
            ui.canvas.itemconfigure(self._ring, image=self._frame(size, size, color, self._pulse))

    # ------------------------------------------------------------------
    # Header: the selected game's title and facts
    # ------------------------------------------------------------------
    def _draw_header(self, selected, count):
        ui, c = self.ui, self.ui.canvas
        c.delete("games-header")
        tags = ("games", "games-header")
        left = ui.px(SIDE)
        self._draw_tabs(left, ui.px(52), tags)

        x, y = left, ui.px(102)
        if selected.get("favorite"):
            star_h = ui.px(28)
            star = ui.photo(("star", star_h, COLOR["GOLD"]), lambda: Glyphs.star(star_h, COLOR["GOLD"]))
            c.create_image(x, y, image=star, anchor="w", tags=tags)
            x += star_h + ui.px(12)
        room = ui.width - x - ui.px(330)            # the status pill and shortcuts on the right
        font = next((f for f in (ui.font(32, "semibold"), ui.font(27, "semibold"), ui.font(23, "semibold"))
                     if f.measure(selected["title"]) <= room), ui.font(23, "semibold"))
        c.create_text(x, y, anchor="w", fill=COLOR["TEXT"], font=font, tags=tags,
                      text=ui.truncate(selected["title"], font, room))

        x, y = left, ui.px(144)
        players = selected.get("players")
        if players:
            state = badge_state(players, self.players)
            color = {"ok": COLOR["OK"], "bad": COLOR["BAD"]}.get(state, COLOR["TEXT"])
            icon_h = ui.px(17)
            icon = ui.photo(("people", 1 if players <= 1 else 2, icon_h, color),
                            lambda: Glyphs.people(1 if players <= 1 else 2, icon_h, color))
            text = players_label(players)
            if self.players >= 2:
                text += "   ·   " + (t("players_fit", n=self.players) if state == "ok" else
                                     t("players_too_many", max=players, n=self.players))
            x += self._chip(x, y, text, color, tags, icon, strong=True) + ui.px(10)
        seconds = selected.get("playtime") or 0
        if seconds >= 60:
            icon_h = ui.px(15)
            icon = ui.photo(("clock", icon_h, COLOR["TEXT_DIM"]), lambda: Glyphs.clock(icon_h, COLOR["TEXT_DIM"]))
            text = playtime_label(seconds)
            if selected.get("last_played"):
                text += "  ·  " + last_played_label(selected["last_played"])
            x += self._chip(x, y, text, COLOR["TEXT_DIM"], tags, icon) + ui.px(10)
        if selected.get("emulator_name"):
            x += self._chip(x, y, selected["emulator_name"], COLOR["TEXT_DIM"], tags) + ui.px(10)
        if selected.get("custom_input"):
            x += self._chip(x, y, "!  " + t("games_custom_input"), COLOR["WARN"], tags) + ui.px(10)
        if selected.get("hidden"):
            x += self._chip(x, y, t("games_hidden"), COLOR["TEXT_DIM"], tags) + ui.px(10)

        pinned = self.layout.pinned
        caption = t("games_continue") if self.index < pinned else f"{self.index - pinned + 1} / {count - pinned}"
        if self.sort_label:
            caption += f"   ·   {self.sort_label.upper()}"
        if self.filter_players:
            caption += f"   ·   {t('games_filter', n=self.filter_players).upper()}"
        c.create_text(ui.width - ui.px(SIDE), y, text=caption, anchor="e", fill=COLOR["TEXT_FAINT"],
                      font=ui.font(12), tags=tags)

    def _draw_tabs(self, x, y, tags):
        """The tabs over the grid: [LB] All 42 · Favourites 5 · ... [RB], the current one lit."""
        ui, c = self.ui, self.ui.canvas
        if len(self.tabs) < 2:
            return
        h = ui.px(34)
        glyph = ui.photo(("glyph", "lb", int(ui.px(14) * 1.5)), lambda: Glyphs.button("lb", int(ui.px(14) * 1.5)))
        c.create_image(x, y, image=glyph, anchor="w", tags=tags)
        x += glyph.width() + ui.px(10)
        font, count_font = ui.font(14), ui.font(12, False)
        for key, label, count in self.tabs:
            current = key == self.tab
            number = str(count)
            w = font.measure(label) + ui.px(8) + count_font.measure(number) + ui.px(32)
            pill = ui.photo(("tab", w, h, current, COLOR["ACCENT"]), lambda: Glyphs.panel(
                w, h, h // 2, COLOR["ROW_SELECTED"] if current else COLOR["CHIP"],
                outline=(COLOR["ACCENT"] if current else COLOR["EDGE"], max(1, ui.px(1.5 if current else 1)))))
            c.create_image(x, y, image=pill, anchor="w", tags=tags)
            c.create_text(x + ui.px(16), y, text=label, anchor="w", font=font, tags=tags,
                          fill=COLOR["TEXT"] if current else COLOR["TEXT_DIM"])
            c.create_text(x + w - ui.px(16), y, text=number, anchor="e", font=count_font, tags=tags,
                          fill=COLOR["TEXT_DIM"] if current else COLOR["TEXT_FAINT"])
            x += w + ui.px(8)
        glyph = ui.photo(("glyph", "rb", int(ui.px(14) * 1.5)), lambda: Glyphs.button("rb", int(ui.px(14) * 1.5)))
        c.create_image(x + ui.px(2), y, image=glyph, anchor="w", tags=tags)

    def _draw_scrollbar(self, metrics, count):
        """A thin bar on the right edge: where the visible rows are in the whole list."""
        ui, c = self.ui, self.ui.canvas
        c.delete("games-scroll")
        rows_total = self.layout.rows
        if rows_total <= self.rows:
            return
        tile, gap, _, top = metrics
        height = self.rows * tile + (self.rows - 1) * gap
        x = ui.width - ui.px(30)
        w = max(2, ui.px(4))
        c.create_rectangle(x, top, x + w, top + height, fill=COLOR["LINE_FAINT"], width=0,
                           tags=("games", "games-scroll"))
        thumb = max(ui.px(24), height * self.rows // rows_total)
        y = top + (height - thumb) * self.first_row // max(1, rows_total - self.rows)
        c.create_rectangle(x, y, x + w, y + thumb, fill=COLOR["TEXT_DIM"], width=0, tags=("games", "games-scroll"))

    def _chip(self, x, y, text, color, tags, icon=None, strong=False):
        """A glass pill with coloured text (and an icon); returns its width. strong: larger, outlined in the colour."""
        ui, c = self.ui, self.ui.canvas
        font = ui.font(15 if strong else 13)
        icon_w = icon.width() + ui.px(8) if icon else 0
        step = ui.px(14)                        # widths in steps: most chips reuse a cached pill
        w, h = -(-(font.measure(text) + icon_w + ui.px(26)) // step) * step, ui.px(36 if strong else 30)
        edge = color if strong else COLOR["EDGE"]
        pill = ui.photo(("chip", w, h, edge), lambda: Glyphs.panel(
            w, h, h // 2, COLOR["CHIP"], outline=(edge, max(1, ui.px(1.5 if strong else 1)))))
        c.create_image(x, y, image=pill, anchor="w", tags=tags)
        if icon:
            c.create_image(x + ui.px(13), y, image=icon, anchor="w", tags=tags)
        c.create_text(x + ui.px(13) + icon_w, y, text=text, anchor="w", fill=color, font=font, tags=tags)
        return w

    # ------------------------------------------------------------------
    # Banners (the launch screen shows the game's banner)
    # ------------------------------------------------------------------
    def _hero_key(self, path):
        from .Ui import current_theme
        ui = self.ui
        return (path, int(ui.width * 0.66), int(ui.height * 0.70), current_theme(), bool(ui.animations))

    @staticmethod
    def _hero_images(key):
        """
        The banner cut and faded for the hero area: PIL images, faintest
        first, the last one the full banner. No Tk here: safe on a thread.
        """
        from .Ui import load_picture
        path, width, height, theme, animated = key
        art = load_picture(path, limit=max(width, 1280))
        if art is None:
            return None
        image = Glyphs.cover_crop(art, width, height).convert("RGBA")
        mask = Glyphs.hero_mask(width, height)
        if theme != "oled":
            mask = mask.point(lambda v: int(v * 0.86))          # the blurred backdrop stays in the picture
        frames = []
        for alpha in (HERO_FADE if animated else ()) + (1.0,):
            frame = image.copy()
            frame.putalpha(mask if alpha == 1.0 else mask.point(lambda v, a=alpha: int(v * a)))
            frames.append(frame)
        return frames

    def _hero_store(self, key, photos):
        self._heroes[key] = photos
        if len(self._heroes) > HERO_CACHE:
            self._heroes.popitem(last=False)

    def _hero_frames(self, path):
        """The banner's pictures, made right now if need be (the launch screen uses the last one)."""
        key = self._hero_key(path)
        photos = self._heroes.get(key)
        if photos is None:
            images = self._hero_images(key)
            if images is None:
                return None
            photos = [ImageTk.PhotoImage(image) for image in images]
            self._hero_store(key, photos)
        return photos

    def flash_letter(self, letter):
        """The letter just jumped to, large in the middle of the grid for a moment."""
        ui, c = self.ui, self.ui.canvas
        c.delete("games-letter")
        if self._letter_job:
            ui.root.after_cancel(self._letter_job)
            self._letter_job = None
        if not ui.width or ui._hold_draw:
            return
        size = ui.px(132)
        cx, cy = ui.width // 2, ui.height // 2 + ui.px(30)
        box = ui.photo(("letter-box", size), lambda: Glyphs.panel(
            size, size, ui.px(30), COLOR["DIALOG"], outline=(COLOR["EDGE_STRONG"], max(1, ui.px(1.5)))))
        c.create_image(cx, cy, image=box, tags=("games", "games-letter"))
        c.create_text(cx, cy, text=letter, fill=COLOR["TEXT"], font=ui.font(72, "semibold"),
                      tags=("games", "games-letter"))

        def clear():
            self._letter_job = None
            c.delete("games-letter")
        self._letter_job = ui.root.after(650, clear)

    def _clear(self):
        ui = self.ui
        for key in ("grid", "grid-size", "ring"):
            ui.animator.finish(key)
        if self._preload_job:
            ui.root.after_cancel(self._preload_job)
            self._preload_job = None
        ui.canvas.delete("games")
        self._items = {}
        self._ring = None
        self._ring_key = None
        self._shown = None

    clear = _clear

    def _draw_placeholder(self):
        ui, c = self.ui, self.ui.canvas
        cx, cy = ui.width // 2, ui.height // 2
        if self.loading:
            c.create_text(cx, cy, fill=COLOR["TEXT_DIM"], font=ui.font(22, "semibold"),
                          text=t("games_loading"), tags="games")
        elif self.filter_players:
            c.create_text(cx, cy - ui.px(20), fill=COLOR["TEXT"], font=ui.font(24, "semibold"),
                          text=t("games_none_for", n=self.filter_players), tags="games")
            ui.rich(cx, cy + ui.px(24), f"[y] {t('hint_filter_off')}", 16, COLOR["TEXT_DIM"],
                    anchor="center", tags="games")
        else:
            c.create_text(cx, cy, fill=COLOR["TEXT"], font=ui.font(24, "semibold"),
                          text=t("games_none"), tags="games")
