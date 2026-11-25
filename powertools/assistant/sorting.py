"""Where the ball is now. What is new goes to the model, one conversation at a time; it lists what is open in it ("asks"), and
the code keeps each ask as one item and corrects what it knows better.

The first ask lives on the conversation's own item, the others on items with a "parent". An ask keeps its item (and with it
its matter, its draft, its "later") from one sorting to the next.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from .. import clock
from ..llm import LLMError
from . import collect, skills
from .drafts import Drafts
from .kit import Kit, Part
from .state import OPEN, cat, newer
from .text import alike, cut

log = logging.getLogger("powertools.assistant")
NO_ASK = {"action": "", "due": "", "person": "", "quote": "", "decision": False}
NOTHING_IN_IT = {"category": "ignore", "commitments": [], "asks": []}
NOT_SORTED = {"category": "fyi", "summary": "", "commitments": [], "asks": []}
KEPT = ("matter", "matter_by", "first_seen", "draft", "status", "until", "done_at", "done_by", "category", "user_category", "summary", "action", "due",
        "person", "quote", "decision", "commitments", "by", "unread")
SHARED = ("title", "people", "who", "kind", "last", "link", "msg_id", "count", "last_from_me", "keys", "text", "seen", "wrote")   # of a conversation


def one_to_one(i: dict) -> bool:
    return len(collect.names(i)) <= 1


def is_open(i: dict) -> bool:
    return i["status"] in ("open", "snoozed") and cat(i) in OPEN


class Sorting(Part):
    def __init__(self, kit: Kit, drafts: Drafts):
        super().__init__(kit)
        self.drafts = drafts

    # ------------------------------------------------------------------ what goes to the model
    def merge(self, found: list[dict]) -> list[dict]:
        """New and changed conversations, and what could not be sorted last time (no model, the limit, the app was closed).
        The others keep what is known about them."""
        with self.state.lock:
            items = self.state.data["items"]
            todo = [self._merged(new, items.get(new["key"])) for new in found if self._changed(new, items.get(new["key"]))]
            items.update({i["key"]: i for i in todo})
            chosen = {i["key"] for i in todo}
            again = [i for i in items.values() if (i.get("error") or i.get("pending")) and i["key"] not in chosen]
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
                "pending": True, "fresh": True, "moved": True}
        if not new["last_from_me"]:
            item["status"] = "open"              # a new message of someone else opens it again, my own does not
            item.pop("until", None)
        # I wrote something since it was sorted, even when someone had the last word
        item["wrote"] = new["last_from_me"] or bool(old) and newer(new.get("mine_at", ""), old["last"])
        if old.get("user_category"):
            item["filed"] = old["user_category"]
        return item

    def sort(self, todo: list[dict], job, said: Callable[[str], None]) -> None:
        broken: list[str] = []
        prompt = {"me": self.state.me, "profile": self.kit.profile(), "corrections": self.kit.corrections()}

        def one(conv: dict) -> None:
            if job.stop:
                return                 # stays "pending": sorted next time
            got, error = self._answer(conv, prompt, broken)
            with self.state.lock:
                self._settle(conv, got, error)
            job.done += 1
            said(f"sorting {job.done}/{job.total}: {conv['title'][:70]}")

        with ThreadPoolExecutor(max_workers=getattr(self.kit.llm, "parallel", 1)) as pool:
            for n, _ in enumerate(pool.map(one, todo), 1):
                if n % 10 == 0:
                    self.state.save()
        self.state.save()

    def _answer(self, conv: dict, prompt: dict, broken: list[str]) -> tuple[dict, str]:
        """(what is in it, error). Three failures in a row end the asking: the rest is tried again next time."""
        if conv["key"] in self.state.data["muted"]:
            return {**NOTHING_IN_IT, "summary": "Muted by you.", "by": "you"}, ""
        if conv["automated"]:
            return {**NOTHING_IN_IT, "summary": "Automatic message.", "by": "rule"}, ""
        if len(broken) >= 3:
            return dict(NOT_SORTED), broken[-1]
        try:
            got = self.classify(conv, prompt)
        except LLMError as e:
            log.warning("triage %s: %s", conv["key"], e)
            broken.append(str(e)[:200])
            return dict(NOT_SORTED), broken[-1]
        except Exception as e:   # one odd conversation must not stop every Update from now on
            log.exception("triage %s", conv["key"])
            return dict(NOT_SORTED), f"{type(e).__name__}: {e}"[:200]
        broken.clear()
        return got, ""

    def classify(self, conv: dict, prompt: dict, provider: str = "") -> dict:
        before = {str(n): i for n, i in enumerate(self._open_in(conv), 1)}
        a = skills.TRIAGE.run(self.kit.llm, cut(conv["text"]) + self._hints(conv, before), priority=skills.BACKGROUND, provider=provider, **prompt)
        stats = a.pop("_stats", {})
        got = skills.clean_triage(a, conv["text"])
        for ask in got["asks"]:
            ask["same"] = before[ask["same"]]["key"] if ask["same"] in before else ""
        return {**got, "by": "model", "orders": skills.gives_orders(conv["text"]), "took_s": stats.get("total_s", 0), "credits": stats.get("credits")}

    @staticmethod
    def _hints(conv: dict, before: dict[str, dict]) -> str:
        """What the text of the conversation does not say."""
        hints = []
        if conv["last_from_me"]:
            hints.append("The latest message is from me.")
        elif conv.get("wrote"):
            hints.append("I wrote here since it was asked.")
        if conv.get("reacted"):
            hints.append(f"I reacted to the latest message with {conv['reacted']}.")
        if conv.get("filed"):
            hints.append(f"Before the newest message I filed this conversation as {conv['filed']}.")
        if before:
            hints.append("Open in it before the newest message: " + "; ".join(f"[{n}] {i['action'] or i['summary']}" for n, i in before.items()))
        return "".join(f"\n\n({h})" for h in hints)

    # ------------------------------------------------------------------ what becomes of the answer
    def _family(self, conv: dict) -> list[dict]:
        return [conv, *self.state.children(conv["key"])]

    def _open_in(self, conv: dict) -> list[dict]:
        return [i for i in self._family(conv) if is_open(i)]

    def _settle(self, conv: dict, got: dict, error: str) -> None:
        family, before = self._family(conv), self._open_in(conv)
        was = {i["key"]: cat(i) for i in before}
        for i in family[1:]:
            i.update({k: conv[k] for k in SHARED if k in conv})
        asks = got["asks"]
        conv.update({k: v for k, v in got.items() if k != "asks"})
        for k in ("error", "pending", "reviewed", "user_category"):
            conv.pop(k, None)
        # one question, one person, and I had the last word: answered, whatever the model says (a group may be about more)
        answered = conv["last_from_me"] and one_to_one(conv) and sum(c == "reply" for c in was.values()) <= 1
        if error and not (answered and "reply" in was.values()):
            conv.update(NO_ASK, error=error)      # stays visible as "Not sorted"; the next Update tries again
            return
        if conv.get("resolved"):
            asks = []                             # the issue is done in Jira
        elif answered:
            asks = [a for a in asks if a["kind"] != "reply"]
        self._assign(conv, before, was, self._not_closed_by_me(asks, conv, family, before))
        self._drafts(family, before, moved=conv.pop("moved", False))

    @staticmethod
    def _not_closed_by_me(asks: list[dict], conv: dict, family: list[dict], before: list[dict]) -> list[dict]:
        """Without what I marked done (or not mine): the same sentence does not bring it back, nor does my own message."""
        closed = [i for i in family if i not in before and i["status"] in ("done", "dropped")]
        mine = conv["last_from_me"] and conv in closed
        return [a for a in asks if not (mine and a["kind"] != "waiting")
                and not any(alike(a["quote"], i.get("quote", "")) and i.get("done_by") is None for i in closed)]

    def _assign(self, conv: dict, before: list[dict], was: dict[str, str], asks: list[dict]) -> None:
        """Each ask to the item it was before, the new ones to the conversation's item or to items of their own."""
        left = list(asks)
        for i in before:
            ask = self._same(i, left, alone=len(before) == len(asks) == 1)
            if ask:
                left.remove(ask)
                self._put(i, ask, conv)
            else:
                self._close(i, conv, was[i["key"]])
        free = conv if conv not in before else None
        if free and not left:
            conv.update(NO_ASK)
            self._news(conv, conv)
        for ask in left:
            self._put(free or self._child(conv, ask), ask, conv)
            free = None

    @staticmethod
    def _same(i: dict, asks: list[dict], alone: bool) -> dict | None:
        by_id = [a for a in asks if a["same"] == i["key"]]
        like = [a for a in asks if not a["same"] and (alike(a["quote"], i["quote"]) or alike(a["action"], i["action"]))]
        return next(iter(by_id or like or (asks if alone else [])), None)

    def _put(self, i: dict, ask: dict, conv: dict) -> None:
        changed = i.get("action") != ask["action"] or i["status"] in ("done", "dropped")
        i.update({k: ask[k] for k in NO_ASK}, category=ask["kind"], summary=conv["summary"], by=conv.get("by", ""))
        for k in ("reviewed", "user_category", "done_at", "done_by"):
            i.pop(k, None)
        if i["status"] in ("done", "dropped"):
            i["status"] = "open"
        if changed or not conv["last_from_me"]:
            self._news(i, conv)

    def _close(self, i: dict, conv: dict, was: str) -> None:
        """It was open and is not any more: I answered, they did, or it is settled."""
        if i is conv:
            i.update(NO_ASK | {"action": i.get("action", ""), "quote": i.get("quote", "")})
        by = "resolved" if conv.get("resolved") else "answer" if conv.get("wrote") else "them" if was == "waiting" else ""
        if (by or i is not conv) and i["status"] == "open":
            i.update(status="done", done_at=clock.now(), done_by=by or "update", was=was)
        if not conv["last_from_me"]:
            self._news(i, conv)

    def _news(self, i: dict, conv: dict) -> None:
        """A dot until the user looks; not for what came with the very first Update, nor for my own messages."""
        if self.state.data["updated"] and not conv["last_from_me"] and cat(i) != "ignore":
            i["unread"] = True

    def _child(self, conv: dict, ask: dict) -> dict:
        key = f"{conv['key']}#{hashlib.sha1((ask['action'] + ask['quote']).encode()).hexdigest()[:6]}"
        child = {**{k: conv[k] for k in SHARED if k in conv}, "key": key, "parent": conv["key"], "source": conv["source"], "automated": False,
                 "status": "open", "first_seen": clock.now(), "commitments": [], "fresh": True,
                 **({"matter": conv["matter"], "matter_by": conv.get("matter_by", "")} if conv.get("matter") else {})}
        self.state.data["items"][key] = child
        return child

    def _drafts(self, family: list[dict], before: list[dict], moved: bool) -> None:
        """A draft ends with its ask; one that is still open was written before the newest message (when there is one)."""
        for i in family:
            if i.get("draft") and (moved or not is_open(i)) and not self.drafts.overtaken(i["draft"], by_me=not is_open(i) or bool(i.get("wrote")) and len(before) == 1):
                i.pop("draft")
                i["fresh"] = True            # the Update writes a new one where one is needed
