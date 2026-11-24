"""The assistant: one Update collects what is new, the model sorts it, the code keeps the open state between updates.

State (items with their text, promises, calendar, cursor) lives in %LOCALAPPDATA%\\powertools\\assistant.json; what is worth
keeping goes to the Markdown memory. An update that stops half way does not move the cursor, so nothing is missed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .. import export
from ..config import data_dir, memory_dir
from ..graph.client import GraphError, TokenExpired
from ..graph.service import resolve_since
from ..jira.client import JiraError
from ..llm import LLMError
from . import collect, skills
from .memory import Memory, parse, rank, section, with_section

log = logging.getLogger("powertools.assistant")
SOURCES = ["mail", "teams", "jira", "meetings"]
OPEN = ("todo", "reply", "waiting")
MAX_CHARS = 6000             # text per conversation sent to the model (the newest part)
MAX_MATCH = 40               # conversations per "which matter" question
SAID_IN = ("profile.md", "style.md", "matters/", "people/")   # memory worth quoting; briefs and cards only repeat it
OVERLAP = timedelta(minutes=10)
KEEP_DAYS = {"fyi": 30, "ignore": 14, "closed": 60}
NOT_SORTED = {"category": "fyi", "summary": "", "action": "", "due": "", "person": "", "quote": "", "decision": False, "commitments": []}
UNDOABLE = ("done", "not_mine", "mute", "snooze", "category")
UNDO_FIELDS = ("status", "until", "done_at", "user_category", "reviewed")
LABELS = {"mail": "Mail", "teams": "Teams", "jira": "Jira", "meeting": "Meeting"}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cut(text: str, n: int = MAX_CHARS) -> str:
    return text if len(text) <= n else text[:600] + "\n[... older part cut ...]\n" + text[-(n - 600):]


def shares_word(title: str, text: str, people: str) -> bool:
    """A significant word of the matter's name (not a person's name) is in the text."""
    names = {w.lower() for w in re.findall(r"\w+", people)}
    words = {w.lower() for w in re.findall(r"\w{4,}", title)} - names - {"with", "from", "project", "matter"}
    hay = {w.lower() for w in re.findall(r"\w{4,}", text)}
    return any(h.startswith(w[:max(4, len(w) - 2)]) for w in words for h in hay)


def tells(text: str) -> bool:
    """Is it (also) a statement to remember, not only a question?"""
    return "?" not in text or bool(re.search(r"\b(remember|note that|actually|is wrong|not true|has changed|correct that)\b", text, re.I))


def said(new: str, old: str, question: str) -> bool:
    """Do at least half of the words an edit adds come from what the user wrote?"""
    def words(t: str) -> set[str]:
        return {w.lower() for w in re.findall(r"\w{3,}", t)}
    added, mine = words(new) - words(old), words(question)
    return sum(w in mine for w in added) * 2 >= len(added) if added else bool(words(old) & mine)


class Assistant:
    def __init__(self, settings, graph, llm, jira=None, meetings=None, path: Path | None = None, memory: Memory | None = None):
        self.settings, self.graph, self.llm, self.jira, self.meetings = settings, graph, llm, jira, meetings
        self.path = path or data_dir() / "assistant.json"
        self.memory = memory or Memory(lambda: memory_dir(settings))
        self._lock = threading.RLock()
        self.data = self._load()
        self.job = None
        self.current = ""            # what the running update does now
        self.talk: list[dict] = []   # the Ask conversation of this session
        self._undo: dict = {}        # the last change of an item, for the Undo in the window
        from .drafts import Drafts
        self.drafts = Drafts(self)

    # ------------------------------------------------------------------ state
    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        for k, v in (("items", {}), ("promises", {}), ("events", []), ("preps", {}), ("people", []), ("cursor", ""),
                     ("updated", 0), ("last", {}), ("me", {}), ("corrections", []), ("muted", [])):
            data.setdefault(k, v)
        return data

    def _save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)

    def item(self, key: str) -> dict:
        return self.data["items"][key]

    @property
    def me(self) -> str:
        return self.data["me"].get("displayName") or "the user"

    def profile(self) -> str:
        text = parse(self.memory.read("profile.md"))[1].strip()
        return "About " + self.me + " (own words):\n" + text[:1500] if text else ""

    def corrections_text(self) -> str:
        rows = self.data["corrections"][-8:]
        return "" if not rows else "The user corrected earlier answers; learn from it:\n" + "\n".join(
            f'- "{c["title"][:80]}" ({c["people"][:60]}): not "{c["was"]}" but "{c["now"]}"' for c in rows)

    # ------------------------------------------------------------------ update
    def update(self, rng: dict | None = None, sources: list[str] | None = None):
        rng = dict(rng or {"kind": "new"})
        with self._lock:
            if self.job and self.job.state in ("queued", "running"):
                raise ValueError("an update is already running")
            if rng.get("kind") == "new":
                since = export.parse_time(self.data["cursor"]) - OVERLAP if self.data["cursor"] else resolve_since({"kind": "days", "days": 3})
            else:
                since = resolve_since(rng)
            if since is None:
                raise ValueError("pick a range of days or a date")
            sources = [s for s in (sources or SOURCES) if s in SOURCES] or SOURCES
            self.current = "waiting for the job that is running now"
        self.job = self.graph.jobs.submit("Update", lambda job: self._run(job, since, sources), "recap")
        return self.job

    def _step(self, text: str) -> None:
        self.current = text

    def _run(self, job, since: datetime, sources: list[str]) -> None:
        began = datetime.now(timezone.utc)
        t0, c0 = time.time(), self._credits()
        g, ex = self.graph.g, collect.Exclusions(self.settings["exclude"])
        self._step("reading your profile")
        self.data["me"] = {k: v for k, v in g.get("/me", {"$select": "id,displayName,mail,userPrincipalName"}).items()
                           if not k.startswith("@")}
        me, until = self.data["me"], datetime.now().astimezone()
        found: list[dict] = []
        problems: list[str] = []
        failed: list[str] = []       # sources that stopped: their part of the range is read again next time

        def part(name: str, fn) -> None:
            try:
                found.extend(fn())
            except TokenExpired:
                raise
            except (GraphError, JiraError) as e:
                problems.append(f"{name}: {e}")
                failed.append(name)
        if "mail" in sources:
            part("Mail", lambda: collect.mail_items(g, me, since, ex, self._step))
        if "teams" in sources:
            part("Teams", lambda: collect.chat_items(g, me, since, until, ex, self._step))
        if "jira" in sources and self.jira is not None and self._jira_ready():
            part("Jira", lambda: collect.jira_items(self.jira.client(), since, ex, self._step))
        if "meetings" in sources and self.meetings is not None:
            found += collect.meeting_items(self.meetings.sessions(60), since, ex)
        self._step("reading the calendar")
        try:
            day = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
            self.data["events"] = collect.events(g, day, day + timedelta(days=8), ex)
        except GraphError as e:
            problems.append(f"Calendar: {e}")
        if not self.data["people"] or time.time() - self.data.get("people_at", 0) > 7 * 86400:
            added = [p for p in self.data["people"] if p.get("added")]
            self.data["people"] = collect.direct_reports(g) + added
            self.data["people_at"] = time.time()
        self._forget_excluded(ex)

        todo = self._merge(found)
        job.total, job.done = len(todo), 0
        job.note(f"{len(found)} conversation(s), {len(todo)} new or changed")
        self._triage(todo, job)
        fresh = [i for i in todo if not i.get("error")]
        self._step("matching to matters")
        self._match(fresh)
        touched = self._to_matters(fresh)
        self._states(touched)
        if self.settings["auto_drafts"]:
            self._auto_drafts(fresh)
        self._prune()
        # the cursor moves only when every chosen source was read (Update now and then with fewer sources is a look, not a sync)
        if not failed and set(SOURCES) <= set(sources):
            self.data["cursor"] = began.strftime("%Y-%m-%dT%H:%M:%SZ")
        self.data["updated"] = time.time()
        errors = sum(bool(i.get("error")) for i in todo)
        if errors:
            problems.append(f"{errors} conversation(s) could not be sorted; the next Update tries them again")
        self.data["last"] = {"found": len(found), "new": len(todo), "seconds": round(time.time() - t0), "problems": problems,
                             "credits": round(self._credits() - c0, 2), "model": self.llm.model, "since": since.isoformat(),
                             "began": began.strftime("%Y-%m-%dT%H:%M:%SZ")}
        self._save()
        self.current = ""
        job.note(f"done in {self.data['last']['seconds']} s")

    def _credits(self) -> float:
        usage = getattr(self.llm, "usage", None)
        return usage.today()["credits"] if usage else 0.0

    def _jira_ready(self) -> bool:
        try:
            c = self.jira.creds()
            return bool(c.base_url and c.token)
        except Exception as e:
            log.info("jira skipped: %s", e)
            return False

    def _merge(self, found: list[dict]) -> list[dict]:
        """New and changed conversations go to the model; the others keep what is known about them."""
        todo = []
        with self._lock:
            for new in found:
                old = self.data["items"].get(new["key"])
                if old and old.get("last") == new["last"] and old.get("category") and not old.get("error"):
                    continue
                keep = {k: old[k] for k in ("matter", "matter_by", "first_seen", "draft") if old and old.get(k)}
                item = {**new, "first_seen": now_iso(), **keep, "seen": now_iso(), "status": "open"}
                self.data["items"][new["key"]] = item
                todo.append(item)
            for old in self.data["items"].values():   # sorted badly last time (no model, limit): try again
                if old.get("error") and all(old is not t for t in todo):
                    todo.append(old)
        return todo

    def _triage(self, todo: list[dict], job) -> None:
        broken: list[str] = []
        prompt = {"me": self.me, "profile": self.profile(), "corrections": self.corrections_text()}

        def one(item: dict) -> None:
            ignored = dict(category="ignore", action="", due="", person="", quote="", decision=False, commitments=[])
            error = ""
            if item["key"] in self.data["muted"]:
                got = {**ignored, "summary": "Muted by you.", "by": "you"}
            elif item["automated"]:
                got = {**ignored, "summary": "Automatic message.", "by": "rule"}
            elif len(broken) >= 3:
                got, error = dict(NOT_SORTED), broken[-1]
            else:
                try:
                    got = self.classify(item, prompt)
                    broken.clear()
                except LLMError as e:
                    log.warning("triage %s: %s", item["key"], e)
                    got, error = dict(NOT_SORTED), str(e)[:200]
                    broken.append(error)
            with self._lock:
                item.update(got)
                item.pop("error", None)
                if error:
                    item["error"] = error
            job.done += 1
            self._step(f"sorting {job.done}/{job.total}: {item['title'][:70]}")

        with ThreadPoolExecutor(max_workers=getattr(self.llm, "parallel", 1)) as pool:
            for n, _ in enumerate(pool.map(one, todo), 1):
                if n % 10 == 0:
                    self._save()
        self._save()

    def classify(self, item: dict, prompt: dict, provider: str = "") -> dict:
        hint = "\n\n(The latest message is from me.)" if item["last_from_me"] else ""
        a = skills.TRIAGE.run(self.llm, cut(item["text"]) + hint, priority=skills.BACKGROUND, provider=provider, **prompt)
        stats = a.pop("_stats", {})
        return {**skills.clean_triage(a, item["text"]), "by": "model", "orders": skills.gives_orders(item["text"]), "took_s": stats.get("total_s", 0),
                "credits": stats.get("credits")}

    # ------------------------------------------------------------------ matters
    def _match(self, fresh: list[dict]) -> None:
        matters = self.memory.matters(every=True)
        by_key: dict[str, set] = {}
        for m in matters:
            if m["status"] != "closed":
                for k in m["keys"]:
                    by_key.setdefault(k, set()).add(m["id"])
        ids = {m["id"] for m in matters}
        rest = []
        for i in fresh:
            if i.get("matter") and i["matter"] not in ids:
                i["matter"] = ""
            if i.get("matter") or i["category"] == "ignore":
                continue
            hits = set().union(*(by_key.get(k, set()) for k in [i["key"]] + ["jira:" + k for k in i.get("keys", [])]))
            if len(hits) == 1:
                with self._lock:
                    i.update(matter=hits.pop(), matter_by="key")
            else:
                rest.append(i)
        live = [m for m in matters if m["status"] in ("active", "waiting")]
        titles = {m["id"]: m["title"] for m in live}
        for n in range(0, len(rest), MAX_MATCH):
            part = rest[n:n + MAX_MATCH]
            user = ("Open matters:\n" + ("\n".join(f"{m['id']}: {m['title']}. {m['state'][:160]}" for m in live) or "(none yet)") +
                    "\n\nConversations:\n" + "\n".join(f"{i['key']} | {i['title'][:100]} | {i['people'][:80]} | {i['summary']}" for i in part))
            try:
                res = skills.MATCH.run(self.llm, user, priority=skills.BACKGROUND, me=self.me)["results"]
            except LLMError as e:
                log.warning("matching: %s", e)
                return
            answers = {str(r.get("key", "")): r for r in res if isinstance(r, dict)}
            with self._lock:
                for i in part:
                    r = answers.get(i["key"], {})
                    if r.get("matter") in titles:
                        # the model alone is too generous: without a word of the matter's name in the text it only asks
                        sure = shares_word(titles[r["matter"]], f"{i['title']} {i['summary']} {i['text']}", i["people"])
                        i.update({"matter": r["matter"], "matter_by": "model"} if sure else {"maybe": r["matter"]})
                    elif str(r.get("new") or "").strip() and i["category"] in OPEN:
                        i["suggest"] = " ".join(str(r["new"]).split())[:60]

    def _to_matters(self, fresh: list[dict]) -> list[str]:
        touched = []
        for i in fresh:
            if i.get("matter") and i["category"] != "ignore":
                try:
                    self.memory.add_entry(i["matter"], i)
                    touched.append(i["matter"])
                except (KeyError, OSError) as e:
                    log.warning("matter %s: %s", i["matter"], e)
        return list(dict.fromkeys(touched))

    def _states(self, touched: list[str]) -> None:
        for n, mid in enumerate(touched, 1):
            self._step(f"updating matter {n}/{len(touched)}")
            try:
                meta, body = self.memory.matter(mid)
                tl = section(body, "Timeline")
                user = (f"Matter: {meta.get('title')}\n\nFile:\n{cut(body, 4000)}\n\nNewest entries:\n" + "\n".join(tl[-12:]))
                a = skills.STATE.run(self.llm, user, priority=skills.BACKGROUND, me=self.me)
                state = " ".join(str(a["state"]).split())
                if state:
                    self.memory.set_lines(mid, "State", [state], "update")
                old = section(body, "Decisions")
                new = [f"- {d}" for d in (" ".join(str(x).split()) for x in a["decisions"][:5]) if d and f"- {d}" not in old]
                if new:
                    self.memory.set_lines(mid, "Decisions", old + new, "update")
            except (LLMError, KeyError, OSError) as e:
                log.warning("state of %s: %s", mid, e)
            self.write_open(mid)

    def write_open(self, mid: str) -> None:
        """The Open section of a matter, from the items (readable without the app)."""
        lines = [f"- {'Waiting for ' + (i['person'] or 'others') if i['category'] == 'waiting' else 'Mine'}: "
                 f"{i['action'] or i['summary']}" + (f" (due {i['due']})" if i["due"] else "")
                 for i in self._open() if i.get("matter") == mid]
        try:
            self.memory.set_lines(mid, "Open", lines, "update")
        except (KeyError, OSError) as e:
            log.warning("open list of %s: %s", mid, e)

    def _auto_drafts(self, fresh: list[dict]) -> None:
        wanted = [i for i in fresh if i["category"] == "reply" and i["source"] in ("mail", "teams") and not i.get("draft")][:6]
        for n, i in enumerate(wanted, 1):
            self._step(f"drafting answers {n}/{len(wanted)}")
            try:
                self.drafts.create(i["key"], priority=skills.BACKGROUND)
            except (LLMError, OSError) as e:
                log.warning("draft for %s: %s", i["key"], e)
                return

    def _forget_excluded(self, ex: collect.Exclusions) -> None:
        """A new exclusion also removes what was read before it."""
        with self._lock:
            for k in [k for k, i in self.data["items"].items() if ex.hit(i["title"], i["people"])]:
                del self.data["items"][k]

    def _prune(self) -> None:
        now = datetime.now(timezone.utc)
        with self._lock:
            for k, i in list(self.data["items"].items()):
                age = (now - export.parse_time(i["last"])).days if i.get("last") else 0
                closed = i["status"] in ("done", "dropped")
                limit = KEEP_DAYS["closed"] if closed else KEEP_DAYS.get(i["category"])
                if limit and age > limit:
                    del self.data["items"][k]

    # ------------------------------------------------------------------ what the window shows
    def _open(self) -> list[dict]:
        today = date.today().isoformat()
        out = []
        for i in self.data["items"].values():
            snoozed = i["status"] == "snoozed" and str(i.get("until", "")) > today
            if i["status"] in ("open", "snoozed") and not snoozed and self._cat(i) in OPEN:
                out.append({**i, "category": self._cat(i)})
        return sorted(out, key=lambda i: i["last"], reverse=True)

    @staticmethod
    def _cat(i: dict) -> str:
        return i.get("user_category") or i.get("category") or "fyi"

    @staticmethod
    def age_days(i: dict) -> int:
        return max(0, (datetime.now(timezone.utc) - export.parse_time(i["last"])).days) if i.get("last") else 0

    def _mine(self, who: str) -> bool:
        who, me = who.strip().lower(), self.me.lower()
        return bool(who) and (who in ("me", "i") or who == me or who == me.split()[0])

    def promises(self, every: bool = False) -> list[dict]:
        """Commitments with a proving quote: mine and other people's."""
        out = []
        for i in self.data["items"].values():
            if i["status"] == "dropped":
                continue
            for c in i.get("commitments") or []:
                pid = hashlib.sha1(f"{i['key']}|{c['what'].lower()}".encode()).hexdigest()[:12]
                state = self.data["promises"].get(pid, "open")
                if every or state == "open":
                    out.append({**c, "id": pid, "state": state, "mine": self._mine(c["who"]), "key": i["key"], "title": i["title"],
                                "source": i["source"], "link": i["link"], "last": i["last"], "days": self.age_days(i),
                                "matter": i.get("matter", "")})
        return sorted(out, key=lambda p: p["last"], reverse=True)

    def view(self) -> dict:
        with self._lock:
            return self._view()

    def _view(self) -> dict:
        def show(i: dict) -> dict:
            return {k: v for k, v in i.items() if k != "text"} | {"category": self._cat(i), "model_category": i.get("category"),
                                                                  "days": self.age_days(i)}
        items = [show(i) for i in self.data["items"].values()]
        shown = {i["key"] for i in self._open()}
        quiet = self.settings["silence_days"]
        day_ago = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        since = min(day_ago, self.data["last"].get("began", day_ago))   # new in the last update, at least the last day
        recent = [i for i in items if i["status"] == "open" and i["category"] == "fyi" and i["seen"] >= since and not i.get("error")]
        ignored = [i for i in items if i["category"] == "ignore" and i["status"] == "open"]
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        closed = [i for i in items if (i["status"] == "snoozed" and i["key"] not in shown) or
                  (i["status"] in ("done", "dropped") and i.get("done_at", "") >= week_ago)]
        by_last = dict(key=lambda i: i["last"], reverse=True)
        mine = sorted((i for i in items if i["key"] in shown and i["category"] in ("todo", "reply")), **by_last)
        waiting = sorted((i for i in items if i["key"] in shown and i["category"] == "waiting"), key=lambda i: i["last"])
        for i in waiting:
            i["quiet"] = i["days"] >= quiet
        promises = self.promises()
        today = date.today()
        events = [{k: v for k, v in e.items() if k != "invite"} | {"prepared": e["id"] in self.data["preps"]}
                  for e in self.data["events"]
                  if export.local(export.parse_time(e["end"])) >= datetime.now().astimezone() and not e["all_day"]]
        reviewed = [i for i in items if i.get("reviewed")]
        matters = self.memory.matters(every=True)
        for m in matters:
            m["silent"] = m["status"] == "active" and bool(m["updated"]) and \
                (today - date.fromisoformat(m["updated"][:10])).days >= self.settings["matter_quiet_days"]
        names = {m["id"]: m["title"] for m in matters}
        for i in (*mine, *waiting, *recent, *promises):
            i["matter_title"] = names.get(i.get("matter") or "", "")
            i["maybe_title"] = "" if i.get("matter") else names.get(i.get("maybe") or "", "")
        job = self.job.view() if self.job else None
        return {"decisions": [i for i in mine if i.get("decision")], "mine": [i for i in mine if not i.get("decision")],
                "waiting": waiting, "promised": [p for p in promises if p["mine"]],
                "owed": [p for p in promises if not p["mine"]],
                "changed": sorted(recent, **by_last), "ignored": sorted(ignored, **by_last)[:200],
                "unsorted": [i for i in items if i.get("error")],
                "closed": sorted(closed, key=lambda i: i.get("done_at") or i.get("until") or "", reverse=True),
                "events": events, "matters": matters, "suggested": self._suggested(items),
                "updated": self.data["updated"], "last": self.data["last"], "job": job, "cursor": self.data["cursor"],
                "current": self.current if job and job["state"] in ("queued", "running") else "",
                "ahead": [j.title for j in self.graph.jobs.all if j.state == "running" and j is not self.job],
                "reviewed": len(reviewed), "agreed": sum(i["category"] == i["model_category"] for i in reviewed),
                "silence_days": quiet, "setup_done": self.settings["setup_done"], "drafts": len(self.drafts.list()),
                "ask_feedback": bool(self.data["updated"]) and self.data.get("asked", "") != today.isoformat()}

    @staticmethod
    def _suggested(items: list[dict]) -> list[dict]:
        groups: dict[str, list] = {}
        for i in items:
            if i.get("suggest") and not i.get("matter") and i["status"] == "open":
                groups.setdefault(i["suggest"], []).append({"key": i["key"], "title": i["title"]})
        return [{"name": k, "items": v} for k, v in groups.items()]

    # ------------------------------------------------------------------ what the user does
    def act(self, key: str, action: str, value: str = "") -> None:
        with self._lock:
            i = self.item(key)
            before = self._cat(i)
            if action in UNDOABLE:
                self._undo = {"key": key, "item": {k: i[k] for k in UNDO_FIELDS if k in i}, "muted": list(self.data["muted"]),
                              "corrections": list(self.data["corrections"])}
            if action == "undo":
                if not self._undo or self._undo["key"] != key:
                    raise ValueError("there is nothing to undo any more")
                for k in UNDO_FIELDS:
                    i.pop(k, None)
                i.update(self._undo["item"])
                self.data["muted"], self.data["corrections"], self._undo = self._undo["muted"], self._undo["corrections"], {}
            elif action == "done":
                i.update(status="done", done_at=now_iso())
            elif action == "reopen":
                i["status"] = "open"
                i.pop("until", None)
                i.pop("done_at", None)
            elif action == "not_mine":
                i.update(status="dropped", done_at=now_iso())
                self._learn(i, before, "ignore")
            elif action == "mute":
                i["user_category"] = "ignore"
                self.data["muted"] = list(dict.fromkeys(self.data["muted"] + [key]))[-500:]
                self._learn(i, before, "ignore")
            elif action == "snooze":
                datetime.strptime(value, "%Y-%m-%d")
                i.update(status="snoozed", until=value)
            elif action == "category":
                if value not in skills.CATEGORIES:
                    raise ValueError(f"category must be one of {', '.join(skills.CATEGORIES)}")
                i.update(user_category=value, reviewed=True, status="open")
                if key in self.data["muted"] and value != "ignore":
                    self.data["muted"].remove(key)
                if value != i.get("category"):
                    self._learn(i, before, value)
            elif action == "right":
                i["reviewed"] = True
            elif action == "matter":
                self._move(i, value)
            elif action == "new_matter":
                mid = self.memory.create_matter(value or i.get("suggest", ""))
                for k in [key] + [x["key"] for x in self.data["items"].values() if i.get("suggest") and x.get("suggest") == i["suggest"]]:
                    self._move(self.item(k), mid)
            elif action == "no_matter":
                i.pop("suggest", None)
                i.pop("maybe", None)
            else:
                raise ValueError(f"unknown action: {action}")
            self._save()
        if i.get("matter"):
            self.write_open(i["matter"])

    def _move(self, i: dict, mid: str) -> None:
        old = i.get("matter")
        if mid:
            self.memory.matter(mid)          # KeyError when it is not there
        if old and old != mid:
            try:
                self.memory.forget_key(old, i["key"])
            except KeyError:
                pass
        i.update(matter=mid, matter_by="you")
        i.pop("suggest", None)
        i.pop("maybe", None)
        if mid:
            self.memory.add_entry(mid, i)
        if old and old != mid:
            self.write_open(old)

    def _learn(self, i: dict, was: str, now: str) -> None:
        if was == now:
            return
        row = {"at": now_iso(), "title": i["title"], "people": i["people"], "was": was, "now": now}
        self.data["corrections"] = (self.data["corrections"] + [row])[-200:]

    def promise(self, pid: str, state: str) -> None:
        if state not in ("open", "done", "dropped"):
            raise ValueError("state must be open, done or dropped")
        if pid not in {p["id"] for p in self.promises(every=True)}:
            raise KeyError(pid)
        with self._lock:
            self.data["promises"][pid] = state
            self._save()

    # ------------------------------------------------------------------ setup (five questions)
    def setup(self, answers: dict) -> dict:
        """The first-start interview: profile.md, the first matters, exclusions, the model."""
        provider = str(answers.get("provider", ""))
        if provider not in ("local", "copilot"):
            raise ValueError("choose where the model runs")
        exclude = [x.strip() for x in re.split(r"[,\n]", str(answers.get("never", ""))) if x.strip()]
        self.settings.update({"ai_provider": provider, "exclude": list(dict.fromkeys(self.settings["exclude"] + exclude)),
                              "setup_done": True})
        rows = [("My role", "role"), ("People I must not miss", "people"), ("What I found out too late", "late")]
        body = "# About me\n\n" + "".join(f"## {t}\n\n{str(answers.get(k, '')).strip()}\n\n" for t, k in rows
                                           if str(answers.get(k, "")).strip())
        if body.strip() != "# About me":
            self.memory.write("profile.md", body, "interview")
        existing = {m["title"].lower() for m in self.memory.matters(every=True)}
        for line in str(answers.get("running", "")).splitlines():
            name = line.strip(" -*\t")
            if name and name.lower() not in existing:
                self.memory.create_matter(name)
                existing.add(name.lower())
        return self.view()

    # ------------------------------------------------------------------ ask
    def _sources(self, question: str) -> list[dict]:
        chunks = self.memory.chunks(SAID_IN)
        for i in self.data["items"].values():
            chunks.append({"file": "", "title": f"{LABELS.get(i['source'], i['source'])}: {i['title']}", "link": i["link"],
                           "text": f"{i['last'][:10]} {i['people']}\n{i.get('summary', '')}\n{cut(i['text'], 1800)}"})
        for e in self.data["events"]:
            chunks.append({"file": "", "title": f"Calendar: {e['title']}", "link": e["link"],
                           "text": f"{export.local(export.parse_time(e['start'])):%A %Y-%m-%d %H:%M} with {', '.join(e['people'][:12])}"})
        past = " ".join(t["question"] for t in self.talk[-2:])
        return rank(f"{question} {question} {past}", chunks, 10)

    def overview(self) -> str:
        opened = self._open()
        lines = [f"(Today is {date.today():%A %Y-%m-%d}.)", "Open matters: " + (", ".join(m["title"] for m in self.memory.matters()) or "none")]
        for label, cats in ((f"{self.me} has to do", ("todo", "reply")), (f"Others owe {self.me}", ("waiting",))):
            rows = [f"- {i['title'][:80]}: {i['action'] or i['summary']}" + (f" [{i['person']}]" if i["person"] else "") + f" ({self.age_days(i)} d)"
                    for i in opened if i["category"] in cats][:15]
            lines.append(f"{label}:\n" + ("\n".join(rows) or "- nothing"))
        return "\n".join(lines)

    def ask(self, question: str) -> dict:
        question = question.strip()
        if not question:
            raise ValueError("ask something")
        turn = {"question": question, "answer": "", "at": now_iso(), "sources": [], "edits": []}
        try:
            if tells(question):
                turn["edits"] = self._remember(question)
            if turn["edits"] and "?" not in question:
                turn["answer"] = "Noted." if any(e["state"] == "applied" for e in turn["edits"]) else "Should I change this?"
            else:
                self._answer(question, turn)
        except LLMError as e:
            raise ValueError(str(e)) from e
        self.talk = (self.talk + [turn])[-30:]
        return turn

    def _answer(self, question: str, turn: dict) -> None:
        found = self._sources(question)
        parts = [f"[S{n}] {c['title']}\n{c['text'][:1800]}" for n, c in enumerate(found, 1)]
        past = "\n".join(f"Earlier question: {t['question']}\nEarlier answer: {t['answer'][:500]}" for t in self.talk[-4:])
        user = f"{self.overview()}\n\nSources:\n" + "\n\n".join(parts) + (f"\n\n{past}" if past else "") + f"\n\nQuestion: {question[:2000]}"
        a = skills.ASK.run(self.llm, user, me=self.me, profile=self.profile())
        used = [int(m) for s in a["sources"] for m in re.findall(r"\d+", str(s)) if 0 < int(m) <= len(found)]
        turn["answer"] = re.sub(r"\s*\[S\d+(?:,\s*S\d+)*\]", "", str(a["answer"])).strip()
        seen = set()
        for c in (found[n - 1] for n in dict.fromkeys(used)):
            if (c["title"], c["file"]) not in seen:
                seen.add((c["title"], c["file"]))
                turn["sources"].append({"title": c["title"], "file": c["file"], "link": c.get("link", "")})

    def _remember(self, text: str) -> list[dict]:
        names = [c["file"] for c in rank(f"{text} {' '.join(t['question'] for t in self.talk[-1:])}", self.memory.chunks(SAID_IN), 8)]
        files = list(dict.fromkeys([*names, "profile.md"]))[:5]
        user = "\n\n".join(f"File {f}:\n{parse(self.memory.read(f))[1][:2500] or '(empty)'}" for f in files) + f"\n\n{self.me} said: {text[:1500]}"
        a = skills.REMEMBER.run(self.llm, user, me=self.me)
        return self._edits([e for e in a["edits"] if isinstance(e, dict)], text)

    def _edits(self, edits: list[dict], question: str) -> list[dict]:
        """What the user said goes into the files at once (with Undo). An edit whose words do not come from the user
        (a guess, or a mail that tries to change the notes) is only proposed."""
        out = []
        for e in edits[:10]:
            if said(str(e.get("new", "")), str(e.get("old", "")), question):
                out += [{**d, "state": "error" if d["error"] else "applied"} for d in self.memory.edit([e], f"you said: {question[:200]}")]
            else:
                out.append({**{k: str(e.get(k, "")) for k in ("file", "old", "new")}, "id": "", "error": "", "state": "proposed"})
        return out

    # ------------------------------------------------------------------ briefs
    def brief(self, kind: str, days: int = 0) -> dict:
        if kind not in skills.BRIEF_TASKS:
            raise ValueError(f"kind must be one of {', '.join(skills.BRIEF_TASKS)}")
        days = days or {"day": 1, "week": 7, "upward": 7, "leave": 14}[kind]
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        facts = [self.overview(), "Promised by me: " + ("; ".join(f"{p['what']} (to {p['to']}, {p['due']})" for p in self.promises() if p["mine"]) or "nothing")]
        if kind in ("day", "leave"):
            today = [e for e in self.data["events"] if export.local(export.parse_time(e["start"])).date() == date.today()]
            facts.append("Meetings today: " + ("; ".join(f"{export.local(export.parse_time(e['start'])):%H:%M} {e['title']}" for e in today) or "none"))
        for m in self.memory.matters(every=True):
            if m["status"] == "closed" and m["updated"] < since[:10]:
                continue
            _, body = self.memory.matter(m["id"])
            tl = [ln for ln in section(body, "Timeline") if ln[2:12] >= since[:10]]
            if tl or m["status"] in ("active", "waiting"):
                facts.append(f"Matter {m['title']} ({m['status']}): {m['state']}\nDecisions: " +
                             " ".join(section(body, "Decisions")[-5:]) + "\nNew:\n" + ("\n".join(tl[-10:]) or "- nothing"))
        rest = [i for i in self.data["items"].values() if i["last"] >= since and not i.get("matter") and self._cat(i) != "ignore"]
        facts.append("Outside the matters:\n" + "\n".join(f"- {i['last'][:10]} {i['title'][:80]}: {i.get('summary', '')} [{i['status']}]" for i in rest[:40]))
        try:
            a = skills.BRIEF.run(self.llm, "\n\n".join(facts)[:24000], me=self.me, profile=self.profile(),
                                 task=skills.BRIEF_TASKS[kind].replace("{{me}}", self.me))
        except LLMError as e:
            raise ValueError(str(e)) from e
        lines = [" ".join(str(x).split()) for x in a["lines"][:14] if str(x).strip()]
        name = f"days/{date.today():%Y-%m-%d}.md" if kind in ("day", "leave") else f"weeks/{date.today():%G-W%V}.md"
        title = {"day": "Brief", "week": "Week", "upward": "Upward", "leave": "Back from leave"}[kind]
        body = self.memory.read(name) or f"# {name.split('/')[1][:-3]}\n\n"
        self.memory.write(name, with_section(body, title, [f"- {x}" for x in lines]), f"brief: {kind}")
        return {"kind": kind, "lines": lines, "file": name}

    # ------------------------------------------------------------------ meetings ahead
    def event(self, eid: str) -> dict:
        for e in self.data["events"]:
            if e["id"] == eid:
                return e
        raise KeyError(eid)

    def prep(self, eid: str = "", again: bool = False, person: str = "") -> dict:
        """The card before a meeting (or a 1:1 with a person who is not on the calendar)."""
        if person:
            e = {"id": f"person:{person}", "title": f"1:1 with {person}", "people": [person], "start": now_iso(), "invite": "", "series": ""}
        else:
            e = self.event(eid)
        have = self.data["preps"].get(e["id"])
        if have and not again:
            return have
        names = {p.lower() for p in e["people"] if p.lower() != self.me.lower()}
        related = [i for i in self._open() if names & {x.strip().lower() for x in i["people"].split(",")}][:12] if len(names) <= 12 else []
        promises = [p for p in self.promises() if names & {p["who"].lower(), p["to"].lower()}][:10]
        query = f"{e['title']} {e['title']} {' '.join(list(names)[:6])} {e['invite'][:400]}"
        known = rank(query, self.memory.chunks(SAID_IN), 6)
        last = self._last_meeting(e["title"])
        user = "\n\n".join([
            f"Meeting: {e['title']}\nWhen: {export.local(export.parse_time(e['start'])):%A %Y-%m-%d %H:%M}\nPeople: {', '.join(e['people'][:20])}\n"
            f"Invitation:\n{e['invite'] or '(empty)'}",
            "Open with these people:\n" + ("\n".join(f"- {i['category']}: {i['title'][:80]}: {i['action'] or i['summary']}" for i in related) or "- nothing known"),
            "Promises:\n" + ("\n".join(f"- {p['who']} to {p['to']}: {p['what']} {p['due']}" for p in promises) or "- none known"),
            "Last time:\n" + (last or "(no recording)"),
            "Memory:\n" + ("\n\n".join(f"{c['title']}:\n{c['text'][:1200]}" for c in known) or "(nothing)")])
        try:
            a = skills.PREP.run(self.llm, user[:20000], me=self.me, profile=self.profile())
        except LLMError as err:
            raise ValueError(str(err)) from err
        card = {"id": e["id"], "title": e["title"], "start": e["start"], "made": now_iso(), "goal": str(a["goal"]).strip()}
        for k in ("known", "open", "questions", "agenda"):
            card[k] = [" ".join(str(x).split()) for x in a[k][:6] if str(x).strip()]
        day = export.local(export.parse_time(e["start"])).strftime("%Y-%m-%d")
        name = f"meetings/{day}-{export.slug(e['title'], 50)}.md"
        body = self.memory.read(name) or f"# {e['title']} ({day})\n\n"
        body = with_section(body, "Goal", [card["goal"]] if card["goal"] else [])
        for k, t in (("known", "What I know"), ("open", "Open from before"), ("questions", "To ask"), ("agenda", "Agenda")):
            body = with_section(body, t, [f"- {x}" for x in card[k]])
        self.memory.write(name, body, "meeting prep")
        card["file"] = name
        with self._lock:
            self.data["preps"][e["id"]] = card
            live = {x["id"] for x in self.data["events"]}
            self.data["preps"] = {k: v for k, v in self.data["preps"].items() if k in live or v["made"] > (
                datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%d")}
            self._save()
        return card

    def _last_meeting(self, title: str) -> str:
        for s in (self.meetings.sessions(100) if self.meetings else []):
            m = s.meta
            if m.get("summary_status") == "done" and m.get("title", "").strip().lower() == title.strip().lower():
                su = m.get("summary") or {}
                return (f"{datetime.fromtimestamp(m['started']):%Y-%m-%d}: {su.get('summary', '')}\n" +
                        "\n".join(f"- {p}" for p in su.get("key_points", [])))[:4000]
        return ""

    # ------------------------------------------------------------------ people
    def people(self) -> list[dict]:
        out = []
        for p in self.data["people"]:
            card = self.person(p["name"])
            out.append({**p, "mine": len(card["mine"]), "theirs": len(card["theirs"]), "last": card["last"]})
        return out

    def person(self, name: str) -> dict:
        """Open both ways and the last contact; no scores, no response times, no moods."""
        low = name.strip().lower()

        def about(i: dict) -> bool:
            return low in {x.strip().lower() for x in i["people"].split(",")} or low == i.get("person", "").lower()
        items = [i for i in self._open() if about(i)]
        promises = [p for p in self.promises() if low in (p["who"].lower(), p["to"].lower())]
        last = max((i["last"] for i in self.data["items"].values() if about(i)), default="")
        keep = ("key", "title", "action", "summary", "due", "link", "last", "source")
        return {"name": name, "last": last, "file": f"people/{export.slug(name)}.md",
                "notes": [ln for ln in section(parse(self.memory.read(f"people/{export.slug(name)}.md"))[1], "Notes")],
                "mine": [{k: i[k] for k in keep} for i in items if i["category"] in ("todo", "reply")] +
                        [{"title": p["what"], "due": p["due"], "link": p["link"], "promise": p["id"]} for p in promises if p["mine"]],
                "theirs": [{k: i[k] for k in keep} for i in items if i["category"] == "waiting"] +
                          [{"title": p["what"], "due": p["due"], "link": p["link"], "promise": p["id"]} for p in promises if not p["mine"]]}

    def add_person(self, name: str, title: str = "", mail: str = "") -> None:
        name = " ".join(name.split())
        if not name:
            raise ValueError("a person needs a name")
        with self._lock:
            if name.lower() not in {p["name"].lower() for p in self.data["people"]}:
                self.data["people"].append({"name": name, "title": title, "mail": mail, "added": True})
                self._save()

    def remove_person(self, name: str) -> None:
        with self._lock:
            self.data["people"] = [p for p in self.data["people"] if p["name"] != name]
            self._save()

    def note_person(self, name: str, text: str) -> None:
        text = " ".join(text.split())
        if not text:
            raise ValueError("the note is empty")
        fname = f"people/{export.slug(name)}.md"
        body = self.memory.read(fname) or f"# {name}\n\n"
        line = f"- {date.today().isoformat()} {text} (you)"
        self.memory.write(fname, with_section(body, "Notes", section(body, "Notes") + [line]), "note")

    # ------------------------------------------------------------------ the daily question
    def feedback(self, text: str) -> None:
        """"What did the Recap miss today?" stays in a local file; the user may send it as an idea."""
        with (data_dir() / "feedback.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": now_iso(), "text": text.strip()[:2000]}, ensure_ascii=False) + "\n")
        with self._lock:
            self.data["asked"] = date.today().isoformat()
            self._save()

    # ------------------------------------------------------------------ measuring the models (the AI tab)
    def compare(self, provider: str, limit: int = 30) -> dict:
        """The same conversations on another model: agreement with what the user confirmed, time and AI credits."""
        rows = [i for i in self.data["items"].values() if i.get("by") == "model" and i.get("text")]
        rows.sort(key=lambda i: (not i.get("reviewed"), i["last"]), reverse=False)
        rows = rows[:max(1, min(limit, 100))]
        if not rows:
            raise ValueError("run an Update first: there is nothing to compare yet")
        prompt = {"me": self.me, "profile": self.profile(), "corrections": ""}
        out = {"provider": provider, "items": 0, "reviewed": 0, "right": 0, "same": 0, "seconds": 0.0, "credits": 0.0, "errors": 0, "rows": []}
        t0 = time.time()
        for i in rows:
            try:
                a = self.classify(i, prompt, provider=provider)
            except LLMError as e:
                out["errors"] += 1
                out["error"] = str(e)[:200]
                if out["errors"] >= 3 and not out["items"]:
                    break
                continue
            out["items"] += 1
            out["credits"] += a.get("credits") or 0.0
            out["same"] += a["category"] == i["category"]
            if i.get("reviewed"):
                out["reviewed"] += 1
                out["right"] += a["category"] == self._cat(i)
            out["rows"].append({"title": i["title"][:80], "now": a["category"], "before": i["category"],
                                "you": self._cat(i) if i.get("reviewed") else ""})
        out["seconds"], out["credits"] = round(time.time() - t0, 1), round(out["credits"], 2)
        return out
