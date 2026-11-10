"""
Core/Menu.py
A list menu drawn over the current screen (canvas tag "menu"): the settings
screen, a game's own menu, the list of save backups, the folder browser.

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

ROW_H = 44
ROW_GAP = 6
TOP = 124
BOTTOM = 84
SIDE = 64
LIST_W = 800                # width of the list without a game picture
ART_W, ART_H = 190, 285


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
        self.draw()

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
    def draw(self):
        ui, c = self.ui, self.ui.canvas
        c.delete("menu")
        frame = self.frame
        if not frame or not ui.width:
            return
        shade = ui.photo(("menu-overlay", ui.width, ui.height),
                         lambda: Image.new("RGBA", (ui.width, ui.height), COLOR["SCREEN"]))
        c.create_image(0, 0, image=shade, anchor="nw", tags="menu")
        left = ui.px(SIDE)
        title = frame["title"]() if callable(frame["title"]) else frame["title"]
        c.create_text(left, ui.px(46), text=ui.truncate(title, ui.font(32), ui.width - 2 * left),
                      anchor="w", fill=COLOR["TEXT"], font=ui.font(32), tags="menu")
        subtitle = frame["subtitle"]() if callable(frame["subtitle"]) else frame["subtitle"]
        if subtitle:
            c.create_text(left, ui.px(88), text=ui.truncate(subtitle, ui.font(15, False), ui.width - 2 * left),
                          anchor="w", fill=COLOR["TEXT_DIM"], font=ui.font(15, False), tags="menu")

        top = ui.px(TOP)
        list_x, list_w = left, min(ui.width - 2 * left, ui.px(LIST_W))
        if frame["game"]:
            art_w, art_h = ui.px(ART_W), ui.px(ART_H)
            c.create_image(left, top, image=ui.grid.tile(frame["game"], art_w, art_h, False),
                           anchor="nw", tags="menu")
            y = top + art_h + ui.px(22)
            small = ui.font(13, False)
            for line in (frame["info"]() if frame["info"] else []):
                c.create_text(left, y, text=ui.truncate(line, small, art_w + ui.px(40)), anchor="w",
                              fill=COLOR["TEXT_DIM"], font=small, tags="menu")
                y += ui.px(22)
            list_x = left + art_w + ui.px(56)
            list_w = min(ui.width - list_x - left, ui.px(LIST_W))

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

        label_font, value_font, caption_font = ui.font(17), ui.font(17, False), ui.font(12)
        for slot, row in enumerate(rows[first:first + visible]):
            index = first + slot
            y = top + slot * (row_h + gap)
            middle = y + row_h // 2
            kind = row.get("kind")
            if kind == "header":
                c.create_text(list_x + ui.px(4), y + row_h - ui.px(12), text=row["label"].upper(), anchor="w",
                              fill=COLOR["TEXT_FAINT"], font=caption_font, tags="menu")
                continue
            if kind == "info":
                c.create_text(list_x + ui.px(18), middle, text=ui.truncate(row["label"], value_font,
                              list_w - ui.px(36)), anchor="w", fill=COLOR["TEXT_DIM"], font=value_font, tags="menu")
                continue
            chosen = index == frame["index"]
            box = ui.photo(("menu-row", list_w, row_h, chosen), lambda: Glyphs.panel(
                list_w, row_h, ui.px(10), COLOR["ROW_SELECTED"] if chosen else COLOR["ROW"],
                outline=(COLOR["ACCENT"], ui.px(2)) if chosen else None))
            c.create_image(list_x, y, image=box, anchor="nw", tags="menu")
            value = row.get("value")
            value_text = "" if value is None else str(value)
            if chosen and row.get("change") and value_text:
                value_text = f"◀  {value_text}  ▶"
            value_w = value_font.measure(value_text) + ui.px(30) if value_text else 0
            c.create_text(list_x + ui.px(18), middle, anchor="w", font=label_font,
                          text=ui.truncate(row["label"], label_font, list_w - ui.px(36) - value_w),
                          fill=row.get("color") or COLOR["TEXT"], tags="menu")
            if value_text:
                c.create_text(list_x + list_w - ui.px(18), middle, anchor="e", font=value_font, text=value_text,
                              fill=COLOR["ACCENT"] if chosen else COLOR["TEXT_DIM"], tags="menu")
        arrow_font = ui.font(13)
        if first > 0:
            c.create_text(list_x + list_w // 2, top - ui.px(12), text="▲", fill=COLOR["TEXT_DIM"],
                          font=arrow_font, tags="menu")
        if first + visible < len(rows):
            c.create_text(list_x + list_w // 2, top + visible * (row_h + gap), text="▼",
                          fill=COLOR["TEXT_DIM"], font=arrow_font, tags="menu")

        from .I18n import t
        ui.rich(ui.width - left, ui.height - ui.px(32), frame["hints"] or t("menu_hints"), 15,
                COLOR["TEXT"], anchor="e", tags="menu")
        ui.raise_overlays()
