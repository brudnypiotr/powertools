"""Window API for Teams, mail and export jobs. `h.app.graph` is the GraphService."""

from __future__ import annotations

import os
import subprocess
import sys

from ..config import output_dir
from ..graph.client import TokenExpired
from .server import route


def guarded(fn):
    """A missing or expired token is a normal state for the window, not a server error."""
    def wrapper(h, *args):
        try:
            return fn(h, *args)
        except TokenExpired:
            h.send(401, {"error": "token", "message": "Paste a fresh token from Graph Explorer."})
    return wrapper


@route("GET", "/api/graph/status")
def graph_status(h):
    h.send(200, h.app.graph.status())


@route("POST", "/api/graph/token")
def graph_token(h):
    h.send(200, h.app.graph.set_token(str(h.json_body().get("token", ""))))


@route("POST", "/api/graph/token/fetch")
def graph_token_fetch(h):
    """Open Graph Explorer in its own app window (Edge, same profile: already signed in) and catch the copied token."""
    from ..app import open_app_window
    from ..graph.service import GRAPH_EXPLORER

    if not open_app_window(GRAPH_EXPLORER):
        import webbrowser
        webbrowser.open(GRAPH_EXPLORER)
    h.send(200, h.app.graph.watch_clipboard())


@route("DELETE", "/api/graph/token")
def graph_token_clear(h):
    h.app.graph.tokens.clear()
    h.send(200, h.app.graph.status())


@route("GET", "/api/teams/chats")
@guarded
def teams_chats(h):
    h.send(200, h.app.graph.chats(refresh=h.query("refresh") == "1"))


@route("POST", "/api/teams/export")
@guarded
def teams_export(h):
    b = h.json_body()
    h.send(200, h.app.graph.export_chats(list(b.get("ids", [])), dict(b.get("range", {}))).view())


@route("GET", "/api/mail/folders")
@guarded
def mail_folders(h):
    h.send(200, h.app.graph.folders())


@route("POST", "/api/mail/threads")
@guarded
def mail_threads(h):
    b = h.json_body()
    h.send(200, h.app.graph.threads(str(b.get("folder", "")), str(b.get("person", "")), str(b.get("subject", "")),
                                    dict(b.get("range", {}))))


@route("POST", "/api/mail/export")
@guarded
def mail_export(h):
    b = h.json_body()
    h.send(200, h.app.graph.export_threads(list(b.get("items", [])), dict(b.get("range", {}))).view())


@route("GET", "/api/jobs")
def jobs(h):
    h.send(200, [j.view() for j in reversed(h.app.graph.jobs.all)])


@route("POST", "/api/open-output")
def open_output(h):
    d = output_dir(h.app.settings)
    if sys.platform == "win32":
        os.startfile(d)  # noqa: S606 (opens Explorer on the user's own folder)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(d)])
    h.send(200, {"path": str(d)})
