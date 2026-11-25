"""Ask: answers from the memory and the items, with sources; what the user says is different goes into the memory."""

from __future__ import annotations

import re

from .. import clock, export
from ..llm import LLMError
from . import skills
from .kit import Part
from .memory import parse, rank
from .text import LABELS, cut, said, tells

SAID_IN = ("profile.md", "style.md", "matters/", "people/")   # memory worth quoting; briefs and cards only repeat it


class Ask(Part):
    def _sources(self, question: str) -> list[dict]:
        chunks = self.kit.memory.chunks(SAID_IN)
        for i in (i for i in self.state.all() if not i.get("parent")):
            chunks.append({"file": "", "title": f"{LABELS.get(i['source'], i['source'])}: {i['title']}", "link": i["link"],
                           "text": f"{i['last'][:10]} {i['people']}\n{i.get('summary', '')}\n{cut(i['text'], 1800)}"})
        for e in self.state.data["events"]:
            chunks.append({"file": "", "title": f"Calendar: {e['title']}", "link": e["link"],
                           "text": f"{export.local(export.parse_time(e['start'])):%A %Y-%m-%d %H:%M} with {', '.join(e['people'][:12])}"})
        past = " ".join(t["question"] for t in self.kit.talk[-2:])
        return rank(f"{question} {question} {past}", chunks, 10)

    def ask(self, question: str) -> dict:
        question = question.strip()
        if not question:
            raise ValueError("ask something")
        turn = {"question": question, "answer": "", "at": clock.now(), "sources": [], "edits": []}
        try:
            if tells(question):
                turn["edits"] = self._remember(question)
            if turn["edits"] and "?" not in question:
                turn["answer"] = "Noted." if any(e["state"] == "applied" for e in turn["edits"]) else "Should I change this?"
            else:
                self._answer(question, turn)
        except LLMError as e:
            raise ValueError(str(e)) from e
        self.kit.talk = (self.kit.talk + [turn])[-30:]
        return turn

    def _answer(self, question: str, turn: dict) -> None:
        found = self._sources(question)
        parts = [f"[S{n}] {c['title']}\n{c['text'][:1800]}" for n, c in enumerate(found, 1)]
        past = "\n".join(f"Earlier question: {t['question']}\nEarlier answer: {t['answer'][:500]}" for t in self.kit.talk[-4:])
        user = f"{self.kit.overview()}\n\nSources:\n" + "\n\n".join(parts) + (f"\n\n{past}" if past else "") + f"\n\nQuestion: {question[:2000]}"
        a = skills.ASK.run(self.kit.llm, user, me=self.state.me, profile=self.kit.profile())
        used = [int(m) for s in a["sources"] for m in re.findall(r"\d+", str(s)) if 0 < int(m) <= len(found)]
        turn["answer"] = re.sub(r"\s*\[S\d+(?:,\s*S\d+)*\]", "", str(a["answer"])).strip()
        seen = set()
        for c in (found[n - 1] for n in dict.fromkeys(used)):
            if (c["title"], c["file"]) not in seen:
                seen.add((c["title"], c["file"]))
                turn["sources"].append({"title": c["title"], "file": c["file"], "link": c.get("link", "")})

    def _remember(self, text: str) -> list[dict]:
        names = [c["file"] for c in rank(f"{text} {' '.join(t['question'] for t in self.kit.talk[-1:])}", self.kit.memory.chunks(SAID_IN), 8)]
        files = list(dict.fromkeys([*names, "profile.md"]))[:5]
        user = "\n\n".join(f"File {f}:\n{parse(self.kit.memory.read(f))[1][:2500] or '(empty)'}" for f in files) + f"\n\n{self.state.me} said: {text[:1500]}"
        a = skills.REMEMBER.run(self.kit.llm, user, me=self.state.me)
        return self._edits([e for e in a["edits"] if isinstance(e, dict)], text)

    def _edits(self, edits: list[dict], question: str) -> list[dict]:
        """What the user said goes into the files at once (with Undo). An edit whose words do not come from the user
        (a guess, or a mail that tries to change the notes) is only proposed."""
        out = []
        for e in edits[:10]:
            if said(str(e.get("new", "")), str(e.get("old", "")), question):
                out += [{**d, "state": "error" if d["error"] else "applied"} for d in self.kit.memory.edit([e], f"you said: {question[:200]}")]
            else:
                out.append({**{k: str(e.get(k, "")) for k in ("file", "old", "new")}, "id": "", "error": "", "state": "proposed"})
        return out
