"""Ideas and problems as GitHub issues: the app opens a filled-in new issue in the browser, the user reads it and submits it.

No token and nothing in the background. Diagnostics are counters only: no text, names or titles.
"""

from __future__ import annotations

import re
from urllib.parse import urlencode

from . import __version__, updater

MAX_URL = 6000


def repo_url() -> str:
    """https address of the repository this copy updates from."""
    try:
        url = updater.git("remote", "get-url", "origin")
    except Exception:
        url = updater.update_source()["remote"]
    url = re.sub(r"^git@([^:]+):", r"https://\1/", url)
    url = re.sub(r"^https://[^@/]+@", "https://", url)
    return url.removesuffix(".git").rstrip("/")


def counters(app) -> list[str]:
    v = app.assistant.view()
    u = app.llm.usage.today()
    return [f"version {__version__}", f"model {app.llm.model}", f"last update: {v['last'].get('found', 0)} conversations, "
            f"{v['last'].get('new', 0)} new, {v['last'].get('seconds', 0)} s, {len(v['last'].get('problems', []))} problem(s)",
            f"answers checked {v['reviewed']}, right {v['agreed']}", f"matters {len(v['matters'])}, drafts {v['drafts']}",
            f"AI credits today {u['credits']:.1f} in {u['calls']} calls"]


def issue_url(app, text: str, diagnostics: bool) -> str:
    text = text.strip()
    if not text:
        raise ValueError("say what the idea is")
    title = text.splitlines()[0][:80]
    body = text + ("\n\n---\n" + "\n".join(f"- {c}" for c in counters(app)) if diagnostics else "")
    url = f"{repo_url()}/issues/new?" + urlencode({"title": title, "body": body})
    while len(url) > MAX_URL and len(body) > 200:
        body = body[: int(len(body) * 0.8)] + " [...]"
        url = f"{repo_url()}/issues/new?" + urlencode({"title": title, "body": body})
    return url
