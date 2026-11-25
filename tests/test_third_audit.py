"""What the third audit found: dictation, meetings, Jira, Graph and the updater when something around them fails."""

import http.client
import subprocess
import threading
import time
import urllib.request

import numpy as np
import pytest

from powertools import files, updater
from powertools.config import Settings
from powertools.dictation import Dictation
from powertools.engine import FINAL, Engine, Result
from powertools.graph import client as graph
from powertools.jira.client import Jira, JiraError
from powertools.meetings.manager import MeetingManager
from powertools.meetings.watcher import Watcher

AUDIO = np.zeros(16000, dtype=np.float32)


def boom(exc):
    def f(*a, **k):
        raise exc
    return f


# ---------------------------------------------------------------- the speech engine
class FakeEngine(Engine):
    """The real worker loop; loading and transcribing are faked. Models named in `broken` do not load."""

    def __init__(self, broken=()):
        super().__init__("turbo")
        self.broken = set(broken)

    def _load(self, name):
        self.model_name = self.loaded_name = name
        self.state = "error" if name in self.broken else "ready"
        self.info = {"error": "no such model"} if name in self.broken else {}

    def _transcribe(self, job):
        return Result(f"by {self.loaded_name}")


def ask(e, **options):
    got, there = [], threading.Event()
    e.submit(AUDIO, lambda r: (got.append(r), there.set()), **options)
    assert there.wait(5)
    return got[0]


def test_a_meeting_model_that_does_not_load_leaves_dictation_alive():
    e = FakeEngine(broken=["large-v3"])
    e.start()
    r = ask(e, priority=FINAL, model="large-v3")
    assert r.text == "by turbo" and not r.error                  # the final pass falls back to the usual model
    for _ in range(50):
        if e.state == "ready":
            break
        time.sleep(0.02)
    assert e.state == "ready" and ask(e).text == "by turbo"
    e.stop()


def test_a_callback_that_fails_does_not_end_the_speech_thread():
    e = FakeEngine(broken=["turbo"])
    e.start()
    e.submit(AUDIO, boom(OSError("disk full")))
    assert "not ready" in ask(e).error
    e.stop()


# ---------------------------------------------------------------- dictation
class Recorder:
    audio, fail = AUDIO, None

    def start(self, name):
        pass

    def stop(self):
        if self.fail:
            raise self.fail
        return self.audio


class Now:
    state, info = "ready", {}

    def submit(self, audio, done, **k):
        done(Result("hello there", "en", 1, 0.1))


class Store:
    fail = None

    def add(self, *a):
        if self.fail:
            raise self.fail
        return 1


@pytest.fixture()
def dict_(tmp_path):
    states = []
    d = Dictation(Settings(tmp_path / "s.json"), Now(), Recorder(), Store(), lambda *a, **k: None, states.append)
    d.states = states
    return d


def test_a_text_that_could_not_be_pasted_is_said_and_kept(dict_):
    dict_.deliver = boom(RuntimeError("the clipboard is locked"))
    dict_.toggle()
    dict_.toggle()
    assert "clipboard is locked" in dict_.last["error"] and dict_.last["text"] == "hello there" and dict_.state == "idle"


def test_a_history_that_cannot_be_written_does_not_hang_dictation(dict_):
    dict_.store.fail = OSError("disk full")
    dict_.toggle()
    dict_.toggle()
    assert dict_.pending == 0 and dict_.state == "error" and "disk full" in dict_.last["error"]


def test_a_microphone_that_goes_away_does_not_hang_dictation(dict_):
    dict_.recorder.fail = RuntimeError("device unplugged")
    dict_.toggle()
    assert dict_.toggle() == "error" and "unplugged" in dict_.last["error"]
    dict_.recorder.fail = None
    assert dict_.toggle() == "recording"


# ---------------------------------------------------------------- meetings
def test_a_recorder_that_does_not_start_leaves_no_ghost_recording(tmp_path, monkeypatch):
    m = MeetingManager(Settings(tmp_path / "s.json"), None, root=tmp_path / "rec")
    monkeypatch.setattr(subprocess, "Popen", boom(OSError("blocked by policy")))
    with pytest.raises(OSError):
        m.start("Talk")
    assert m.current is None
    assert [s.meta["status"] for s in m.sessions()] == ["done"] and "blocked" in m.sessions()[0].meta["error"]


def test_the_call_watcher_survives_an_error_of_what_it_starts():
    calls = []
    w = Watcher(boom(RuntimeError("no card")), lambda: calls.append("end"), probe=lambda: ["MSTeams"], grace=0)
    w.step(1)
    w.probe = lambda: []
    w.step(100)
    assert calls == ["end"]


def test_two_threads_write_the_same_file(tmp_path):
    f, errors = tmp_path / "meta.json", []

    def write():
        for n in range(60):
            try:
                files.save_json(f, {"n": n})
            except Exception as e:
                errors.append(e)
    threads = [threading.Thread(target=write) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and files.load_json(f)["n"] >= 0 and [p.name for p in tmp_path.iterdir()] == ["meta.json"]


# ---------------------------------------------------------------- Jira and Graph
class Response:
    def __init__(self, body=b"", exc=None, ctype="application/json"):
        self.body, self.exc = body, exc
        self.headers = type("H", (), {"get_content_type": lambda s: ctype})()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def read(self):
        if self.exc:
            raise self.exc
        return self.body


@pytest.mark.parametrize("exc", [TimeoutError("slow"), ConnectionResetError("reset"), http.client.IncompleteRead(b"x")])
def test_a_jira_answer_that_breaks_off_is_a_jira_error(monkeypatch, exc):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Response(exc=exc))
    with pytest.raises(JiraError, match="cannot reach"):
        Jira("https://j.example.invalid", "", "t").myself()


def test_a_jira_answer_that_is_not_json_is_a_jira_error(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Response(b"<oops", ctype="text/plain"))
    with pytest.raises(JiraError, match="not JSON"):
        Jira("https://j.example.invalid", "", "t").myself()


class Reply:
    ok, reason = True, ""

    def __init__(self, status=200, headers=None, data=None):
        self.status_code, self.headers, self.data = status, headers or {}, data
        self.ok = status < 400

    def json(self):
        if self.data is None:
            raise ValueError("not JSON")
        return self.data


def test_graph_survives_a_retry_date_a_broken_stream_and_an_answer_that_is_not_json(monkeypatch):
    import requests

    monkeypatch.setattr(graph.time, "sleep", lambda s: None)
    g = graph.Graph(lambda: "t")
    replies = [Reply(429, {"Retry-After": "Wed, 25 Nov 2026 08:00:00 GMT"}), Reply(data={"a": 1})]
    g.s.get = lambda *a, **k: replies.pop(0)
    assert g.get("/me") == {"a": 1}
    g.s.get = boom(requests.exceptions.ChunkedEncodingError("cut"))
    with pytest.raises(graph.GraphError, match="no connection"):
        g.get("/me")
    g.s.get = lambda *a, **k: Reply()
    with pytest.raises(graph.GraphError, match="not JSON"):
        g.get("/me")


# ---------------------------------------------------------------- the updater
def test_git_that_hangs_is_a_git_error(monkeypatch):
    monkeypatch.setattr(updater, "git_exe", lambda: "git")
    monkeypatch.setattr(subprocess, "run", boom(subprocess.TimeoutExpired("git", 120)))
    with pytest.raises(updater.GitError, match="did not finish"):
        updater.git("pull")
    assert updater.about()["error"]


# ---------------------------------------------------------------- one odd conversation must not stop every Update
def test_a_message_that_cannot_be_rendered_costs_only_its_chat(tmp_path, monkeypatch):
    from powertools.assistant import collect

    from .test_assistant import make

    a, g = make(tmp_path)
    g.chat_msgs[0]["attachments"] = [{"id": "a1", "contentType": "reference", "name": "f", "contentUrl": None}]
    a.updates.start()
    assert "teams:c1" in a.state.data["items"]                                # no link, but the text is there
    monkeypatch.setattr(collect, "chat_item", boom(KeyError("from")))
    a.updates.start({"kind": "days", "days": 9})
    assert any("Jan" in p for p in a.state.data["last"]["problems"]) and a.state.data["cursor"]


def test_a_source_that_breaks_or_an_odd_calendar_entry_is_said_and_the_rest_goes_on(tmp_path, monkeypatch):
    from powertools.assistant import collect

    from .test_assistant import make

    a, g = make(tmp_path)
    monkeypatch.setattr(collect.teams, "list_chats", boom(AttributeError("odd")))
    monkeypatch.setattr(collect, "events", boom(KeyError("start")))
    a.updates.start()
    said = " ".join(a.state.data["last"]["problems"])
    assert "Teams" in said and "Calendar" in said and "mail:cv1" in a.state.data["items"] and a.state.data["cursor"] == ""


def test_an_odd_answer_is_skipped_by_the_check(tmp_path, monkeypatch):
    from powertools.assistant import collect

    from .test_assistant import make
    from .test_assistant_failures import said_in_chat

    a, g = make(tmp_path)
    a.updates.start()
    g.chat_msgs = [*g.chat_msgs, said_in_chat("t2", 0.2, "Sure.", me=True)]
    monkeypatch.setattr(collect, "chat_item", boom(KeyError("from")))
    assert a.answers.check().state == "done" and a.state.data["items"]["teams:c1"]["status"] == "open"
