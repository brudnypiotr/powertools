"""Pure drawing for the overlay (PIL, no windowing): the pill and the "Record?" card.

Drawn at 3x and scaled down for smooth edges. Kept separate from the Win32 code so it can be tested and
previewed on any system.
"""

from __future__ import annotations

import math
import os
import textwrap

from PIL import Image, ImageDraw, ImageFont

SS = 3  # supersampling
PILL_W, PILL_H = 148, 40
CARD_W = 340
BG = (22, 23, 28, 238)
BORDER = (255, 255, 255, 28)
RED = (255, 69, 80, 255)
AMBER = (255, 184, 48, 255)
WHITE = (242, 242, 246, 255)
MUTED = (160, 164, 176, 255)
BTN = (52, 54, 62, 255)
BTN_HOVER = (70, 72, 82, 255)
RED_HOVER = (255, 98, 108, 255)


def _font(size: int, bold: bool = False):
    windir = os.environ.get("WINDIR", r"C:\Windows")
    names = ["seguisb.ttf", "segoeuib.ttf"] if bold else ["segoeui.ttf"]
    names += ["DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", "Arial Bold.ttf" if bold else "Arial.ttf"]
    for n in names:
        for d in (os.path.join(windir, "Fonts"), "/Library/Fonts", "/System/Library/Fonts/Supplemental", ""):
            try:
                return ImageFont.truetype(os.path.join(d, n) if d else n, size)
            except OSError:
                continue
    return ImageFont.load_default()


def pill(state: str, t: float, levels: list[float], scale: float = 1.0) -> Image.Image:
    """state: recording | transcribing | error. t: seconds since the state began. levels: recent mic levels 0..1."""
    k = scale * SS
    w, h = int(PILL_W * k), int(PILL_H * k)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = h // 2
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=r, fill=BG, outline=BORDER, width=max(1, int(k)))
    cy = h / 2
    if state == "recording":
        cx = 22 * k
        pulse = (6 + 1.6 * (1 + math.sin(t * 5.5)) / 2) * k
        halo = pulse + 5 * k * (0.6 + 0.4 * (1 + math.sin(t * 5.5)) / 2)
        glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse((cx - halo, cy - halo, cx + halo, cy + halo), fill=(255, 69, 80, 70))
        img.alpha_composite(glow)
        d = ImageDraw.Draw(img)
        d.ellipse((cx - pulse, cy - pulse, cx + pulse, cy + pulse), fill=RED)
        n = 11
        x0, gap, bw = 42 * k, 8.6 * k, 4 * k
        for i in range(n):
            lv = levels[-n + i] if len(levels) >= n - i else 0.0
            idle = 0.10 + 0.05 * math.sin(t * 4.5 + i * 0.7)
            bh = (5 + (PILL_H - 16) * max(min(lv, 1.0), idle)) * k
            x = x0 + i * gap
            d.rounded_rectangle((x, cy - bh / 2, x + bw, cy + bh / 2), radius=bw / 2, fill=WHITE)
    elif state == "transcribing":
        for i in range(3):
            phase = t * 6 - i * 0.8
            dy = 5 * k * math.sin(phase)
            a = int(150 + 105 * (1 + math.sin(phase)) / 2)
            cx = w / 2 + (i - 1) * 18 * k
            rr = 5 * k
            d.ellipse((cx - rr, cy - rr - dy, cx + rr, cy + rr - dy), fill=AMBER[:3] + (a,))
    elif state == "error":
        f = _font(int(13 * k), bold=True)
        d.text((w / 2, cy), "!  not available", fill=MUTED, font=f, anchor="mm")
    return img.resize((int(PILL_W * scale), int(PILL_H * scale)), Image.LANCZOS)


def card(title: str, text: str, hover: str = "", scale: float = 1.0) -> tuple[Image.Image, dict]:
    """The "Record?" card. Returns the image and the button boxes {name: (x0, y0, x1, y1)} in output pixels."""
    k = scale * SS
    f_small, f_text, f_btn = _font(int(12 * k)), _font(int(15 * k), bold=True), _font(int(13 * k), bold=True)
    lines = textwrap.wrap(text, 38)[:3]
    pad = 16 * k
    h = int((16 + 18 + 8 + 22 * len(lines) + 14 + 34 + 16) * k)
    w = int(CARD_W * k)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=14 * k, fill=BG, outline=BORDER, width=max(1, int(k)))
    y = 16 * k
    d.ellipse((pad, y + 4 * k, pad + 9 * k, y + 13 * k), fill=RED)
    d.text((pad + 16 * k, y), title, fill=MUTED, font=f_small)
    y += 26 * k
    for line in lines:
        d.text((pad, y), line, fill=WHITE, font=f_text)
        y += 22 * k
    y += 14 * k
    bh, gap = 34 * k, 8 * k
    boxes = {}
    x1 = w - pad
    for name, label, base, hov in (("yes", "Record", RED, RED_HOVER), ("no", "Not now", BTN, BTN_HOVER)):
        tw = d.textlength(label, font=f_btn) + 28 * k
        x0 = x1 - tw
        d.rounded_rectangle((x0, y, x1, y + bh), radius=9 * k, fill=hov if hover == name else base)
        d.text(((x0 + x1) / 2, y + bh / 2), label, fill=WHITE, font=f_btn, anchor="mm")
        boxes[name] = tuple(int(v / SS) for v in (x0, y, x1, y + bh))
        x1 = x0 - gap
    out = img.resize((int(w / SS), int(h / SS)), Image.LANCZOS)
    return out, boxes
