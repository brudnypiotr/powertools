"""The report for support: versions, counts, flags and the last errors. Never text, names, titles, addresses or tokens:
of an error only the place and the kind stay, and everything the app knows by name is taken out of log lines."""

from __future__ import annotations

import json
import platform
import re
import sys
from collections import Counter
from importlib import metadata

from . import __version__, updater
from .config import DEFAULTS, data_dir

PACKAGES = ("microsoft-copilot-sdk", "faster-whisper", "onnx-asr", "ctranslate2", "PyAudioWPatch", "requests", "pystray")
PRIVATE = {"exclude", "vocabulary", "replacements", "jira_url", "jira_email", "memory_dir", "output_dir", "input_device",
           "meeting_mic", "meeting_output"}
LINES = 120
HEAD = re.compile(r"^(\d{4}-\d\d-\d\d\S*(?: \d\d:\d\d:\d\d\S*)?) (WARNING|ERROR|CRITICAL) +(\S+?): ?(.*)$")
PLACE = re.compile(r'^\s+File "[^"]*?([^"\\/]+)", (line \d+, in \S+)')
KIND = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception|Busy|Expired|Stopped|Timeout)\w*)(?::|$)")


def known_words(app) -> list[str]:
    """What the app knows by name: it must not be in the report."""
    a, s = app.assistant, app.settings
    words = {a.me, *s["exclude"], *s["vocabulary"]}
    for i in list(a.data["items"].values()):
        words |= {i["title"], *i["people"].split(", ")}
    words |= {p["name"] for p in a.data["people"]} | {e["title"] for e in a.data["events"]}
    words |= {m["title"] for m in a.memory.matters(every=True)} | {x.meta.get("title", "") for x in app.meetings.sessions(100)}
    return sorted((w for w in words if len(w) > 2), key=len, reverse=True)


def redact(text: str, words: list[str]) -> str:
    text = re.sub(r"https?://\S+|[\w.+-]+@[\w.-]+|(?:[A-Za-z]:\\|/Users/|/home/)\S+|\b(?:mail|teams|jira|meeting):\S+", "<...>", text)
    text = re.sub(r"'[^']*'|\"[^\"]*\"", "<...>", text)
    for w in words:
        text = re.sub(re.escape(w), "<...>", text, flags=re.I)
    return text[:300]


def errors(text: str, words: list[str]) -> list[str]:
    """Warnings and errors of the log: the line without names, and where an exception came from."""
    out = []
    for line in text.splitlines():
        head, place, kind = HEAD.match(line), PLACE.match(line), KIND.match(line)
        if head:
            out.append(f"{head[1]} {head[2]} {head[3]}: {redact(head[4], words)}")
        elif place:
            out.append(f"  {place[1]} {place[2]}")
        elif kind:
            out.append(f"  {kind[1]}")
    return out[-LINES:]


def _version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return ""


def _commit() -> str:
    try:
        return updater.git("log", "-1", "--format=%h %cs", timeout=10)
    except Exception:
        return ""


def report(app) -> dict:
    a, words = app.assistant, known_words(app)
    items, last, usage, engine = list(a.data["items"].values()), a.data["last"], app.llm.usage.today(), app.engine.status()
    log = data_dir() / "app.log"
    return {
        "app": {"version": __version__, "commit": _commit(), "before": updater.previous().get("version", "")},
        "system": {"python": sys.version.split()[0], "os": platform.platform(), "packages": {p: _version(p) for p in PACKAGES}},
        "speech": {k: engine.get(k) for k in ("state", "model", "device", "compute", "note")},
        "model": {**{k: v for k, v in app.llm.available().items() if k in ("provider", "model", "installed", "running", "limit", "github_days")},
                  "calls_today": usage["calls"], "credits_today": usage["credits"], "estimated_calls": usage.get("estimated", 0)},
        "graph": {k: v for k, v in app.graph.status().items() if k in ("has_token", "expires_min", "chats", "mail")} | {"scopes": app.graph.tokens.scopes()},
        "recap": {"items": Counter(i.get("user_category") or i.get("category") or "new" for i in items),
                  "status": Counter(i["status"] for i in items), "not_sorted": sum(bool(i.get("error")) for i in items),
                  "waiting_to_be_sorted": sum(bool(i.get("pending")) for i in items), "cursor": a.data["cursor"], "updated": a.data["updated"],
                  "last": {k: last.get(k) for k in ("found", "new", "seconds", "credits")}, "problems": len(last.get("problems", [])),
                  "matters": len(a.memory.matters(every=True)), "drafts": len(a.drafts.list()), "people": len(a.data["people"])},
        "jobs": [{"kind": j.kind, "state": j.state, "done": j.done, "total": j.total, "error": redact(j.error, words)} for j in app.graph.jobs.all[-10:]],
        "settings": {k: (len(v) if isinstance(v, list) else bool(v)) if k in PRIVATE else v for k, v in app.settings.all().items() if k in DEFAULTS},
        "errors": errors(log.read_text(encoding="utf-8", errors="replace"), words) if log.exists() else [],
    }


def as_text(rep: dict) -> str:
    return json.dumps(rep, ensure_ascii=False, indent=1, default=str)
