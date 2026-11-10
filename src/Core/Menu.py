"""
Core/Menu.py
A list menu in a drawer that slides in from the right edge over the dimmed
screen (canvas tag "menu"): the settings, a game's own menu, the list of
save backups, the folder browser.

    menu = Menu(ui, sound=sounds.play)
    menu.open(title, build, subtitle="", game=None, info=None)
    menu.move(+1) / menu.change(-1) / menu.activate() / menu.back()

`build()` returns the rows, top to bottom, and is called again after every
change, so a row's text always shows the current value:

    {"label": "Volume", "value": "70", "change": fn(delta)}     left / right
    {"label": "Restore a save", "action": fn}                   A
    {"label": "Emulator", "kind": "header"}                     a caption
    {"label": "No backups yet", "kind": "info"}                 plain text

A row with only "change" also reacts to A (next value). Menus stack: open()
on top of an open menu shows the new one, back() returns to the previous.
Core/App.py owns the input; this module only keeps the selection and draws.
"""

from PIL import Image

from . import Glyphs
from .Theme import COLOR

DRAWER_W = 580              # 720p baseline
ROW_H = 44
ROW_GAP = 6
TOP = 132                   # first row (lower when a game's picture heads the drawer)
BOTTOM = 76
SIDE = 36
ART = 92                    # the game's tile next to the title
SLIDE_MS = 170


def selectable(row):
    return bool(row.get("change") or row.get("action"))


class Menu:
    def __init__(self, ui, sound=None):
        self.ui = ui
        self.sound = sound or (lambda name: None)
        self.stack = []             # frames, the visible one last

    @property
    def active(self):
        return bool(self.stack)

    @property
    def frame(self):
        return self.stack[-1] if self.stack else None

    # ------------------------------------------------------------------
    # Model
    # ------------------------------------------------------------------
    def open(self, title, build, subtitle="", game=None, info=None, hints=None, on_back=None):
        """
        Args:
            title    (str | callable): heading.
            build    (callable): () -> rows.
            subtitle (str | callable): line under the title.
            game     (dict | None): its cover is shown on the left.
            info     (callable | None): () -> lines of text under the cover.
            hints    (str | None): footer markup; None for the default.
            on_back  (callable | None): called when this menu is left with B.
        """
        self.stack.append({"title": title, "build": build, "subtitle": subtitle, "game": game,
                           "info": info, "hints": hints, "on_back": on_back,
                           "rows": [], "index": 0, "first": 0})
        self._rebuild(reset=True)
        self.draw(slide=len(self.stack) == 1)

    def _rebuild(self, reset=False):
        frame = self.frame
        frame["rows"] = list(frame["build"]())
        choices = [i for i, row in enumerate(frame["rows"]) if selectable(row)]
        if not choices:
            frame["index"] = 0
        elif reset or frame["index"] not in choices:
            # Keep the place in the list when the row under the cursor went away
            later = [i for i in choices if i >= frame["index"]]
            frame["index"] = choices[0] if reset else (later[0] if later else choices[-1])

    def refresh(self):
        """Rows changed (a value was set elsewhere): rebuild and redraw."""
        if self.active:
            self._rebuild()
            self.draw()

    def move(self, delta):
        """Selection up (-1) / down (+1), wrapping, skipping captions; a page with ±5."""
        frame = self.frame
        choices = [i for i, row in enumerate(frame["rows"]) if selectable(row)]
        if len(choices) < 2:
            return
        position = choices.index(frame["index"]) if frame["index"] in choices else 0
        if abs(delta) == 1:
            position = (position + delta) % len(choices)
        else:
            position = max(0, min(len(choices) - 1, position + delta))
        if choices[position] != frame["index"]:
            frame["index"] = choices[position]
            self.sound("move")
            self.draw()

    def change(self, delta):
        """Left / right on the selected row."""
        row = self._selected()
        if row and row.get("change"):
            row["change"](delta)
            self.sound("toggle")
            self.refresh()

    def activate(self):
        """A on the selected row."""
        row = self._selected()
        if not row:
            return
        depth = len(self.stack)
        if row.get("action"):
            self.sound("select")
            row["action"]()
        elif row.get("change"):
            row["change"](1)
            self.sound("toggle")
        if self.active and len(self.stack) == depth:
            self.refresh()

    def back(self):
        """B: leave the visible menu (back to the one below, or close)."""
        if not self.active:
            return
        frame = self.stack.pop()
        self.sound("back")
        if frame["on_back"]:
            frame["on_back"]()
        if self.active:
            self._rebuild()
            self.draw()
        else:
            self.ui.canvas.delete("menu")

    def close(self):
        """Close every menu without calling on_back."""
        self.stack = []
        self.ui.canvas.delete("menu")

    def _selected(self):
        frame = self.frame
        if not frame or not frame["rows"]:
            return None
        row = frame["rows"][min(frame["index"], len(frame["rows"]) - 1)]
        return row if selectable(row) else None

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------
    def draw(self, slide=False):
        """
        The visible menu as a drawer on the right edge, over the dimmed
        screen. slide=True (a menu just opened) brings it in from the edge.
        """
        ui, c = self.ui, self.ui.canvas
        c.delete("menu")
        frame = self.frame
        if not frame or not ui.width:
            return
        shade = ui.photo(("menu-overlay", ui.width, ui.height),
                         lambda: Image.new("RGBA", (ui.width, ui.height), COLOR["OVERLAY"]))
        c.create_image(0, 0, image=shade, anchor="nw", tags="menu")
        tags = ("menu", "menu-drawer")
        width = min(ui.width, ui.px(DRAWER_W))
        x0 = ui.width - width
        radius = ui.px(26)
        # Rounded on the left only: the panel runs off the right edge
        panel = ui.photo(("drawer", width, ui.height), lambda: Glyphs.panel(
            width + radius, ui.height, radius, COLOR["DRAWER"], outline=(COLOR["EDGE"], max(1, ui.px(1.2)))))
        c.create_image(x0, 0, image=panel, anchor="nw", tags=tags)

        left = x0 + ui.px(SIDE)
        inner = width - 2 * ui.px(SIDE)
        title = frame["title"]() if callable(frame["title"]) else frame["title"]
        subtitle = frame["subtitle"]() if callable(frame["subtitle"]) else frame["subtitle"]
        title_font, sub_font = ui.font(26, "semibold"), ui.font(14, False)
        text_x, text_w, top = left, inner, ui.px(TOP)
        if frame["game"]:
            art = ui.px(ART)
            c.create_image(left, ui.px(40), image=ui.grid.tile(frame["game"], art, art), anchor="nw", tags=tags)
            text_x, text_w = left + art + ui.px(20), inner - art - ui.px(20)
        c.create_text(text_x, ui.px(62), text=ui.truncate(title, title_font, text_w), anchor="w",
                      fill=COLOR["TEXT"], font=title_font, tags=tags)
        lines = ([subtitle] if subtitle else []) + (list(frame["info"]()) if frame["info"] else [])
        if frame["game"]:
            # Facts about the game: two per line under the title
            lines = ["   ·   ".join(lines[n:n + 2]) for n in range(0, len(lines), 2)][:3]
            top = ui.px(40) + ui.px(ART) + ui.px(30)
        y = ui.px(96)
        for line in lines[:3 if frame["game"] else 1]:
            c.create_text(text_x, y, text=ui.truncate(line, sub_font, text_w), anchor="w",
                          fill=COLOR["TEXT_DIM"], font=sub_font, tags=tags)
            y += ui.px(22)

        rows = frame["rows"]
        row_h, gap = ui.px(ROW_H), ui.px(ROW_GAP)
        visible = max(1, (ui.height - top - ui.px(BOTTOM) + gap) // (row_h + gap))
        # Scroll just enough to keep the selection (and a caption above it) in view
        first = frame["first"]
        if frame["index"] < first + 1:
            first = max(0, frame["index"] - 1)
        elif frame["index"] >= first + visible:
            first = frame["index"] - visible + 1
        first = frame["first"] = max(0, min(first, max(0, len(rows) - visible)))

        label_font, value_font, caption_font = ui.font(16), ui.font(15, False), ui.font(11)
        for slot, row in enumerate(rows[first:first + visible]):
            index = first + slot
            y = top + slot * (row_h + gap)
            middle = y + row_h // 2
            kind = row.get("kind")
            if kind == "header":
                c.create_text(left + ui.px(4), y + row_h - ui.px(12), text=row["label"].upper(), anchor="w",
                              fill=COLOR["TEXT_FAINT"], font=caption_font, tags=tags)
                continue
            if kind == "info":
                c.create_text(left + ui.px(16), middle, text=ui.truncate(row["label"], value_font,
                              inner - ui.px(32)), anchor="w", fill=COLOR["TEXT_DIM"], font=value_font, tags=tags)
                continue
            chosen = index == frame["index"]
            box = ui.photo(("menu-row", inner, row_h, chosen, COLOR["ACCENT"]), lambda: Glyphs.panel(
                inner, row_h, ui.px(14), COLOR["ROW_SELECTED"] if chosen else COLOR["ROW"],
                outline=(COLOR["ACCENT"], ui.px(2)) if chosen else None))
            c.create_image(left, y, image=box, anchor="nw", tags=tags)
            value = row.get("value")
            value_text = "" if value is None else str(value)
            if chosen and row.get("change") and value_text:
                value_text = f"◀  {value_text}  ▶"
            value_w = value_font.measure(value_text) + ui.px(28) if value_text else 0
            c.create_text(left + ui.px(16), middle, anchor="w", font=label_font,
                          text=ui.truncate(row["label"], label_font, inner - ui.px(32) - value_w),
                          fill=row.get("color") or COLOR["TEXT"], tags=tags)
            if value_text:
                c.create_text(left + inner - ui.px(16), middle, anchor="e", font=value_font, text=value_text,
                              fill=COLOR["TEXT"] if chosen else COLOR["TEXT_DIM"], tags=tags)
        arrow_font = ui.font(12)
        if first > 0:
            c.create_text(left + inner // 2, top - ui.px(10), text="▲", fill=COLOR["TEXT_DIM"],
                          font=arrow_font, tags=tags)
        if first + visible < len(rows):
            c.create_text(left + inner // 2, top + visible * (row_h + gap) + ui.px(2), text="▼",
                          fill=COLOR["TEXT_DIM"], font=arrow_font, tags=tags)

        from .I18n import t
        hints = frame["hints"] or t("menu_hints")
        size = 14 if ui.rich_width(hints, 14) <= inner else 12
        ui.rich(left, ui.height - ui.px(36), hints, size, COLOR["TEXT_DIM"], tags=tags)
        ui.raise_overlays()

        if slide and ui.animations:
            c.move("menu-drawer", width, 0)
            offset = [1.0]

            def step(progress):
                c.move("menu-drawer", width * (1.0 - progress - offset[0]), 0)
                offset[0] = 1.0 - progress
            ui.animator.run("drawer", SLIDE_MS, step)
        else:
            ui.animator.finish("drawer")
