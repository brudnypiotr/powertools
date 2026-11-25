"""What the parts of the assistant share. A part gets the kit, not the other parts, unless it really uses one."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from ..exclusions import Exclusions
from .collect import names
from .memory import Memory, bare, parse
from .state import State, age_days

MISSED = "missed.md"
log = logging.getLogger("powertools.assistant")


@dataclass
class Kit:
    settings: Any
    graph: Any                   # GraphService: client, tokens, jobs
    llm: Any                     # Brain
    memory: Memory
    state: State
    jira: Any = None
    meetings: Any = None

    def exclusions(self) -> Exclusions:
        return Exclusions(self.settings["exclude"])

    def excluded(self, i: dict) -> bool:
        return self.exclusions().hit(i["title"], i["people"], *names(i))

    def jira_ready(self) -> bool:
        if self.jira is None:
            return False
        try:
            c = self.jira.creds()
            return bool(c.base_url and c.token)
        except Exception as e:
            log.info("jira skipped: %s", e)
            return False

    def profile(self) -> str:
        text = parse(self.memory.read("profile.md"))[1].strip()
        return "About " + self.state.me + " (own words):\n" + text[:1500] if text else ""

    def corrections(self) -> str:
        d = self.state.data
        rows = [f'- "{c["title"][:80]}" ({c["people"][:60]}): not "{c["was"]}" but "{c["now"]}"' for c in d["corrections"][-8:]]
        rows += [f"- missed earlier, in the user's words: {bare(ln)[:200]}" for ln in self.memory.read(MISSED).splitlines() if ln.startswith("- ")][-5:]
        return "The user corrected earlier answers; learn from it:\n" + "\n".join(rows) if rows else ""

    def overview(self) -> str:
        opened = self.state.open()
        lines = [f"(Today is {date.today():%A %Y-%m-%d}.)", "Open matters: " + (", ".join(m["title"] for m in self.memory.matters()) or "none")]
        for label, cats in ((f"{self.state.me} has to do", ("todo", "reply")), (f"Others owe {self.state.me}", ("waiting",))):
            rows = [f"- {i['title'][:80]}: {i['action'] or i['summary']}" + (f" [{i['person']}]" if i["person"] else "") + f" ({age_days(i)} d)"
                    for i in opened if i["category"] in cats][:15]
            lines.append(f"{label}:\n" + ("\n".join(rows) or "- nothing"))
        return "\n".join(lines)


class Part:
    def __init__(self, kit: Kit):
        self.kit, self.state = kit, kit.state
