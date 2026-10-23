"""The org chart from Microsoft Graph: the management chain above a person, and everyone below them.

What the feasibility check showed on the laptop (check.cmd -Only org): the whole chain comes in one request
(manager($levels=max)), a branch is walked level by level with $batch (20 people per request, ~7x faster than one
request per person), and the directory listed with each person's manager does not work (no manager returned), so
the tree is always walked from a chosen person. Active Directory is not reachable from the laptop.

Output under org/ in the export folder: a Markdown tree (for the wiki inbox), a CSV (one row per person, for Excel),
the data as JSON (the Org tab opens saved charts from it, no token needed) and an interactive chart as one .html file
that works offline and can be sent to anyone (the same org_chart.js as the Org tab, inlined).
"""

from __future__ import annotations

import csv
import html as htmllib
import io
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .. import export

CHART_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "org_chart.js"

FIELDS = "id,displayName,jobTitle,department,officeLocation,mail"
EVENTUAL = {"ConsistencyLevel": "eventual"}
MAX_PEOPLE = 5000


@dataclass
class Person:
    id: str
    name: str
    title: str = ""
    department: str = ""
    office: str = ""
    mail: str = ""
    manager: str = ""                   # the manager's id ("" for the root and the chain's top)
    level: int = 0                      # 0 = the chosen person, 1 = their direct reports, ...
    reports: list["Person"] = field(default_factory=list)

    @classmethod
    def of(cls, u: dict, manager: str = "", level: int = 0) -> "Person":
        return cls(u["id"], u.get("displayName") or "(no name)", u.get("jobTitle") or "", u.get("department") or "",
                   u.get("officeLocation") or "", u.get("mail") or "", manager, level)

    def view(self) -> dict:
        return {"id": self.id, "name": self.name, "title": self.title, "department": self.department}

    def card(self) -> dict:
        return {**self.view(), "office": self.office, "mail": self.mail}

    def tree(self) -> dict:
        return {**self.card(), "reports": [r.tree() for r in self.reports]}


def chain(g, uid: str = "me") -> list[Person]:
    """The person and their managers up to the top, the person first (one request)."""
    base = "/me" if uid == "me" else f"/users/{uid}"
    data = g.get(base, {"$select": FIELDS, "$expand": f"manager($levels=max;$select={FIELDS})"}, EVENTUAL)
    out, node = [], data
    while node:
        out.append(Person.of(node))
        node = node.get("manager")
    for below, above in zip(out, out[1:]):
        below.manager = above.id
    return out


def search(g, text: str, top: int = 25) -> list[Person]:
    """People whose name or e-mail address starts with the words typed (the directory's own search)."""
    text = text.replace('"', "").strip()
    if len(text) < 2:
        return []
    data = g.get("/users", {"$search": f'"displayName:{text}" OR "mail:{text}"', "$select": FIELDS, "$top": str(top),
                            "$count": "true"}, EVENTUAL)
    return [Person.of(u) for u in data.get("value", []) if u.get("id")]


def walk(g, root: Person, depth: int = 0, progress: Callable[[int, int], None] | None = None,
         limit: int = MAX_PEOPLE) -> int:
    """Fill root.reports downward, level by level, 20 people per $batch request. depth 0 = all levels.
    Returns how many people were found below root (cut at `limit`)."""
    level, todo, seen, found = 0, [root], {root.id}, 0
    while todo and (not depth or level < depth) and found < limit:
        nxt: list[Person] = []
        for i in range(0, len(todo), 20):
            group = todo[i:i + 20]
            bodies = g.batch([f"/users/{p.id}/directReports?$select={FIELDS}&$top=999" for p in group])
            for boss, body in zip(group, bodies):
                for u in sorted(body.get("value", []), key=lambda u: (u.get("displayName") or "").lower()):
                    if u.get("@odata.type", "#microsoft.graph.user") != "#microsoft.graph.user" or u.get("id") in seen:
                        continue
                    if found >= limit:
                        break
                    seen.add(u["id"]); found += 1
                    p = Person.of(u, boss.id, level + 1)
                    boss.reports.append(p); nxt.append(p)
            if progress:
                progress(found, level + 1)
        todo, level = nxt, level + 1
    return found


def everyone(root: Person) -> list[Person]:
    out, todo = [], [root]
    while todo:
        p = todo.pop(0)
        out.append(p)
        todo.extend(p.reports)
    return out


def _line(p: Person) -> str:
    extra = ", ".join(x for x in (p.title, p.department) if x)
    return f"**{p.name}**" + (f", {extra}" if extra else "") + (f" · {p.office}" if p.office else "")


def markdown(root: Person, above: list[Person], depth: int, cut: bool, when: datetime) -> str:
    people = everyone(root)
    levels = max((p.level for p in people), default=0)
    head = export.frontmatter({"source": "org chart", "title": f"Org chart: {root.name}", "date": when.strftime("%Y-%m-%d %H:%M"),
                               "root": root.name, "people_below": len(people) - 1, "levels_below": levels,
                               "depth": depth or "all", "cut_at_limit": cut})
    out = [head, f"# Org chart: {root.name}", ""]
    if above:
        out += ["## Above", ""] + [f"{'  ' * i}- {_line(p)}" for i, p in enumerate(reversed(above))] + [""]
    out += [f"## {root.name} and everyone below ({len(people) - 1} people, {levels} levels)", ""]

    def tree(p: Person, indent: int) -> None:
        n = len(everyone(p)) - 1
        out.append(f"{'  ' * indent}- {_line(p)}" + (f" ({len(p.reports)} direct, {n} in total)" if p.reports else ""))
        for r in p.reports:
            tree(r, indent + 1)
    tree(root, 0)
    if cut:
        out += ["", f"_Stopped at {MAX_PEOPLE} people._"]
    return "\n".join(out) + "\n"


def table(root: Person, above: list[Person]) -> str:
    """CSV: one row per person; the chain above the root has negative levels."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["level", "name", "job_title", "department", "office", "mail", "manager", "id", "manager_id"])
    names = {p.id: p.name for p in above + everyone(root)}
    rows = [(-i, p) for i, p in reversed(list(enumerate(above, 1)))] + [(p.level, p) for p in everyone(root)]
    for level, p in rows:
        w.writerow([level, p.name, p.title, p.department, p.office, p.mail, names.get(p.manager, ""), p.id, p.manager])
    return buf.getvalue()


def chart_data(root: Person, above: list[Person], when: datetime) -> dict:
    """What org_chart.js draws."""
    return {"root": root.tree(), "above": [p.card() for p in above], "generated": when.strftime("%Y-%m-%d %H:%M"),
            "people": len(everyone(root)) - 1}


def page(data: dict) -> str:
    """One self-contained .html: the chart script and the data inlined, no network needed."""
    title = htmllib.escape(f"Org chart: {data['root']['name']}")
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")       # never closes the <script> early
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>html,body{{margin:0;height:100%}}body{{display:flex;flex-direction:column;font:13px "Segoe UI",system-ui,sans-serif;
background:#f4f4f6}}header{{padding:10px 16px}}header b{{font-size:15px}}header span{{color:#74747e;margin-left:8px}}#chart{{flex:1;padding:0 12px 12px}}
@media (prefers-color-scheme:dark){{body{{background:#141417;color:#ececf1}}:root{{--bg:#141417;--panel:#1c1c21;--text:#ececf1;--muted:#8c8c97;--line:#2b2b33;--hover:#24242b}}}}</style>
</head><body><header><b>{title}</b><span>{data['people']} people below · {htmllib.escape(data['generated'])} · powertools</span></header>
<div id="chart"></div><script>{CHART_JS.read_text(encoding="utf-8")}</script>
<script>OrgChart(document.getElementById("chart"), {blob});</script></body></html>
"""


def save(out_dir: Path, root: Person, above: list[Person], depth: int, cut: bool, when: datetime) -> list[Path]:
    base = out_dir / "org" / f"{when.strftime('%Y-%m-%d')}_{export.slug(root.name)}"
    data = chart_data(root, above, when)
    md = export.write(base.with_suffix(".md"), markdown(root, above, depth, cut, when))
    view = export.write(base.with_suffix(".html"), page(data))
    export.write(base.with_suffix(".json"), json.dumps(data, ensure_ascii=False))
    # utf-8-sig: Excel opens the CSV with the right characters (names with accents)
    path = base.with_suffix(".csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(table(root, above), encoding="utf-8-sig", newline="")
    return [view, md, path]


SAVED = re.compile(r"^\d{4}-\d{2}-\d{2}_[a-z0-9-]+$")


def saved(out_dir: Path) -> list[dict]:
    """Charts exported earlier, newest first (their JSON), for the Org tab."""
    out = []
    for f in sorted((out_dir / "org").glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)[:50]:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            out.append({"name": f.stem, "root": d["root"]["name"], "people": d.get("people", 0), "generated": d.get("generated", "")})
        except (ValueError, KeyError):
            continue
    return out


def load(out_dir: Path, name: str) -> dict:
    if not SAVED.match(name):
        raise ValueError("unknown chart")
    f = out_dir / "org" / f"{name}.json"
    if not f.exists():
        raise ValueError("unknown chart")
    return json.loads(f.read_text(encoding="utf-8"))
