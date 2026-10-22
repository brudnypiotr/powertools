"""Tray icon: its colour shows the state; the menu toggles dictation, opens the window or quits."""

from __future__ import annotations

COLORS = {"idle": (90, 150, 230), "recording": (224, 36, 44), "transcribing": (240, 160, 32),
          "loading": (150, 150, 150), "error": (110, 110, 110)}


def icon_image(state: str):
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((6, 6, 58, 58), fill=COLORS.get(state, COLORS["idle"]) + (255,), outline=(255, 255, 255, 255), width=4)
    return img


class Tray:
    def __init__(self, title: str, hotkey: str, on_toggle, on_open, on_quit):
        import pystray

        self._pystray = pystray
        self.hotkey = hotkey
        menu = pystray.Menu(
            pystray.MenuItem(lambda _item: f"Dictate ({self.hotkey})", lambda: on_toggle()),
            pystray.MenuItem("Open window", lambda: on_open(), default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda: on_quit()),
        )
        self.icon = pystray.Icon("powertools", icon_image("loading"), title, menu)

    def start(self) -> None:
        self.icon.run_detached()

    def set_state(self, state: str, tooltip: str = "") -> None:
        self.icon.icon = icon_image(state)
        if tooltip:
            self.icon.title = tooltip[:120]

    def stop(self) -> None:
        self.icon.stop()
