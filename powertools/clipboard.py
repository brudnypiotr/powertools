"""The clipboard around a paste: what the user had copied comes back whole, pictures and formatted text too.

On Windows every format that lives in global memory is copied (text, HTML, RTF, bitmaps as DIB, PNG, file lists); handles of
other kinds (GDI bitmaps and metafiles, owner-drawn) are left out: Windows makes the bitmap again from the DIB.
Anything that goes wrong falls back to plain text. Other systems: text only.
"""

from __future__ import annotations

import logging
import sys
import time

log = logging.getLogger("powertools.clipboard")
MAX_BYTES = 60_000_000
NOT_MEMORY = {2, 3, 9, 14, 0x80, 0x82, 0x83, 0x8E}      # BITMAP, METAFILEPICT, PALETTE, ENHMETAFILE, OWNERDISPLAY, DSP*
GMEM_MOVEABLE = 0x0002
TRIES = 10


def save():
    """What is in the clipboard now, for restore(); None when it cannot be read."""
    if sys.platform == "win32":
        try:
            return _read()
        except Exception as e:
            log.warning("clipboard: only the text is kept (%s)", e)
    import pyperclip
    try:
        return pyperclip.paste()
    except Exception as e:
        log.warning("could not read the clipboard (%s); it will not be restored", e)
        return None


def restore(saved) -> None:
    if saved is None:
        return
    if isinstance(saved, list):
        try:
            return _write(saved)
        except Exception as e:
            log.warning("could not put the clipboard back (%s)", e)
            return
    import pyperclip
    pyperclip.copy(saved)


# ---------------------------------------------------------------- Windows
def _api():
    import ctypes
    from ctypes import wintypes as w

    u, k = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("kernel32", use_last_error=True)
    for fn, args, res in ((u.OpenClipboard, [w.HWND], w.BOOL), (u.CloseClipboard, [], w.BOOL), (u.EmptyClipboard, [], w.BOOL),
                          (u.EnumClipboardFormats, [w.UINT], w.UINT), (u.GetClipboardData, [w.UINT], w.HANDLE),
                          (u.SetClipboardData, [w.UINT, w.HANDLE], w.HANDLE), (k.GlobalLock, [w.HGLOBAL], w.LPVOID),
                          (k.GlobalUnlock, [w.HGLOBAL], w.BOOL), (k.GlobalSize, [w.HGLOBAL], ctypes.c_size_t),
                          (k.GlobalAlloc, [w.UINT, ctypes.c_size_t], w.HGLOBAL), (k.GlobalFree, [w.HGLOBAL], w.HGLOBAL)):
        fn.argtypes, fn.restype = args, res
    return u, k


class _Open:
    """The clipboard belongs to one program at a time: try for a moment."""

    def __init__(self, user32):
        self.u = user32

    def __enter__(self):
        for _ in range(TRIES):
            if self.u.OpenClipboard(None):
                return self
            time.sleep(0.03)
        raise OSError("another program holds the clipboard")

    def __exit__(self, *exc):
        self.u.CloseClipboard()


def _read() -> list[tuple[int, bytes]]:
    import ctypes

    u, k = _api()
    out, size, fmt = [], 0, 0
    with _Open(u):
        while fmt := u.EnumClipboardFormats(fmt):
            if fmt in NOT_MEMORY:
                continue
            handle = u.GetClipboardData(fmt)
            n = k.GlobalSize(handle) if handle else 0
            if not n or size + n > MAX_BYTES:
                continue
            p = k.GlobalLock(handle)
            if not p:
                continue
            try:
                out.append((fmt, ctypes.string_at(p, n)))
                size += n
            finally:
                k.GlobalUnlock(handle)
    return out


def _write(formats: list[tuple[int, bytes]]) -> None:
    import ctypes

    u, k = _api()
    with _Open(u):
        u.EmptyClipboard()
        for fmt, data in formats:
            handle = k.GlobalAlloc(GMEM_MOVEABLE, len(data))
            p = k.GlobalLock(handle) if handle else None
            if not p:
                continue
            ctypes.memmove(p, data, len(data))
            k.GlobalUnlock(handle)
            if not u.SetClipboardData(fmt, handle):      # else the system owns the memory now
                k.GlobalFree(handle)
