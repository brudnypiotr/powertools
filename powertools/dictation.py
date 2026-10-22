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
    def __init__(self, settings, engine, recorder, store, deliver: Callable, on_state: Callable[[str], None]):
        self.settings, self.engine, self.recorder, self.store = settings, engine, recorder, store
        self.deliver = deliver
        self.on_state = on_state      # "idle" | "recording" | "transcribing" | "error"
        self.state = "idle"
        self.last: dict = {}
        self.pending = 0
        self._lock = threading.RLock()  # the result callback may run inside toggle() (fast engine, tests)

    def _set(self, state: str) -> None:
        self.state = state
        try:
            self.on_state(state)
        except Exception:
            log.exception("state listener failed")

    def toggle(self) -> str:
        with self._lock:
            if self.state == "recording":
                return self._stop()
            return self._start()

    def _start(self) -> str:
        if self.engine.state == "error":
            self.last = {"error": "Whisper is not available: " + self.engine.info.get("error", "")}
            self._set("error")
            return self.state
        try:
            self.recorder.start(self.settings["input_device"])
        except Exception as e:
            log.exception("microphone failed")
            self.last = {"error": f"microphone: {e}"}
            self._set("error")
            return self.state
        self._set("recording")
        return self.state

    def _stop(self) -> str:
        audio = self.recorder.stop()
        if len(audio) / 16000 < MIN_AUDIO_S:
            self._set("idle")
            return self.state
        self.pending += 1
        self._set("transcribing")
        s = self.settings
        self.engine.submit(audio, self._done, language=s["language"], beam_size=s["beam_size"],
                           initial_prompt=textproc.vocabulary_prompt(s["vocabulary"]))
        return self.state

    def _done(self, r: Result) -> None:
        with self._lock:
            self.pending -= 1
            still_busy = self.state == "recording" or self.pending > 0
        if r.error:
            self.last = {"error": r.error}
            log.warning("dictation failed: %s", r.error)
            if not still_busy:
                self._set("error")
            return
        raw = textproc.tidy(r.text)
        text = textproc.apply_replacements(raw, self.settings["replacements"])
        if text:
            try:
                self.deliver(text, paste=self.settings["paste"], restore=self.settings["restore_clipboard"])
            except Exception as e:
                log.exception("paste failed")
                self.last = {"error": f"paste: {e}"}
            item_id = self.store.add(raw, text, r.language, r.audio_s, r.took_s)
            self.last = {"id": item_id, "text": text, "language": r.language, "audio_s": round(r.audio_s, 1),
                         "took_s": round(r.took_s, 2)}
        else:
            self.last = {"text": "", "note": "no speech detected"}
        if not still_busy:
            self._set("idle")
