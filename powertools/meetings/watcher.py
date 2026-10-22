"""Notices Teams calls: Windows records which apps use the microphone right now (HKCU, no admin rights;
checked on the laptop: 'MSTeams' shows up during a call). Polls every few seconds and reports transitions."""

from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Callable

log = logging.getLogger("powertools.watcher")
KEY = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"


def apps_using_mic() -> list[str]:
    if sys.platform != "win32":
        return []
    import winreg

    active: list[str] = []

    def visit(path: str, label) -> None:
        try:
            root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            return
        i = 0
        while True:
            try:
                name = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            if name == "NonPackaged":
                continue
            try:
                with winreg.OpenKey(root, name) as k:
                    start = winreg.QueryValueEx(k, "LastUsedTimeStart")[0]
                    stop = winreg.QueryValueEx(k, "LastUsedTimeStop")[0]
            except OSError:
                continue
            if start and not stop:
                active.append(label(name))

    visit(KEY, lambda n: n.split("_")[0])
    visit(KEY + r"\NonPackaged", lambda n: n.split("#")[-1])
    return active


def teams_in_call(apps: list[str]) -> bool:
    return any("teams" in a.lower() for a in apps)


class Watcher:
    """Calls on_start() when Teams starts using the microphone and on_end() after it has stopped for `grace` s."""

    def __init__(self, on_start: Callable[[], None], on_end: Callable[[], None], probe=apps_using_mic,
                 interval: float = 3.0, grace: float = 20.0):
        self.on_start, self.on_end, self.probe = on_start, on_end, probe
        self.interval, self.grace = interval, grace
        self.in_call = False
        self._last_seen = 0.0
        self._stop = threading.Event()

    def step(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        try:
            active = teams_in_call(self.probe())
        except Exception:
            log.exception("microphone probe failed")
            return
        if active:
            self._last_seen = now
            if not self.in_call:
                self.in_call = True
                self.on_start()
        elif self.in_call and now - self._last_seen >= self.grace:
            self.in_call = False
            self.on_end()

    def start(self) -> None:
        def loop():
            while not self._stop.wait(self.interval):
                self.step()
        threading.Thread(target=loop, name="watcher", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
