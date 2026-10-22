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
PORT = 48613


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


def web_app_id(manifest_id: str) -> str:
    """Chromium's id of an installed web app: sha256 of the manifest id, first 32 hex digits mapped to a-p."""
    import hashlib

    return "".join(chr(ord("a") + int(c, 16)) for c in hashlib.sha256(manifest_id.encode()).hexdigest()[:32])


def installed_profile(app_id: str) -> str:
    """The Edge profile folder in which the window is installed as an app, or ''."""
    import os
    from pathlib import Path

    user_data = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "User Data"
    for prof in sorted(user_data.glob("*")) if user_data.is_dir() else []:
        if (prof / "Web Applications" / "Manifest Resources" / app_id).is_dir():
            return prof.name
    return ""


def open_app_window(url: str) -> bool:
    """The window without browser bars: the installed app (own taskbar icon) when the user installed it,
    else Edge in app mode. False when Edge is not found."""
    if sys.platform != "win32":
        return False
    import os
    import subprocess

    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        exe = os.path.join(base or "", "Microsoft", "Edge", "Application", "msedge.exe")
        if base and os.path.exists(exe):
            app_id = web_app_id(f"http://127.0.0.1:{PORT}/")
            profile = installed_profile(app_id) if url.startswith(f"http://127.0.0.1:{PORT}/") else ""
            if profile:
                args = [exe, f"--profile-directory={profile}", f"--app-id={app_id}"]
            else:
                args = [exe, f"--app={url}", "--window-size=1000,680"]
            log.info("window: %s", " ".join(args[1:]))
            subprocess.Popen(args, close_fds=True)
            return True
    return False


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
        from .graph.service import GraphService

        from .meetings.manager import MeetingManager

        self.graph = GraphService(self.settings, self.store)
        from .jira.service import JiraService

        self.jira = JiraService(self.settings, self.graph.jobs)
        self.meetings = MeetingManager(self.settings, self.engine, self._meeting_changed, self._meeting_event)
        self.watcher = None
        self.pending_prompt: dict = {}
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
            "meeting": self.meetings.current.view() if self.meetings.current else None,
            "pending_prompt": self.pending_prompt,
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

    def busy(self) -> str:
        """What an update would interrupt, or ''."""
        if self.dictation.state in ("recording", "transcribing"):
            return "dictation in progress"
        if self.meetings.current:
            return "a meeting is being recorded"
        if any(j.state in ("queued", "running") for j in self.graph.jobs.all):
            return "an export is running"
        return ""

    def log_tail(self, n: int) -> list[str]:
        f = data_dir() / "app.log"
        if not f.exists():
            return []
        return f.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]

    def open_window(self) -> None:
        if self.settings["app_window"] and open_app_window(self.url):
            return
        webbrowser.open(self.url)

    def quit(self) -> None:
        self._quit.set()
        if self.overlay:
            self.overlay.quit()

    # ------------------------------------------------------------------ meetings
    def meeting_devices(self) -> dict:
        from .meetings.devices import meeting_devices
        try:
            return meeting_devices()
        except Exception as e:
            log.warning("cannot list meeting devices: %s", e)
            return {"inputs": [], "outputs": []}

    def start_meeting(self, title: str = "", event: dict | None = None, auto: bool = False):
        self.pending_prompt = {}
        if event is None:
            event = self._calendar_event()
        return self.meetings.start(title, event, auto)

    def toggle_meeting(self) -> None:
        if self.meetings.current:
            self.meetings.stop()
        else:
            self.start_meeting()

    def _calendar_event(self) -> dict:
        try:
            return self.graph.current_event()
        except Exception as e:  # no token, expired token, network: record without metadata
            log.info("no calendar metadata: %s", e)
            return {}

    def _call_started(self) -> None:
        if self.meetings.current or not self.settings["meeting_prompt"]:
            return
        event = self._calendar_event()
        title = event.get("title") or "Teams call"
        who = f"{len(event['attendees'])} people" if event.get("attendees") else "no calendar entry"
        self.pending_prompt = {"title": title, "detail": who, "event": event}

        def yes() -> None:
            if not self.meetings.current:
                self.start_meeting(event=event, auto=True)

        def no() -> None:
            self.pending_prompt = {}

        self.notify("Teams call", f"Record '{title}'? ({who}) Answer in the card at the bottom right.")
        if not (self.overlay and self.overlay.ask("Teams call", f"Record '{title}'? ({who})", yes, no)):
            self.open_window()  # no tkinter: the window shows the question

    def _call_ended(self) -> None:
        self.pending_prompt = {}
        s = self.meetings.current
        if s and s.meta.get("auto"):
            log.info("Teams left the microphone: stopping the recording")
            threading.Thread(target=self.meetings.stop, daemon=True).start()

    def notify(self, title: str, message: str) -> None:
        if self.tray and self.settings["notifications"]:
            self.tray.notify(title, message)

    def _meeting_event(self, kind: str, session) -> None:
        title = session.meta.get("title", "meeting")
        if kind == "started":
            self.notify("Recording meeting", f"'{title}': your microphone (Me) and what you hear (Others).")
        elif kind == "stopped":
            mins = round(session.meta.get("seconds", 0) / 60)
            self.notify("Meeting recorded", f"'{title}', {mins} min. Writing the transcript...")
        elif kind == "saved":
            self.notify("Transcript saved", session.meta.get("md", title))

    def _meeting_changed(self) -> None:
        if self.tray and self.dictation.state == "idle":
            self.tray.set_state("meeting" if self.meetings.current else "idle",
                                "powertools: recording a meeting" if self.meetings.current else "powertools: ready")

    # ------------------------------------------------------------------ state display
    def _on_state(self, state: str) -> None:
        if self.overlay:
            self.overlay.show(state)
        if self.tray and state == "idle" and self.meetings.current:
            self.tray.set_state("meeting", "powertools: recording a meeting")
        elif self.tray:
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

        try:
            self.httpd = serve(self, PORT)  # a fixed port lets an open window reconnect after a restart
        except OSError:
            self.httpd = serve(self)
        port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{port}/"
        (data_dir() / "port.txt").write_text(str(port))
        log.info("powertools %s on %s", __version__, self.url)

        self.overlay = make_overlay(lambda: self.recorder.level)
        log.info("overlay: %s", type(self.overlay).__name__)
        self._console_ctrl_c()
        self.engine.start()
        self.hotkey = Hotkey(self.dictation.toggle)
        try:
            self.hotkey.bind(self.settings["hotkey"])
        except Exception as e:
            log.error("hotkey %s failed: %s", self.settings["hotkey"], e)
        self.tray = Tray("powertools", self.settings["hotkey"], self.dictation.toggle, self.open_window, self.quit,
                         meeting=(lambda: bool(self.meetings.current), self.toggle_meeting, self.meetings.mark))
        self.tray.start()
        from .meetings.watcher import Watcher

        self.watcher = Watcher(self._call_started, self._call_ended)
        self.watcher.start()
        self._watch_engine()
        if open_browser and self.settings["open_window_on_start"]:
            self.open_window()

        try:
            try:
                self.overlay.run()  # blocks until quit()
            except Exception:
                log.exception("overlay failed; running without it")
                from .overlay import NullOverlay
                self.overlay = NullOverlay()
                if not self._quit.is_set():
                    self.overlay.run()
        except KeyboardInterrupt:
            log.info("Ctrl+C: quitting")
        finally:
            self._quit.set()
            if self.watcher:
                self.watcher.stop()
            if self.meetings.current:
                self.meetings.stop()
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

    def _console_ctrl_c(self) -> None:
        """Ctrl+C / closing the console window quits cleanly (the main thread sits in a native message loop)."""
        if sys.platform != "win32":
            return
        import ctypes

        handler_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)

        def handler(_event) -> int:
            self.quit()
            return 1

        self._ctrl_handler = handler_type(handler)  # keep a reference
        ctypes.windll.kernel32.SetConsoleCtrlHandler(self._ctrl_handler, True)

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
    if sys.platform == "win32":
        from .winoverlay import make_dpi_aware
        make_dpi_aware()
    port = running_instance()
    if port:
        webbrowser.open(f"http://127.0.0.1:{port}/")  # already running: just show it
        return 0
    try:
        return App().run(open_browser=not args.no_browser)
    except Exception:
        log.exception("powertools crashed")
        raise
