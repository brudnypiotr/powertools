"""What the window shows: the open state, sorted into lists. Reads only."""

from __future__ import annotations

from datetime import date, datetime

from .. import clock, export
from ..jobs import ACTIVE
from .answered import Answers
from .drafts import Drafts
from .kit import Kit, Part
from .state import age_days, cat
from .update import Updates

MAX_IGNORED = 200


def newest(items) -> list[dict]:
    return sorted(items, key=lambda i: i["last"], reverse=True)


class View(Part):
    def __init__(self, kit: Kit, updates: Updates, answers: Answers, drafts: Drafts):
        super().__init__(kit)
        self.updates, self.answers, self.drafts = updates, answers, drafts

    def build(self) -> dict:
        with self.state.lock:
            items = [self._shown(i) for i in self.state.all()]
            matters, promises, lists = self._matters(), self.state.promises(), self._lists(items)
            self._name_matters(matters, [*lists["decisions"], *lists["mine"], *lists["waiting"], *lists["changed"], *promises])
            return {**lists, **self._progress(items), "matters": matters, "events": self._events(), "suggested": self._suggested(items),
                    "promised": [p for p in promises if p["mine"]], "owed": [p for p in promises if not p["mine"]],
                    "setup_done": self.kit.settings["setup_done"], "silence_days": self.kit.settings["silence_days"],
                    "drafts": len(self.drafts.list())}

    @staticmethod
    def _shown(i: dict) -> dict:
        return {k: v for k, v in i.items() if k != "text"} | {"category": cat(i), "model_category": i.get("category"), "days": age_days(i)}

    def _lists(self, items: list[dict]) -> dict:
        shown, quiet = {i["key"] for i in self.state.open()}, self.kit.settings["silence_days"]
        open_now = [i for i in items if i["key"] in shown]
        waiting = sorted((i for i in open_now if i["category"] == "waiting"), key=lambda i: i["last"])
        for i in waiting:
            i["quiet"] = i["days"] >= quiet
        mine = newest(i for i in open_now if i["category"] in ("todo", "reply"))
        closed = self._closed(items, shown)
        return {"unread": sum(bool(i.get("unread")) for i in open_now), "decisions": [i for i in mine if i.get("decision")], "mine": [i for i in mine if not i.get("decision")], "waiting": waiting,
                "changed": newest(i for i in items if self._is_news(i)),
                "ignored": newest(i for i in items if i["category"] == "ignore" and i["status"] == "open")[:MAX_IGNORED],
                "answered": [i for i in closed if i.get("done_by") and i.get("done_at", "") >= clock.days_ago(1)],
                "closed": closed, "unsorted": [i for i in items if i.get("error")]}

    def _is_news(self, i: dict) -> bool:
        """New in the last update, at least in the last day. (An item not sorted yet has no category of the model.)"""
        day_ago = clock.days_ago(1)
        since = min(day_ago, self.state.data["last"].get("began", day_ago))
        return i["status"] == "open" and i["category"] == "fyi" and i["seen"] >= since and not i.get("error") and bool(i["model_category"])

    @staticmethod
    def _closed(items: list[dict], shown: set[str]) -> list[dict]:
        week_ago = clock.days_ago(7)
        rows = [i for i in items if (i["status"] == "snoozed" and i["key"] not in shown) or
                (i["status"] in ("done", "dropped") and i.get("done_at", "") >= week_ago)]
        return sorted(rows, key=lambda i: i.get("done_at") or i.get("until") or "", reverse=True)

    def _events(self) -> list[dict]:
        d, now = self.state.data, datetime.now().astimezone()
        return [{k: v for k, v in e.items() if k != "invite"} | {"prepared": e["id"] in d["preps"]}
                for e in d["events"] if export.local(export.parse_time(e["end"])) >= now and not e["all_day"]]

    def _matters(self) -> list[dict]:
        matters, limit = self.kit.memory.matters(every=True), self.kit.settings["matter_quiet_days"]
        for m in matters:
            m["silent"] = m["status"] == "active" and quiet_days(m["updated"]) >= limit
        return matters

    @staticmethod
    def _name_matters(matters: list[dict], rows: list[dict]) -> None:
        names = {m["id"]: m["title"] for m in matters}
        for i in rows:
            i["matter_title"] = names.get(i.get("matter") or "", "")
            i["maybe_title"] = "" if i.get("matter") else names.get(i.get("maybe") or "", "")

    def _progress(self, items: list[dict]) -> dict:
        d, u, look = self.state.data, self.updates, self.answers.job
        job, reviewed = u.job.view() if u.job else None, [i for i in items if i.get("reviewed")]
        state, doing = job["state"] if job else "", u.progress.text
        return {"brief": self._brief(), "updated": d["updated"], "last": d["last"], "cursor": d["cursor"], "job": job,
                "current": doing if state in ACTIVE else "", "paused_at": doing if state == "needs_token" else "",
                "ahead": [j.title for j in self.kit.graph.jobs.all if j.state == "running" and j is not u.job],
                "checked": d["checked"], "checking": self.answers.running,
                "check_error": look.error if look and look.state == "error" else "",
                "reviewed": len(reviewed), "agreed": sum(i["category"] == i["model_category"] for i in reviewed),
                "ask_feedback": bool(d["updated"]) and d.get("asked", "") != date.today().isoformat()}

    def _brief(self) -> dict | None:
        """The brief that came with today's Update."""
        b = self.state.data.get("brief")
        return b if b and date.fromtimestamp(b["at"]) == date.today() else None

    @staticmethod
    def _suggested(items: list[dict]) -> list[dict]:
        groups: dict[str, list] = {}
        for i in items:
            if i.get("suggest") and not i.get("matter") and i["status"] == "open":
                groups.setdefault(i["suggest"], []).append({"key": i["key"], "title": i["title"]})
        return [{"name": k, "items": v} for k, v in groups.items()]


def quiet_days(day: str) -> int:
    """Days since a date written in a file (by hand too: anything else counts as today)."""
    try:
        return (date.today() - date.fromisoformat(day[:10])).days
    except ValueError:
        return 0
