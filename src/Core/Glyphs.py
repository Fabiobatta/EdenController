"""
Core/Glyphs.py
Pillow drawings used by the UI: controller button prompts, the "players"
icon, glass panels and fonts. Everything is drawn at 4x and scaled down
(anti-aliasing), then cached - each picture is made once per size.

    button("a", 28)            -> green A button, 28 px high (RGBA)
    button("lb", 28)           -> LB bumper pill
    people(2, 20, "#FFFFFF")   -> two-person icon
    star(20, "#F5D90A")        -> favourite star
    trophy(24, "#F5D90A")      -> achievement cup
    panel(300, 120, 16, (0, 0, 0, 150), outline=("#F5D90A", 3))
"""

from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

SUPERSAMPLE = 4

# Xbox face button colours; letter colour chosen for contrast
FACE = {
    "a": ((91, 187, 74), (255, 255, 255)),
    "b": ((224, 68, 59), (255, 255, 255)),
    "x": ((57, 134, 219), (255, 255, 255)),
    "y": ((245, 190, 40), (40, 30, 0)),
}
NEUTRAL = (74, 76, 84)
NEUTRAL_LIGHT = (236, 236, 240)

FONT_FILES = {
    True: ("segoeuib.ttf", "seguisb.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"),
    False: ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"),
}


@lru_cache(maxsize=64)
def font(size, bold=True):
    """TrueType font close to the UI font; Pillow's default as a last resort."""
    for name in FONT_FILES[bold]:
        try:
            return ImageFont.truetype(name, max(1, int(size)))
        except OSError:
            continue
    try:
        return ImageFont.load_default(max(1, int(size)))
    except TypeError:           # Pillow < 10.1
        return ImageFont.load_default()


def _rgb(color):
    if isinstance(color, str):
        color = color.lstrip("#")
        return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))
    return tuple(color)


def _shade(color, factor):
    return tuple(max(0, min(255, int(c * factor))) for c in color[:3])


def cover_crop(image, width, height):
    """Scale to fill width x height, cropping the overflow (CSS object-fit: cover)."""
    ratio = max(width / image.width, height / image.height)
    size = (max(1, round(image.width * ratio)), max(1, round(image.height * ratio)))
    image = image.resize(size, Image.LANCZOS if ratio < 1 else Image.BICUBIC)
    left, top = (image.width - width) // 2, (image.height - height) // 2
    return image.crop((left, top, left + width, top + height))


def vivid_color(image, default):
    """
    The picture's most prominent saturated colour, pushed bright enough to
    glow on the dark UI ("#RRGGBB"). `default` when the picture is mostly
    grey (black-and-white art, dark screenshots...).

    Hues are counted in 24 buckets weighted by saturation x brightness, so
    a large grey sky loses to a smaller red kart.
    """
    import colorsys
    small = image.resize((28, 28), Image.BOX, reducing_gap=2.0).convert("RGB")
    buckets = {}
    data = small.tobytes()
    for i in range(0, len(data), 3):
        r, g, b = data[i], data[i + 1], data[i + 2]
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        weight = s * v
        if s < 0.3 or v < 0.25:
            continue
        bucket = buckets.setdefault(int(h * 24) % 24, [0.0, 0.0, 0.0, 0.0])
        bucket[0] += weight
        bucket[1] += r * weight
        bucket[2] += g * weight
        bucket[3] += b * weight
    if not buckets:
        return default
    total, r, g, b = max(buckets.values(), key=lambda x: x[0])
    if total < 40:              # a few coloured pixels only
        return default
    h, s, v = colorsys.rgb_to_hsv(r / total / 255, g / total / 255, b / total / 255)
    r, g, b = colorsys.hsv_to_rgb(h, max(s, 0.6), max(v, 0.92))
    return "#%02X%02X%02X" % (int(r * 255), int(g * 255), int(b * 255))


def _finish(image, size):
    """Downscale a supersampled drawing to its final size."""
    return image.resize(size, Image.LANCZOS)


# ============================================================================
# BUTTON PROMPTS
# ============================================================================
@lru_cache(maxsize=128)
def button(name, height):
    """
    A controller button prompt, `height` px tall.

    Names: a b x y (face buttons), lb rb (bumpers), lt rt (triggers),
    ls rs (stick clicks), start, back, dpad.
    """
    h = max(8, int(height))
    s = SUPERSAMPLE
    if name in ("lb", "rb", "lt", "rt"):
        w = int(h * 1.7)
        img = Image.new("RGBA", (w * s, h * s), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        # Bumpers are pills, triggers rounded only on top (their shape on the pad)
        radius = h * s // 2 if name in ("lb", "rb") else h * s // 3
        d.rounded_rectangle((0, 0, w * s - 1, h * s - 1), radius=radius, fill=NEUTRAL + (255,),
                            corners=None if name in ("lb", "rb") else (True, True, False, False))
        d.text((w * s / 2, h * s / 2), name.upper(), font=font(h * s * 0.48), fill=(255, 255, 255),
               anchor="mm")
        return _finish(img, (w, h))

    img = Image.new("RGBA", (h * s, h * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    full = h * s - 1
    if name in FACE:
        fill, letter = FACE[name]
        d.ellipse((0, 0, full, full), fill=_shade(fill, 0.78) + (255,))
        inset = h * s * 0.06
        d.ellipse((inset, inset, full - inset, full - inset), fill=fill + (255,))
        d.text((h * s / 2, h * s * 0.52), name.upper(), font=font(h * s * 0.62), fill=letter, anchor="mm")
    elif name in ("ls", "rs"):
        d.ellipse((0, 0, full, full), fill=NEUTRAL + (255,))
        d.text((h * s / 2, h * s * 0.52), name.upper(), font=font(h * s * 0.4), fill=(255, 255, 255),
               anchor="mm")
    elif name == "start":
        d.ellipse((0, 0, full, full), fill=NEUTRAL + (255,))
        bar_w, bar_h = h * s * 0.44, h * s * 0.075
        for dy in (-0.16, 0, 0.16):
            cy = h * s * (0.5 + dy)
            d.rounded_rectangle((h * s / 2 - bar_w / 2, cy - bar_h / 2, h * s / 2 + bar_w / 2, cy + bar_h / 2),
                                radius=bar_h / 2, fill=(255, 255, 255, 255))
    elif name == "back":
        d.ellipse((0, 0, full, full), fill=NEUTRAL + (255,))
        box, line = h * s * 0.28, max(2, int(h * s * 0.06))
        for dx, dy in ((-0.07, -0.07), (0.07, 0.07)):
            x, y = h * s * (0.5 + dx), h * s * (0.5 + dy)
            d.rounded_rectangle((x - box / 2, y - box / 2, x + box / 2, y + box / 2),
                                radius=box * 0.2, outline=(255, 255, 255, 255), width=line,
                                fill=NEUTRAL + (255,))
    elif name == "dpad":
        arm = h * s * 0.34
        c = h * s / 2
        for box in ((c - arm / 2, 0, c + arm / 2, full), (0, c - arm / 2, full, c + arm / 2)):
            d.rounded_rectangle(box, radius=arm * 0.25, fill=NEUTRAL_LIGHT + (255,))
        tri = arm * 0.32
        for (x, y), points in (((c, arm * 0.55), ((0, -1), (-1, 1), (1, 1))),
                               ((c, full - arm * 0.55), ((0, 1), (-1, -1), (1, -1))),
                               ((arm * 0.55, c), ((-1, 0), (1, -1), (1, 1))),
                               ((full - arm * 0.55, c), ((1, 0), (-1, -1), (-1, 1)))):
            d.polygon([(x + px * tri, y + py * tri) for px, py in points], fill=NEUTRAL + (255,))
    else:
        raise ValueError(f"unknown button {name!r}")
    return _finish(img, (h, h))


# ============================================================================
# ICONS
# ============================================================================
def _person(draw, cx, top, size, fill):
    head = size * 0.36
    draw.ellipse((cx - head / 2, top, cx + head / 2, top + head), fill=fill)
    body_w, body_top = size * 0.62, top + head * 1.08
    draw.pieslice((cx - body_w / 2, body_top, cx + body_w / 2, body_top + size * 0.9),
                  180, 360, fill=fill)


@lru_cache(maxsize=64)
def people(count, height, color):
    """One person (count == 1) or a pair (count > 1), `height` px tall."""
    h = max(8, int(height))
    s = SUPERSAMPLE
    rgb = _rgb(color)
    w = h if count <= 1 else int(h * 1.45)
    img = Image.new("RGBA", (w * s, h * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    size = h * s
    if count <= 1:
        _person(d, w * s / 2, size * 0.04, size, rgb + (255,))
    else:
        _person(d, w * s * 0.64, size * 0.04, size, _shade(rgb, 0.72) + (255,))
        _person(d, w * s * 0.38, size * 0.10, size * 0.94, rgb + (255,))
    return _finish(img, (w, h))


@lru_cache(maxsize=16)
def gamepad(height, color):
    """A small controller silhouette (connected controllers counter)."""
    h = max(8, int(height))
    s = SUPERSAMPLE
    w = int(h * 1.5)
    rgb = _rgb(color) + (255,)
    img = Image.new("RGBA", (w * s, h * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    W, H = w * s, h * s
    d.rounded_rectangle((W * 0.08, H * 0.18, W * 0.92, H * 0.7), radius=H * 0.26, fill=rgb)
    d.ellipse((W * 0.02, H * 0.3, W * 0.4, H * 0.96), fill=rgb)
    d.ellipse((W * 0.6, H * 0.3, W * 0.98, H * 0.96), fill=rgb)
    hole = (0, 0, 0, 0)
    d.rectangle((W * 0.2, H * 0.4, W * 0.32, H * 0.46), fill=hole)
    d.rectangle((W * 0.23, H * 0.34, W * 0.29, H * 0.52), fill=hole)
    d.ellipse((W * 0.68, H * 0.34, W * 0.75, H * 0.44), fill=hole)
    d.ellipse((W * 0.76, H * 0.44, W * 0.83, H * 0.54), fill=hole)
    return _finish(img, (w, h))


@lru_cache(maxsize=16)
def star(height, color, outline=None):
    """Five-pointed star (favourites)."""
    import math
    h = max(8, int(height))
    s = SUPERSAMPLE
    size = h * s
    points = []
    for i in range(10):
        radius = size * (0.5 if i % 2 == 0 else 0.21)
        angle = -math.pi / 2 + i * math.pi / 5
        points.append((size / 2 + radius * math.cos(angle), size * 0.53 + radius * math.sin(angle)))
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.polygon(points, fill=_rgb(color) + (255,))
    if outline:
        d.line(points + points[:1], fill=_rgb(outline) + (255,), width=max(1, size // 14), joint="curve")
    return _finish(img, (h, h))


@lru_cache(maxsize=16)
def trophy(height, color):
    """A cup with handles on a small stand (achievements)."""
    h = max(8, int(height))
    s = SUPERSAMPLE
    size = h * s
    rgb = _rgb(color) + (255,)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    line = max(1, int(size * 0.07))
    # handles, then the cup over them
    d.ellipse((size * 0.08, size * 0.14, size * 0.4, size * 0.48), outline=rgb, width=line)
    d.ellipse((size * 0.6, size * 0.14, size * 0.92, size * 0.48), outline=rgb, width=line)
    d.pieslice((size * 0.22, -size * 0.3, size * 0.78, size * 0.62), 0, 180, fill=rgb)
    d.rectangle((size * 0.22, size * 0.06, size * 0.78, size * 0.17), fill=rgb)
    d.rectangle((size * 0.45, size * 0.6, size * 0.55, size * 0.8), fill=rgb)
    d.rounded_rectangle((size * 0.27, size * 0.78, size * 0.73, size * 0.94), radius=size * 0.04, fill=rgb)
    return _finish(img, (h, h))


@lru_cache(maxsize=64)
def split_screen(width, height, regions, highlight, color):
    """
    A small TV showing how the screen is split: `regions` are (x0, y0, x1, y1)
    fractions, the one at index `highlight` filled with `color`, the others
    faint. One region covering everything = full screen.
    """
    w, h = max(8, int(width)), max(6, int(height))
    s = SUPERSAMPLE
    W, H = w * s, h * s
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    border = max(1, int(s * 1.5))
    d.rounded_rectangle((0, 0, W - 1, H - 1), radius=H * 0.12, fill=(18, 20, 27, 255),
                        outline=(120, 126, 140, 255), width=border)
    inset = border + s
    inner = (W - 2 * inset, H - 2 * inset)
    rgb = _rgb(color)
    for i, (x0, y0, x1, y1) in enumerate(regions):
        gap = s
        box = (inset + x0 * inner[0] + gap, inset + y0 * inner[1] + gap,
               inset + x1 * inner[0] - gap, inset + y1 * inner[1] - gap)
        if i == highlight:
            d.rounded_rectangle(box, radius=s * 2, fill=rgb + (255,))
        else:
            d.rounded_rectangle(box, radius=s * 2, fill=(255, 255, 255, 38))
    return _finish(img, (w, h))


@lru_cache(maxsize=16)
def split_map(width, height, regions, colors, text_color="#111111", icons=None):
    """
    A TV showing every player's part of the screen: `regions` (fractions, one
    per player) filled with that player's colour and labelled P1, P2...; with
    regions=None the screen is shared and the players are discs in a row
    (no players: just the empty screen). `icons`: each player's avatar
    (AVATARS name), drawn above the label.
    """
    w, h = max(16, int(width)), max(9, int(height))
    s = 2
    W, H = w * s, h * s
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    border = max(2, int(s * 1.5))
    corner = H * 0.085
    d.rounded_rectangle((0, 0, W - 1, H - 1), radius=corner, fill=(9, 10, 15, 236))
    rim = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(rim).rounded_rectangle((0, 0, W - 1, H - 1), radius=corner, outline=(255, 255, 255, 56),
                                          width=border)
    img = Image.alpha_composite(img, rim)
    d = ImageDraw.Draw(img)
    inset = border + int(H * 0.035)
    inner = (W - 2 * inset, H - 2 * inset)
    gap = max(s, int(H * 0.012))
    radius = corner * 0.62
    if regions:
        for i, (x0, y0, x1, y1) in enumerate(regions[:len(colors)]):
            box = (inset + x0 * inner[0] + gap, inset + y0 * inner[1] + gap,
                   inset + x1 * inner[0] - gap, inset + y1 * inner[1] - gap)
            rgb = _rgb(colors[i])
            d.rounded_rectangle(box, radius=radius, fill=rgb + (255,))
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            short = min(box[3] - box[1], box[2] - box[0])
            icon = icons[i] if icons and i < len(icons) else None
            if icon in AVATARS:
                side = int(short * 0.36)
                mark = _avatar_mask(icon, side)
                img.paste(_rgb(text_color) + (255,), (int(cx - side / 2), int(cy - side * 0.78)), mark)
                d.text((cx, cy + short * 0.2), f"P{i + 1}", font=font(short * 0.2), fill=_rgb(text_color),
                       anchor="mm")
            else:
                d.text((cx, cy), f"P{i + 1}", font=font(short * 0.3), fill=_rgb(text_color), anchor="mm")
    elif colors:
        d.rounded_rectangle((inset + gap, inset + gap, W - inset - gap, H - inset - gap),
                            radius=radius, fill=(255, 255, 255, 20))
        count = len(colors)
        per_row = 4 if count > 4 else count
        rows = (count + per_row - 1) // per_row
        size = int(min(inner[0] / (per_row * 1.3), inner[1] / (rows * 1.4)))
        for i, color in enumerate(colors):
            in_row = min(per_row, count - (i // per_row) * per_row)
            cx = W / 2 + ((i % per_row) - (in_row - 1) / 2) * size * 1.2
            cy = H / 2 + ((i // per_row) - (rows - 1) / 2) * size * 1.2
            d.ellipse((cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 2), fill=_rgb(color) + (255,))
            icon = icons[i] if icons and i < len(icons) else None
            if icon in AVATARS:
                side = int(size * 0.56)
                img.paste(_rgb(text_color) + (255,), (int(cx - side / 2), int(cy - side / 2)),
                          _avatar_mask(icon, side))
            else:
                d.text((cx, cy), f"P{i + 1}", font=font(size * 0.42), fill=_rgb(text_color), anchor="mm")
    return _finish(img, (w, h))


@lru_cache(maxsize=16)
def clock(height, color):
    """Clock face (play time)."""
    h = max(8, int(height))
    s = SUPERSAMPLE
    size = h * s
    rgb = _rgb(color) + (255,)
    line = max(1, int(size * 0.11))
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((line / 2, line / 2, size - line / 2, size - line / 2), outline=rgb, width=line)
    c = size / 2
    d.line((c, c, c, size * 0.24), fill=rgb, width=line)
    d.line((c, c, size * 0.7, size * 0.62), fill=rgb, width=line)
    return _finish(img, (h, h))


# ============================================================================
# PANELS
# ============================================================================
@lru_cache(maxsize=64)
def panel(width, height, radius, fill, outline=None, glow=None):
    """
    Rounded rectangle with a translucent fill.

    Args:
        fill    (tuple): RGBA.
        outline (tuple | None): (color, width) border.
        glow    (tuple | None): (color, size) soft outer glow (the panel is
                                grown by `size` on each side to hold it).
    """
    from PIL import ImageFilter

    width, height = max(2, int(width)), max(2, int(height))
    pad = int(glow[1]) if glow else 0
    s = 2  # panels are large: 2x is enough
    img = Image.new("RGBA", ((width + 2 * pad) * s, (height + 2 * pad) * s), (0, 0, 0, 0))
    box = (pad * s, pad * s, (pad + width) * s - 1, (pad + height) * s - 1)
    if glow:
        # Blur only the alpha mask: blurring RGBA would bleed black into the colour
        mask = Image.new("L", img.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius * s, fill=235)
        halo = Image.new("RGBA", img.size, _rgb(glow[0]) + (0,))
        halo.putalpha(mask.filter(ImageFilter.GaussianBlur(pad * s * 0.4)))
        img = Image.alpha_composite(img, halo)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box, radius=radius * s, fill=tuple(fill))
    if outline:
        color, line = outline
        rgba = tuple(color) if not isinstance(color, str) and len(color) == 4 else _rgb(color) + (255,)
        if rgba[3] < 255:
            # A translucent rim: drawn on its own layer so it blends over the fill
            rim = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(rim).rounded_rectangle(box, radius=radius * s, outline=rgba, width=max(1, int(line * s)))
            img = Image.alpha_composite(img, rim)
        else:
            d.rounded_rectangle(box, radius=radius * s, outline=rgba, width=max(1, int(line * s)))
    return _finish(img, (width + 2 * pad, height + 2 * pad))


@lru_cache(maxsize=96)
def focus_ring(width, height, radius, color, strength=1.0, line=3, pad=24):
    """
    The frame around the focused item: a white rim hugging a width x height
    rounded box and a soft glow of `color` around it, `strength` (0..1)
    strong. The picture is `pad` larger on every side and empty inside.
    """
    from PIL import ImageFilter

    width, height, pad = max(4, int(width)), max(4, int(height)), int(pad)
    s = 2
    size = ((width + 2 * pad) * s, (height + 2 * pad) * s)
    box = (pad * s, pad * s, (pad + width) * s - 1, (pad + height) * s - 1)
    grow = int(line * s * 1.5)
    outer = (box[0] - grow, box[1] - grow, box[2] + grow, box[3] + grow)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(outer, radius=radius * s + grow, fill=int(255 * max(0.0, min(1.0, strength))))
    mask = mask.filter(ImageFilter.GaussianBlur(pad * s * 0.34))
    # Nothing shines through the tile itself
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius * s, fill=0)
    img = Image.new("RGBA", size, _rgb(color) + (0,))
    img.putalpha(mask)
    ImageDraw.Draw(img).rounded_rectangle(outer, radius=radius * s + grow, outline=(255, 255, 255, 255),
                                          width=max(2, int(line * s)))
    return _finish(img, (width + 2 * pad, height + 2 * pad))


@lru_cache(maxsize=8)
def hero_mask(width, height):
    """
    Alpha for the large picture behind a game's title: solid on the right,
    fading out to the left (where the text is) and to the bottom.
    """
    width, height = max(2, int(width)), max(2, int(height))
    ramp = Image.linear_gradient("L")                       # 256x256, black at the top
    across = ramp.rotate(90, expand=True).resize((width, height))       # black on the left
    across = across.point(lambda v: 0 if v < 26 else min(255, int((v - 26) * 2.3)))
    down = ramp.transpose(Image.FLIP_TOP_BOTTOM).resize((width, height))
    down = down.point(lambda v: min(255, int(v * 2.6)))
    from PIL import ImageChops
    return ImageChops.multiply(across, down)


def rounded(image, radius):
    """`image` as RGBA with rounded corners."""
    image = image.convert("RGBA")
    mask = Image.new("L", (image.width * 2, image.height * 2), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, mask.width - 1, mask.height - 1), radius=radius * 2, fill=255)
    image.putalpha(mask.resize(image.size, Image.LANCZOS))
    return image


@lru_cache(maxsize=64)
def disc(text, diameter, fill, text_color="#111111"):
    """A filled circle with centred text (player number badges)."""
    d_px = max(8, int(diameter))
    s = SUPERSAMPLE
    img = Image.new("RGBA", (d_px * s, d_px * s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((0, 0, d_px * s - 1, d_px * s - 1), fill=_rgb(fill) + (255,))
    draw.text((d_px * s / 2, d_px * s * 0.52), text, font=font(d_px * s * 0.42),
              fill=_rgb(text_color), anchor="mm")
    return _finish(img, (d_px, d_px))


@lru_cache(maxsize=8)
def vertical_fade(width, height, alpha_top, alpha_bottom, color=(0, 0, 0)):
    """Band of `color` fading from alpha_top to alpha_bottom (footer backdrop)."""
    column = Image.linear_gradient("L").resize((1, max(1, int(height))))
    column = column.point(lambda v: int(alpha_top + (alpha_bottom - alpha_top) * v / 255))
    band = Image.new("RGBA", (1, max(1, int(height))), tuple(color) + (0,))
    band.putalpha(column)
    return band.resize((max(1, int(width)), max(1, int(height))))


# ============================================================================
# PLAYER AVATARS AND BATTERIES
# ============================================================================
AVATARS = ("star", "heart", "leaf", "bolt", "mushroom", "flame", "moon", "crown", "flower", "ghost")


def _avatar_shape(name, d, S):
    """Draw avatar `name` on the mask `d` (S x S): 255 = shape, 0 = cut out."""
    import math

    def P(*points):
        return [(x * S, y * S) for x, y in points]

    def E(x0, y0, x1, y1, value=255):
        d.ellipse((x0 * S, y0 * S, x1 * S, y1 * S), fill=value)

    if name == "star":
        pts = []
        for i in range(10):
            r = 0.49 if i % 2 == 0 else 0.22
            a = -math.pi / 2 + i * math.pi / 5
            pts.append((0.5 + r * math.cos(a), 0.54 + r * math.sin(a)))
        d.polygon(P(*pts), fill=255)
    elif name == "heart":
        E(0.06, 0.14, 0.52, 0.6)
        E(0.48, 0.14, 0.94, 0.6)
        d.polygon(P((0.09, 0.47), (0.5, 0.92), (0.91, 0.47), (0.5, 0.3)), fill=255)
    elif name == "leaf":
        pts = []
        for i in range(48):                        # a pointed leaf: two arcs
            a = math.pi * i / 47
            pts.append((0.5 + 0.42 * math.cos(a) * 0.98, 0.5 - 0.3 * math.sin(a)))
        pts += [(x, 1 - y) for x, y in reversed(pts)]
        rotated = [(0.5 + (x - 0.5) * 0.7071 - (y - 0.5) * 0.7071 * -1, 0.5 - (x - 0.5) * 0.7071 + (y - 0.5) * 0.7071)
                   for x, y in pts]
        d.polygon(P(*rotated), fill=255)
        d.line(P((0.24, 0.76), (0.66, 0.34)), fill=0, width=max(1, int(S * 0.06)))
        d.line(P((0.08, 0.92), (0.26, 0.74)), fill=255, width=max(1, int(S * 0.08)))
    elif name == "bolt":
        d.polygon(P((0.6, 0.03), (0.18, 0.56), (0.46, 0.56), (0.36, 0.97), (0.82, 0.41), (0.54, 0.41),
                    (0.68, 0.03)), fill=255)
    elif name == "mushroom":
        d.pieslice((0.04 * S, 0.1 * S, 0.96 * S, 0.94 * S), 180, 360, fill=255)
        d.rounded_rectangle((0.3 * S, 0.56 * S, 0.7 * S, 0.94 * S), radius=0.1 * S, fill=255)
        d.rectangle((0.04 * S, 0.5 * S, 0.96 * S, 0.555 * S), fill=0)
        E(0.38, 0.17, 0.62, 0.38, 0)
        E(0.13, 0.32, 0.3, 0.48, 0)
        E(0.7, 0.32, 0.87, 0.48, 0)
    elif name == "flame":
        E(0.2, 0.4, 0.8, 0.96)
        d.polygon(P((0.2, 0.68), (0.34, 0.36), (0.52, 0.04), (0.6, 0.3), (0.74, 0.2), (0.8, 0.68)), fill=255)
        E(0.38, 0.62, 0.62, 0.9, 0)
        d.polygon(P((0.38, 0.76), (0.5, 0.5), (0.62, 0.76)), fill=0)
    elif name == "moon":
        E(0.06, 0.06, 0.94, 0.94)
        E(0.32, -0.04, 1.08, 0.72, 0)
    elif name == "crown":
        d.polygon(P((0.08, 0.8), (0.08, 0.3), (0.3, 0.52), (0.5, 0.16), (0.7, 0.52), (0.92, 0.3), (0.92, 0.8)),
                  fill=255)
        d.rectangle((0.08 * S, 0.84 * S, 0.92 * S, 0.93 * S), fill=255)
        for x, y in ((0.08, 0.27), (0.5, 0.13), (0.92, 0.27)):
            E(x - 0.07, y - 0.07, x + 0.07, y + 0.07)
    elif name == "flower":
        for i in range(5):
            a = -math.pi / 2 + i * 2 * math.pi / 5
            x, y = 0.5 + 0.25 * math.cos(a), 0.5 + 0.25 * math.sin(a)
            E(x - 0.2, y - 0.2, x + 0.2, y + 0.2)
        E(0.37, 0.37, 0.63, 0.63, 0)
        E(0.42, 0.42, 0.58, 0.58)
    elif name == "ghost":
        d.pieslice((0.14 * S, 0.06 * S, 0.86 * S, 0.78 * S), 180, 360, fill=255)
        d.rectangle((0.14 * S, 0.42 * S, 0.86 * S, 0.84 * S), fill=255)
        for i in range(3):                          # the wavy hem
            x = 0.14 + i * 0.24
            E(x, 0.72, x + 0.24, 0.96)
        for x in (0.38, 0.62):
            E(x - 0.07, 0.86, x + 0.07, 1.0, 0)
        E(0.3, 0.32, 0.43, 0.5, 0)
        E(0.57, 0.32, 0.7, 0.5, 0)


@lru_cache(maxsize=64)
def _avatar_mask(name, size):
    """Alpha mask (L) of avatar `name`, size x size."""
    size = max(4, int(size))
    S = size * SUPERSAMPLE
    mask = Image.new("L", (S, S), 0)
    _avatar_shape(name, ImageDraw.Draw(mask), S)
    return mask.resize((size, size), Image.LANCZOS)


@lru_cache(maxsize=64)
def avatar(name, size, color):
    """Avatar `name` in `color` on transparent."""
    mask = _avatar_mask(name, size)
    image = Image.new("RGBA", mask.size, _rgb(color) + (0,))
    image.putalpha(mask)
    return image


@lru_cache(maxsize=64)
def avatar_disc(name, diameter, fill, icon_color="#111111"):
    """A player's disc: their colour with their avatar inside (P-number disc without one)."""
    if name not in AVATARS:
        return disc(name or "", diameter, fill, icon_color)
    image = disc("", diameter, fill, icon_color).copy()
    side = int(max(8, int(diameter)) * 0.56)
    offset = (image.width - side) // 2
    image.paste(_rgb(icon_color) + (255,), (offset, offset), _avatar_mask(name, side))
    return image


@lru_cache(maxsize=64)
def battery(height, level, color, charging=False):
    """
    A battery seen from the side, `level` 0-100 full, in `color` (the outline)
    - the fill turns amber under 35 % and red under 15 %. A bolt when charging.
    """
    h = max(6, int(height))
    w = int(h * 1.9)
    s = SUPERSAMPLE
    W, H = w * s, h * s
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rgb = _rgb(color) + (255,)
    line = max(s, int(H * 0.12))
    nub = int(W * 0.08)
    d.rounded_rectangle((0, 0, W - nub - 1, H - 1), radius=H * 0.22, outline=rgb, width=line)
    d.rounded_rectangle((W - nub, H * 0.3, W - 1, H * 0.7), radius=nub * 0.4, fill=rgb)
    level = max(0, min(100, int(level)))
    fill = (255, 82, 82, 255) if level < 15 else (255, 196, 60, 255) if level < 35 else rgb
    inner = line + s
    right = inner + (W - nub - 2 * inner) * max(0.08, level / 100)
    d.rounded_rectangle((inner, inner, right, H - inner - 1), radius=H * 0.1, fill=fill)
    if charging:
        bolt = [(0.56, 0.08), (0.36, 0.56), (0.5, 0.56), (0.44, 0.92), (0.66, 0.42), (0.52, 0.42), (0.6, 0.08)]
        d.polygon([(x * (W - nub), y * H) for x, y in bolt], fill=(20, 22, 28, 255))
    return _finish(img, (w, h))
