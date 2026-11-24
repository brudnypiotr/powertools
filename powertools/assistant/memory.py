"""The memory: Markdown files the user can read and edit in any editor (Documents\\powertools\\memory by default).

profile.md  style.md  matters/<slug>.md  people/<slug>.md  meetings/  days/  weeks/  drafts/

Files are read from disk every time, so an edit made outside is never lost. Lines marked "(you)" are the user's: no
automatic update removes them. Every write keeps the version before it in .history, so it can be undone.
"""

from __future__ import annotations

import itertools
import json
import logging
import re
import threading
import time
from datetime import date, datetime
from pathlib import Path

from .. import export, files

log = logging.getLogger("powertools.memory")
STATUSES = ["active", "waiting", "parked", "closed"]
YOU = "(you)"
SECTIONS = ["State", "Open", "Decisions", "Timeline", "Notes"]
HEAD = re.compile(r"\A---\n(.*?)\n---\n", re.S)
WORD = re.compile(r"\w{3,}", re.U)
KEEP_HISTORY = 400
DATED = re.compile(r"\d{4}-\d\d-\d\d · ")
_serial = itertools.count(1)


def parse(text: str) -> tuple[dict, str]:
    """(frontmatter, body); values are JSON, anything else stays text."""
    text = text.replace("\r\n", "\n")
    m = HEAD.match(text)
    meta: dict = {}
    if m:
        for line in m.group(1).splitlines():
            k, sep, v = line.partition(":")
            if sep:
                try:
                    meta[k.strip()] = json.loads(v.strip())
                except ValueError:
                    meta[k.strip()] = v.strip()
        text = text[m.end():]
    return meta, text


def render(meta: dict, body: str) -> str:
    return "---\n" + "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in meta.items()) + "\n---\n" + body


def section(body: str, name: str) -> list[str]:
    m = re.search(rf"^## {re.escape(name)}\n(.*?)(?=^## |\Z)", body, re.S | re.M)
    return [ln for ln in m.group(1).splitlines() if ln.strip()] if m else []


def with_section(body: str, name: str, lines: list[str]) -> str:
    block = f"## {name}\n\n" + "\n".join(lines) + ("\n\n" if lines else "\n")
    rx = re.compile(rf"^## {re.escape(name)}\n.*?(?=^## |\Z)", re.S | re.M)
    return rx.sub(lambda _: block, body, count=1) if rx.search(body) else body.rstrip() + "\n\n" + block


def bare(line: str) -> str:
    return line.strip().removeprefix("- ").strip()


class Memory:
    def __init__(self, root):
        self._root = root                  # a function: the folder can change in Settings
        self.lock = threading.RLock()
        self._known: dict[Path, str] = {}

    @property
    def root(self) -> Path:
        d = Path(self._root() if callable(self._root) else self._root)
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ------------------------------------------------------------------ files
    def path(self, name: str) -> Path:
        """A Markdown file inside the memory folder, nothing else."""
        p = (self.root / name).resolve()
        if p.suffix != ".md" or self.root.resolve() not in p.parents or any(x.startswith(".") for x in p.relative_to(self.root.resolve()).parts):
            raise ValueError(f"not a memory file: {name}")
        return p

    def read(self, name: str) -> str:
        """Empty when there is no such file; files.Busy when it cannot be read now (never an empty text by mistake)."""
        return files.read_text(self.path(name))

    def listed(self, folder: str) -> list[tuple[Path, str]]:
        """Every file of a folder with its text; one that is locked right now as it was read last."""
        out = []
        for p in sorted((self.root / folder).glob("*.md")):
            try:
                self._known[p] = files.read_text(p)
            except files.Busy as e:
                log.warning("%s", e)
            if p in self._known:
                out.append((p, self._known[p]))
        return out

    def write(self, name: str, text: str, why: str = "") -> dict | None:
        """Atomic write with history; None when nothing changed."""
        with self.lock:
            before = self.read(name)
            if before == text:
                return None
            entry = {"id": f"{time.time_ns()}{next(_serial):06d}", "file": Path(name).as_posix(), "before": before, "after": text, "why": why,
                     "existed": self.path(name).exists()}
            files.write_text(self.path(name), text)
            hist = self.root / ".history"
            hist.mkdir(exist_ok=True)
            (hist / f"{entry['id']}.json").write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
            for old in sorted(hist.glob("*.json"))[:-KEEP_HISTORY]:
                old.unlink(missing_ok=True)
            return entry

    def undo(self, entry_id: str) -> str:
        if not re.fullmatch(r"\d+", entry_id):
            raise KeyError(entry_id)
        with self.lock:
            f = self.root / ".history" / f"{entry_id}.json"
            if not f.exists():
                raise KeyError(entry_id)
            e = json.loads(f.read_text(encoding="utf-8"))
            if self.read(e["file"]) != e["after"]:
                raise ValueError(f"{e['file']} changed since then: undo would lose that change")
            if e["existed"]:
                self.write(e["file"], e["before"], "undo")
            else:
                self.path(e["file"]).unlink()
            return e["file"]

    def files(self) -> list[str]:
        return sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*.md")
                      if not any(x.startswith(".") for x in p.relative_to(self.root).parts))

    def edit(self, changes: list[dict], why: str) -> list[dict]:
        """Line edits the user asked for: each changed line is marked (you). Edits that do not fit are skipped with a reason."""
        done = []
        for c in changes[:10]:
            name, old, new = str(c.get("file", "")).strip(), str(c.get("old", "")).strip(), str(c.get("new", "")).strip()
            new = " ".join(new.split())
            try:
                with self.lock:
                    self.path(name)
                    text = self.read(name)
                    lines = text.splitlines()
                    if new and YOU not in new:
                        new += f" {YOU}"
                    # the timeline is history with sources: a correction of it is a new line, never a rewrite
                    at = [i for i, ln in enumerate(lines) if old and bare(ln) == bare(old) and not DATED.match(bare(ln))]
                    if old and not at and not new:
                        raise ValueError("that line is not in the file")
                    if at:
                        if len(at) > 1:
                            raise ValueError("that line is there more than once")
                        bullet = "- " if lines[at[0]].lstrip().startswith("- ") and new and not new.startswith("- ") else ""
                        lines[at[0]:at[0] + 1] = [bullet + new] if new else []
                        out = "\n".join(lines) + "\n"
                    elif new:
                        line = new if new.startswith("- ") else "- " + new
                        meta, body = parse(text)
                        body = with_section(body or "", "Notes", section(body, "Notes") + [line])
                        out = render(meta, body) if meta else body.lstrip()
                    else:
                        continue
                    entry = self.write(name, out, why)
                if entry:
                    done.append({"id": entry["id"], "file": name, "old": old, "new": new, "error": ""})
            except ValueError as e:
                done.append({"id": "", "file": name, "old": old, "new": new, "error": str(e)})
        return done

    # ------------------------------------------------------------------ matters
    def matters(self, every: bool = False) -> list[dict]:
        out = []
        for p, text in self.listed("matters"):
            meta, body = parse(text)
            st = meta.get("status") if meta.get("status") in STATUSES else "active"
            if st == "parked" and meta.get("return_on") and str(meta["return_on"]) <= date.today().isoformat():
                st = "active"   # parked until a date that has come
            if every or st in ("active", "waiting"):
                tl = section(body, "Timeline")
                out.append({"id": p.stem, "title": str(meta.get("title") or p.stem), "status": st, "updated": str(meta.get("updated", "")),
                            "return_on": str(meta.get("return_on") or ""), "reason": str(meta.get("reason") or ""),
                            "keys": [str(k) for k in meta.get("keys") or []], "entries": len(tl),
                            "state": " ".join(section(body, "State"))[:400]})
        return out

    def matter(self, mid: str) -> tuple[dict, str]:
        """(frontmatter, body). Any file name in matters/ is an id: files are made by hand and by OneDrive too."""
        if not mid or Path(mid).name != mid or "\\" in mid or not (self.root / "matters" / f"{mid}.md").is_file():
            raise KeyError(mid)
        return parse(self.read(f"matters/{mid}.md"))

    def create_matter(self, title: str, keys: list[str] = ()) -> str:
        title = " ".join(title.split())[:80]
        if not title:
            raise ValueError("a matter needs a name")
        with self.lock:
            mid = base = export.slug(title, 50)
            n = 2
            while (self.root / "matters" / f"{mid}.md").exists():
                mid, n = f"{base}-{n}", n + 1
            today = date.today().isoformat()
            body = f"# {title}\n\n" + "".join(f"## {s}\n\n" for s in SECTIONS)
            self.write(f"matters/{mid}.md", render({"title": title, "status": "active", "keys": list(dict.fromkeys(keys)),
                                                    "created": today, "updated": today}, body), "new matter")
        return mid

    def set_status(self, mid: str, status: str, reason: str = "", return_on: str = "") -> None:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        if return_on:
            datetime.strptime(return_on, "%Y-%m-%d")
        with self.lock:
            meta, body = self.matter(mid)
            meta.update(status=status, reason=reason.strip(), return_on=return_on, updated=date.today().isoformat())
            self.write(f"matters/{mid}.md", render(meta, body), f"status: {status}")

    def add_entry(self, mid: str, item: dict) -> None:
        """One line in the timeline with a link to the source; the conversation belongs to the matter from now on."""
        with self.lock:
            meta, body = self.matter(mid)
            day = export.local(export.parse_time(item["last"])).strftime("%Y-%m-%d") if item.get("last") else date.today().isoformat()
            src = {"mail": "Mail", "teams": "Teams", "jira": "Jira", "meeting": "Meeting"}.get(item["source"], item["source"])
            what = item.get("summary") or item["title"]
            link = f" ([source]({item['link']}))" if item.get("link") else f" (source: {item['title']})"
            line = f"- {day} · {src} · {what}{link}"
            tl = section(body, "Timeline")
            if line not in tl:
                body = with_section(body, "Timeline", tl + [line])
            meta["keys"] = list(dict.fromkeys([*(meta.get("keys") or []), item["key"]]))
            meta["updated"] = max(str(meta.get("updated", "")), day)
            self.write(f"matters/{mid}.md", render(meta, body), "update")

    def set_lines(self, mid: str, name: str, lines: list[str], why: str) -> None:
        """Replace a section; the user's own lines stay, on top."""
        with self.lock:
            meta, body = self.matter(mid)
            own = [ln for ln in section(body, name) if YOU in ln]
            self.write(f"matters/{mid}.md", render(meta, with_section(body, name, own + [ln for ln in lines if ln not in own])), why)

    def forget_key(self, mid: str, key: str) -> None:
        with self.lock:
            meta, body = self.matter(mid)
            meta["keys"] = [k for k in meta.get("keys") or [] if k != key]
            self.write(f"matters/{mid}.md", render(meta, body), "moved to another matter")

    # ------------------------------------------------------------------ search
    def chunks(self, only: tuple[str, ...] = ("",)) -> list[dict]:
        """Every file (whose name starts with one of `only`) in parts of about 1500 characters, cut at headings and lines."""
        out = []
        for name in (n for n in self.files() if n.startswith(only)):
            try:
                meta, body = parse(self.read(name))
            except files.Busy as e:
                log.warning("%s", e)
                continue
            title, cur = str(meta.get("title") or name), ""
            for line in body.splitlines():
                if cur and (line.startswith("## ") or len(cur) + len(line) > 1500):
                    out.append({"file": name, "title": title, "text": cur})
                    cur = ""
                cur += line + "\n"
            if cur.strip():
                out.append({"file": name, "title": title, "text": cur})
        return [c for c in out if c["text"].strip()]


def rank(query: str, chunks: list[dict], top: int = 12) -> list[dict]:
    """BM25 over the chunks, title words counted twice; nothing but the standard library."""
    import math

    words = [w.lower() for w in WORD.findall(query)]
    docs = [[w.lower() for w in WORD.findall(f"{c['title']} {c['title']} {c['text']}")] for c in chunks]
    if not words or not docs:
        return []
    avg = sum(map(len, docs)) / len(docs) or 1
    df = {w: sum(any(x.startswith(w) for x in set(d)) for d in docs) for w in set(words)}
    scored = []
    for c, d in zip(chunks, docs):
        score = 0.0
        for w in set(words):
            tf = sum(x.startswith(w) for x in d)
            if tf:
                idf = math.log(1 + (len(docs) - df[w] + 0.5) / (df[w] + 0.5))
                score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * len(d) / avg))
        if score > 0:
            scored.append((score, c))
    return [c for _, c in sorted(scored, key=lambda x: -x[0])[:top]]
