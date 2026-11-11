"""Recap: mail (Inbox) and Teams chats of the last N days, sorted by the local model into
todo / reply / fyi / ignore, with a one-line summary and the next step.

An experiment: the user can move an item to the right group, and the tab counts how often the model was right.
Results and corrections stay in %LOCALAPPDATA%\\powertools (recap.json); a classification is cached per
conversation and its latest message, so running the recap again only asks the model about what changed.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from . import export
from .config import data_dir
from .graph import mail, teams
from .graph.service import resolve_since
from .llm import BACKGROUND, LLMError, parse_json

log = logging.getLogger("powertools.recap")
CATEGORIES = ["todo", "reply", "fyi", "ignore"]
MAX_CHARS = 6000          # text per conversation sent to the model (the newest part)
MAX_CHATS = 80
AUTOMATED = re.compile(r"no-?reply|do-?not-?reply|notification|mailer-daemon|postmaster|newsletter", re.I)

SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": CATEGORIES},
        "summary": {"type": "string"},
        "action": {"type": "string"},
        "due": {"type": "string"},
    },
    "required": ["category", "summary", "action", "due"],
}


def system_prompt(me: str) -> str:
    return f"""You triage the work mail and Teams chats of {me} after some time away. For ONE conversation decide what
{me} has to do now:
- "todo": {me} is asked or clearly expected to do something besides replying: a task, a review, a fix, a decision,
  a document, preparing for or attending something.
- "reply": someone asked {me} a question or is waiting for an answer from {me}, and nobody has answered it yet.
- "fyi": worth knowing for {me}'s work, but nothing to do: updates, decisions already made, relevant announcements.
- "ignore": newsletters, automatic notifications, marketing, mass mailings, out-of-office replies, things that do not
  concern {me}, or things already handled.
Most conversations are "fyi" or "ignore". Choose "todo" or "reply" only when the text shows that {me} personally has
to act. When the latest message is from {me}, the ball is with the others: choose "fyi" or "ignore" unless {me}
still owes something.
"summary": one sentence in English: what it is about. "action": for todo and reply the concrete next step for {me}
(imperative, at most 15 words), else "". "due": a deadline mentioned in the text, as written, else ""."""


def _short(text: str, n: int = 1500) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text or "").strip()
    return text if len(text) <= n else text[:n] + " [...]"


def mail_items(g, me: dict, since: datetime) -> list[dict]:
    addresses = {a.lower() for a in (me.get("mail"), me.get("userPrincipalName")) if a}
    items = []
    for t in mail.search_threads(g, "inbox", since=since, limit=400):
        msgs = mail.fetch_thread(g, t["id"])   # received and sent: shows whether it was answered
        if not msgs:
            continue
        lines, senders = [], set()
        for m in msgs[-6:]:
            addr = ((m.get("from") or {}).get("emailAddress") or {}).get("address", "")
            senders.add(addr.lower())
            when = export.local(export.parse_time(m.get("receivedDateTime") or m.get("sentDateTime"))).strftime("%Y-%m-%d %H:%M")
            frm = ", ".join(mail.person_names([m.get("from")])) if m.get("from") else "?"
            to = ", ".join(mail.person_names(m.get("toRecipients")))
            cc = ", ".join(mail.person_names(m.get("ccRecipients")))
            lines.append(f"--- {when} from {frm} <{addr}> to {to}" + (f" cc {cc}" if cc else "") +
                         f"\n{_short(mail.body_text(m))}")
        last = msgs[-1]
        last_addr = ((last.get("from") or {}).get("emailAddress") or {}).get("address", "").lower()
        items.append({
            "key": "mail:" + t["id"], "source": "mail", "title": t["subject"],
            "people": ", ".join(t["people"]), "last": last.get("receivedDateTime") or last.get("sentDateTime") or "",
            "link": last.get("webLink", ""), "count": len(msgs), "last_from_me": last_addr in addresses,
            "automated": bool(senders) and all(AUTOMATED.search(s or "") for s in senders - addresses),
            "text": f"E-mail thread. Subject: {t['subject']}\n\n" + "\n\n".join(lines),
        })
    return items


def chat_items(g, me: dict, since: datetime, until: datetime) -> list[dict]:
    items = []
    chats = [c for c in teams.list_chats(g, me.get("id", "")) if c["last"] and export.parse_time(c["last"]) >= since]
    for c in chats[:MAX_CHATS]:
        msgs = teams.fetch_messages(g, c["id"], since, until)
        if not msgs:
            continue
        lines = []
        for m in msgs[-15:]:
            t = export.local(export.parse_time(m["createdDateTime"])).strftime("%Y-%m-%d %H:%M")
            lines.append(f"{teams.sender(m)} {t}: {_short(teams.render_message(m, {}), 600)}")
        last_user = ((msgs[-1].get("from") or {}).get("user") or {}).get("id", "")
        kind = {"oneOnOne": "1:1 chat", "group": "group chat", "meeting": "meeting chat"}.get(c["type"], "chat")
        items.append({
            "key": "teams:" + c["id"], "source": "teams", "title": c["title"],
            "people": ", ".join(c["members"][:8]), "last": msgs[-1]["createdDateTime"], "link": c.get("web", ""),
            "count": len(msgs), "last_from_me": bool(last_user) and last_user == me.get("id"), "automated": False,
            "text": f"Teams {kind}: {c['title']}\n\n" + "\n".join(lines),
        })
    return items


class Recap:
    def __init__(self, settings, graph, llm, path: Path | None = None):
        self.settings, self.graph, self.llm = settings, graph, llm
        self.path = path or data_dir() / "recap.json"
        self._lock = threading.Lock()
        self.data = self._load()
        self.job = None

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"items": [], "cache": {}, "range": {}, "created": 0}

    def _save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)

    # ------------------------------------------------------------------ window
    def view(self) -> dict:
        items = [{k: v for k, v in i.items() if k != "text"} for i in self.data.get("items", [])]
        reviewed = [i for i in items if i.get("user_category")]
        return {"items": items, "range": self.data.get("range", {}), "created": self.data.get("created", 0),
                "model": self.data.get("model", ""), "job": self.job.view() if self.job else None,
                "reviewed": len(reviewed), "agreed": sum(i["user_category"] == i.get("category") for i in reviewed)}

    def correct(self, key: str, category: str) -> dict:
        if category not in CATEGORIES + [""]:
            raise ValueError(f"category must be one of {', '.join(CATEGORIES)}")
        for i in self.data.get("items", []):
            if i["key"] == key:
                i["user_category"] = category
                c = self.data.setdefault("cache", {}).get(self._cache_key(i))
                if c is not None:
                    c["user_category"] = category
                self._save()
                return self.view()
        raise KeyError(key)

    def clear(self) -> dict:
        self.data = {"items": [], "cache": self.data.get("cache", {}), "range": {}, "created": 0}
        self._save()
        return self.view()

    # ------------------------------------------------------------------ the job
    def _cache_key(self, item: dict) -> str:
        return f"{item['key']}|{item['last']}|{self.data.get('model') or self.settings['llm_model']}"

    def start(self, rng: dict, sources: list[str]):
        since = resolve_since(rng)
        if since is None:
            raise ValueError("pick a range of days or a date")
        sources = [s for s in sources if s in ("mail", "teams")] or ["mail", "teams"]
        if self.job and self.job.state in ("queued", "running"):
            raise ValueError("a recap is already running")

        def run(job) -> None:
            until = datetime.now().astimezone()
            g = self.graph.g
            me = g.get("/me", {"$select": "id,displayName,mail,userPrincipalName"})
            name = me.get("displayName") or "the user"
            job.note("collecting mail and chats ...")
            items = (mail_items(g, me, since) if "mail" in sources else []) + \
                    (chat_items(g, me, since, until) if "teams" in sources else [])
            items.sort(key=lambda i: i["last"], reverse=True)
            cache = self.data.get("cache", {})
            self.data = {"items": items, "cache": cache, "range": {**rng, "sources": sources},
                         "created": time.time(), "model": self.settings["llm_model"]}
            job.total, job.done = len(items), 0
            job.note(f"{len(items)} conversation(s); the model sorts them now")
            system = system_prompt(name)
            for item in items:
                ck = self._cache_key(item)
                if ck in cache:
                    item.update(cache[ck])
                elif item["automated"]:
                    item.update(category="ignore", summary="Automatic message.", action="", due="", by="rule")
                else:
                    item.update(self._classify(system, item))
                    if not item.get("error"):
                        cache[ck] = {k: item.get(k, "") for k in ("category", "summary", "action", "due", "by")}
                job.done += 1
                self._save()
            if len(cache) > 3000:   # keep the newest entries
                self.data["cache"] = dict(list(cache.items())[-2000:])
            self._save()
            job.note(f"done: {sum(i.get('category') in ('todo', 'reply') for i in items)} to act on")

        self.job = self.graph.jobs.submit("Recap", run)
        return self.job

    def _classify(self, system: str, item: dict) -> dict:
        text = item["text"]
        if len(text) > MAX_CHARS:
            text = text[:600] + "\n[... older part cut ...]\n" + text[-(MAX_CHARS - 600):]
        hint = "\n\n(The latest message is from me.)" if item["last_from_me"] else ""
        try:
            content, stats = self.llm.chat(system, text + hint, SCHEMA, priority=BACKGROUND, max_tokens=200,
                                           num_ctx=8192)
            a = parse_json(content)
        except LLMError as e:
            log.warning("recap: %s: %s", item["key"], e)
            return {"category": "fyi", "summary": "", "action": "", "due": "", "error": str(e)[:200]}
        cat = a.get("category") if a.get("category") in CATEGORIES else "fyi"
        return {"category": cat, "summary": str(a.get("summary", ""))[:300], "action": str(a.get("action", ""))[:200],
                "due": str(a.get("due", ""))[:60], "by": "model", "took_s": stats["total_s"]}
