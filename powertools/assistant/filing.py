"""Which matter a conversation belongs to, and the files of the matters."""

from __future__ import annotations

import logging
from collections.abc import Callable

from ..llm import LLMError
from . import skills
from .kit import Part
from .memory import section
from .state import OPEN
from .text import cut, lines, one_line, shares_word

log = logging.getLogger("powertools.assistant")
MAX_MATCH = 40               # conversations per "which matter" question


class Filing(Part):
    def match(self, fresh: list[dict]) -> set[str]:
        """Sets the matter: by thread or Jira key when that is clear, else asks the model. Returns the keys it could not be
        asked about."""
        matters = self.kit.memory.matters(every=True)
        rest = [i for i in fresh if i["category"] != "ignore" and not self._known(i, matters)]
        live = [m for m in matters if m["status"] in ("active", "waiting")]
        for n in range(0, len(rest), MAX_MATCH):
            part = rest[n:n + MAX_MATCH]
            try:
                answers = self._ask(part, live)
            except LLMError as e:
                log.warning("matching: %s", e)
                return {i["key"] for i in rest[n:]}
            with self.state.lock:
                for i in part:
                    self._apply(i, answers.get(i["key"], {}), {m["id"]: m["title"] for m in live})
        return set()

    def _known(self, i: dict, matters: list[dict]) -> bool:
        if i.get("matter") and i["matter"] not in {m["id"] for m in matters}:
            i["matter"] = ""                     # its file is gone
        if i.get("matter"):
            return True
        keys = {i["key"], *("jira:" + k for k in i.get("keys", []))}
        hits = [m["id"] for m in matters if m["status"] != "closed" and keys & set(m["keys"])]
        if len(hits) == 1:
            with self.state.lock:
                i.update(matter=hits[0], matter_by="key")
        return len(hits) == 1

    def _ask(self, part: list[dict], live: list[dict]) -> dict[str, dict]:
        known = "\n".join(f"{m['id']}: {m['title']}. {m['state'][:160]}" for m in live) or "(none yet)"
        rows = "\n".join(f"{i['key']} | {i['title'][:100]} | {i['people'][:80]} | {i['summary']}" for i in part)
        a = skills.MATCH.run(self.kit.llm, f"Open matters:\n{known}\n\nConversations:\n{rows}", priority=skills.BACKGROUND, me=self.state.me)
        return {str(r.get("key", "")): r for r in a["results"] if isinstance(r, dict)} if isinstance(a["results"], list) else {}

    @staticmethod
    def _apply(i: dict, answer: dict, titles: dict[str, str]) -> None:
        mid, new = answer.get("matter"), one_line(answer.get("new") or "", 60)
        if mid in titles:
            # the model alone is too generous: without a word of the matter's name in the text it only asks
            sure = shares_word(titles[mid], f"{i['title']} {i['summary']} {i['text']}", i["people"])
            i.update({"matter": mid, "matter_by": "model"} if sure else {"maybe": mid})
        elif new and i["category"] in OPEN:
            i["suggest"] = new

    def to_matters(self, fresh: list[dict]) -> tuple[list[str], set[str]]:
        """Adds the conversations to their matters' timelines: (matters touched, keys that did not get in)."""
        touched, failed = {}, set()
        for i in fresh:
            if i.get("matter") and i["category"] != "ignore":
                try:
                    self.kit.memory.add_entry(i["matter"], i)
                    touched[i["matter"]] = True
                except (KeyError, OSError) as e:
                    log.warning("matter %s: %s", i["matter"], e)
                    failed.add(i["key"])
        return list(touched), failed

    def update_files(self, touched: list[str], step: Callable[[str], None]) -> None:
        for n, mid in enumerate(touched, 1):
            step(f"updating matter {n}/{len(touched)}")
            try:
                meta, body = self.kit.memory.matter(mid)
                tl = section(body, "Timeline")
                user = (f"Matter: {meta.get('title')}\n\nFile:\n{cut(body, 4000)}\n\nNewest entries:\n" + "\n".join(tl[-12:]))
                a = skills.STATE.run(self.kit.llm, user, priority=skills.BACKGROUND, me=self.state.me)
                state = one_line(a["state"])
                if state:
                    self.kit.memory.set_lines(mid, "State", [state], "update")
                old = section(body, "Decisions")
                new = [f"- {d}" for d in lines(a["decisions"], 5) if f"- {d}" not in old]
                if new:
                    self.kit.memory.set_lines(mid, "Decisions", old + new, "update")
            except (LLMError, KeyError, OSError) as e:
                log.warning("state of %s: %s", mid, e)
            self.write_open(mid)

    def write_open(self, mid: str) -> None:
        """The Open section of a matter, from the items (readable without the app)."""
        rows = [f"- {'Waiting for ' + (i['person'] or 'others') if i['category'] == 'waiting' else 'Mine'}: "
                 f"{i['action'] or i['summary']}" + (f" (due {i['due']})" if i["due"] else "")
                 for i in self.state.open() if i.get("matter") == mid]
        try:
            self.kit.memory.set_lines(mid, "Open", rows, "update")
        except (KeyError, OSError) as e:
            log.warning("open list of %s: %s", mid, e)
