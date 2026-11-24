"""What the parts of the assistant share. A part gets the kit, not the other parts, unless it really uses one."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..exclusions import Exclusions
from .collect import names
from .memory import Memory, parse
from .state import State, age_days


@dataclass
class Kit:
    settings: Any
    graph: Any                   # GraphService: client, tokens, jobs
    llm: Any                     # Brain
    memory: Memory
    state: State
    jira: Any = None
    meetings: Any = None
    talk: list[dict] = field(default_factory=list)     # the Ask conversation of this session

    def exclusions(self) -> Exclusions:
        return Exclusions(self.settings["exclude"])

    def excluded(self, i: dict) -> bool:
        return self.exclusions().hit(i["title"], i["people"], *names(i))

    def profile(self) -> str:
        text = parse(self.memory.read("profile.md"))[1].strip()
        return "About " + self.state.me + " (own words):\n" + text[:1500] if text else ""

    def corrections(self) -> str:
        d = self.state.data
        rows = [f'- "{c["title"][:80]}" ({c["people"][:60]}): not "{c["was"]}" but "{c["now"]}"' for c in d["corrections"][-8:]]
        rows += [f"- missed earlier, in the user's words: {t[:200]}" for t in d["missed"][-5:]]
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
