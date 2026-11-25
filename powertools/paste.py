"""Deliver text into the focused window: clipboard + Ctrl+V, then put the previous clipboard back."""

from __future__ import annotations

import time

from . import clipboard


def deliver(text: str, paste: bool = True, restore: bool = True) -> None:
    import pyperclip

    previous = clipboard.save() if paste and restore else None
    pyperclip.copy(text)
    if not paste:
        return
    import keyboard

    time.sleep(0.05)
    keyboard.send("ctrl+v")
    if previous is not None:
        time.sleep(0.3)  # the target app reads the clipboard asynchronously
        clipboard.restore(previous)
