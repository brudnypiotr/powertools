"""The powertools icon: a plain red dot in the middle, on a transparent background.

Tray states keep the dot: idle red, recording red with a ring, transcribing amber, meeting purple, loading/error grey.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

SS = 4
COLORS = {"idle": (232, 52, 64), "recording": (232, 52, 64), "transcribing": (240, 160, 30),
          "meeting": (140, 82, 224), "loading": (150, 150, 158), "error": (120, 120, 128)}
DOT = 0.28       # radius as a share of the icon size (diameter 56 %)


def draw(size: int = 256, state: str = "idle") -> Image.Image:
    s = size * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c, color = s / 2, COLORS.get(state, COLORS["idle"]) + (255,)
    r = s * (DOT - 0.06 if state == "recording" else DOT)
    if state == "recording":
        ring, w = s * 0.44, s * 0.07
        d.ellipse((c - ring, c - ring, c + ring, c + ring), outline=color, width=int(w))
    d.ellipse((c - r, c - r, c + r, c + r), fill=color)
    return img.resize((size, size), Image.LANCZOS)


def save_ico(path: str) -> None:
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    draw(256).save(path, sizes=[(n, n) for n in sizes])
