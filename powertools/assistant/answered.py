"""Did the user answer in the meantime? Looks only at the open questions and tasks: one request for up to 20 chats and one
for the sent mail, no model. Only conversations the user wrote in (or reacted in) are then read and sorted again."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime
from urllib.parse import quote

from .. import clock, export
from ..graph import teams
from ..graph.client import GraphError
from ..jobs import ACTIVE, Job
from . import collect
from .digest import Digest
from .kit import Kit, Part

log = logging.getLogger("powertools.assistant")
BATCH = 20
EVERY = 300


class Answers(Part):
    def __init__(self, kit: Kit, digest: Digest, updating: Callable[[], bool]):
        super().__init__(kit)
        self.digest, self.progress, self.updating = digest, digest.progress, updating
        self.job: Job | None = None

    @property
    def running(self) -> bool:
        return self.job is not None and self.job.state in ACTIVE

    def check(self) -> Job | None:
        """Closes the questions the user answered since the last Update; None when there is nothing to look at."""
        with self.state.lock:
            if self.updating() or self.running:
                raise ValueError("an update is running")
            if not self.state.data["updated"] or not self._open():
                return None
            if self.job is not None and self.job.state == "needs_token":
                return self.job
        self.job = self.kit.graph.jobs.submit("Check answers", self._run, "recap")
        return self.job

    def check_quietly(self) -> None:
        try:
            if self.kit.settings["check_answers"] and self.kit.graph.tokens.get() and not self.kit.graph.jobs.busy():
                self.check()
        except Exception as e:
            log.warning("looking for answers: %s", e)

    def watch(self, stop: threading.Event) -> None:
        """Every five minutes while there is a token (Graph Explorer gives one for about an hour)."""
        while not stop.wait(EVERY):
            self.check_quietly()

    def _open(self) -> list[dict]:
        return [i for i in self.state.open() if i["category"] in ("todo", "reply") and i["source"] in ("mail", "teams") and not self.kit.excluded(i)]

    def _run(self, job: Job) -> None:
        self.progress.job = job
        rows = {i["key"]: i for i in self._open()}
        keys = find(self.kit.graph.g, self.state.data["me"].get("id", ""), list(rows.values()))
        found = [new for k in keys if (new := self._read(rows[k]))]
        if found:
            self.digest.run(found, job, [])
        self.state.data["checked"] = time.time()
        self.state.save()
        self.progress.say("")
        job.note(f"{len(found)} answered")

    def _read(self, i: dict) -> dict | None:
        g, me, cid = self.kit.graph.g, self.state.data["me"], i["key"].split(":", 1)[1]
        try:
            if i["source"] == "mail":
                return collect.mail_item(g, me, cid, i["title"], self.kit.exclusions())
            chat = {"id": cid, "title": i["title"], "members": collect.names(i), "type": i.get("kind", ""), "web": i["link"]}
            return collect.chat_item(g, me, chat, export.parse_time(i["last"]), datetime.now().astimezone())
        except GraphError as e:
            log.warning("answer in %s: %s", i["key"], e)
            return None


def find(g, me_id: str, items: list[dict]) -> set[str]:
    """Keys of the conversations with something newer from the user."""
    return _chats(g, me_id, [i for i in items if i["source"] == "teams"]) | _mail(g, [i for i in items if i["source"] == "mail"])


def newer(when: str, than: str) -> bool:
    return bool(when) and export.parse_time(when) > export.parse_time(than)


def _chats(g, me_id: str, items: list[dict]) -> set[str]:
    def wrote(m: dict, since: str) -> bool:
        return (teams.sender_id(m) == me_id and newer(m.get("createdDateTime", ""), since)) or newer(teams.reaction(m, me_id)[0], since)
    out = set()
    for n in range(0, len(items), BATCH):
        part = items[n:n + BATCH]
        bodies = g.batch([f"/chats/{quote(i['key'][6:], safe=':@')}/messages?$top=20" for i in part])
        out |= {i["key"] for i, body in zip(part, bodies, strict=True) if any(wrote(m, i["last"]) for m in body.get("value", []))}
    return out


def _mail(g, items: list[dict]) -> set[str]:
    if not items:
        return set()
    last = {i["key"][5:]: i["last"] for i in items}
    oldest = clock.iso(min(export.parse_time(t) for t in last.values()))
    sent = g.pages("/me/mailFolders/sentitems/messages", {"$filter": f"sentDateTime ge {oldest}", "$top": "100",
                                                          "$select": "conversationId,sentDateTime,receivedDateTime"}, None, 500)
    return {"mail:" + m["conversationId"] for m in sent
            if m.get("conversationId") in last and newer(m.get("sentDateTime") or m.get("receivedDateTime") or "", last[m["conversationId"]])}
