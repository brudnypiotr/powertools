"""Window API for Teams, mail and export jobs. `h.app.graph` is the GraphService."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

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


@route("DELETE", "/api/jobs")
def jobs_clear(h):
    h.app.graph.jobs.clear()
    h.send(200, [j.view() for j in reversed(h.app.graph.jobs.all)])


@route("GET", "/api/jobs")
def jobs(h):
    h.send(200, [j.view() for j in reversed(h.app.graph.jobs.all)])


OUTPUT_KINDS = {"teams": "teams", "mail": "mail", "org": "org", "jira": "jira"}


def _reveal(p, folder: bool) -> None:
    """Open an exported file, or show it selected in its folder."""
    if sys.platform == "win32":
        if folder:
            subprocess.Popen(f'explorer /select,"{p}"')  # noqa: S602,S603 (Explorer on the user's own file)
        else:
            os.startfile(p)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(p)] if folder else ["open", str(p)])


@route("POST", "/api/open-path")
def open_path(h):
    """Open file / Open folder next to an export; only paths inside the export folder."""
    b = h.json_body() or {}
    root = output_dir(h.app.settings).resolve()
    p = Path(str(b.get("path", ""))).resolve()
    if root not in p.parents or not p.exists():
        h.send(400, {"error": "not an exported file"})
        return
    _reveal(p, bool(b.get("folder")))
    h.send(200, {"path": str(p)})


@route("POST", "/api/open-output")
def open_output(h):
    kind = OUTPUT_KINDS.get(str((h.json_body() or {}).get("kind", "")), "")
    d = output_dir(h.app.settings) / kind if kind else output_dir(h.app.settings)
    d.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(d)  # noqa: S606 (opens Explorer on the user's own folder)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(d)])
    h.send(200, {"path": str(d)})
