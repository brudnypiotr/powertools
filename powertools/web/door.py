"""The key of the window: without it 127.0.0.1 answers no other user and no other program of this computer.

The key is made once and kept in a file only this Windows user can read. The app opens its window with the key in the
link; the server turns it into a cookie and takes it out of the address. /api/ping (a second start looks for the first
one), /api/quit (setup closes the app) and /api/unlock (opens a window with the key) work without it and tell nothing.
"""

from __future__ import annotations

import os
import secrets
from http.cookies import CookieError, SimpleCookie
from pathlib import Path

from .. import files

COOKIE = "powertools"
OPEN = ("/api/ping", "/api/quit", "/api/unlock")


class Door:
    def __init__(self, path: Path):
        self.path = path
        self.key = files.read_text(path).strip()
        if len(self.key) < 30:
            self.key = secrets.token_urlsafe(32)
            files.write_text(path, self.key)
            os.chmod(path, 0o600)

    @property
    def seen(self) -> bool:
        """A window came in with the cookie once, so the installed app (which has no link) can be used."""
        return self.path.with_suffix(".seen").exists()

    def link(self, base: str) -> str:
        return f"{base}?t={self.key}"

    def is_key(self, value: str) -> bool:
        return secrets.compare_digest(value.encode(), self.key.encode())

    def admits(self, headers) -> bool:
        if self.is_key(headers.get("X-Powertools-Key", "")):
            return True
        try:
            cookie = SimpleCookie(headers.get("Cookie", "")).get(COOKIE)
        except CookieError:
            return False
        ok = cookie is not None and self.is_key(cookie.value)
        if ok and not self.seen:
            self.path.with_suffix(".seen").touch()
        return ok

    def cookie(self) -> str:
        return f"{COOKIE}={self.key}; Path=/; HttpOnly; SameSite=Strict; Max-Age=34560000"
