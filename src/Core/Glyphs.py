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
    small = image.resize((40, 40), Image.BOX, reducing_gap=2.0).convert("RGB")
    buckets = {}
    for r, g, b in small.getdata():
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

    Names: a b x y (face buttons), lb rb (bumpers), ls rs (stick clicks),
    start, back, dpad.
    """
    h = max(8, int(height))
    s = SUPERSAMPLE
    if name in ("lb", "rb"):
        w = int(h * 1.7)
        img = Image.new("RGBA", (w * s, h * s), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((0, 0, w * s - 1, h * s - 1), radius=h * s // 2, fill=NEUTRAL + (255,))
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
        d.rounded_rectangle(box, radius=radius * s, outline=_rgb(color) + (255,), width=int(line * s))
    return _finish(img, (width + 2 * pad, height + 2 * pad))


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
def vertical_fade(width, height, alpha_top, alpha_bottom):
    """Black band fading from alpha_top to alpha_bottom (footer backdrop)."""
    column = Image.linear_gradient("L").resize((1, max(1, int(height))))
    column = column.point(lambda v: int(alpha_top + (alpha_bottom - alpha_top) * v / 255))
    band = Image.new("RGBA", (1, max(1, int(height))), (0, 0, 0, 0))
    band.putalpha(column)
    return band.resize((max(1, int(width)), max(1, int(height))))
