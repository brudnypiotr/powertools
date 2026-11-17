"""Global hotkey through the keyboard library (a user-level hook; no admin rights needed, checked on the laptop)."""

from __future__ import annotations

import logging
import threading

log = logging.getLogger("powertools.hotkey")


class Hotkey:
    def __init__(self, callback):
        self.callback = callback
        self.combo = ""
        self._handle = None

    def bind(self, combo: str, suppress: bool = True) -> None:
        import keyboard

        if self._handle is not None:
            keyboard.remove_hotkey(self._handle)
            self._handle = None
        # suppress=True keeps the key (e.g. the "q" of Alt+Q) out of the focused app (False: the app gets it too);
        # the callback runs in its own thread so the keyboard hook never waits for audio code
        self._handle = keyboard.add_hotkey(combo, lambda: threading.Thread(target=self.callback, daemon=True).start(),
                                           suppress=suppress, trigger_on_release=False)
        self.combo = combo
        log.info("hotkey bound: %s", combo)

    def unbind(self) -> None:
        if self._handle is not None:
            import keyboard

            keyboard.remove_hotkey(self._handle)
            self._handle = None
