"""Window API for Jira downloads. `h.app.jira` is the JiraService; jobs share the Teams/mail job list."""

from __future__ import annotations

from ..jira.client import JiraError
from .server import route


def guarded(fn):
    """Missing site/token or a refused token is a normal state for the window, not a server error."""
    def wrapper(h, *args):
        try:
            return fn(h, *args)
        except JiraError as e:
            h.send(400 if e.status in (0, 400) else 502 if e.status >= 500 else 401 if e.status in (401, 403) else 400,
                   {"error": str(e)})
    return wrapper


@route("GET", "/api/jira/status")
def jira_status(h):
    h.send(200, h.app.jira.status(check=h.query("check") == "1"))


@route("POST", "/api/jira/token")
def jira_token(h):
    h.send(200, h.app.jira.set_token(str(h.json_body().get("token", ""))))


@route("DELETE", "/api/jira/token")
def jira_token_clear(h):
    h.send(200, h.app.jira.clear_token())


@route("GET", "/api/jira/fields")
@guarded
def jira_fields(h):
    h.send(200, h.app.jira.fields())


@route("POST", "/api/jira/search")
@guarded
def jira_search(h):
    b = h.json_body()
    h.send(200, h.app.jira.search(dict(b.get("form", {})), int(b.get("limit", 200))))


@route("POST", "/api/jira/download")
@guarded
def jira_download(h):
    b = h.json_body()
    h.send(200, h.app.jira.download(dict(b.get("seed", {})), dict(b.get("options", {}))).view())


@route("GET", "/api/jira/downloaded")
def jira_downloaded(h):
    h.send(200, h.app.jira.downloaded())


@route("POST", "/api/jira/refresh")
@guarded
def jira_refresh(h):
    b = h.json_body()
    h.send(200, h.app.jira.refresh(list(b.get("keys") or []) or None, dict(b.get("options", {}))).view())
