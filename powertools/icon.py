"""The powertools icon: a dark rounded square with a red recording dot and three sound bars.

The tray version keeps the shape and changes the background with the state, so it reads at 16 px:
idle dark, recording red, transcribing amber, meeting purple, loading/error grey.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

SS = 4
BACKS = {
    "idle": ((46, 48, 66), (24, 25, 36)),
    "recording": ((240, 70, 82), (196, 30, 46)),
    "transcribing": ((246, 184, 60), (214, 138, 18)),
    "meeting": ((150, 92, 230), (102, 52, 180)),
    "loading": ((120, 122, 132), (84, 86, 96)),
    "error": ((96, 96, 104), (64, 64, 70)),
}
RED = (255, 72, 84, 255)
WHITE = (246, 246, 250, 255)


def draw(size: int = 256, state: str = "idle") -> Image.Image:
    s = size * SS
    top, bottom = BACKS.get(state, BACKS["idle"])
    grad = Image.new("RGBA", (1, s))
    for y in range(s):
        t = y / (s - 1)
        grad.putpixel((0, y), tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,))
    grad = grad.resize((s, s))
    mask = Image.new("L", (s, s), 0)
    pad = int(s * 0.04)
    ImageDraw.Draw(mask).rounded_rectangle((pad, pad, s - pad, s - pad), radius=int(s * 0.24), fill=255)
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    cy = s / 2
    # recording dot (white on the coloured backgrounds, red on the dark one)
    dot = RED if state in ("idle", "loading", "error") else WHITE
    r = s * 0.13
    cx = s * 0.33
    if state == "idle":  # soft glow, blended over the background
        g = r * 1.45
        glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse((cx - g, cy - g, cx + g, cy + g), fill=(255, 72, 84, 70))
        img.alpha_composite(glow)
        d = ImageDraw.Draw(img)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=dot)
    # three bars
    bw = s * 0.075
    for i, h in enumerate((0.22, 0.40, 0.28)):
        x = s * 0.56 + i * s * 0.12
        d.rounded_rectangle((x, cy - s * h / 2, x + bw, cy + s * h / 2), radius=bw / 2, fill=WHITE)
    return img.resize((size, size), Image.LANCZOS)


def save_ico(path: str) -> None:
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    draw(256).save(path, sizes=[(n, n) for n in sizes])
