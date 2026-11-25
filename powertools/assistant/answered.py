"""Was it answered in the meantime? Looks only at conversations with something open: one request for up to 20 chats, one for
the mail, one for Jira, no model. Only those the user wrote in, those the user waits in and somebody wrote, and changed
issues are then read and sorted again."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime
from urllib.parse import quote

from .. import clock, export
from ..graph import teams
from ..jira.client import JiraError
from ..jobs import ACTIVE, Job
from . import collect
from .digest import Digest
from .kit import Kit, Part
from .state import conversation, newer

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
        """Closes what was answered since the last Update; None when there is nothing to look at."""
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
            if self.kit.settings["check_answers"] and not self.kit.graph.jobs.busy() and (self.kit.graph.tokens.get() or self.kit.jira_ready()):
                self.check()
        except Exception as e:
            log.warning("looking for answers: %s", e)

    def watch(self, stop: threading.Event) -> None:
        """Every five minutes while there is a token (Graph Explorer gives one for about an hour)."""
        while not stop.wait(EVERY):
            self.check_quietly()

    def _open(self) -> list[dict]:
        return [i for i in self.state.open() if i["source"] in ("mail", "teams", "jira") and not self.kit.excluded(i)]

    def _run(self, job: Job) -> None:
        self.progress.job, reading = job, collect.Reading()
        rows = self._open()
        found = self._from_graph(rows, reading) if self.kit.graph.tokens.get() else []
        found += self._from_jira([i for i in rows if i["source"] == "jira"], reading)
        if found:
            self.digest.run(found, job, [])
        self.state.data["checked"] = time.time()
        self.state.save()
        self.progress.say("")
        job.note(f"{len(found)} changed")

    def _from_graph(self, rows: list[dict], reading: collect.Reading) -> list[dict]:
        convs = {conversation(i): i for i in rows if i["source"] in ("mail", "teams")}
        waited = {conversation(i) for i in rows if i["category"] == "waiting"}
        keys = find(self.kit.graph.g, self.state.data["me"].get("id", ""), self._addresses(), convs, waited)
        return [new for k in sorted(keys) if (new := reading.one(k, lambda k=k: self._read(k, convs[k])))]

    def _addresses(self) -> set[str]:
        me = self.state.data["me"]
        return {a.lower() for a in (me.get("mail"), me.get("userPrincipalName")) if a}

    def _read(self, key: str, i: dict) -> dict | None:
        g, me, cid = self.kit.graph.g, self.state.data["me"], key.split(":", 1)[1]
        if i["source"] == "mail":
            return collect.mail_item(g, me, cid, i["title"], self.kit.exclusions())
        chat = {"id": cid, "title": i["title"], "members": collect.names(i), "type": i.get("kind", ""), "web": i["link"]}
        return collect.chat_item(g, me, chat, export.parse_time(i["last"]), datetime.now().astimezone())

    def _from_jira(self, rows: list[dict], reading: collect.Reading) -> list[dict]:
        if not rows or not self.kit.jira_ready():
            return []
        last = {conversation(i): i["last"] for i in rows}
        keys = ", ".join(k.split(":", 1)[1] for k in last)
        try:
            client = self.kit.jira.client()
            issues = client.search(f"key in ({keys})", collect.JIRA_FIELDS, len(last))
        except JiraError as e:
            log.info("Jira: %s", e)
            return []
        me, ex = collect.jira_me(client), self.kit.exclusions()
        items = [reading.one(i["key"], lambda i=i: collect.jira_item(i, client.base, me, ex)) for i in issues]
        return [x for x in items if x and x["key"] in last and newer(x["last"], last[x["key"]])]


def find(g, me_id: str, addresses: set[str], convs: dict[str, dict], waited: set[str]) -> set[str]:
    """Keys of the conversations with something newer: from the user, or (where the user waits) from anybody."""
    chats = {k: i for k, i in convs.items() if i["source"] == "teams"}
    mails = {k: i for k, i in convs.items() if i["source"] == "mail"}
    return _chats(g, me_id, chats, waited) | _mail(g, addresses, mails, waited)


def _chats(g, me_id: str, chats: dict[str, dict], waited: set[str]) -> set[str]:
    def news(key: str, m: dict, since: str) -> bool:
        mine = teams.sender_id(m) == me_id
        wrote = (mine or key in waited and m.get("messageType") == "message") and newer(m.get("createdDateTime", ""), since)
        return wrote or newer(teams.reaction(m, me_id)[0], since)
    out, keys = set(), list(chats)
    for n in range(0, len(keys), BATCH):
        part = keys[n:n + BATCH]
        bodies = g.batch([f"/chats/{quote(k[6:], safe=':@')}/messages?$top=20" for k in part])
        out |= {k for k, body in zip(part, bodies, strict=True) if any(news(k, m, chats[k]["last"]) for m in body.get("value", []))}
    return out


def _mail(g, addresses: set[str], mails: dict[str, dict], waited: set[str]) -> set[str]:
    if not mails:
        return set()
    oldest = clock.iso(min(export.parse_time(i["last"]) for i in mails.values()))
    out = set()
    for m in g.pages("/me/messages", {"$filter": f"receivedDateTime ge {oldest}", "$top": "100",
                                      "$select": "conversationId,receivedDateTime,from,isDraft"}, None, 1000):
        key = "mail:" + (m.get("conversationId") or "")
        mine = collect.address(m) in addresses
        if key in mails and not m.get("isDraft") and (mine or key in waited) and newer(m.get("receivedDateTime") or "", mails[key]["last"]):
            out.add(key)
    return out
