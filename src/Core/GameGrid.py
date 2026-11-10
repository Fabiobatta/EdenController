"""
Core/GameGrid.py
The game browser, drawn on the UI canvas (tag "games"):

    hero    the selected game: its banner fading into the screen on the
            right, its title and facts (players, play time...) on the left
    shelf   one row of square tiles that scrolls under a fixed focus
            frame; the focused tile is larger and its frame glows in the
            colour of the game's art, breathing slowly

Every tile carries a players badge - how many can play together on one
console - coloured against the number of controllers assigned:

    green   the game takes everyone who joined
    red     fewer players than joined
    grey    nothing to compare (unknown count, or a single player)

Motion, and why it stays cheap:
    - a move slides the existing canvas items (a handful of small
      pictures) for a few frames; only the two tiles changing size get
      new pictures, taken from one cached master per game
    - tiles about to scroll into view are composed ahead, a couple per
      idle moment, so a move finds them ready
    - the banner is decoded and cut on a background thread, once per
      game, after the selection has rested for a moment, and fades in
      over two extra pictures
    - the glow breathes by swapping six pictures of the frame, each a
      cached glow shape tinted with the game's colour
Tiles are composed once per game (a master at the focus size) and cached.
"""

import hashlib
import threading
from collections import OrderedDict

from PIL import Image, ImageDraw, ImageFilter, ImageTk

from . import Glyphs
from .I18n import last_played_label, players_label, playtime_label, t
from .Theme import COLOR

TILE = 132                      # 720p baseline, square
FOCUS = 184                     # the focused tile
GAP = 18
FOCUS_X = 156                   # centre of the focused tile: its left edge under the title
SHELF_BOTTOM = 622              # the tiles stand on this line
RADIUS = 16
MASTER_CACHE = 120              # composed tiles kept (PIL, focus size)
PHOTO_CACHE = 420               # tile pictures kept (every size shown)
HERO_CACHE = 5
HERO_DELAY_MS = 170             # the selection rests this long before its banner is made
# The banner and the backdrop repaint most of the window when they change:
# they change together, and the banner takes one half-way step, not more
HERO_FADE = (0.5,)              # alpha steps before the full banner
HERO_STEP_MS = 60
MOVE_MS = 110
GROW_STEPS = 4                  # sizes a tile passes through between normal and focused
ENTER_MS = 190
PULSE = (0.5, 0.6, 0.72, 0.84, 0.94, 1.0)
PULSE_MS = 120
PRELOAD_MS = 40
SQUARE_MIN = 0.9                # width / height from which a tile counts as square

BADGE = {                       # fill, text
    "ok": ((46, 160, 67, 240), (255, 255, 255)),
    "bad": ((210, 58, 48, 240), (255, 255, 255)),
    "neutral": ((12, 14, 20, 200), (240, 240, 240)),
}


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


def shelf_positions(count, index, tile, focus, gap, focus_x, width, margin):
    """
    Centre x of every tile of a shelf of `count` games with `index` focused
    (the focused tile is `focus` wide, the others `tile`).

    The focused tile sits at focus_x while the row is long enough to
    scroll; near the end of the list the row stops with its last tile at
    the right margin and the focus travels instead, and a row that fits on
    screen never scrolls at all.

    Returns:
        callable: i -> centre x on screen.
    """
    step = tile + gap

    def content(i):                     # centre of tile i, the row starting at 0
        if i < index:
            return i * step + tile / 2
        if i == index:
            return i * step + focus / 2
        return i * step + (focus - tile) + tile / 2

    total = count * step - gap + (focus - tile)
    scroll = content(index) - focus_x
    scroll = max(-margin, min(scroll, max(-margin, total + margin - width)))
    return lambda i: content(i) - scroll


class GameGrid:
    def __init__(self, ui):
        self.ui = ui
        self.games = []
        self.index = 0
        self.players = 0
        self.filter_players = 0
        self.loading = False
        self.columns = 1                # tiles from the focus to the right edge (one "page")
        self.rows = 1
        self.sort_label = ""
        self.nav = None                 # (label, colour) of the player browsing
        self._masters = OrderedDict()   # tile key -> PIL image at the focus size
        self._photos = OrderedDict()    # (tile key, px) -> PhotoImage
        self._heroes = OrderedDict()    # (path, w, h) -> [PhotoImage per fade step]
        self._items = {}                # game index -> canvas item
        self._shown = None              # (games id, count, index, layout) the items are for
        self._ring = None
        self._ring_key = None
        self._pulse = 0
        self._pulse_job = None
        self._hero_job = None
        self._hero_path = None          # banner on screen
        self._hero_wanted = None        # banner being prepared: its cache key
        self._hero_made = None          # (key, [PIL frames]) handed over by the worker thread
        self._preload_job = None
        self._letter_job = None
        self._pulse_tick()

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    def set(self, games, index, players, filter_players):
        self.games, self.index = games, index
        self.players, self.filter_players = players, filter_players
        self.loading = False

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
        return max(1, self.columns)

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

        tile = Glyphs.rounded(tile, max(2, int(ui.px(RADIUS) * min(width, height) / ui.px(FOCUS))))
        scale = min(width, height) / ui.px(FOCUS)
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
        """Players pill in the bottom-left corner: [people icon] 1-4."""
        h = max(12, int(self.ui.px(30) * scale))
        margin = max(3, int(self.ui.px(10) * scale))
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
        d = max(14, int(self.ui.px(34) * scale))
        margin = max(3, int(self.ui.px(9) * scale))
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
            size = self.ui.px(FOCUS)
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
            pad = ui.px(26)
            radius = ui.px(RADIUS) * min(width, height) // ui.px(FOCUS)
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
        tile, focus, gap = ui.px(TILE), ui.px(FOCUS), ui.px(GAP)
        focus_x, bottom = ui.px(FOCUS_X), ui.height - ui.px(720 - SHELF_BOTTOM)
        self.columns = max(1, int((ui.width - focus_x - focus / 2) // (tile + gap)) + 1)
        return tile, focus, gap, focus_x, bottom

    def _layout(self, index, metrics):
        """
        Where everything stands with `index` focused.

        Returns:
            (place, visible): place(i) -> (centre x, centre y, size);
                              visible: the indices on (or just off) screen.
        """
        tile, focus, gap, focus_x, bottom = metrics
        ui = self.ui
        x_of = shelf_positions(len(self.games), index, tile, focus, gap, focus_x, ui.width, ui.px(64))

        def place(i):
            size = focus if i == index else tile
            return x_of(i), bottom - size / 2, size
        visible = [i for i in range(len(self.games)) if -tile <= x_of(i) <= ui.width + tile]
        return place, visible

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------
    def draw(self, full=False):
        """
        Show the games with self.index focused. A move of one or two places
        on the same list slides the shelf; anything else rebuilds it.
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

        layout = (id(self.games), count, ui.width, ui.height, self.players)
        previous = self._shown
        moved = self.index - previous[2] if previous and previous[3] == layout else None
        ui.animator.finish("shelf")
        if full or moved is None or abs(moved) > 2 or not self._items or not ui.animations:
            self._build(metrics, selected, direction=0 if moved in (None, 0) else (1 if moved > 0 else -1))
        elif moved:
            self._slide(metrics, previous[2])
        self._shown = (layout[0], count, self.index, layout)
        self._draw_hero_text(selected, count, animate=bool(moved) or moved is None or full)
        self._schedule_hero(selected, at_once=moved is None or full)
        self._place_ring(metrics, selected)
        if self._preload_job is None:
            self._preload_job = ui.root.after(PRELOAD_MS, self._preload)

    def _preload(self):
        """
        Compose the tiles (and the focus frames) the next moves will need,
        a couple per call, while nothing else is happening.
        """
        ui = self.ui
        self._preload_job = None
        if ui.view != "games" or not self.games or ui.animator.active("shelf"):
            if ui.view == "games" and self.games:
                self._preload_job = ui.root.after(PRELOAD_MS, self._preload)
            return
        count, tile, focus = len(self.games), ui.px(TILE), ui.px(FOCUS)
        ahead = self.columns + 4
        order = [self.index + d * side for d in range(1, ahead + 1) for side in (1, -1)]
        made = 0
        for i in order:
            if not 0 <= i < count:
                continue
            game = self.games[i]
            key = self._key(game, "square")
            if key not in self._masters or (key, tile) not in self._photos:
                self._square(game, tile)
                made += 1
            elif abs(i - self.index) <= 2 and ("ring", focus, focus, self.accent(game), 0) not in ui._photos:
                for level in range(len(PULSE)):         # the frame in this game's colour
                    self._frame(focus, focus, self.accent(game), level)
                made += 1
            if made >= 2:
                self._preload_job = ui.root.after(PRELOAD_MS, self._preload)
                return

    def _build(self, metrics, selected, direction=0):
        """Create the shelf from scratch, sliding in from `direction` (0: rising a little)."""
        ui, c = self.ui, self.ui.canvas
        c.delete("tiles")
        self._items = {}
        place, visible = self._layout(self.index, metrics)
        for i in visible:
            x, y, size = place(i)
            self._items[i] = c.create_image(x, y, image=self._square(self.games[i], int(size)),
                                            tags=("games", "tiles"))
        if not ui.animations:
            return
        dx, dy = (ui.px(70) * direction, 0) if direction else (0, ui.px(26))
        c.move("tiles", dx, dy)
        offset = [1.0]                          # how much of (dx, dy) the tiles are still away

        def step(progress):
            c.move("tiles", dx * (1.0 - progress - offset[0]), dy * (1.0 - progress - offset[0]))
            offset[0] = 1.0 - progress
        ui.animator.run("shelf", ENTER_MS, step)

    def _slide(self, metrics, old_index):
        """
        Move the shelf from old_index to self.index: tiles slide, the two
        focus tiles change size, the focus frame follows its tile.
        """
        ui, c = self.ui, self.ui.canvas
        games, index = self.games, self.index
        before, _ = self._layout(old_index, metrics)
        place, visible = self._layout(index, metrics)
        wanted = set(visible)
        for i in wanted - set(self._items):                 # entering: start where they were before the move
            x, y, size = before(i)
            self._items[i] = c.create_image(x, y, image=self._square(games[i], int(size)), tags=("games", "tiles"))
        start = {i: c.coords(item) for i, item in self._items.items()}
        tile, focus, bottom = metrics[0], metrics[1], metrics[4]
        ring = self._ring if self._ring and c.type(self._ring) else None

        def step(progress):
            for i, item in self._items.items():
                x, y, size = place(i)
                sx, sy = start[i]
                x = sx + (x - sx) * progress
                if i in (index, old_index):
                    # Growing into the focus / shrinking out of it, standing on the shelf line
                    # ...in a few fixed sizes, so the pictures are reused from the cache
                    grow = progress if i == index else 1 - progress
                    size = int(tile + (focus - tile) * round(grow * GROW_STEPS) / GROW_STEPS) // 2 * 2
                    c.itemconfigure(item, image=self._square(games[i], size))
                    c.coords(item, x, bottom - size / 2)
                    if i == index and ring:
                        c.coords(ring, x, bottom - focus / 2)
                else:
                    c.coords(item, x, sy + (y - sy) * progress)

        def done():
            for i in [i for i in self._items if i not in wanted]:
                c.delete(self._items.pop(i))
        ui.animator.run("shelf", MOVE_MS, step, done)

    # ------------------------------------------------------------------
    # The focus frame
    # ------------------------------------------------------------------
    def _place_ring(self, metrics, selected):
        ui, c = self.ui, self.ui.canvas
        focus = metrics[1]
        place, _ = self._layout(self.index, metrics)
        x, y = place(self.index)[0], metrics[4] - focus / 2
        sliding = ui.animator.active("shelf") and self._ring and c.type(self._ring)
        self._ring_key = (focus, self.accent(selected))
        image = self._frame(focus, focus, self._ring_key[1], self._pulse if ui.animations else len(PULSE) - 1)
        if self._ring and c.type(self._ring):
            if not sliding:                     # while the shelf slides, the slide moves the frame
                c.coords(self._ring, x, y)
            c.itemconfigure(self._ring, image=image)
        else:
            self._ring = c.create_image(x, y, image=image, tags=("games", "games-ring"))
        c.tag_raise("games-ring")
        # Who is browsing: that player's number on the corner of the frame
        c.delete("games-nav")
        if self.nav:
            d = ui.px(34)
            disc = ui.photo(("nav", self.nav, d), lambda: Glyphs.disc(self.nav[0], d, self.nav[1], COLOR["DISC_TEXT"]))
            back = ui.photo(("nav-back", d), lambda: Glyphs.panel(d + 6, d + 6, (d + 6) // 2, (8, 9, 14, 255)))
            cx, cy = x - focus / 2 + ui.px(4), y - focus / 2 + ui.px(4)
            c.create_image(cx, cy, image=back, tags=("games", "games-nav"))
            c.create_image(cx, cy, image=disc, tags=("games", "games-nav"))

    def _pulse_tick(self):
        """The glow of the focus frame breathes: six cached pictures, there and back."""
        ui = self.ui
        self._pulse_job = ui.root.after(PULSE_MS, self._pulse_tick)
        if not self._ring_key or not ui.animations or ui.view != "games" or ui.quiet():
            return
        steps = len(PULSE)
        self._beat = (getattr(self, "_beat", 0) + 1) % (2 * steps - 2)
        self._pulse = self._beat if self._beat < steps else 2 * steps - 2 - self._beat
        if self._ring and ui.canvas.type(self._ring):
            focus, color = self._ring_key
            ui.canvas.itemconfigure(self._ring, image=self._frame(focus, focus, color, self._pulse))

    # ------------------------------------------------------------------
    # Hero: the selected game's title, facts and banner
    # ------------------------------------------------------------------
    def _draw_hero_text(self, selected, count, animate=True):
        ui, c = self.ui, self.ui.canvas
        c.delete("games-herotext")
        tags = ("games", "games-herotext")
        left = ui.px(64)
        base = ui.height - ui.px(720 - SHELF_BOTTOM) - ui.px(FOCUS)        # top of the shelf
        y = base - ui.px(150)

        caption = f"{self.index + 1} / {count}"
        if self.sort_label:
            caption += f"   ·   {self.sort_label.upper()}"
        if self.filter_players:
            caption += f"   ·   {t('games_filter', n=self.filter_players).upper()}"
        c.create_text(left, y, text=caption, anchor="w", fill=self.accent(selected), font=ui.font(13), tags=tags)

        y += ui.px(46)
        x = left
        if selected.get("favorite"):
            star_h = ui.px(30)
            star = ui.photo(("star", star_h, COLOR["GOLD"]), lambda: Glyphs.star(star_h, COLOR["GOLD"]))
            c.create_image(x, y, image=star, anchor="w", tags=tags)
            x += star_h + ui.px(14)
        room = int(ui.width * 0.56) - x
        font = next((f for f in (ui.font(42, "semibold"), ui.font(34, "semibold"), ui.font(28, "semibold"))
                     if f.measure(selected["title"]) <= room), ui.font(28, "semibold"))
        c.create_text(x, y, anchor="w", fill=COLOR["TEXT"], font=font, tags=tags,
                      text=ui.truncate(selected["title"], font, room))

        y += ui.px(54)
        x = left
        players = selected.get("players")
        if players:
            icon_h = ui.px(16)
            icon = ui.photo(("people", 1 if players <= 1 else 2, icon_h, COLOR["TEXT"]),
                            lambda: Glyphs.people(1 if players <= 1 else 2, icon_h, COLOR["TEXT"]))
            x += self._chip(x, y, players_label(players), COLOR["TEXT"], tags, icon) + ui.px(10)
            if self.players >= 2:
                fits = players >= self.players
                text = ("✓  " + t("players_fit", n=self.players)) if fits else \
                       ("✕  " + t("players_too_many", max=players, n=self.players))
                x += self._chip(x, y, text, COLOR["OK"] if fits else COLOR["BAD"], tags) + ui.px(10)
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

        c.tag_raise("tiles")
        c.tag_raise("games-ring")
        c.tag_raise("games-nav")
        if animate and ui.animations:
            # The text settles in from a little to the right
            offset = ui.px(18)
            c.move("games-herotext", offset, 0)
            done = [0.0]

            def step(progress):
                c.move("games-herotext", -offset * (progress - done[0]), 0)
                done[0] = progress
            ui.animator.run("herotext", 160, step)
        else:
            ui.animator.finish("herotext")

    def _chip(self, x, y, text, color, tags, icon=None):
        """A glass pill with coloured text (and an icon); returns its width."""
        ui, c = self.ui, self.ui.canvas
        font = ui.font(13)
        icon_w = icon.width() + ui.px(8) if icon else 0
        step = ui.px(14)                        # widths in steps: most chips reuse a cached pill
        w, h = -(-(font.measure(text) + icon_w + ui.px(26)) // step) * step, ui.px(30)
        pill = ui.photo(("chip", w, h), lambda: Glyphs.panel(
            w, h, h // 2, COLOR["CHIP"], outline=(COLOR["EDGE"], max(1, ui.px(1)))))
        c.create_image(x, y, image=pill, anchor="w", tags=tags)
        if icon:
            c.create_image(x + ui.px(13), y, image=icon, anchor="w", tags=tags)
        c.create_text(x + ui.px(13) + icon_w, y, text=text, anchor="w", fill=color, font=font, tags=tags)
        return w

    def _schedule_hero(self, selected, at_once=False):
        """The backdrop and the banner follow the selection once it has rested (at once on entering)."""
        ui = self.ui
        if self._hero_job:
            ui.root.after_cancel(self._hero_job)
        delay = 1 if at_once or not ui.animations else HERO_DELAY_MS
        self._hero_job = ui.root.after(delay, self._show_hero, selected)

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

    def _show_hero(self, selected):
        """The selection rested: bring its banner in over the previous one."""
        ui, c = self.ui, self.ui.canvas
        self._hero_job = None
        if ui.view != "games" or not self.games or selected is not self.games[min(self.index, len(self.games) - 1)]:
            return
        path = selected.get("background")
        if path == self._hero_path and c.find_withtag("games-heroimg"):
            ui.set_backdrop(selected, fade=False)
            return
        if not path:
            # No banner: the blurred backdrop alone changes, with its own cross-fade
            self._hero_path, self._hero_wanted = None, None
            c.delete("games-heroimg")
            ui.set_backdrop(selected)
            return
        key = self._hero_key(path)
        self._hero_wanted = key
        self._hero_game = selected
        if key in self._heroes:
            self._heroes.move_to_end(key)
            self._present_hero(key, self._heroes[key])
            return

        def work():
            try:
                images = self._hero_images(key)
            except Exception:
                images = None
            if self._hero_wanted == key:            # still the one being waited for
                self._hero_made = (key, images)
        threading.Thread(target=work, daemon=True).start()
        self._hero_job = ui.root.after(15, self._collect_hero, 200)

    def _collect_hero(self, tries):
        """Wait (without blocking) for the worker thread's banner, then show it."""
        ui = self.ui
        self._hero_job = None
        made = self._hero_made
        if made is None or made[0] != self._hero_wanted:
            if self._hero_wanted is not None and tries > 0:
                self._hero_job = ui.root.after(15, self._collect_hero, tries - 1)
            return
        self._hero_made = None
        key, images = made
        if images is None or ui.view != "games":
            self._hero_path = None
            ui.canvas.delete("games-heroimg")
            if ui.view == "games":
                ui.set_backdrop(getattr(self, "_hero_game", None))
            return
        self._present_hero(key, images)

    def _present_hero(self, key, frames):
        """
        Fade the banner in over the previous one. `frames` are PhotoImages
        (cached) or PIL images straight from the worker: those become
        pictures one per step, so no single step is heavy.
        """
        ui, c = self.ui, self.ui.canvas
        old = c.find_withtag("games-heroimg")
        self._hero_path, self._hero_wanted = key[0], None
        photos = []
        ui.set_backdrop(getattr(self, "_hero_game", None), fade=False)    # same repaint as the banner

        def photo(n):
            frame = frames[n]
            return frame if isinstance(frame, ImageTk.PhotoImage) else ImageTk.PhotoImage(frame)

        photos.append(photo(0))
        item = c.create_image(ui.width, 0, image=photos[0], anchor="ne", tags=("games", "games-heroimg"))
        c.tag_lower(item)
        for earlier in old:                                  # under the text and tiles, over the old banner
            c.tag_lower(earlier)
        c.tag_lower("bg")

        def step(n=1):
            if not c.type(item):
                return
            if n < len(frames):
                photos.append(photo(n))
                c.itemconfigure(item, image=photos[n])
                ui.root.after(HERO_STEP_MS, step, n + 1)
            else:
                for earlier in old:
                    c.delete(earlier)
                if key not in self._heroes:
                    self._hero_store(key, photos)
        ui.root.after(HERO_STEP_MS, step)

    # ------------------------------------------------------------------
    def flash_letter(self, letter):
        """The letter just jumped to, large over the hero for a moment."""
        ui, c = self.ui, self.ui.canvas
        c.delete("games-letter")
        if self._letter_job:
            ui.root.after_cancel(self._letter_job)
            self._letter_job = None
        if not ui.width or ui._hold_draw:
            return
        size = ui.px(132)
        cx, cy = ui.width // 2, ui.height // 2 - ui.px(90)
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
        ui.animator.finish("shelf")
        ui.animator.finish("herotext")
        if self._hero_job:
            ui.root.after_cancel(self._hero_job)
            self._hero_job = None
        if self._preload_job:
            ui.root.after_cancel(self._preload_job)
            self._preload_job = None
        self._hero_wanted = None
        ui.canvas.delete("games")
        self._items = {}
        self._ring = None
        self._ring_key = None
        self._hero_path = None
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
