"""Ideas and problems: a zip on the Desktop the user sends (Teams or mail), or a filled-in GitHub issue when the copy follows
an organisation's own repository. Nothing is sent by the app, and the public repository never gets an idea (the user's
words can be about work)."""

from __future__ import annotations

import base64
import json
import os
import re
import time
import zipfile
from pathlib import Path
from urllib.parse import urlencode

from . import __version__, diagnostics, updater

MAX_URL = 6000
PUBLIC = "github.com/brudnypiotr/powertools"
PICTURE = re.compile(r"data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)")
MAX_PICTURES = 6


def repo_url() -> str:
    """https address of the repository this copy updates from."""
    try:
        url = updater.git("remote", "get-url", "origin")
    except Exception:
        url = updater.update_source()["remote"]
    url = re.sub(r"^git@([^:]+):", r"https://\1/", url)
    url = re.sub(r"^https://[^@/]+@", "https://", url)
    return url.removesuffix(".git").rstrip("/")


def issues() -> bool:
    return PUBLIC not in repo_url().lower()


def contact() -> str:
    """Who gets feedback (setup/site.json of an organisation's copy: {"contact": "someone@..."}); '' when nobody is named."""
    try:
        who = json.loads((updater.ROOT / "setup" / "site.json").read_text(encoding="utf-8")).get("contact") or ""
    except (OSError, ValueError):
        who = ""
    return who if re.fullmatch(r"[\w.+-]+@[\w.-]+", who) else ""


def desktop() -> Path:
    """The user's Desktop (also when OneDrive keeps it), else the home folder."""
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
                p = Path(os.path.expandvars(winreg.QueryValueEx(k, "Desktop")[0]))
            if p.is_dir():
                return p
        except OSError:
            pass
    p = Path.home() / "Desktop"
    return p if p.is_dir() else Path.home()


def package(app, text: str, with_report: bool, pictures: list[str]) -> Path:
    """powertools-feedback-<time>.zip: the user's words, pasted pictures and (when wanted) the report."""
    text = _needed(text)
    out = desktop() / f"powertools-feedback-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1000:03d}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("feedback.md", f"# Feedback\n\npowertools {__version__}, {time.strftime('%Y-%m-%d %H:%M')}\n\n{text}\n")
        for n, m in enumerate((m for p in pictures[:MAX_PICTURES] if (m := PICTURE.fullmatch(p))), 1):
            z.writestr(f"screenshots/{n}.{m[1].replace('jpeg', 'jpg')}", base64.b64decode(m[2]))
        if with_report:
            z.writestr("diagnostics.json", diagnostics.as_text(diagnostics.report(app)))
    return out


def _needed(text: str) -> str:
    if not text.strip():
        raise ValueError("say what the idea or the problem is")
    return text.strip()


def counters(app) -> list[str]:
    r = diagnostics.report(app)
    return [f"version {r['app']['version']}", f"model {r['model']['model']}", f"matters {r['recap']['matters']}, drafts {r['recap']['drafts']}",
            f"last update: {r['recap']['last']}, {r['recap']['problems']} problem(s)", f"AI credits today {r['model']['credits_today']:.1f}"]


def issue_url(app, text: str, with_counters: bool) -> str:
    text = _needed(text)
    if not issues():
        raise ValueError("this copy follows the public repository: make the file instead")
    title = text.splitlines()[0][:80]
    body = text + ("\n\n---\n" + "\n".join(f"- {c}" for c in counters(app)) if with_counters else "")
    url = f"{repo_url()}/issues/new?" + urlencode({"title": title, "body": body})
    while len(url) > MAX_URL and len(body) > 200:
        body = body[: int(len(body) * 0.8)] + " [...]"
        url = f"{repo_url()}/issues/new?" + urlencode({"title": title, "body": body})
    return url
