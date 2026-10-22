"""The Graph Explorer token: pasted in the window, kept encrypted with Windows DPAPI (current user only).

On other systems (tests, development) it falls back to a plain file readable only by the user.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path


def claims(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def _dpapi(data: bytes, protect: bool) -> bytes:
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


class TokenStore:
    def __init__(self, path: Path):
        self.path = path
        self._token = ""
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = self.path.read_bytes()
            self._token = (_dpapi(raw, False) if sys.platform == "win32" else raw).decode("utf-8")
        except Exception:
            self._token = ""

    def set(self, token: str) -> None:
        token = token.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if token.count(".") != 2:
            raise ValueError("this does not look like an access token (copy it from the 'Access token' tab)")
        data = token.encode("utf-8")
        self.path.write_bytes(_dpapi(data, True) if sys.platform == "win32" else data)
        if sys.platform != "win32":
            os.chmod(self.path, 0o600)
        self._token = token

    def clear(self) -> None:
        self._token = ""
        try:
            self.path.unlink()
        except OSError:
            pass

    def get(self) -> str:
        return self._token if self.expires_in() > 60 else ""

    def expires_in(self) -> float:
        """Seconds until the token expires (0 when missing or expired)."""
        if not self._token:
            return 0
        return max(0.0, claims(self._token).get("exp", 0) - time.time())

    def scopes(self) -> list[str]:
        return sorted(claims(self._token).get("scp", "").split()) if self._token else []
