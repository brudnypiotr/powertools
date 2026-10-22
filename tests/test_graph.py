"""Graph probes against fake responses: catches logic bugs without a token or network."""

from datetime import datetime, timedelta, timezone

import checks.graph as G
from checks.common import FAIL, OK, Context

NOW = datetime.now(timezone.utc)
EVENT = {"start": {"dateTime": (NOW - timedelta(days=1)).isoformat()},
         "end": {"dateTime": (NOW - timedelta(days=1) + timedelta(hours=1)).isoformat()},
         "onlineMeeting": {"joinUrl": "https://example.invalid/join"}, "attendees": [{}, {}], "bodyPreview": "x"}
# payload {"scp": "Chat.Read Mail.Read", "exp": 9999999999}
TOKEN = "h.eyJzY3AiOiJDaGF0LlJlYWQgTWFpbC5SZWFkIiwiZXhwIjo5OTk5OTk5OTk5fQ.s"


def fake_get(self, path, params=None, headers=None, raw=False):
    if raw:
        return 200, b"WEBVTT\n<v A>a</v>\n<v B>b</v>\n", ""
    routes = {
        "/me/chats": {"value": [{"id": "c1", "chatType": "oneOnOne", "members": [{"displayName": "x"}]},
                                {"id": "c2", "chatType": "meeting", "members": []}]},
        "/me/joinedTeams": {"value": [{"id": "t1"}]},
        "/me/mailFolders": {"value": [{"childFolderCount": 3}]},
        "/me/messages": {"value": [{"id": "m1", "conversationId": "cv", "hasAttachments": True}]},
        "/me/calendarView": {"value": [EVENT]},
        "/me/onlineMeetings": {"value": [{"id": "om1"}]},
    }
    if path in routes:
        return 200, routes[path], ""
    if path.startswith("/chats/"):
        return 200, {"value": [{"attachments": [{"contentType": "reference", "contentUrl": "https://e.invalid/f"}]}]}, ""
    if path.startswith("/shares/"):
        return 200, {"id": "i1", "size": 2048, "parentReference": {"driveId": "d1"}}, ""
    if path.endswith("/channels"):
        return 200, {"value": [{"id": "ch1"}]}, ""
    if "/channels/" in path:
        return 403, None, "Forbidden: missing scope"
    if path.endswith("/attachments"):
        return 200, {"value": [{"id": "a1", "@odata.type": "#microsoft.graph.fileAttachment",
                                "contentType": "application/pdf"}]}, ""
    if path.endswith("/transcripts"):
        return 200, {"value": [{"id": "tr1"}]}, ""
    return 200, {"value": []}, ""


def run_with(monkeypatch, getter):
    monkeypatch.setattr(G.Graph, "__init__", lambda self, token: None)
    monkeypatch.setattr(G.Graph, "get", getter)
    monkeypatch.setattr(G, "load_token", lambda ctx, prompt=True: TOKEN)
    ctx = Context(interactive=False)
    G.run(ctx)
    return {r.id: r for r in ctx.results}


def test_all_probes_run(monkeypatch):
    res = run_with(monkeypatch, fake_get)
    assert res["graph.token.scopes"].detail == "Chat.Read Mail.Read"
    for key in ("graph.chats.types", "graph.chat.file.download", "graph.mail.attachment.download",
                "graph.calendar", "graph.transcripts"):
        assert res[key].status == OK, key
    assert "ChannelMessage.Read.All" in res["graph.teams.channel.messages"].detail
    assert "2 distinct speakers" in res["graph.transcripts.content"].detail


def test_rejected_token_stops(monkeypatch):
    res = run_with(monkeypatch, lambda self, path, params=None, headers=None, raw=False: (401, None, "InvalidAuth"))
    assert res["graph.me"].status == FAIL
    assert res["graph"].status == FAIL
    assert "graph.chats.list" not in res


def test_report_never_contains_content(monkeypatch):
    res = run_with(monkeypatch, fake_get)
    text = " ".join(r.detail for r in res.values())
    for secret in ("example.invalid", "e.invalid", "<v A>", "displayName"):
        assert secret not in text
