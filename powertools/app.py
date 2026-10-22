"""Wiring: settings, history, Whisper engine, recorder, dictation, hotkey, tray, overlay and the window.

Threads: tkinter overlay on the main thread, tray detached, web server, hotkey hook, Whisper worker.
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import sys
import threading
import time
import urllib.request
import webbrowser

from . import __version__
from .config import Settings, data_dir
from .store import Store

log = logging.getLogger("powertools")


def setup_logging(console: bool) -> None:
    handlers: list[logging.Handler] = [logging.handlers.RotatingFileHandler(
        data_dir() / "app.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")]
    if console and sys.stderr:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")


def running_instance() -> int:
    """Port of an already running copy, else 0."""
    f = data_dir() / "port.txt"
    try:
        port = int(f.read_text().strip())
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1) as r:
            if json.loads(r.read()).get("app") == "powertools":
                return port
    except Exception:
        pass
    return 0


class App:
    def __init__(self, selftest: bool = False):
        from .audio import Recorder
        from .dictation import Dictation
        from .engine import Engine
        from .paste import deliver

        self.selftest = selftest
        self.settings = Settings()
        self.store = Store(data_dir() / "history.sqlite")
        self.engine = Engine(self.settings["model"])
        self.recorder = Recorder()
        self.overlay = None
        self.tray = None
        self.hotkey = None
        self.httpd = None
        self.url = ""
        self.dictation = Dictation(self.settings, self.engine, self.recorder, self.store, deliver, self._on_state)
        self._quit = threading.Event()

    # ------------------------------------------------------------------ used by the API
    def state(self) -> dict:
        return {
            "version": __version__,
            "dictation": self.dictation.state,
            "last": self.dictation.last,
            "level": round(self.recorder.level, 4),
            "engine": self.engine.status(),
            "hotkey": self.settings["hotkey"],
            "overlay": bool(self.overlay and self.overlay.available),
            "python": sys.version.split()[0],
            "data_dir": str(data_dir()),
        }

    def apply_settings(self, changes: dict) -> dict:
        if "hotkey" in changes and self.hotkey is not None:
            new = str(changes["hotkey"]).strip().lower()
            old = self.settings["hotkey"]
            if new != old:
                try:
                    self.hotkey.bind(new)
                except Exception as e:
                    self.hotkey.bind(old)
                    raise ValueError(f"hotkey '{new}' not accepted: {e}") from e
        changed = self.settings.update(changes)
        if "model" in changed:
            self.engine.reload(changed["model"])
            self._watch_engine()
        if "hotkey" in changed and self.tray:
            self.tray.hotkey = changed["hotkey"]
            self.tray.icon.update_menu()
        return changed

    def input_devices(self) -> list[str]:
        try:
            from .audio import input_devices
            return input_devices()
        except Exception as e:
            log.warning("cannot list devices: %s", e)
            return []

    def log_tail(self, n: int) -> list[str]:
        f = data_dir() / "app.log"
        if not f.exists():
            return []
        return f.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]

    def open_window(self) -> None:
        webbrowser.open(self.url)

    def quit(self) -> None:
        self._quit.set()
        if self.overlay:
            self.overlay.quit()

    # ------------------------------------------------------------------ state display
    def _on_state(self, state: str) -> None:
        if self.overlay:
            self.overlay.show(state)
        if self.tray:
            self.tray.set_state(state, f"powertools: {state}")

    def _watch_engine(self) -> None:
        def watch():
            while self.engine.state in ("idle", "loading") and not self._quit.is_set():
                if self.tray:
                    self.tray.set_state("loading", "powertools: loading Whisper ...")
                time.sleep(0.5)
            ok = self.engine.state == "ready"
            if self.tray:
                self.tray.set_state("idle" if ok else "error",
                                    f"powertools: ready ({self.settings['hotkey']})" if ok
                                    else "powertools: Whisper failed, see the window")
        threading.Thread(target=watch, name="engine-watch", daemon=True).start()

    # ------------------------------------------------------------------ lifecycle
    def run(self, open_browser: bool) -> int:
        from .hotkey import Hotkey
        from .overlay import make_overlay
        from .tray import Tray
        from .web.server import serve

        self.httpd = serve(self)
        port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{port}/"
        (data_dir() / "port.txt").write_text(str(port))
        log.info("powertools %s on %s", __version__, self.url)

        self.overlay = make_overlay(lambda: self.recorder.level)
        if not self.overlay.available:
            log.warning("tkinter missing: no recording dot, the tray icon shows the state")
        self.engine.start()
        self.hotkey = Hotkey(self.dictation.toggle)
        try:
            self.hotkey.bind(self.settings["hotkey"])
        except Exception as e:
            log.error("hotkey %s failed: %s", self.settings["hotkey"], e)
        self.tray = Tray("powertools", self.settings["hotkey"], self.dictation.toggle, self.open_window, self.quit)
        self.tray.start()
        self._watch_engine()
        if open_browser and self.settings["open_window_on_start"]:
            self.open_window()

        try:
            self.overlay.run()  # blocks until quit()
        finally:
            self._quit.set()
            if self.hotkey:
                self.hotkey.unbind()
            if self.recorder.active:
                self.recorder.stop()
            self.tray.stop()
            self.engine.stop()
            self.httpd.shutdown()
            try:
                (data_dir() / "port.txt").unlink()
            except OSError:
                pass
            self.store.close()
        return 0

    def run_selftest(self) -> int:
        """No GPU, audio, hotkey or tray: starts the window server and calls the API (used by CI)."""
        from .web.server import serve

        self.httpd = serve(self)
        base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

        def call(method: str, path: str, body: dict | None = None):
            req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body else None,
                                         headers={"X-Powertools": "1", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read())

        checks = {
            "ping": call("GET", "/api/ping")["app"] == "powertools",
            "state": call("GET", "/api/state")["version"] == __version__,
            "settings": "hotkey" in call("GET", "/api/settings"),
            "history": isinstance(call("GET", "/api/history"), list),
            "page": b"powertools" in urllib.request.urlopen(base + "/", timeout=5).read(),
        }
        self.httpd.shutdown()
        self.store.close()
        for name, ok in checks.items():
            print(f"[{'OK' if ok else 'FAIL'}] selftest.{name}")
        return 0 if all(checks.values()) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="powertools")
    ap.add_argument("--console", action="store_true", help="also log to the console")
    ap.add_argument("--no-browser", action="store_true", help="do not open the window on start")
    ap.add_argument("--selftest", action="store_true", help="start the window server, call the API, exit")
    args = ap.parse_args(argv)
    setup_logging(args.console or args.selftest)

    if args.selftest:
        return App(selftest=True).run_selftest()
    port = running_instance()
    if port:
        webbrowser.open(f"http://127.0.0.1:{port}/")  # already running: just show it
        return 0
    try:
        return App().run(open_browser=not args.no_browser)
    except Exception:
        log.exception("powertools crashed")
        raise
