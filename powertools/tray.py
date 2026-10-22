"""Tray icon: its colour shows the state; the menu toggles dictation, opens the window or quits."""

from __future__ import annotations

def icon_image(state: str):
    from .icon import draw

    return draw(64, state)


class Tray:
    def __init__(self, title: str, hotkey: str, on_toggle, on_open, on_quit, meeting=None):
        """meeting: (is_recording, on_start_stop, on_mark) callables, or None."""
        import pystray

        self._pystray = pystray
        self.hotkey = hotkey
        recording, start_stop, mark = meeting or (lambda: False, lambda: None, lambda: None)
        menu = pystray.Menu(
            pystray.MenuItem(lambda _item: f"Dictate ({self.hotkey})", lambda: on_toggle()),
            pystray.MenuItem(lambda _item: "Stop meeting recording" if recording() else "Record meeting",
                             lambda: start_stop()),
            pystray.MenuItem("Mark this moment", lambda: mark(), visible=lambda _item: recording()),
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

    def notify(self, title: str, message: str) -> None:
        """A Windows notification from the tray icon."""
        try:
            self.icon.notify(message[:250], title[:60])
        except Exception:
            pass

    def stop(self) -> None:
        self.icon.stop()
