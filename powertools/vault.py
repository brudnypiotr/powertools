"""One secret in one file: encrypted with Windows DPAPI for this user; elsewhere a plain file only the user can read."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    src = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    dst = Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    UI_FORBIDDEN = 0x1
    if not fn(ctypes.byref(src), None, None, None, None, UI_FORBIDDEN, ctypes.byref(dst)):
        raise OSError("DPAPI failed")
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(dst.pbData)


class SecretFile:
    """Subclasses say in `check` what a right secret looks like."""

    def __init__(self, path: Path):
        self.path = path
        self._secret = self._read()

    def _read(self) -> str:
        try:
            raw = self.path.read_bytes()
            return (dpapi(raw, False) if sys.platform == "win32" else raw).decode("utf-8")
        except (OSError, ValueError):       # not there, or written by another Windows user
            return ""

    def check(self, secret: str) -> None:
        """Raises ValueError when this cannot be the secret."""

    def get(self) -> str:
        return self._secret

    def set(self, secret: str) -> None:
        secret = secret.strip()
        if secret.lower().startswith("bearer "):
            secret = secret[7:].strip()
        self.check(secret)
        data = secret.encode("utf-8")
        self.path.write_bytes(dpapi(data, True) if sys.platform == "win32" else data)
        if sys.platform != "win32":
            os.chmod(self.path, 0o600)
        self._secret = secret

    def clear(self) -> None:
        self._secret = ""
        self.path.unlink(missing_ok=True)
