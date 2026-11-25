"""The app with a fictional mailbox and a model that answers at once: for the window tests, and to look at the window
(python -m tests.demo, then http://127.0.0.1:48777/). Nothing real is read and nothing leaves the process."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import numpy as np

from powertools.engine import Result
from powertools.graph.client import TokenExpired

from . import test_assistant as t

PEOPLE = [("Anna Kowalska", "anna"), ("Ola Nowak-Wiśniewska", "ola"), ("Mark Johnson", "mark"), ("Priya Raman", "priya"),
          ("Thomas Müller", "thomas"), ("Jan Zieliński", "jan")]
MAILS = [  # category, title, summary, action, due
    ("todo", "Review the acceptance criteria for release 4.2", "The customer waits for your review of the acceptance criteria before Thursday's workshop.",
     "Review the acceptance criteria and mark open points", "Thursday"),
    ("reply", "Question about the migration window", "Is the window on 12 December still fine, and who confirms it?", "Confirm the migration window", ""),
    ("todo", "Budget for Q1: pick option A or B", "Finance needs a choice between option A and option B for Q1.", "Choose option A or B", "Wednesday 26 Nov"),
    ("reply", "RE: RE: FW: Statement of work, version 7 (final, really final) with the changes the legal department asked for last week",
     "Legal asks whether clause 8.2 can stay.", "Answer on clause 8.2", "tomorrow"),
    ("waiting", "Signed contract", "Ola owes the signed contract.", "the signed contract", ""),
    ("waiting", "Test data from the customer", "Mark promised the test data.", "the anonymised test data", "last Friday"),
    ("fyi", "Release 4.1 is live", "Release 4.1 went live on Monday without incidents.", "", ""),
    ("fyi", "Team lunch moved", "Lunch moved to Friday.", "", ""),
    ("ignore", "Weekly digest", "Automatic message.", "", "")]


def three(kinds=("todo", "todo", "reply")) -> list[dict]:
    texts = ("Book the room", "Send the agenda", "Confirm the window with Jan")
    return [{"same": "", "kind": k, "action": a, "due": "", "person": "Jan", "quote": "", "decision": False} for k, a in zip(kinds, texts, strict=True)]


def triage(user: str) -> dict:
    if "THREE THINGS" in user:
        return {"summary": "Jan plans the day.", "category": "fyi", "commitments": [], "asks": three()}
    for c, title, summary, action, due in MAILS:
        if title in user:
            who = next((x for x, _ in PEOPLE if x in user), "")
            promise = [{"who": "me", "to": who, "what": "send the minutes", "due": "Monday", "quote": summary}] if "Budget" in title else []
            return {**t.NOTHING, "category": c, "summary": summary, "action": action, "due": due, "person": who, "quote": summary,
                    "decision": "option A" in summary, "commitments": promise}
    return {**t.NOTHING, "category": "reply", "summary": "Jan has a question.", "action": "Answer Jan", "person": "Jan"}


def model() -> t.Model:
    return t.Model(
        triage=triage, match_matters=lambda u: {"results": [{"key": "mail:cv4", "matter": "", "new": "Contract renewal"}]},
        brief={"lines": ["Finance needs your choice between A and B by Wednesday.", "Ola has owed the signed contract for 5 days."]},
        meeting_prep={"goal": "Get a date for the signed contract", "known": ["The plan was promised for tomorrow"], "open": ["Ola: the signed contract"],
                      "questions": ["Ola: what blocks the signature?"], "agenda": ["Contract", "Plan"]},
        ask={"answer": "You wait for Ola (signed contract, 5 days).", "sources": ["S1", "S2"]},
        remember={"edits": [{"file": "profile.md", "old": "", "new": "The contract deadline is Thursday"}]},
        revise_draft={"body": "Jan, 3 pm works.", "rule": "No greeting"})


class Mailbox:
    """The fake Graph behind a token that can end at a given call."""

    def __init__(self, app, g, cut: int = 0):
        self.app, self.g, self.cut, self.calls = app, g, cut, 0

    def _token(self) -> None:
        if not self.app.graph.tokens.get():
            raise TokenExpired("no valid token")
        self.calls += 1
        if self.calls == self.cut:
            self.app.graph.tokens.clear()
            raise TokenExpired("it ended")

    def get(self, path, *a, **k):
        self._token()
        return {"value": []} if path == "/me/mailFolders" else self.g.get(path, *a, **k)

    def pages(self, path, params=None, headers=None, max_items=100_000):
        yield from self.get(path, params, headers).get("value", [])[:max_items]

    def batch(self, paths):
        self._token()
        return self.g.batch(paths)

    def reply_draft(self, *a, **k):
        self._token()
        return {"webLink": "https://outlook.example.invalid/draft"}


class Microphone:
    active, level = False, 0.0

    def start(self, name):
        pass

    def stop(self):
        return np.zeros(16000, dtype=np.float32)


class Speech:
    """Understands everything at once."""
    state, info = "ready", {}

    def submit(self, audio, done, **k):
        done(Result("the deadline is Thursday", "en", 1, 0.1))

    def retry(self):
        pass


def build(home: Path, cut: int = 0):
    """(app, the fake Graph). The token store is empty; tests put a token in with `token(app)`."""
    from powertools.app import App

    app = App(selftest=True)
    app.input_devices = lambda: ["Mic"]
    app.engine.state = "ready"
    app.settings.update({"output_dir": str(home / "out"), "memory_dir": str(home / "memory")})
    g = t.FakeGraph()
    g.threads = {}
    for n, (c, title, summary, *_rest) in enumerate(MAILS):
        name, short = PEOPLE[n % len(PEOPLE)]
        who, cv = t.addr(name, short + "@example.invalid"), f"cv{n}"
        g.threads[cv] = [t.mail("m" + cv, cv, title, 3 + n * 7, t.ME_ADDR if c == "waiting" else who, summary + " Please answer.",
                                (who,) if c == "waiting" else (t.ME_ADDR,))]
    app.llm.local.available = lambda: {"installed": True, "running": True}     # the same with and without Ollama
    app.pasted = []
    app.dictation.recorder, app.dictation.engine = Microphone(), Speech()
    app.dictation.deliver = lambda text, **k: app.pasted.append(text)
    m = model()
    m.available = app.llm.available
    app.assistant.kit.llm, app.graph.g = m, Mailbox(app, g, cut)
    for i in range(3):
        app.store.add("raw", "Please move the meeting to Thursday and tell Anna about it. " * (1 + i), "en", 5, 0.6)
    return app, g


def my_answer() -> dict:
    return {"id": "t2", "messageType": "message", "createdDateTime": t.iso(0.1), "from": {"user": {"id": "u-me", "displayName": "Piotr B"}},
            "body": {"contentType": "text", "content": "Sure, at 3 pm."}}


def token(app, seconds: int = 3600) -> str:
    """A token that looks like one of Graph Explorer (it is checked only against the fake mailbox)."""
    import base64
    import json

    body = base64.urlsafe_b64encode(json.dumps({"exp": time.time() + seconds, "aud": "https://graph.microsoft.com",
                                                "scp": "Mail.ReadWrite Chat.Read"}).encode()).decode().rstrip("=")
    app.graph.tokens.set(f"eyJh.{body}.x")
    return f"eyJh.{body}.x"


if __name__ == "__main__":
    home = Path(tempfile.mkdtemp())
    os.environ["POWERTOOLS_HOME"] = str(home)
    from powertools.web.server import serve

    demo, _ = build(home, int(os.environ.get("CUT", "0")))
    if os.environ.get("TOKEN", "1") == "1":
        token(demo)
    serve(demo, 48777)
    print(demo.door.link("http://127.0.0.1:48777/"), flush=True)
    while True:
        time.sleep(60)
