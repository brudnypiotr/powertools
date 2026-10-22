"""Meetings without audio hardware: merging, Markdown, the Teams watcher, the final pass, engine priorities."""

import json
import threading
import time
import types

import numpy as np

from powertools.config import Settings
from powertools.engine import DICTATION, FINAL, LIVE, Engine, Result
from powertools.meetings import manager as mm
from powertools.meetings.watcher import Watcher, teams_in_call
from powertools.meetings.worker import to_16k_mono, write_wav


def seg(speaker, start, end, text):
    return {"speaker": speaker, "start": start, "end": end, "text": text}


def test_merge_orders_and_joins_same_speaker():
    out = mm.merge([seg("others", 5, 7, "dzień dobry"), seg("me", 0, 2, "cześć"), seg("me", 2.5, 4, "jak leci"),
                    seg("others", 7.5, 9, "wszystko ok"), seg("me", 20, 21, " ")])
    assert [(s["speaker"], s["text"]) for s in out] == [("me", "cześć jak leci"), ("others", "dzień dobry wszystko ok")]


def test_markdown_has_metadata_marks_and_speakers():
    meta = {"title": "Sync", "started": time.time(), "seconds": 600, "attendees": ["Anna", "Jan"],
            "organizer": "Anna", "invite": "Agenda: plan", "marks": [{"t": 3, "note": "decision"}],
            "transcript_model": "large-v3"}
    md = mm.meeting_markdown(meta, [seg("me", 0, 2, "start"), seg("others", 5, 7, "ok")])
    assert '"source": "meeting"' not in md and 'source: "meeting"' in md
    assert "## Invitation" in md and "Agenda: plan" in md and "- 00:00:03 decision" in md
    assert "**[00:00:00] Me:** start" in md and "*** marked 00:00:03 ***" in md and "**[00:00:05] Others:** ok" in md


def test_watcher_start_and_end_with_grace():
    events, apps = [], [[]]
    w = Watcher(lambda: events.append("start"), lambda: events.append("end"), probe=lambda: apps[0], grace=20)
    w.step(0)
    apps[0] = ["MSTeams"]
    w.step(1); w.step(2)
    apps[0] = ["python.exe"]
    w.step(10)
    assert events == ["start"]
    w.step(25)
    assert events == ["start", "end"]
    assert teams_in_call(["ms-teams.exe"]) and not teams_in_call(["Zoom.exe"])


def test_resample_and_wav(tmp_path):
    stereo48 = np.zeros(48000 * 2, np.float32)  # 1 s stereo at 48 kHz
    mono16 = to_16k_mono(stereo48, 48000, 2)
    assert len(mono16) == 16000
    write_wav(tmp_path / "a.wav", mono16)
    assert mm.concat_wavs([tmp_path / "a.wav", tmp_path / "a.wav"], tmp_path / "all.wav") == 2.0


class FakeEngine:
    def __init__(self):
        self.jobs = []

    def submit(self, audio, done, priority=0, **opts):
        self.jobs.append((audio, priority, opts))
        track = "me" if str(audio).replace("\\", "/").rsplit("/", 1)[-1].startswith("me") else "others"
        done(Result("x", segments=[(1.0, 2.0, f"{track} says hi")]))


def test_finalize_writes_markdown(tmp_path, monkeypatch):
    monkeypatch.setenv("POWERTOOLS_HOME", str(tmp_path / "home"))
    s = Settings(tmp_path / "s.json")
    s.update({"output_dir": str(tmp_path / "out")})
    eng = FakeEngine()
    m = mm.MeetingManager(s, eng)
    folder = m.root / "20261022-100000"
    folder.mkdir()
    for t in ("me", "others"):
        write_wav(folder / f"{t}_0000.wav", np.zeros(16000, np.float32))
    session = mm.Session(folder, {"title": "Planning", "started": time.time(), "seconds": 60, "marks": [],
                                  "status": "interrupted"})
    m.finalize(session)
    assert session.meta["status"] == "done"
    md = open(session.meta["md"], encoding="utf-8").read()
    assert "Me:** me says hi" in md and "Others:** others says hi" in md
    assert all(p == FINAL and o["model"] == "large-v3" for _, p, o in eng.jobs)
    assert not list(folder.glob("*_0000.wav"))  # chunks removed, full tracks kept


def test_recover_marks_interrupted(tmp_path, monkeypatch):
    monkeypatch.setenv("POWERTOOLS_HOME", str(tmp_path / "home"))
    s = Settings(tmp_path / "s.json")
    m = mm.MeetingManager(s, FakeEngine())
    d = m.root / "20261022-090000"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"title": "x", "status": "recording", "started": time.time()}))
    m2 = mm.MeetingManager(s, FakeEngine())
    assert m2.get("20261022-090000").meta["status"] == "interrupted"


def test_engine_priorities_and_model_swap(monkeypatch):
    order, loads = [], []
    e = Engine("turbo")

    def fake_load(self, name):
        loads.append(name)
        self.model_name, self.state = name, "ready"

    def fake_transcribe(self, job):
        order.append((job.options.get("tag"), self.model_name))
        return Result("ok")

    monkeypatch.setattr(Engine, "_load", fake_load)
    monkeypatch.setattr(Engine, "_transcribe", fake_transcribe)
    gate = threading.Event()
    e.submit(None, lambda r: gate.wait(2), priority=LIVE, tag="first")  # holds the worker
    e.start()
    time.sleep(0.1)
    e.submit(None, lambda r: None, priority=FINAL, tag="final", model="large-v3")
    e.submit(None, lambda r: None, priority=LIVE, tag="live")
    e.submit(None, lambda r: None, priority=DICTATION, tag="dictation")
    gate.set()
    for _ in range(50):
        if len(order) == 4 and loads[-1] == "turbo" and len(loads) == 3:
            break
        time.sleep(0.05)
    assert [t for t, _ in order] == ["first", "dictation", "live", "final"]
    assert order[-1] == ("final", "large-v3") and loads == ["turbo", "large-v3", "turbo"]
