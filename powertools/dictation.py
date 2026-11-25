"""Dictation flow: toggle starts recording; toggle again stops, transcribes, cleans up and pastes."""

from __future__ import annotations

import logging
import threading
from typing import Callable

from . import textproc
from .engine import Result

log = logging.getLogger("powertools.dictation")
MIN_AUDIO_S = 0.4


class Dictation:
    def __init__(self, settings, engine, recorder, store, deliver: Callable, on_state: Callable[[str], None],
                 cleaner: Callable[[str], tuple[str, str]] | None = None):
        self.settings, self.engine, self.recorder, self.store = settings, engine, recorder, store
        self.deliver = deliver
        self.cleaner = cleaner        # the local model's clean-up (Settings > dictation_cleanup); text -> (text, note)
        self.on_state = on_state      # "idle" | "recording" | "transcribing" | "error"
        self.state = "idle"
        self.last: dict = {}
        self.pending = 0
        self.target = ""              # where the text of this recording goes: "" = the active window, "window" = our own
        self._seq = 0
        self._lock = threading.RLock()  # the result callback may run inside toggle() (fast engine, tests)

    def _set(self, state: str) -> None:
        self.state = state
        try:
            self.on_state(state)
        except Exception:
            log.exception("state listener failed")

    def toggle(self, target: str = "") -> str:
        """Start or stop. `target` counts at the start: "window" hands the text to our window instead of pasting it."""
        with self._lock:
            if self.state == "recording":
                return self._stop()
            self.target = target
            return self._start()

    def _start(self) -> str:
        if self.engine.state == "error":
            self.engine.retry()      # what failed (a download, a full GPU) may work now
            return self._fail("Whisper is not available, trying to load it again: " + self.engine.info.get("error", ""))
        try:
            self.recorder.start(self.settings["input_device"])
        except Exception as e:
            log.exception("microphone failed")
            return self._fail(f"microphone: {e}")
        self._set("recording")
        return self.state

    def _fail(self, error: str) -> str:
        self._show({"error": error}, self.target)
        self._set("error")
        return self.state

    def _show(self, last: dict, to: str) -> None:
        self._seq += 1
        self.last = {**last, "to": to, "seq": self._seq}

    def _stop(self) -> str:
        try:
            audio = self.recorder.stop()
        except Exception as e:               # unplugged meanwhile
            log.exception("microphone failed")
            return self._fail(f"microphone: {e}")
        if len(audio) / 16000 < MIN_AUDIO_S:
            self._set("idle")
            return self.state
        self.pending += 1
        self._set("transcribing")
        s = self.settings
        to = self.target
        self.engine.submit(audio, lambda r: self._done(r, to), language=s["language"], beam_size=s["beam_size"],
                           initial_prompt=textproc.vocabulary_prompt(s["vocabulary"]))
        return self.state

    def _done(self, r: Result, to: str) -> None:
        if not r.error and r.text and self.cleaner and self.settings["dictation_cleanup"]:
            # the model may take seconds: not on the Whisper thread (meeting chunks wait behind it)
            threading.Thread(target=self._finish, args=(r, to), name="cleanup", daemon=True).start()
        else:
            self._finish(r, to)

    def _finish(self, r: Result, to: str) -> None:
        try:
            self._show({"error": r.error} if r.error else self._hand_over(r, to), to)
        except Exception as e:
            log.exception("dictation failed")
            self._show({"error": f"{type(e).__name__}: {e}"}, to)
        with self._lock:
            self.pending -= 1
            still_busy = self.state == "recording" or self.pending > 0
        if not still_busy:
            self._set("error" if "error" in self.last and "text" not in self.last else "idle")

    def _hand_over(self, r: Result, to: str) -> dict:
        """Clean, paste and keep the text; what the window shows. A paste that fails is said; the text is in the history."""
        raw = textproc.tidy(r.text)
        text, note, error = raw, "", ""
        if raw and self.cleaner and self.settings["dictation_cleanup"]:
            text, note = self.cleaner(raw)
        text = textproc.apply_replacements(text, self.settings["replacements"])
        if not text:
            return {"text": "", "note": "no speech detected"}
        try:
            if to != "window":
                self.deliver(text, paste=self.settings["paste"], restore=self.settings["restore_clipboard"])
        except Exception as e:
            log.exception("paste failed")
            error = f"paste: {e} (the text is in the history)"
        item_id = self.store.add(raw, text, r.language, r.audio_s, r.took_s)
        return {"id": item_id, "text": text, "language": r.language, "audio_s": round(r.audio_s, 1), "took_s": round(r.took_s, 2),
                **({"note": note} if note else {}), **({"error": error} if error else {})}
