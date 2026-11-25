"""What the user does with an item, a promise and in the first-start questions."""

from __future__ import annotations

import re
from datetime import date, datetime

from .. import clock
from . import skills
from .filing import Filing
from .kit import Kit, Part
from .state import cat, conversation

UNDO_FIELDS = ("status", "until", "done_at", "done_by", "user_category", "reviewed")


class Actions(Part):
    """act() runs the method _<action>; the ones in UNDOABLE can be taken back once."""

    UNDOABLE = ("done", "not_mine", "mute", "snooze", "category")
    OTHERS = ("undo", "reopen", "right", "matter", "new_matter", "no_matter")

    def __init__(self, kit: Kit, filing: Filing):
        super().__init__(kit)
        self.filing = filing
        self._last: dict = {}        # the last change of an item, for the Undo in the window

    def act(self, key: str, action: str, value: str = "") -> None:
        if action not in self.UNDOABLE + self.OTHERS:
            raise ValueError(f"unknown action: {action}")
        with self.state.lock:
            i, d = self.state.item(key), self.state.data
            before = {"key": key, "item": {k: i[k] for k in UNDO_FIELDS if k in i}, "muted": list(d["muted"]), "corrections": list(d["corrections"])}
            getattr(self, f"_{action}")(i, value)
            if action in self.UNDOABLE:
                self._last = before
            self.state.save()
        if i.get("matter"):
            self.filing.write_open(i["matter"])

    # ------------------------------------------------------------------ can be taken back
    def _done(self, i: dict, _value: str) -> None:
        i.update(status="done", done_at=clock.now())

    def _not_mine(self, i: dict, _value: str) -> None:
        self._learn(i, "ignore")
        i.update(status="dropped", done_at=clock.now())

    def _mute(self, i: dict, _value: str) -> None:
        self._learn(i, "ignore")
        conv = conversation(i)
        for x in [i, *self.state.children(conv), *([self.state.item(conv)] if conv in self.state.data["items"] else [])]:
            x["user_category"] = "ignore"
        self.state.data["muted"] = list(dict.fromkeys([*self.state.data["muted"], conv]))[-500:]

    def _snooze(self, i: dict, value: str) -> None:
        datetime.strptime(value, "%Y-%m-%d")
        i.update(status="snoozed", until=value)

    def _category(self, i: dict, value: str) -> None:
        if value not in skills.CATEGORIES:
            raise ValueError(f"category must be one of {', '.join(skills.CATEGORIES)}")
        if value != i.get("category"):
            self._learn(i, value)
        i.update(user_category=value, reviewed=True, status="open")
        if value != "ignore" and conversation(i) in self.state.data["muted"]:
            self.state.data["muted"].remove(conversation(i))

    # ------------------------------------------------------------------ the others
    def _undo(self, i: dict, _value: str) -> None:
        last, self._last = self._last, {}
        if last.get("key") != i["key"]:
            self._last = last
            raise ValueError("there is nothing to undo any more")
        for k in UNDO_FIELDS:
            i.pop(k, None)
        i.update(last["item"])
        self.state.data.update(muted=last["muted"], corrections=last["corrections"])

    def _reopen(self, i: dict, _value: str) -> None:
        if i.get("done_by") == "answer":
            i["user_category"] = i["was"]        # not settled after all: back to where it was
        i["status"] = "open"
        for k in ("until", "done_at", "done_by"):
            i.pop(k, None)

    def _right(self, i: dict, _value: str) -> None:
        i["reviewed"] = True

    def _matter(self, i: dict, mid: str) -> None:
        old = i.get("matter")
        if mid:
            self.kit.memory.matter(mid)          # KeyError when it is not there
        if old and old != mid:
            try:
                self.kit.memory.forget_key(old, i["key"])
            except KeyError:
                pass
        i.update(matter=mid, matter_by="you")
        self._no_matter(i, "")
        if mid:
            self.kit.memory.add_entry(mid, i)
        if old and old != mid:
            self.filing.write_open(old)

    def _new_matter(self, i: dict, name: str) -> None:
        suggested = i.get("suggest")
        mid = self.kit.memory.create_matter(name or suggested or "")
        for x in [i, *(x for x in self.state.all() if suggested and x is not i and x.get("suggest") == suggested)]:
            self._matter(x, mid)

    def _no_matter(self, i: dict, _value: str) -> None:
        i.pop("suggest", None)
        i.pop("maybe", None)

    def _learn(self, i: dict, now: str) -> None:
        """A correction the next prompts show to the model."""
        was = cat(i)
        if was != now:
            row = {"at": clock.now(), "title": i["title"], "people": i["people"], "was": was, "now": now}
            self.state.data["corrections"] = [*self.state.data["corrections"], row][-200:]

    def seen(self, keys: list[str]) -> None:
        """The user looked at these (none named: at everything)."""
        with self.state.lock:
            for i in [self.state.item(k) for k in keys if k in self.state.data["items"]] if keys else self.state.all():
                i.pop("unread", None)
            self.state.save()

    # ------------------------------------------------------------------ the daily question, the first start
    def feedback(self, text: str) -> None:
        """"What did the Recap miss today?": stays on this laptop and goes into the next sorting."""
        with self.state.lock:
            if text.strip():
                self.state.data["missed"] = [*self.state.data["missed"], " ".join(text.split())[:500]][-20:]
            self.state.data["asked"] = date.today().isoformat()
            self.state.save()

    def setup(self, answers: dict) -> None:
        """The first-start interview: profile.md, the first matters, exclusions, the model."""
        def answer(key: str) -> str:
            return str(answers.get(key, "")).strip()
        if answer("provider") not in ("local", "copilot"):
            raise ValueError("choose where the model runs")
        exclude = [x.strip() for x in re.split(r"[,\n]", answer("never")) if x.strip()]
        self.kit.settings.update({"ai_provider": answer("provider"), "setup_done": True,
                                  "exclude": list(dict.fromkeys(self.kit.settings["exclude"] + exclude))})
        rows = (("My role", "role"), ("People I must not miss", "people"), ("What I found out too late", "late"))
        about = "".join(f"## {title}\n\n{answer(k)}\n\n" for title, k in rows if answer(k))
        if about:
            self.kit.memory.write("profile.md", "# About me\n\n" + about, "interview")
        known = {m["title"].lower() for m in self.kit.memory.matters(every=True)}
        for name in (line.strip(" -*\t") for line in answer("running").splitlines()):
            if name and name.lower() not in known:
                self.kit.memory.create_matter(name)
                known.add(name.lower())
