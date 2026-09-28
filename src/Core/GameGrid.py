"""
Core/GameGrid.py
The game picker drawn as a Playnite-style grid of portrait covers over a
blurred, darkened copy of the selected game's art.

Everything is drawn on one tk.Canvas with Pillow-made images, cached per
game and size, so moving the selection only re-places cached images and
blurs one small thumbnail for the background.

Art for a game, best first:
    game["cover"]   portrait cover (covers folder / SteamGridDB)
    game["image"]   square icon (e.g. the one the emulator cached)
    nothing         a coloured card with the title
"""

import hashlib
import os
import sys
import tkinter as tk

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageTk

# 720p baseline sizes, multiplied by the UI scale
TILE_W = 150
TILE_H = 225
GAP = 26
MARGIN_X = 70
HEADER_H = 120
RADIUS = 10
BORDER = 5
SELECTED_GROW = 1.07

ACCENT = "#F5D90A"          # selection border (Playnite yellow)
TEXT = "#F2F2F2"
TEXT_DIM = "#B8B8B8"
BG = "#0F0F0F"


def _font(size, bold=True):
    """A TrueType font close to the UI font, falling back to Pillow's default."""
    names = (["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"] if bold
             else ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"])
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:           # Pillow < 10.1
        return ImageFont.load_default()


def _cover_crop(image, width, height):
    """Scale to fill width x height, cropping the overflow (CSS object-fit: cover)."""
    ratio = max(width / image.width, height / image.height)
    size = (max(1, round(image.width * ratio)), max(1, round(image.height * ratio)))
    image = image.resize(size, Image.LANCZOS)
    left = (image.width - width) // 2
    top = (image.height - height) // 2
    return image.crop((left, top, left + width, top + height))


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
    """Owns the canvas and the image caches; App/Ui call render()."""

    def __init__(self, root, scale, footer_height):
        self.root = root
        self.scale = scale
        self.canvas = tk.Canvas(root, bg=BG, highlightthickness=0, bd=0)
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1, height=-footer_height)
        self.canvas.bind("<Configure>", lambda e: self._redraw())
        self.games = []
        self.index = 0
        self.first_row = 0
        self.columns = 1
        self.rows = 1
        self._art = {}          # path -> PIL image (RGB)
        self._tiles = {}        # (key, w, h) -> PhotoImage
        self._backgrounds = {}  # (key, w, h) -> PhotoImage
        self._keep = []         # PhotoImages on the canvas right now

    def destroy(self):
        self.canvas.destroy()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def render(self, games, index):
        self.games = games
        self.index = index
        self._redraw()

    def forget_art(self, game):
        """Drop cached images of a game whose art changed (cover downloaded)."""
        key = game["path"]
        self._tiles = {k: v for k, v in self._tiles.items() if k[0] != key}
        self._backgrounds = {k: v for k, v in self._backgrounds.items() if k[0] != key}

    def page_size(self):
        return self.columns * self.rows

    # ------------------------------------------------------------------
    # Images
    # ------------------------------------------------------------------
    def _load(self, path):
        if not path:
            return None
        if path not in self._art:
            try:
                with Image.open(path) as image:
                    self._art[path] = image.convert("RGB")
            except Exception:
                self._art[path] = None
        return self._art[path]

    def _tile_image(self, game, width, height, selected=False):
        """PIL RGBA tile with rounded corners (and the accent border if selected)."""
        cover = self._load(game.get("cover"))
        icon = self._load(game.get("image"))
        if cover:
            tile = _cover_crop(cover, width, height)
        elif icon:
            # Square icon on a blurred, darkened fill of itself
            tile = _cover_crop(icon, width, height).filter(ImageFilter.GaussianBlur(width // 10))
            tile = Image.blend(tile, Image.new("RGB", tile.size, (0, 0, 0)), 0.35)
            side = width
            tile.paste(icon.resize((side, side), Image.LANCZOS), (0, (height - side) // 2))
        else:
            tile = Image.new("RGB", (width, height), _placeholder_color(game["title"]))
            draw = ImageDraw.Draw(tile)
            font = _font(max(10, width // 8))
            lines = _wrap(draw, game["title"], font, width - width // 6)[:5]
            line_h = font.size * 1.25
            y = (height - line_h * len(lines)) / 2
            for line in lines:
                draw.text((width / 2, y), line, font=font, fill=TEXT, anchor="ma")
                y += line_h

        mask = Image.new("L", (width, height), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, width - 1, height - 1), radius=max(2, int(RADIUS * self.scale)), fill=255)
        tile = tile.convert("RGBA")
        tile.putalpha(mask)
        if selected:
            border = max(2, int(BORDER * self.scale))
            ImageDraw.Draw(tile).rounded_rectangle(
                (0, 0, width - 1, height - 1), radius=max(2, int(RADIUS * self.scale)),
                outline=ACCENT, width=border)
        return tile

    def _tile(self, game, width, height, selected=False):
        key = (game["path"], width, height, selected)
        if key not in self._tiles:
            self._tiles[key] = ImageTk.PhotoImage(self._tile_image(game, width, height, selected))
        return self._tiles[key]

    def _background(self, game, width, height):
        key = (game["path"], width, height)
        if key not in self._backgrounds:
            art = self._load(game.get("cover")) or self._load(game.get("image"))
            if art:
                # Blur a thumbnail and scale it up: cheap and very smooth
                small = _cover_crop(art, 64, max(1, round(64 * height / width)))
                small = small.filter(ImageFilter.GaussianBlur(3))
                image = small.resize((width, height), Image.BILINEAR)
                image = Image.blend(image, Image.new("RGB", image.size, (0, 0, 0)), 0.55)
            else:
                image = Image.new("RGB", (width, height), (15, 15, 15))
            # Darker towards the bottom, where the footer sits
            shade = Image.linear_gradient("L").resize((width, height)).point(lambda v: v * 0.55)
            image = Image.composite(Image.new("RGB", image.size, (0, 0, 0)), image, shade)
            if len(self._backgrounds) > 24:
                self._backgrounds.clear()
            self._backgrounds[key] = ImageTk.PhotoImage(image)
        return self._backgrounds[key]

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def _redraw(self):
        canvas = self.canvas
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width < 50 or height < 50 or not self.games:
            return
        s = self.scale
        tile_w, tile_h, gap = int(TILE_W * s), int(TILE_H * s), int(GAP * s)
        margin, header = int(MARGIN_X * s), int(HEADER_H * s)

        self.columns = max(1, (width - 2 * margin + gap) // (tile_w + gap))
        self.rows = max(1, (height - header - gap + gap) // (tile_h + gap))
        count = len(self.games)
        self.index = max(0, min(self.index, count - 1))

        # Scroll only when the selection leaves the visible rows
        row = self.index // self.columns
        if row < self.first_row:
            self.first_row = row
        elif row >= self.first_row + self.rows:
            self.first_row = row - self.rows + 1
        last_row = (count - 1) // self.columns
        self.first_row = max(0, min(self.first_row, max(0, last_row - self.rows + 1)))

        canvas.delete("all")
        keep = []
        selected = self.games[self.index]

        bg = self._background(selected, width, height)
        keep.append(bg)
        canvas.create_image(0, 0, image=bg, anchor="nw")

        # Header: selected title and position
        canvas.create_text(margin, int(38 * s), text=selected["title"], anchor="w",
                           fill=TEXT, font=("Segoe UI", -int(30 * s), "bold"))
        canvas.create_text(margin, int(76 * s), text=f"{self.index + 1} / {count}", anchor="w",
                           fill=TEXT_DIM, font=("Segoe UI", -int(15 * s)))

        grid_w = self.columns * tile_w + (self.columns - 1) * gap
        left = (width - grid_w) // 2
        top = header
        first = self.first_row * self.columns
        for item in range(first, min(count, first + self.columns * self.rows)):
            col = (item - first) % self.columns
            r = (item - first) // self.columns
            x = left + col * (tile_w + gap)
            y = top + r * (tile_h + gap)
            game = self.games[item]
            if item == self.index:
                grow_w, grow_h = int(tile_w * SELECTED_GROW), int(tile_h * SELECTED_GROW)
                cx, cy = x + tile_w // 2, y + tile_h // 2
                image = self._tile(game, grow_w, grow_h, selected=True)
                canvas.create_image(cx, cy, image=image, anchor="center")
            else:
                image = self._tile(game, tile_w, tile_h)
                canvas.create_image(x, y, image=image, anchor="nw")
            keep.append(image)

        # Hint that more rows exist
        if self.first_row > 0:
            canvas.create_text(width // 2, header - int(14 * s), text="▲", fill=TEXT_DIM,
                               font=("Segoe UI", -int(14 * s)))
        if self.first_row + self.rows <= last_row:
            canvas.create_text(width // 2, height - int(12 * s), text="▼", fill=TEXT_DIM,
                               font=("Segoe UI", -int(14 * s)))

        # The canvas does not hold references to its images
        self._keep = keep
