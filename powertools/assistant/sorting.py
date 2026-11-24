"""Where the ball is now: what is new goes to the model, and the code corrects what it knows better."""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from .. import clock
from ..llm import LLMError
from . import answered, collect, skills
from .drafts import Drafts
from .kit import Kit, Part
from .text import cut

log = logging.getLogger("powertools.assistant")
NOT_SORTED = {"category": "fyi", "summary": "", "action": "", "due": "", "person": "", "quote": "", "decision": False, "commitments": []}
KEPT = ("matter", "matter_by", "first_seen", "draft", "status", "until", "done_at", "done_by", "category", "user_category", "summary", "action", "due",
        "person", "quote", "decision", "commitments", "by")


def one_to_one(i: dict) -> bool:
    return len(collect.names(i)) <= 1


class Sorting(Part):
    def __init__(self, kit: Kit, drafts: Drafts):
        super().__init__(kit)
        self.drafts = drafts

    def merge(self, found: list[dict]) -> list[dict]:
        """What goes to the model: new and changed conversations, and what could not be sorted last time (no model, the
        limit, the app was closed). The others keep what is known about them."""
        with self.state.lock:
            items = self.state.data["items"]
            todo = [self._merged(new, items.get(new["key"])) for new in found if self._changed(new, items.get(new["key"]))]
            items.update({i["key"]: i for i in todo})
            chosen = {i["key"] for i in todo}
            again = [i for i in items.values() if (i.get("error") or i.get("pending")) and i["key"] not in chosen]
        for i in todo:
            if i.get("draft") and not self.drafts.overtaken(i["draft"], by_me=i["wrote"]):
                i.pop("draft")
        return todo + again

    @staticmethod
    def _changed(new: dict, old: dict | None) -> bool:
        sorted_well = bool(old) and bool(old.get("category")) and not old.get("error") and not old.get("pending")
        return not sorted_well or old["last"] != new["last"]

    @staticmethod
    def _merged(new: dict, old: dict | None) -> dict:
        """Until it is sorted again a conversation stays where it was."""
        old = old or {}
        item = {**new, "first_seen": clock.now(), "status": "open", **{k: old[k] for k in KEPT if k in old}, "seen": clock.now(),
                "pending": True, "fresh": True}
        if not new["last_from_me"]:
            item["status"] = "open"              # a new message of someone else opens it again, my own does not
            item.pop("until", None)
        # I wrote something since it was sorted, even when someone had the last word
        item["wrote"] = new["last_from_me"] or bool(old) and answered.newer(new.get("mine_at", ""), old["last"])
        if old.get("user_category"):
            item["filed"] = old["user_category"]
        return item

    def sort(self, todo: list[dict], job, said: Callable[[str], None]) -> None:
        broken: list[str] = []
        prompt = {"me": self.state.me, "profile": self.kit.profile(), "corrections": self.kit.corrections()}

        def one(item: dict) -> None:
            if job.stop:
                return                 # stays "pending": sorted next time
            ignored = dict(category="ignore", action="", due="", person="", quote="", decision=False, commitments=[])
            error = ""
            if item["key"] in self.state.data["muted"]:
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
                except Exception as e:   # one odd conversation must not stop every Update from now on
                    log.exception("triage %s", item["key"])
                    got, error = dict(NOT_SORTED), f"{type(e).__name__}: {e}"[:200]
            with self.state.lock:
                self._settle(item, got, error)
            job.done += 1
            said(f"sorting {job.done}/{job.total}: {item['title'][:70]}")

        with ThreadPoolExecutor(max_workers=getattr(self.kit.llm, "parallel", 1)) as pool:
            for n, _ in enumerate(pool.map(one, todo), 1):
                if n % 10 == 0:
                    self.state.save()
        self.state.save()
        
    def _settle(self, item: dict, got: dict, error: str) -> None:
        """The answer of the model, with what the code knows better: after the user's own answer nobody waits for it."""
        was = item.get("category")
        item.update(got)
        for k in ("error", "pending", "user_category", "reviewed"):
            item.pop(k, None)
        mine = item["last_from_me"] and one_to_one(item)      # in a group the last word can be about something else
        if error and mine and was == "reply":
            error = ""                 # a question the user answered needs no model to be closed
        if error:
            item["error"] = error
        elif mine and item["category"] == "reply":
            item.update(category="fyi", decision=False)
        if not error and item.get("wrote") and was in ("todo", "reply") and item["category"] in ("fyi", "ignore") and item["status"] == "open":
            item.update(status="done", done_at=clock.now(), done_by="answer", was=was)

    def classify(self, item: dict, prompt: dict, provider: str = "") -> dict:
        hint = "\n\n(The latest message is from me.)" if item["last_from_me"] else "\n\n(I wrote here since it was asked.)" if item.get("wrote") else ""
        hint += f"\n\n(I reacted to the latest message with {item['reacted']}.)" if item.get("reacted") else ""
        hint += f"\n\n(Before the newest message I filed this conversation as {item['filed']}.)" if item.get("filed") else ""
        a = skills.TRIAGE.run(self.kit.llm, cut(item["text"]) + hint, priority=skills.BACKGROUND, provider=provider, **prompt)
        stats = a.pop("_stats", {})
        return {**skills.clean_triage(a, item["text"]), "by": "model", "orders": skills.gives_orders(item["text"]), "took_s": stats.get("total_s", 0),
                "credits": stats.get("credits")}
