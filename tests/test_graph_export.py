"""Teams and mail export against a fake Graph: Markdown content, files, cursors, ranges, token handling."""

import base64
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from powertools import export
from powertools.graph import mail, teams
from powertools.graph.client import TokenExpired
from powertools.graph.service import GraphService, resolve_since
from powertools.graph.token import TokenStore
from powertools.config import Settings
from powertools.store import Store

NOW = datetime.now(timezone.utc)


def ts(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S.1234567Z")


def make_token(exp_in: int = 3600) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": time.time() + exp_in, "scp": "Chat.Read Mail.Read"}).encode())
    return "h." + payload.decode().rstrip("=") + ".s"


class FakeGraph:
    def __init__(self, routes, downloads=None):
        self.routes, self.downloads, self.calls = routes, downloads or {}, []

    def get(self, path, params=None, headers=None):
        self.calls.append((path, params))
        for prefix, value in self.routes.items():
            if path.startswith(prefix):
                return value(params) if callable(value) else value
        raise AssertionError(f"unexpected GET {path}")

    def pages(self, path, params=None, headers=None, max_items=100_000):
        yield from self.get(path, params, headers).get("value", [])[:max_items]

    def download(self, path):
        for prefix, data in self.downloads.items():
            if path.startswith(prefix):
                return data
        raise AssertionError(f"unexpected download {path}")


CHAT = {"id": "c1", "type": "oneOnOne", "title": "Anna Nowak", "members": ["Anna Nowak", "Me"]}
MESSAGES = [
    {"id": "m1", "messageType": "message", "createdDateTime": ts(30), "from": {"user": {"displayName": "Anna Nowak"}},
     "body": {"contentType": "html", "content": "<p>Hej <at id=\"0\">Piotr</at>, plik:</p><attachment id=\"a1\"></attachment>"},
     "attachments": [{"id": "a1", "contentType": "reference", "name": "spec v2.docx",
                      "contentUrl": "https://example.invalid/sites/x/spec%20v2.docx"}]},
    {"id": "m2", "messageType": "systemEventMessage", "createdDateTime": ts(20), "body": {"content": ""}},
    {"id": "m3", "messageType": "message", "createdDateTime": ts(2), "from": {"user": {"displayName": "Me"}},
     "lastEditedDateTime": ts(1), "body": {"contentType": "html", "content": "Dzięki&nbsp;<b>wielkie</b><br>OK"},
     "attachments": [{"id": "q1", "contentType": "messageReference",
                      "content": json.dumps({"messagePreview": "plik:", "messageSender": {"user": {"displayName": "Anna Nowak"}}})}]},
    {"id": "m4", "messageType": "message", "createdDateTime": ts(1), "deletedDateTime": ts(0.5),
     "from": {"user": {"displayName": "Me"}}, "body": {"contentType": "text", "content": "deleted"}},
]


def chat_graph():
    return FakeGraph({"/chats/c1/messages": {"value": MESSAGES},
                      "/shares/": {"size": 10, "file": {}}},
                     {"/shares/": b"DOCX"})


def test_html_to_text():
    out = export.html_to_text('<p>A&amp;B <a href="https://x.y">link</a></p><ul><li>one</li><li>two</li></ul><img src="z">')
    assert out == "A&B [link](https://x.y)\n\n- one\n- two\n[image]"


def test_chat_export_markdown_and_files(tmp_path):
    notes = []
    path, newest = teams.export_chat(chat_graph(), CHAT, None, NOW, tmp_path, notes.append)
    md = path.read_text(encoding="utf-8")
    assert path.parent == tmp_path / "teams" / "anna-nowak"
    assert (path.parent / "files" / "spec v2.docx").read_bytes() == b"DOCX"
    assert '"source": "teams-chat"' not in md and 'source: "teams-chat"' in md
    assert "Hej @Piotr, plik:" in md and "[spec v2.docx](files/spec%20v2.docx)" in md
    assert "> Re Anna Nowak: plik:" in md and "Dzięki wielkie\nOK" in md and "(edited)" in md
    assert "deleted" not in md and "messages: 2" in md
    assert newest == MESSAGES[2]["createdDateTime"]


def test_chat_range_filters_by_creation_time(tmp_path):
    g = chat_graph()
    path, _ = teams.export_chat(g, CHAT, NOW - timedelta(hours=10), NOW, tmp_path, lambda n: None)
    md = path.read_text(encoding="utf-8")
    assert "Hej" not in md and "Dzięki" in md
    params = [p for (pth, p) in g.calls if pth == "/chats/c1/messages"][0]
    assert "lastModifiedDateTime gt" in params["$filter"]


def test_resolve_since():
    assert resolve_since({"kind": "all"}) is None
    assert resolve_since({"kind": "new"}, "") is None
    assert resolve_since({"kind": "new"}, "2026-10-20T10:00:00Z") > export.parse_time("2026-10-20T10:00:00Z")
    assert resolve_since({"kind": "since", "date": "2026-10-01"}).day == 1
    with pytest.raises(ValueError):
        resolve_since({"kind": "since", "date": "1.10.2026"})


THREAD = [
    {"id": "e1", "subject": "Plan Q4", "conversationId": "cv1", "receivedDateTime": ts(48), "hasAttachments": True,
     "from": {"emailAddress": {"name": "Jan Kowalski", "address": "jan@example.invalid"}},
     "toRecipients": [{"emailAddress": {"name": "Me"}}], "ccRecipients": [],
     "uniqueBody": {"contentType": "text", "content": "Cześć,\r\nw załączniku plan."}},
    {"id": "e2", "subject": "RE: Plan Q4", "conversationId": "cv1", "receivedDateTime": ts(3), "hasAttachments": False,
     "from": {"emailAddress": {"name": "Me"}}, "toRecipients": [{"emailAddress": {"name": "Jan Kowalski"}}],
     "ccRecipients": [{"emailAddress": {"name": "Ola"}}], "uniqueBody": {"contentType": "text", "content": "Dzięki!"}},
]


def mail_graph():
    return FakeGraph({"/me/messages/e1/attachments": {"value": [
                          {"id": "at1", "@odata.type": "#microsoft.graph.fileAttachment", "name": "plan.pdf", "size": 3},
                          {"id": "at2", "@odata.type": "#microsoft.graph.fileAttachment", "name": "logo.png", "isInline": True}]},
                      "/me/messages": {"value": THREAD}},
                     {"/me/messages/e1/attachments/at1/$value": b"PDF"})


def test_mail_thread_export(tmp_path):
    path, newest = mail.export_thread(mail_graph(), "cv1", None, NOW, tmp_path, lambda n: None)
    md = path.read_text(encoding="utf-8")
    assert path.parent == tmp_path / "mail" / "plan-q4"
    assert (path.parent / "attachments" / "plan.pdf").read_bytes() == b"PDF"
    assert not (path.parent / "attachments" / "logo.png").exists()
    assert "w załączniku plan." in md and "Dzięki!" in md and "(cc: Ola)" in md
    assert "Attachment: [plan.pdf](attachments/plan.pdf)" in md
    assert newest == THREAD[1]["receivedDateTime"]


def test_mail_search_groups_threads():
    g = FakeGraph({"/me/messages": {"value": THREAD}})
    threads = mail.search_threads(g, person="Jan Kowalski", since=NOW - timedelta(days=5))
    assert len(threads) == 1 and threads[0]["count"] == 2 and threads[0]["attachments"]
    search = g.calls[0][1]["$search"]
    assert 'participants:\\"Jan Kowalski\\"' in search and "received>=" in search


def test_token_store(tmp_path):
    t = TokenStore(tmp_path / "tok")
    with pytest.raises(ValueError):
        t.set("not a token")
    t.set("Bearer " + make_token())
    assert TokenStore(tmp_path / "tok").get() and "Mail.Read" in t.scopes()
    t.set(make_token(exp_in=10))
    assert t.get() == ""  # about to expire counts as missing


def test_service_export_job_and_cursor(tmp_path, monkeypatch):
    monkeypatch.setenv("POWERTOOLS_HOME", str(tmp_path / "home"))
    s = Settings(tmp_path / "s.json")
    s.update({"output_dir": str(tmp_path / "out")})
    svc = GraphService(s, Store(tmp_path / "h.sqlite"))
    svc.g = chat_graph()
    svc._chats = [CHAT]
    job = svc.export_chats(["c1"], {"kind": "new"})
    for _ in range(100):
        if job.state not in ("queued", "running"):
            break
        time.sleep(0.05)
    assert job.state == "done" and len(job.files) == 1
    assert svc.store.cursor("teams", "c1") == MESSAGES[2]["createdDateTime"]
    job2 = svc.export_chats(["c1"], {"kind": "new"})
    for _ in range(100):
        if job2.state not in ("queued", "running"):
            break
        time.sleep(0.05)
    assert job2.files == [] and "no messages" in job2.notes[0]


def test_job_pauses_on_expired_token(tmp_path, monkeypatch):
    monkeypatch.setenv("POWERTOOLS_HOME", str(tmp_path / "home"))
    svc = GraphService(Settings(tmp_path / "s.json"), Store(tmp_path / "h.sqlite"))

    def boom(job):
        raise TokenExpired("x")

    job = svc.jobs.submit("t", boom)
    for _ in range(100):
        if job.state == "needs_token":
            break
        time.sleep(0.05)
    assert job.state == "needs_token" and svc.jobs.resume_paused() == 1
