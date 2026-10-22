"""Deliver text into the focused window: clipboard + Ctrl+V, then put the previous clipboard back."""

from __future__ import annotations

import logging
import time

log = logging.getLogger("powertools.paste")


def deliver(text: str, paste: bool = True, restore: bool = True) -> None:
    import pyperclip

    previous = None
    if paste and restore:
        try:
            previous = pyperclip.paste()
        except Exception:
            log.warning("could not read the clipboard; it will not be restored")
    pyperclip.copy(text)
    if not paste:
        return
    import keyboard

    time.sleep(0.05)
    keyboard.send("ctrl+v")
    if previous is not None:
        time.sleep(0.3)  # the target app reads the clipboard asynchronously
        pyperclip.copy(previous)
