"""Everything the window needs from Graph: token status, chat and mail listings, export jobs with cursors."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta

from .. import export
from ..config import data_dir, output_dir
from ..jobs import Job, Jobs
from . import mail, org, teams
from .client import Graph, TokenExpired
from .token import TokenStore, claims

log = logging.getLogger("powertools.graph")
GRAPH_EXPLORER = "https://developer.microsoft.com/graph/graph-explorer"
GRAPH_AUDIENCES = ("https://graph.microsoft.com", "00000003-0000-0000-c000-000000000000")


def graph_token_in(text: str) -> str:
    """The Graph access token in copied text ('Bearer ...' too), or ''."""
    t = (text or "").strip()
    if t.lower().startswith("bearer "):
        t = t[7:].strip()
    if not t.startswith("eyJ") or t.count(".") != 2 or len(t) > 20000 or any(c.isspace() for c in t):
        return ""
    c = claims(t)
    return t if c.get("aud") in GRAPH_AUDIENCES and c.get("exp", 0) > time.time() + 60 else ""


def resolve_since(rng: dict, cursor: str = "") -> datetime | None:
    """Range from the window -> start time. kind: days | since | all | new (from the last export, else all)."""
    kind = rng.get("kind", "days")
    now = datetime.now().astimezone()
    if kind == "days":
        days = int(rng.get("days", 2))
        if not 0 < days <= 3650:
            raise ValueError("days must be 1..3650")
        return now - timedelta(days=days)
    if kind == "since":
        try:
            return datetime.strptime(str(rng.get("date", "")), "%Y-%m-%d").astimezone()
        except ValueError as e:
            raise ValueError("date must be YYYY-MM-DD") from e
    if kind == "all":
        return None
    if kind == "new":
        return export.parse_time(cursor) + timedelta(milliseconds=1) if cursor else None
    raise ValueError(f"unknown range kind: {kind}")


class GraphService:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store
        self.tokens = TokenStore(data_dir() / "graph-token.bin")
        self.g = Graph(self.tokens.get)
        self.jobs = Jobs()
        self.me: dict = {}
        self._chats: list[dict] = []
        self._chats_at = 0.0
        self.watch: dict = {"active": False, "until": 0.0, "result": ""}

    # ------------------------------------------------------------------ token
    def status(self) -> dict:
        left = self.tokens.expires_in()
        scopes = self.tokens.scopes()
        return {"watch": dict(self.watch), "has_token": left > 60, "expires_min": int(left // 60), "user": self.me.get("displayName", ""),
                "chats": "Chat.Read" in scopes or "Chat.ReadWrite" in scopes,
                "mail": any(s.startswith("Mail.Read") for s in scopes)}

    def set_token(self, token: str) -> dict:
        self.tokens.set(token)
        try:
            self.me = self.g.get("/me", {"$select": "id,displayName"})
        except TokenExpired as e:
            self.tokens.clear()
            raise ValueError("Graph rejected this token; copy a fresh one from Graph Explorer") from e
        resumed = self.jobs.resume_paused()
        return {**self.status(), "resumed_jobs": resumed}

    def watch_clipboard(self, seconds: int = 300, read=None, clear=None, interval: float = 1.0) -> dict:
        """Wait for a Graph token to be copied (Graph Explorer > Access token > copy) and save it; then clear the
        clipboard if it still holds the token. Only while the window asked for it, at most `seconds`."""
        if self.watch["active"]:
            self.watch["until"] = time.time() + seconds
            return dict(self.watch)
        if read is None or clear is None:
            import pyperclip
            read, clear = read or pyperclip.paste, clear or (lambda: pyperclip.copy(""))
        current = self.tokens.get()
        self.watch = {"active": True, "until": time.time() + seconds, "result": ""}

        def run() -> None:
            try:
                while time.time() < self.watch["until"]:
                    try:
                        tok = graph_token_in(read())
                    except Exception:
                        tok = ""
                    if tok and tok != current:
                        try:
                            self.set_token(tok)
                            self.watch["result"] = "saved"
                        except ValueError as e:
                            self.watch["result"] = str(e)
                        try:
                            if graph_token_in(read()) == tok:
                                clear()
                        except Exception:
                            pass
                        return
                    time.sleep(interval)
                self.watch["result"] = "timeout"
            finally:
                self.watch["active"] = False

        threading.Thread(target=run, name="token-watch", daemon=True).start()
        return dict(self.watch)

    def _me(self) -> dict:
        if not self.me:
            self.me = self.g.get("/me", {"$select": "id,displayName"})
        return self.me

    # ------------------------------------------------------------------ calendar
    def current_event(self) -> dict:
        """The Teams meeting on the calendar right now (title, attendees, organizer, invite text), or {}."""
        if not self.tokens.get():
            return {}
        now = datetime.now().astimezone()
        data = self.g.get("/me/calendarView",
                          {"startDateTime": (now - timedelta(minutes=20)).isoformat(timespec="seconds"),
                           "endDateTime": (now + timedelta(minutes=20)).isoformat(timespec="seconds"), "$top": "20",
                           "$select": "subject,start,end,isCancelled,onlineMeeting,attendees,organizer,body"},
                          {"Prefer": 'outlook.timezone="UTC", outlook.body-content-type="text"'})
        best, best_gap = None, None
        for e in data.get("value", []):
            if e.get("isCancelled") or not (e.get("onlineMeeting") or {}).get("joinUrl"):
                continue
            start = export.parse_time(e["start"]["dateTime"] + "Z")
            end = export.parse_time(e["end"]["dateTime"] + "Z")
            if not (start - timedelta(minutes=15) <= now <= end + timedelta(minutes=15)):
                continue
            gap = abs((now - start).total_seconds())
            if best is None or gap < best_gap:
                best, best_gap = e, gap
        if not best:
            return {}
        names = [(a.get("emailAddress") or {}).get("name", "") for a in best.get("attendees", [])]
        return {"title": best.get("subject") or "Teams meeting",
                "attendees": [n for n in names if n],
                "organizer": ((best.get("organizer") or {}).get("emailAddress") or {}).get("name", ""),
                "invite": ((best.get("body") or {}).get("content") or "").strip()[:4000]}

    # ------------------------------------------------------------------ Teams
    def chats(self, refresh: bool = False) -> list[dict]:
        if refresh or not self._chats or time.time() - self._chats_at > 300:
            self._chats = teams.list_chats(self.g, self._me().get("id", ""))
            self._chats_at = time.time()
        done = self.store.cursors("teams")
        return [{**c, "exported": done.get(c["id"], {}).get("newest", "")} for c in self._chats]

    def export_chats(self, ids: list[str], rng: dict) -> Job:
        if not ids:
            raise ValueError("pick at least one chat")
        resolve_since(rng)  # validate now, not in the background
        by_id = {c["id"]: c for c in self._chats}
        chats = [by_id[i] for i in ids if i in by_id]
        until = datetime.now().astimezone()
        out = output_dir(self.settings)

        def run(job: Job) -> None:
            job.total = len(chats)
            for chat in chats[job.done:]:  # a resumed job continues where it stopped
                since = resolve_since(rng, self.store.cursor("teams", chat["id"]))
                path, newest = teams.export_chat(self.g, chat, since, until, out, job.note)
                if path:
                    job.files.append(str(path))
                    self.store.set_cursor("teams", chat["id"], newest)
                else:
                    job.note(f"{chat['title']}: no messages in this range")
                job.done += 1

        title = f"Teams: {len(chats)} chat(s)" if len(chats) > 1 else f"Teams: {chats[0]['title']}"
        return self.jobs.submit(title, run)

    # ------------------------------------------------------------------ mail
    def folders(self) -> list[dict]:
        return mail.folders(self.g)

    def threads(self, folder: str, person: str, subject: str, rng: dict) -> list[dict]:
        threads = mail.search_threads(self.g, folder, person.strip(), subject.strip(), resolve_since(rng))
        done = self.store.cursors("mail")
        return [{**t, "exported": done.get(t["id"], {}).get("newest", "")} for t in threads]

    def export_threads(self, items: list[dict], rng: dict) -> Job:
        if not items:
            raise ValueError("pick at least one thread")
        resolve_since(rng)
        until = datetime.now().astimezone()
        out = output_dir(self.settings)
        attachments = self.settings["mail_attachments"]

        def run(job: Job) -> None:
            job.total = len(items)
            for t in items[job.done:]:
                since = resolve_since(rng, self.store.cursor("mail", t["id"]))
                path, newest = mail.export_thread(self.g, t["id"], since, until, out, job.note, attachments)
                if path:
                    job.files.append(str(path))
                    self.store.set_cursor("mail", t["id"], newest)
                else:
                    job.note(f"{t.get('subject', 'thread')}: no messages in this range")
                job.done += 1

        title = f"Mail: {len(items)} thread(s)" if len(items) > 1 else f"Mail: {items[0].get('subject', '')}"
        return self.jobs.submit(title, run)

    # ------------------------------------------------------------------ org chart
    def org_chain(self) -> list[dict]:
        """You and your managers up to the top: the usual places to start a chart from."""
        return [p.view() for p in org.chain(self.g)]

    def org_search(self, text: str) -> list[dict]:
        return [p.view() for p in org.search(self.g, text)]

    def export_org(self, root_id: str, depth: int, with_above: bool = True) -> Job:
        if not root_id:
            raise ValueError("pick a person to start from")
        if not 0 <= depth <= 20:
            raise ValueError("depth must be 0 (all levels) to 20")
        out = output_dir(self.settings)

        def run(job: Job) -> None:
            people = org.chain(self.g, root_id)          # the root with everything above it, one request
            root, above = people[0], people[1:] if with_above else []
            job.note(f"{len(above)} level(s) above {root.name}" if above else f"starting from {root.name}")

            def progress(found: int, level: int) -> None:
                job.done, job.total = found, 0
            found = org.walk(self.g, root, depth, progress)
            job.done = found
            files = org.save(out, root, above, depth, found >= org.MAX_PEOPLE, datetime.now())
            job.files += [str(f) for f in files]
            job.result = files[0].stem               # the Org tab opens the chart when the job is done
            job.note(f"{found} people below {root.name}")

        return self.jobs.submit("Org chart", run)

    def org_saved(self) -> list[dict]:
        return org.saved(output_dir(self.settings))

    def org_load(self, name: str) -> dict:
        return org.load(output_dir(self.settings), name)

