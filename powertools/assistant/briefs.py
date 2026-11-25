"""Texts made of what is known: the day, the week, for the manager, back from leave, and the card before a meeting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from .. import clock, export
from ..llm import LLMError
from . import collect, skills
from .ask import SAID_IN
from .kit import Part
from .memory import rank, section, with_section
from .state import cat
from .text import lines, one_line

MAX_FACTS = 24000
KEEP_CARDS = 14              # days a card of a meeting that is over is kept
CARD = (("known", "What I know"), ("open", "Open from before"), ("questions", "To ask"), ("agenda", "Agenda"))


@dataclass(frozen=True)
class Kind:
    title: str
    days: int
    daily: bool              # written into the day's file (else the week's), with today's meetings

    @property
    def file(self) -> str:
        return f"days/{date.today():%Y-%m-%d}.md" if self.daily else f"weeks/{date.today():%G-W%V}.md"


KINDS = {"day": Kind("Brief", 1, True), "week": Kind("Week", 7, False), "upward": Kind("Upward", 7, False), "leave": Kind("Back from leave", 14, True)}


def bullets(rows, none: str) -> str:
    return "\n".join(f"- {r}" for r in rows) or none


class Briefs(Part):
    # ------------------------------------------------------------------ briefs
    def brief(self, kind: str, days: int = 0) -> dict:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        k = KINDS[kind]
        a = self._run(skills.BRIEF, "\n\n".join(self._facts(k, clock.days_ago(days or k.days)))[:MAX_FACTS],
                      task=skills.BRIEF_TASKS[kind].replace("{{me}}", self.state.me))
        rows = lines(a["lines"], 14)
        body = self.kit.memory.read(k.file) or f"# {k.file.split('/')[1][:-3]}\n\n"
        self.kit.memory.write(k.file, with_section(body, k.title, [f"- {x}" for x in rows]), f"brief: {kind}")
        return {"kind": kind, "lines": rows, "file": k.file}

    def _run(self, skill, user: str, **values) -> dict:
        try:
            return skill.run(self.kit.llm, user, me=self.state.me, profile=self.kit.profile(), **values)
        except LLMError as e:
            raise ValueError(str(e)) from e

    def _facts(self, k: Kind, since: str) -> list[str]:
        mine = [f"{p['what']} (to {p['to']}, {p['due']})" for p in self.state.promises() if p["mine"]]
        facts = [self.kit.overview(), "Promised by me: " + ("; ".join(mine) or "nothing")]
        if k.daily:
            facts.append("Meetings today: " + ("; ".join(self._today()) or "none"))
        rest = [i for i in self.state.all() if i["last"] >= since and not i.get("matter") and cat(i) != "ignore"][:40]
        return [*facts, *self._matters(since), "Outside the matters:\n" + bullets(
            (f"{i['last'][:10]} {i['title'][:80]}: {i.get('summary', '')} [{i['status']}]" for i in rest), "")]

    def _today(self) -> list[str]:
        starts = [(export.local(export.parse_time(e["start"])), e["title"]) for e in self.state.data["events"]]
        return [f"{at:%H:%M} {title}" for at, title in starts if at.date() == date.today()]

    def _matters(self, since: str) -> list[str]:
        out = []
        for m in self.kit.memory.matters(every=True):
            if m["status"] == "closed" and m["updated"] < since[:10]:
                continue
            _, body = self.kit.memory.matter(m["id"])
            news = [ln for ln in section(body, "Timeline") if ln[2:12] >= since[:10]]
            if news or m["status"] in ("active", "waiting"):
                decided = " ".join(section(body, "Decisions")[-5:])
                out.append(f"Matter {m['title']} ({m['status']}): {m['state']}\nDecisions: {decided}\nNew:\n" + ("\n".join(news[-10:]) or "- nothing"))
        return out

    # ------------------------------------------------------------------ the card before a meeting
    def prep(self, eid: str = "", again: bool = False, person: str = "") -> dict:
        """The card before a meeting (or a 1:1 with a person who is not on the calendar)."""
        e = self._one_to_one(person) if person else self._event(eid)
        have = self.state.data["preps"].get(e["id"])
        if have and not again:
            return have
        a = self._run(skills.PREP, self._about(e)[:20000])
        card = {"id": e["id"], "title": e["title"], "start": e["start"], "made": clock.now(), "goal": one_line(a["goal"]),
                **{k: lines(a[k], 6) for k, _ in CARD}}
        card["file"] = self._write_card(card)
        self._keep_card(card)
        return card

    def drop(self, eid: str) -> dict | None:
        """Throws the card of a meeting away; the undo brings its file back."""
        with self.state.lock:
            card = self.state.data["preps"].pop(eid)
            self.state.save()
        return self.kit.memory.remove(card["file"]) if self.kit.memory.read(card["file"]) else None

    def _event(self, eid: str) -> dict:
        for e in self.state.data["events"]:
            if e["id"] == eid:
                return e
        raise KeyError(eid)

    @staticmethod
    def _one_to_one(person: str) -> dict:
        return {"id": f"person:{person}", "title": f"1:1 with {person}", "people": [person], "start": clock.now(), "invite": "", "series": ""}

    def _about(self, e: dict) -> str:
        """Everything the model gets about one meeting."""
        names = {p.lower() for p in e["people"]} - {self.state.me.lower()}
        related = [i for i in self.state.open() if names & {x.lower() for x in collect.names(i)}][:12] if len(names) <= 12 else []
        promises = [p for p in self.state.promises() if names & {p["who"].lower(), p["to"].lower()}][:10]
        known = rank(f"{e['title']} {e['title']} {' '.join(list(names)[:6])} {e['invite'][:400]}", self.kit.memory.chunks(SAID_IN), 6)
        when = export.local(export.parse_time(e["start"]))
        return "\n\n".join([
            f"Meeting: {e['title']}\nWhen: {when:%A %Y-%m-%d %H:%M}\nPeople: {', '.join(e['people'][:20])}\nInvitation:\n{e['invite'] or '(empty)'}",
            "Open with these people:\n" + bullets((f"{i['category']}: {i['title'][:80]}: {i['action'] or i['summary']}" for i in related), "- nothing known"),
            "Promises:\n" + bullets((f"{p['who']} to {p['to']}: {p['what']} {p['due']}" for p in promises), "- none known"),
            "Last time:\n" + (self._last_meeting(e["title"]) or "(no recording)"),
            "Memory:\n" + ("\n\n".join(f"{c['title']}:\n{c['text'][:1200]}" for c in known) or "(nothing)")])

    def _last_meeting(self, title: str) -> str:
        for s in (self.kit.meetings.sessions(100) if self.kit.meetings else []):
            m = s.meta
            if m.get("summary_status") == "done" and m.get("title", "").strip().lower() == title.strip().lower():
                su = m.get("summary") or {}
                return f"{datetime.fromtimestamp(m['started']):%Y-%m-%d}: {su.get('summary', '')}\n{bullets(su.get('key_points', []), '')}"[:4000]
        return ""

    def _write_card(self, card: dict) -> str:
        day = export.local(export.parse_time(card["start"])).strftime("%Y-%m-%d")
        name = f"meetings/{day}-{export.slug(card['title'], 50)}.md"
        body = self.kit.memory.read(name) or f"# {card['title']} ({day})\n\n"
        body = with_section(body, "Goal", [card["goal"]] if card["goal"] else [])
        for k, title in CARD:
            body = with_section(body, title, [f"- {x}" for x in card[k]])
        self.kit.memory.write(name, body, "meeting prep")
        return name

    def _keep_card(self, card: dict) -> None:
        with self.state.lock:
            d, ahead = self.state.data, {e["id"] for e in self.state.data["events"]}
            cards = {**d["preps"], card["id"]: card}
            d["preps"] = {k: c for k, c in cards.items() if k in ahead or c["made"] > clock.days_ago(KEEP_CARDS)}
            self.state.save()
