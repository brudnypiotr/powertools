"""Meetings without audio hardware: merging, Markdown, the Teams watcher, the final pass, engine priorities."""

import json
import sys
import threading
import time
import types

import numpy as np

from powertools.config import Settings
from powertools.engine import DICTATION, FINAL, LIVE, Engine, Job, Result
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


def test_compact_layout_and_calendar_fill(tmp_path, monkeypatch):
    """One line per turn, listening-only turns left out; an older file gets the calendar's attendees and the layout."""
    meta = {"title": "Teams call", "started": time.time(), "seconds": 60, "marks": [{"t": 4, "note": ""}]}
    md = mm.meeting_markdown(meta, [seg("me", 0, 2, "start"), seg("others", 3, 4, "Uh huh. Mm-hmm."),
                                    seg("others", 5, 7, "Uh huh. Okay, agreed.")])
    assert "Uh huh. Mm-hmm." not in md and "- **[00:00:05] Others:** Uh huh. Okay, agreed." in md
    assert "\n\n- **" not in md.split("## Transcript")[1].lstrip("\n") and "- *** marked 00:00:04 ***" in md
    old = ('---\ntitle: "Teams call"\nparticipants: []\norganizer: ""\n---\n\n# Teams call\n\n## Transcript\n\n'
           "**[00:00:01] Me:** hello\n\n**[00:00:02] Others:** Mm-hmm.\n\n**[00:00:03] Others:** hi there\n")
    monkeypatch.setenv("POWERTOOLS_HOME", str(tmp_path / "home"))
    m = mm.MeetingManager(Settings(tmp_path / "s.json"), None)
    f = tmp_path / "old.md"; f.write_text(old, encoding="utf-8")
    folder = m.root / "20261030-133000"; folder.mkdir()
    s = mm.Session(folder, {"title": "Teams call", "started": time.time(), "status": "done", "md": str(f)})
    r = m.update_file(s, {"title": "Pitch rehearsal", "attendees": ["Rodrigo", "Piotr"], "organizer": "Piotr"})
    new = f.read_text(encoding="utf-8")
    assert r["calendar"] and s.meta["title"] == "Pitch rehearsal" and s.meta["attendees"] == ["Rodrigo", "Piotr"]
    assert 'participants: ["Rodrigo", "Piotr"]' in new and 'organizer: "Piotr"' in new and "# Pitch rehearsal" in new
    assert "- **[00:00:01] Me:** hello\n- **[00:00:03] Others:** hi there" in new and "Mm-hmm" not in new
    m.update_file(s, None)                                   # no calendar entry: only the layout, idempotent
    assert f.read_text(encoding="utf-8") == new


def test_parakeet_loads_on_the_cpu_and_returns_vad_segments(monkeypatch):
    loaded = []

    class FakeAsr:
        def with_vad(self, vad, **kw):
            loaded.append(("vad", kw["min_silence_duration_ms"]))
            return self

        def recognize(self, audio):
            return iter([types.SimpleNamespace(start=0.0, end=1.0, text=" Dzień dobry"),
                         types.SimpleNamespace(start=1.5, end=2.0, text="Merlot ")])

    fake = types.SimpleNamespace(load_model=lambda name, **kw: loaded.append((name, kw["quantization"])) or FakeAsr(),
                                 load_vad=lambda name, **kw: name)
    monkeypatch.setitem(sys.modules, "onnx_asr", fake)
    e = Engine("parakeet-v3")
    e._load("parakeet-v3")
    assert e.state == "ready" and e.info["device"] == "cpu"
    assert loaded == [("nemo-parakeet-tdt-0.6b-v3", "int8"), ("vad", 500)]
    r = e._transcribe(Job(np.zeros(32000, np.float32), lambda r: None, {"language": "pl"}))
    assert r.text == "Dzień dobry Merlot" and r.language == "pl" and r.audio_s == 2.0 and len(r.segments) == 2


# ---------------------------------------------------------------- muted in Teams, summary
def test_teams_link_pairs_and_reports_mute(tmp_path):
    """A fake Teams on a local port: unpaired update -> pair request -> token -> mute changes reach on_mute."""
    import base64
    import hashlib
    import socket

    from powertools.meetings.teams_api import TeamsLink, WebSocket

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    got, seen = [], []

    def teams():
        c, _ = srv.accept()
        head = b""
        while b"\r\n\r\n" not in head:
            head += c.recv(4096)
        seen.append(head.decode())
        key = [l.split(": ")[1] for l in head.decode().split("\r\n") if l.lower().startswith("sec-websocket-key")][0]
        acc = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        c.sendall(f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  f"Sec-WebSocket-Accept: {acc}\r\n\r\n".encode())
        ws = WebSocket(c)

        def out(obj):  # server frames are not masked
            data = json.dumps(obj).encode()
            c.sendall(bytes([0x81, 126]) + len(data).to_bytes(2, "big") + data if len(data) > 125
                      else bytes([0x81, len(data)]) + data)

        out({"meetingUpdate": {"meetingPermissions": {"canPair": True}}})
        got.append(json.loads(ws.recv()))           # the pair request
        out({"tokenRefresh": "tok-1"})
        state = {"isInMeeting": True, "isMuted": True}
        out({"meetingUpdate": {"meetingState": state, "meetingPermissions": {"canPair": True}}})
        out({"meetingUpdate": {"meetingState": {**state, "isMuted": False}, "meetingPermissions": {"canPair": True}}})
        c.sendall(bytes([0x88, 0]))                 # close
        c.close()

    threading.Thread(target=teams, daemon=True).start()
    calls = []
    link = TeamsLink(calls.append, token_file=tmp_path / "teams_api.json", port=port, retry=60)
    link._session()
    srv.close()
    assert "protocol-version=2.0.0" in seen[0] and "token=" not in seen[0]
    assert got[0]["action"] == "pair" and got[0]["requestId"] == 1
    assert link.token == "tok-1" and calls == [True, False]
    assert not link.connected and link.muted is None


def test_muted_spans_drop_my_words_and_skip_silent_chunks(tmp_path, monkeypatch):
    monkeypatch.setenv("POWERTOOLS_HOME", str(tmp_path / "home"))
    s = Settings(tmp_path / "s.json")
    s.update({"output_dir": str(tmp_path / "out"), "meeting_summary": False})
    eng = FakeEngine()
    m = mm.MeetingManager(s, eng)
    folder = m.root / "20261117-100000"
    folder.mkdir()
    session = mm.Session(folder, {"title": "Sync", "started": time.time() - 30, "seconds": 0, "marks": [],
                                  "status": "recording", "muted": []})
    m.current = session
    m.set_muted(True)
    assert (folder / "MUTED").exists() and session.meta["muted"][0][1] is None
    m.set_muted(False)
    assert not (folder / "MUTED").exists() and session.meta["muted"][0][1] is not None
    m.set_muted(True)
    m.current = None
    session.meta["muted"] = [[0.0, 5.0], [20.0, None]]
    session.meta["seconds"] = 30
    out = mm.drop_muted([seg("me", 1, 3, "muted talk"), seg("me", 10, 12, "heard"), seg("others", 1, 3, "them"),
                         seg("me", 25, 27, "still muted")], session.meta["muted"])
    assert [s["text"] for s in out] == ["heard", "them"]
    assert mm.muted_seconds(session.meta["muted"], 30) == 15
    md = mm.meeting_markdown(session.meta, out)
    assert "me_muted_min: 0.2" in md

    class Proc:  # the recorder's event stream: a silent chunk is not sent to Whisper
        stdout = iter([json.dumps({"event": "chunk", "track": "me", "path": "me_0000.wav", "start": 0, "silent": True}),
                       json.dumps({"event": "chunk", "track": "others", "path": "others_0000.wav", "start": 0})])
        stderr = None

        def wait(self):
            return 0

    m._finish = lambda s: None
    m._read_events(session, Proc())
    assert [a for a, _, _ in eng.jobs] == ["others_0000.wav"]


def test_summary_after_the_transcript(tmp_path, monkeypatch):
    from powertools.meetings import summary

    monkeypatch.setenv("POWERTOOLS_HOME", str(tmp_path / "home"))
    s = Settings(tmp_path / "s.json")
    s.update({"output_dir": str(tmp_path / "out")})

    class LLM:
        settings = {"llm_model": "gemma4:e4b"}

        def __init__(self):
            self.calls = []

        def chat(self, system, user, schema=None, **kw):
            self.calls.append((system, user, kw))
            n = len(self.calls)
            return json.dumps({"summary": f"Summary {n}.", "key_points": [f"Point {n}a", f"Point {n}b"]}), {}

    llm = LLM()
    m = mm.MeetingManager(s, FakeEngine(), llm=llm)
    m.summarize = lambda session: None  # run it by hand below, not in a thread
    folder = m.root / "20261117-110000"
    folder.mkdir()
    for t in ("me", "others"):
        write_wav(folder / f"{t}_0000.wav", np.zeros(16000, np.float32))
    session = mm.Session(folder, {"title": "Planning", "started": time.time(), "seconds": 60, "marks": [],
                                  "status": "interrupted"})
    m.finalize(session)
    mm.MeetingManager.summarize(m, session)
    md = open(session.meta["md"], encoding="utf-8").read()
    assert session.meta["summary_status"] == "done" and session.meta["summary"]["key_points"] == ["Point 1a", "Point 1b"]
    assert md.index("## Summary") < md.index("## Key points") < md.index("## Transcript")
    assert "- Point 1a" in md and "Me:** me says hi" in md
    assert "Me: me says hi" in llm.calls[0][1] and llm.calls[0][2]["num_ctx"] == summary.NUM_CTX
    mm.MeetingManager.summarize(m, session)  # again: replaced, not added twice
    md = open(session.meta["md"], encoding="utf-8").read()
    assert md.count("## Summary") == 1 and "- Point 2a" in md

    # a long meeting: parts first, then one call that joins them
    llm.calls.clear()
    segs = [seg("others", i * 10, i * 10 + 5, "word " * 60) for i in range(100)]
    r = summary.summarize(llm, {"title": "Long"}, segs)
    parts = len(summary.split(summary.transcript_text(segs)))
    assert parts > 1 and len(llm.calls) == parts + 1 and r["summary"] == f"Summary {parts + 1}."
    assert all(len(p) <= summary.CHUNK_CHARS for p in summary.split(summary.transcript_text(segs)))

    # an older file without transcript.json: the turns come from the Markdown
    assert summary.segments_from_md(md)[0]["speaker"] in ("me", "others")


def test_summary_error_is_shown_not_raised(tmp_path, monkeypatch):
    from powertools.llm import LLMError

    monkeypatch.setenv("POWERTOOLS_HOME", str(tmp_path / "home"))
    s = Settings(tmp_path / "s.json")

    class Down:
        def chat(self, *a, **k):
            raise LLMError("Ollama is not running")

    m = mm.MeetingManager(s, FakeEngine(), llm=Down())
    folder = m.root / "20261117-120000"
    folder.mkdir()
    (folder / "transcript.json").write_text(json.dumps([seg("me", 0, 1, "hello")]))
    session = mm.Session(folder, {"title": "x", "started": time.time(), "status": "done"})
    m.summarize(session)
    assert session.meta["summary_status"] == "error" and "not running" in session.meta["summary_error"]
