"""One Update: read every source since the last one, digest what is new, tidy up. One that stops half way does not move the
cursor, so nothing is missed; one that waits for a token keeps what it read."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .. import clock, export
from ..graph.client import GraphError
from ..graph.service import resolve_since
from ..jira.client import JiraError
from ..jobs import ACTIVE, Job
from . import collect
from .digest import Digest
from .drafts import Drafts
from .kit import Kit, Part

log = logging.getLogger("powertools.assistant")
SOURCES = ["mail", "teams", "jira", "meetings"]
OVERLAP = timedelta(minutes=10)
FIRST_DAYS = 3
KEEP_DAYS = {"fyi": 30, "ignore": 14, "closed": 60, "promise": 180}
PEOPLE_EVERY = 7 * 86400
Reader = Callable[[collect.Reading], Iterator[dict]]


@dataclass
class Plan:
    """What an Update was asked for, and what it read so far (kept while it waits for a token)."""
    kind: str
    since: datetime
    sources: list[str]
    began: datetime | None = None
    read: dict[str, list[dict]] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    again: bool = False              # something stopped: the range is read again next time

    @property
    def complete(self) -> bool:
        """Every source was read; a look at some sources is not a sync and does not move the cursor."""
        return not self.again and set(SOURCES) <= set(self.sources)


class Updates(Part):
    def __init__(self, kit: Kit, digest: Digest, drafts: Drafts):
        super().__init__(kit)
        self.digest, self.drafts, self.progress = digest, drafts, digest.progress
        self.job: Job | None = None
        self._plan: Plan | None = None

    def start(self, rng: dict | None = None, sources: list[str] | None = None) -> Job:
        rng = rng or {"kind": "new"}
        with self.state.lock:
            if self.running:
                raise ValueError("an update is already running")
            waiting = self.job if self.job and self.job.state == "needs_token" else None
            if not (waiting and rng["kind"] == "new" and self._plan.kind == "new"):     # the same again keeps what it read
                self._plan = Plan(rng["kind"], self._since(rng), [s for s in (sources or SOURCES) if s in SOURCES] or SOURCES)
            if waiting:
                return waiting           # goes on by itself when the token is there, as asked last
            self.progress.say("waiting for the job that is running now")
        self.job = self.kit.graph.jobs.submit("Update", self._run, "recap")
        return self.job

    @property
    def running(self) -> bool:
        return self.job is not None and self.job.state in ACTIVE

    def _since(self, rng: dict) -> datetime:
        cursor = self.state.data["cursor"]
        if rng["kind"] == "new":
            return export.parse_time(cursor) - OVERLAP if cursor else resolve_since({"kind": "days", "days": FIRST_DAYS})
        since = resolve_since(rng)
        if since is None:
            raise ValueError("pick a range of days or a date")
        return since

    # ------------------------------------------------------------------ the run
    def _run(self, job: Job) -> None:
        self.progress.job, plan, d = job, self._plan, self.state.data
        plan.began = plan.began or datetime.now(timezone.utc)
        started, credits = time.time(), self._credits()
        self.progress.step("reading your profile")
        me = self.kit.graph.g.get("/me", {"$select": "id,displayName,mail,userPrincipalName"})
        d["me"] = {k: v for k, v in me.items() if not k.startswith("@")}
        self._read_sources(plan)
        self._read_calendar(plan)
        self._read_people()
        self._forget_excluded()
        found = [row for rows in plan.read.values() for row in rows]
        todo = self.digest.run(found, job, plan.problems)
        self.prune()
        self._close_drafts()
        if plan.complete:
            d["cursor"] = clock.iso(plan.began)
        errors = sum(bool(i.get("error")) for i in todo)
        if errors:
            plan.problems.append(f"{errors} conversation(s) could not be sorted; the next Update tries them again")
        d["updated"] = d["checked"] = time.time()
        d["last"] = {"found": len(found), "new": len(todo), "seconds": round(time.time() - started), "problems": plan.problems,
                     "credits": round(self._credits() - credits, 2), "model": self.kit.llm.model, "since": plan.since.isoformat(),
                     "began": clock.iso(plan.began)}
        self.state.save()
        self.progress.say("")
        job.note(f"done in {d['last']['seconds']} s")

    def _readers(self, plan: Plan) -> dict[str, Reader]:
        k, ex, me, until = self.kit, self.kit.exclusions(), self.state.data["me"], datetime.now().astimezone()
        readers: dict[str, Reader] = {"mail": lambda r: collect.mail_items(k.graph.g, me, plan.since, ex, r),
                                      "teams": lambda r: collect.chat_items(k.graph.g, me, plan.since, until, ex, r)}
        if k.jira is not None and self._jira_ready():
            readers["jira"] = lambda r: collect.jira_items(k.jira.client(), plan.since, ex, r)
        if k.meetings is not None:
            readers["meetings"] = lambda r: collect.meeting_items(k.meetings.sessions(60), plan.since, ex)
        return readers

    def _read_sources(self, plan: Plan) -> None:
        readers = self._readers(plan)
        for name in SOURCES:
            if name not in plan.sources or name not in readers or name in plan.read:
                continue
            r, rows = collect.Reading(self.progress.step), []
            try:
                for row in readers[name](r):       # one by one: what was read before a failure is kept
                    rows.append(row)
            except (GraphError, JiraError) as e:
                r.notes.append(str(e))
                r.again = True
            plan.read[name] = rows
            plan.problems += [f"{name.capitalize()}: {note}" for note in r.notes]
            plan.again = plan.again or r.again

    def _read_calendar(self, plan: Plan) -> None:
        self.progress.step("reading the calendar")
        day = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            self.state.data["events"] = collect.events(self.kit.graph.g, day, day + timedelta(days=8), self.kit.exclusions())
        except GraphError as e:
            plan.problems.append(f"Calendar: {e}")

    def _read_people(self) -> None:
        d = self.state.data
        if d["people"] and time.time() - d.get("people_at", 0) < PEOPLE_EVERY:
            return
        try:
            d["people"] = collect.direct_reports(self.kit.graph.g) + [p for p in d["people"] if p.get("added")]
            d["people_at"] = time.time()
        except GraphError as e:
            log.info("no direct reports: %s", e)

    def _credits(self) -> float:
        usage = getattr(self.kit.llm, "usage", None)
        return usage.today()["credits"] if usage else 0.0

    def _jira_ready(self) -> bool:
        try:
            c = self.kit.jira.creds()
            return bool(c.base_url and c.token)
        except Exception as e:
            log.info("jira skipped: %s", e)
            return False

    # ------------------------------------------------------------------ tidying up
    def _forget_excluded(self) -> None:
        """A new exclusion also removes what was read before it, its drafts and its lines in the matters."""
        for i in [i for i in self.state.all() if self.kit.excluded(i)]:
            self.drafts.forget(i["key"])
            if i.get("matter"):
                try:
                    self.kit.memory.forget(i)
                except KeyError:
                    pass                 # its matter is gone
            with self.state.lock:
                del self.state.data["items"][i["key"]]

    def prune(self) -> None:
        now = datetime.now(timezone.utc)
        with self.state.lock:
            for k, i in list(self.state.data["items"].items()):
                age = (now - export.parse_time(i["last"])).days if i.get("last") else 0
                limit = KEEP_DAYS["closed"] if i["status"] in ("done", "dropped") else KEEP_DAYS.get(i.get("category", ""))
                if limit and age > limit and not (age <= KEEP_DAYS["promise"] and self.state.open_promises(i)):
                    del self.state.data["items"][k]

    def _close_drafts(self) -> None:
        """Drafts of conversations that are done (since yesterday at least, so Undo still finds them) or gone."""
        for d in self.drafts.list():
            i = self.state.data["items"].get(d["key"])
            if d["key"] and (i is None or (i["status"] in ("done", "dropped") and i.get("done_at", "") < clock.days_ago(1))):
                self.drafts.close(d["id"], "used")
