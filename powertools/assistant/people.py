"""People: what is open both ways and the last contact; no scores, no response times, no moods."""

from __future__ import annotations

from datetime import date

from .. import export
from . import collect
from .kit import Part
from .memory import parse, section, with_section


class People(Part):
    def all(self) -> list[dict]:
        out = []
        for p in (p for p in self.state.data["people"] if p["name"] not in self.state.data["hidden"]):
            card = self.person(p["name"])
            out.append({**p, "mine": len(card["mine"]), "theirs": len(card["theirs"]), "last": card["last"]})
        return out

    def person(self, name: str) -> dict:
        """Open both ways and the last contact; no scores, no response times, no moods."""
        low = name.strip().lower()

        def about(i: dict) -> bool:
            return low in {x.lower() for x in collect.names(i)} or low == i.get("person", "").lower()
        items = [i for i in self.state.open() if about(i)]
        promises = [p for p in self.state.promises() if low in (p["who"].lower(), p["to"].lower())]
        last = max((i["last"] for i in self.state.all() if about(i)), default="")
        keep = ("key", "title", "action", "summary", "due", "link", "last", "source")
        return {"name": name, "last": last, "file": f"people/{export.slug(name)}.md",
                "notes": [ln for ln in section(parse(self.kit.memory.read(f"people/{export.slug(name)}.md"))[1], "Notes")],
                "mine": [{k: i[k] for k in keep} for i in items if i["category"] in ("todo", "reply")] +
                        [{"title": p["what"], "due": p["due"], "link": p["link"], "promise": p["id"]} for p in promises if p["mine"]],
                "theirs": [{k: i[k] for k in keep} for i in items if i["category"] == "waiting"] +
                          [{"title": p["what"], "due": p["due"], "link": p["link"], "promise": p["id"]} for p in promises if not p["mine"]]}

    def add(self, name: str, title: str = "", mail: str = "") -> None:
        name = " ".join(name.split())
        if not name:
            raise ValueError("a person needs a name")
        with self.state.lock:
            self.state.data["hidden"] = [n for n in self.state.data["hidden"] if n.lower() != name.lower()]
            if name.lower() not in {p["name"].lower() for p in self.state.data["people"]}:
                self.state.data["people"].append({"name": name, "title": title, "mail": mail, "added": True})
            self.state.save()

    def remove(self, name: str) -> None:
        """Someone the user added goes; a direct report is hidden (the next Update would bring them back)."""
        with self.state.lock:
            d = self.state.data
            added = any(p["name"] == name and p.get("added") for p in d["people"])
            if added:
                d["people"] = [p for p in d["people"] if p["name"] != name]
            else:
                d["hidden"] = list(dict.fromkeys([*d["hidden"], name]))
            self.state.save()

    def note(self, name: str, text: str) -> None:
        text = " ".join(text.split())
        if not text:
            raise ValueError("the note is empty")
        fname = f"people/{export.slug(name)}.md"
        body = self.kit.memory.read(fname) or f"# {name}\n\n"
        line = f"- {date.today().isoformat()} {text} (you)"
        self.kit.memory.write(fname, with_section(body, "Notes", section(body, "Notes") + [line]), "note")
