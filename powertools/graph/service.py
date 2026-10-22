"""Everything the window needs from Graph: token status, chat and mail listings, export jobs with cursors."""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from .. import export
from ..config import data_dir, output_dir
from ..jobs import Job, Jobs
from . import mail, teams
from .client import Graph, TokenExpired
from .token import TokenStore


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

    # ------------------------------------------------------------------ token
    def status(self) -> dict:
        left = self.tokens.expires_in()
        scopes = self.tokens.scopes()
        return {"has_token": left > 60, "expires_min": int(left // 60), "user": self.me.get("displayName", ""),
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

    def _me(self) -> dict:
        if not self.me:
            self.me = self.g.get("/me", {"$select": "id,displayName"})
        return self.me

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
