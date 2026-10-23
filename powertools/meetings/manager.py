"""Meeting sessions: start/stop the recorder subprocess, live transcription of 30 s chunks, the final pass with
the accurate model, and the Markdown file (Me / Others, calendar metadata, marked moments).

Session folder: %LOCALAPPDATA%\\powertools\\meetings\\<id>\\ (meta.json + WAV chunks; audio is deleted after
`meeting_keep_audio_days`).
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

from .. import export
from ..config import data_dir, output_dir
from ..engine import FINAL, LIVE, Result

log = logging.getLogger("powertools.meetings")
SPEAKERS = {"me": "Me", "others": "Others"}
ROOT = Path(__file__).resolve().parents[2]


def fmt_offset(s: float) -> str:
    s = int(s)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def merge(segments: list[dict], gap: float = 2.0) -> list[dict]:
    """Sort both tracks by time and join consecutive pieces of the same speaker."""
    out: list[dict] = []
    for s in sorted((s for s in segments if s["text"].strip()), key=lambda s: s["start"]):
        if out and out[-1]["speaker"] == s["speaker"] and s["start"] - out[-1]["end"] <= gap:
            out[-1]["text"] += " " + s["text"].strip()
            out[-1]["end"] = max(out[-1]["end"], s["end"])
        else:
            out.append({**s, "text": s["text"].strip()})
    return out


def concat_wavs(paths: list[Path], dest: Path) -> float:
    with wave.open(str(dest), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        total = 0
        for p in paths:
            with wave.open(str(p), "rb") as w:
                frames = w.readframes(w.getnframes())
                out.writeframes(frames)
                total += w.getnframes()
    return total / 16000


def speech_stats(segments: list[dict]) -> dict:
    """Speaking time and words per track (Me / Others) from the transcript segments."""
    out = {t: {"seconds": 0.0, "words": 0} for t in SPEAKERS}
    for s in segments:
        st = out.setdefault(s["speaker"], {"seconds": 0.0, "words": 0})
        st["seconds"] = round(st["seconds"] + max(0.0, s.get("end", s["start"]) - s["start"]), 1)
        st["words"] += len(s["text"].split())
    return out


def meeting_markdown(meta: dict, segments: list[dict]) -> str:
    start = datetime.fromtimestamp(meta["started"]).astimezone()
    head = export.frontmatter({
        "source": "meeting",
        "title": meta["title"],
        "date": start.strftime("%Y-%m-%d %H:%M"),
        "duration_min": round(meta.get("seconds", 0) / 60),
        "participants": meta.get("attendees", []),
        "organizer": meta.get("organizer", ""),
        "speakers": "Me = my microphone; Others = everyone else (one track, not split by person)",
        "transcript": meta.get("transcript_model", ""),
    })
    out = [head, f"# {meta['title']}", ""]
    if meta.get("invite"):
        out += ["## Invitation", "", meta["invite"].strip(), ""]
    if meta.get("marks"):
        out += ["## Marked moments", ""] + [f"- {fmt_offset(m['t'])} {m.get('note', '')}".rstrip() for m in meta["marks"]] + [""]
    out += ["## Transcript", ""]
    marks = sorted(m["t"] for m in meta.get("marks", []))
    for s in segments:
        while marks and marks[0] <= s["start"]:
            out += [f"*** marked {fmt_offset(marks.pop(0))} ***", ""]
        out += [f"**[{fmt_offset(s['start'])}] {SPEAKERS.get(s['speaker'], s['speaker'])}:** {s['text']}", ""]
    if not segments:
        out.append("_(no speech recognised)_")
    return "\n".join(out)


class Session:
    def __init__(self, folder: Path, meta: dict):
        self.folder, self.meta = folder, meta
        self.live: list[dict] = meta.setdefault("live", [])
        self.finishing = False

    @property
    def id(self) -> str:
        return self.folder.name

    def save(self) -> None:
        tmp = self.folder / "meta.tmp"
        tmp.write_text(json.dumps(self.meta, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.folder / "meta.json")

    def view(self, with_live: bool = False) -> dict:
        keys = ("title", "status", "started", "seconds", "attendees", "organizer", "marks", "md", "error", "auto",
                "devices", "transcript_model", "stats")
        v = {"id": self.id, **{k: self.meta.get(k) for k in keys}}
        v["invite"] = (self.meta.get("invite") or "")[:600]
        v["audio"] = any((self.folder / f"{t}.wav").exists() or any(self.folder.glob(f"{t}_*.wav")) for t in SPEAKERS)
        if with_live:
            v["live"] = merge(self.live)[-200:]
        return v


class MeetingManager:
    def __init__(self, settings, engine, on_change=None, on_event=None):
        self.settings, self.engine = settings, engine
        self.on_change = on_change or (lambda: None)
        self.on_event = on_event or (lambda kind, session: None)  # started | stopped | saved
        self.root = data_dir() / "meetings"
        self.root.mkdir(parents=True, exist_ok=True)
        self.current: Session | None = None
        self.proc: subprocess.Popen | None = None
        self.levels = {"me": 0.0, "others": 0.0}
        self._lock = threading.Lock()
        self._recover()
        self.cleanup_audio()

    # ------------------------------------------------------------------ sessions on disk
    def sessions(self, limit: int = 30) -> list[Session]:
        out = []
        for d in sorted(self.root.iterdir(), reverse=True)[:limit]:
            try:
                out.append(Session(d, json.loads((d / "meta.json").read_text(encoding="utf-8"))))
            except (OSError, ValueError):
                continue
        return out

    def get(self, sid: str) -> Session:
        if self.current and self.current.id == sid:
            return self.current
        d = self.root / sid
        if not (d / "meta.json").exists() or d.parent != self.root:
            raise KeyError(sid)
        return Session(d, json.loads((d / "meta.json").read_text(encoding="utf-8")))

    def _recover(self) -> None:
        """Sessions left 'recording' by a crash or a reboot can still be finished from their chunks."""
        for s in self.sessions(100):
            if s.meta.get("status") in ("recording", "finalizing"):
                s.meta["status"] = "interrupted"
                s.save()

    def cleanup_audio(self) -> None:
        keep = self.settings["meeting_keep_audio_days"] * 86400
        for s in self.sessions(500):
            if s.meta.get("status") == "done" and time.time() - s.meta.get("started", 0) > keep:
                for f in s.folder.glob("*.wav"):
                    f.unlink(missing_ok=True)

    # ------------------------------------------------------------------ recording
    def start(self, title: str = "", event: dict | None = None, auto: bool = False) -> Session:
        with self._lock:
            if self.current:
                raise ValueError("a meeting is already being recorded")
            event = event or {}
            started = time.time()
            sid = datetime.fromtimestamp(started).strftime("%Y%m%d-%H%M%S")
            folder = self.root / sid
            folder.mkdir(parents=True)
            meta = {"title": title or event.get("title") or "Teams call", "status": "recording", "started": started,
                    "seconds": 0, "attendees": event.get("attendees", []), "organizer": event.get("organizer", ""),
                    "invite": event.get("invite", ""), "marks": [], "auto": auto}
            self.current = Session(folder, meta)
            self.current.save()
            boot = (f"import sys, runpy; sys.path.insert(0, {str(ROOT)!r}); "
                    "runpy.run_module('powertools.meetings.worker', run_name='__main__', alter_sys=True)")
            exe = sys.executable.replace("pythonw.exe", "python.exe")
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self.proc = subprocess.Popen(
                [exe, "-c", boot, str(folder), self.settings["meeting_mic"], self.settings["meeting_output"]],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=flags)
            threading.Thread(target=self._read_events, args=(self.current, self.proc), daemon=True).start()
            log.info("meeting started: %s", meta["title"])
        self.on_change()
        self.on_event("started", self.current)
        return self.current

    def _read_events(self, session: Session, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            kind = ev.get("event")
            if kind == "level":
                self.levels = {"me": ev.get("me", 0.0), "others": ev.get("others", 0.0)}
            elif kind == "chunk":
                self._live(session, ev["track"], ev["path"], float(ev["start"]))
            elif kind == "error":
                session.meta["error"] = ev.get("message", "")
                log.error("recorder: %s", session.meta["error"])
            elif kind == "started":
                session.meta["devices"] = {"mic": ev.get("mic"), "output": ev.get("output")}
            elif kind == "stopped":
                session.meta["seconds"] = ev.get("seconds", 0)
        err = proc.stderr.read() if proc.stderr else ""
        if proc.wait() != 0 and err:
            session.meta["error"] = (session.meta.get("error") or "") + err.strip()[-400:]
            log.error("recorder exited: %s", err.strip()[-400:])
        session.save()
        if self.current is session:  # the recorder died on its own
            self._finish(session)

    def _live(self, session: Session, track: str, path: str, offset: float) -> None:
        def done(r: Result) -> None:
            if r.error:
                log.warning("live chunk failed: %s", r.error)
                return
            session.live.extend({"speaker": track, "start": offset + a, "end": offset + b, "text": t}
                                for a, b, t in r.segments)
            session.save()
            self.on_change()

        s = self.settings
        self.engine.submit(path, done, priority=LIVE, language=s["language"], beam_size=1)

    def mark(self, note: str = "") -> dict:
        if not self.current:
            raise ValueError("no meeting is being recorded")
        m = {"t": round(time.time() - self.current.meta["started"], 1), "note": note.strip()}
        self.current.meta["marks"].append(m)
        self.current.save()
        self.on_change()
        return m

    def stop(self) -> Session:
        with self._lock:
            session, proc = self.current, self.proc
            if not session:
                raise ValueError("no meeting is being recorded")
            (session.folder / "STOP").touch()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
        self._finish(session)
        return session

    def _finish(self, session: Session) -> None:
        with self._lock:
            if session.finishing:  # stop() and the event reader may both get here
                return
            session.finishing = True
            if self.current is session:
                self.current, self.proc = None, None
                self.levels = {"me": 0.0, "others": 0.0}
        if not session.meta.get("seconds"):
            session.meta["seconds"] = round(time.time() - session.meta["started"], 1)
        self.on_event("stopped", session)
        self.finalize(session)

    # ------------------------------------------------------------------ final pass
    def finalize(self, session: Session) -> None:
        # chunks while recording; after a transcript only the joined me.wav / others.wav are kept ("Transcribe again")
        tracks = {t: sorted(session.folder.glob(f"{t}_*.wav")) or
                  ([session.folder / f"{t}.wav"] if (session.folder / f"{t}.wav").exists() else []) for t in SPEAKERS}
        if not any(tracks.values()):
            session.meta.update(status="done", error=session.meta.get("error") or "no audio was recorded")
            session.save()
            self.on_change()
            return
        model = self.settings["meeting_final_model"]
        if model == "live":
            self._write(session, merge(session.live), "live (turbo)")
            return
        session.meta["status"] = "finalizing"
        session.meta.pop("error", None)  # a new attempt: an old error (e.g. a broken decoder) no longer applies
        session.save()
        self.on_change()
        results: dict[str, list] = {}

        def done_for(track: str):
            def done(r: Result) -> None:
                results[track] = [{"speaker": track, "start": a, "end": b, "text": t} for a, b, t in r.segments]
                if r.error:
                    session.meta["error"] = f"final pass ({track}): {r.error}"
                if len(results) == len(jobs):
                    segs = [s for v in results.values() for s in v]
                    if session.meta.get("error") and not segs:
                        segs = session.live  # fall back to the live transcript
                    self._write(session, merge(segs), model)
            return done

        jobs = {}
        for track, files in tracks.items():
            if files:
                full = session.folder / f"{track}.wav"
                if files != [full]:
                    concat_wavs(files, full)
                jobs[track] = full
        for track, full in jobs.items():
            self.engine.submit(str(full), done_for(track), priority=FINAL, model=model,
                               language=self.settings["language"], beam_size=5)

    def _write(self, session: Session, segments: list[dict], model: str) -> None:
        session.meta["transcript_model"] = model
        session.meta["stats"] = speech_stats(segments)
        start = datetime.fromtimestamp(session.meta["started"])
        name = f"{start:%Y-%m-%d-%H%M}_{export.slug(session.meta['title'])}.md"
        path = export.write(export.unique(output_dir(self.settings) / "meetings" / name),
                            meeting_markdown(session.meta, segments))
        session.meta.update(status="done", md=str(path))
        session.save()
        for f in session.folder.glob("*_*.wav"):  # chunks are no longer needed; me.wav/others.wav stay for N days
            f.unlink(missing_ok=True)
        log.info("meeting written: %s", path)
        self.on_change()
        self.on_event("saved", session)

    def delete(self, sid: str) -> None:
        s = self.get(sid)
        if s is self.current:
            raise ValueError("stop the recording first")
        shutil.rmtree(s.folder)
