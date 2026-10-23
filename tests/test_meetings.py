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


def test_finalize_reports_events(tmp_path, monkeypatch):
    monkeypatch.setenv("POWERTOOLS_HOME", str(tmp_path / "home"))
    s = Settings(tmp_path / "s.json")
    s.update({"output_dir": str(tmp_path / "out")})
    events = []
    m = mm.MeetingManager(s, FakeEngine(), on_event=lambda kind, sess: events.append(kind))
    folder = m.root / "20261022-110000"
    folder.mkdir()
    write_wav(folder / "me_0000.wav", np.zeros(16000, np.float32))
    m._finish(mm.Session(folder, {"title": "x", "started": time.time(), "seconds": 5, "marks": []}))
    assert events == ["stopped", "saved"]


def test_load_wav_without_pyav(tmp_path):
    import wave

    from powertools.engine import load_wav
    write_wav(tmp_path / "a.wav", np.full(16000, 0.5, np.float32))
    a = load_wav(str(tmp_path / "a.wav"))
    assert a.dtype == np.float32 and len(a) == 16000 and abs(a[0] - 0.5) < 1e-3
    pcm = (np.full((48000, 2), 0.25) * 32767).astype("<i2")  # 48 kHz stereo
    with wave.open(str(tmp_path / "b.wav"), "wb") as w:
        w.setnchannels(2), w.setsampwidth(2), w.setframerate(48000), w.writeframes(pcm.tobytes())
    b = load_wav(str(tmp_path / "b.wav"))
    assert len(b) == 16000 and abs(b[100] - 0.25) < 1e-3


def test_engine_steps_down_from_gpu_to_saving_mode_to_cpu(monkeypatch):
    import sys
    import types

    loads, calls = [], []

    class FakeModel:
        def __init__(self, name, device, compute_type):
            loads.append((name, device, compute_type))
            if compute_type == "float16":
                raise RuntimeError("CUDA failed with error out of memory")
            self.device = device

        def transcribe(self, audio, **kw):
            calls.append(self.device)
            if self.device == "cuda":
                raise RuntimeError("CUDA failed with error out of memory")
            seg = types.SimpleNamespace(start=0.0, end=1.0, text="hello")
            return iter([seg]), types.SimpleNamespace(language="en", language_probability=0.9, duration=1.0)

    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(get_cuda_device_count=lambda: 1))
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    e = Engine("turbo")
    out = []
    e.submit(np.zeros(16000, np.float32), out.append)
    e.start()  # the real worker: loads (float16 fails -> int8_float16), transcribes (CUDA OOM -> CPU), answers
    for _ in range(100):
        if out:
            break
        time.sleep(0.02)
    e.stop()
    assert loads[:2] == [("turbo", "cuda", "float16"), ("turbo", "cuda", "int8_float16")]
    assert out[0].text == "hello" and not out[0].error and calls == ["cuda", "cpu"]
    assert loads[-1] == ("small", "cpu", "int8") and e.status()["model"] == "small" and e.floor == 2
    assert "CPU" in e.info["note"]
    e._load("large-v3")  # stays on the CPU, with the smaller stand-in
    assert loads[-1] == ("turbo", "cpu", "int8") and e.status()["asked_model"] == "large-v3"


def test_transcribe_again_uses_kept_tracks_and_counts_speech(tmp_path, monkeypatch):
    monkeypatch.setenv("POWERTOOLS_HOME", str(tmp_path / "home"))
    s = Settings(tmp_path / "s.json")
    s.update({"output_dir": str(tmp_path / "out")})
    eng = FakeEngine()
    m = mm.MeetingManager(s, eng)
    folder = m.root / "20261023-100000"
    folder.mkdir()
    for t in ("me", "others"):
        write_wav(folder / f"{t}_0000.wav", np.zeros(16000, np.float32))
    session = mm.Session(folder, {"title": "Sync", "started": time.time(), "seconds": 60, "marks": [],
                                  "status": "interrupted", "attendees": ["A", "B", "C"]})
    m.finalize(session)
    assert not list(folder.glob("*_0000.wav")) and (folder / "me.wav").exists()
    session.meta["error"] = "old decoder error"
    eng.jobs.clear()
    m.finalize(session)  # "Transcribe again": only me.wav / others.wav are left
    assert sorted(str(a).replace("\\", "/").rsplit("/", 1)[-1] for a, _, _ in eng.jobs) == ["me.wav", "others.wav"]
    v = session.view()
    assert v["status"] == "done" and not v.get("error") and v["audio"]
    assert v["stats"]["me"] == {"seconds": 1.0, "words": 3} and v["attendees"] == ["A", "B", "C"]
