"""Which matter a conversation belongs to, and the files of the matters."""

from __future__ import annotations

import logging
from collections.abc import Callable

from ..llm import LLMError
from . import skills
from .kit import Part
from .memory import section
from .state import OPEN
from .text import cut, shares_word

log = logging.getLogger("powertools.assistant")
MAX_MATCH = 40               # conversations per "which matter" question


class Filing(Part):
    def match(self, fresh: list[dict]) -> set[str]:
        """Sets the matter; returns the conversations the model could not be asked about."""
        matters = self.kit.memory.matters(every=True)
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
                with self.state.lock:
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
                res = skills.MATCH.run(self.kit.llm, user, priority=skills.BACKGROUND, me=self.state.me)["results"]
            except LLMError as e:
                log.warning("matching: %s", e)
                return {i["key"] for i in rest[n:]}
            answers = {str(r.get("key", "")): r for r in res if isinstance(r, dict)}
            with self.state.lock:
                for i in part:
                    r = answers.get(i["key"], {})
                    if r.get("matter") in titles:
                        # the model alone is too generous: without a word of the matter's name in the text it only asks
                        sure = shares_word(titles[r["matter"]], f"{i['title']} {i['summary']} {i['text']}", i["people"])
                        i.update({"matter": r["matter"], "matter_by": "model"} if sure else {"maybe": r["matter"]})
                    elif str(r.get("new") or "").strip() and i["category"] in OPEN:
                        i["suggest"] = " ".join(str(r["new"]).split())[:60]
        return set()

    def to_matters(self, fresh: list[dict]) -> list[str]:
        touched = []
        for i in fresh:
            if i.get("matter") and i["category"] != "ignore":
                try:
                    self.kit.memory.add_entry(i["matter"], i)
                    touched.append(i["matter"])
                except (KeyError, OSError) as e:
                    log.warning("matter %s: %s", i["matter"], e)
        return list(dict.fromkeys(touched))

    def update_files(self, touched: list[str], step: Callable[[str], None]) -> None:
        for n, mid in enumerate(touched, 1):
            step(f"updating matter {n}/{len(touched)}")
            try:
                meta, body = self.kit.memory.matter(mid)
                tl = section(body, "Timeline")
                user = (f"Matter: {meta.get('title')}\n\nFile:\n{cut(body, 4000)}\n\nNewest entries:\n" + "\n".join(tl[-12:]))
                a = skills.STATE.run(self.kit.llm, user, priority=skills.BACKGROUND, me=self.state.me)
                state = " ".join(str(a["state"]).split())
                if state:
                    self.kit.memory.set_lines(mid, "State", [state], "update")
                old = section(body, "Decisions")
                new = [f"- {d}" for d in (" ".join(str(x).split()) for x in a["decisions"][:5]) if d and f"- {d}" not in old]
                if new:
                    self.kit.memory.set_lines(mid, "Decisions", old + new, "update")
            except (LLMError, KeyError, OSError) as e:
                log.warning("state of %s: %s", mid, e)
            self.write_open(mid)

    def write_open(self, mid: str) -> None:
        """The Open section of a matter, from the items (readable without the app)."""
        lines = [f"- {'Waiting for ' + (i['person'] or 'others') if i['category'] == 'waiting' else 'Mine'}: "
                 f"{i['action'] or i['summary']}" + (f" (due {i['due']})" if i["due"] else "")
                 for i in self.state.open() if i.get("matter") == mid]
        try:
            self.kit.memory.set_lines(mid, "Open", lines, "update")
        except (KeyError, OSError) as e:
            log.warning("open list of %s: %s", mid, e)
