"""Drafts live in the app (memory/drafts/*.md). Nothing is ever sent: the most the app can do is put a reply into the
Outlook Drafts folder when the user clicks it, and there is no code path that sends."""

from __future__ import annotations

import html
import re
import secrets
import time


from ..llm import INTERACTIVE, LLMError
from . import skills
from .memory import parse, render, section

KINDS = {"reply": "a reply to the latest message", "reminder": "a short friendly reminder that the answer or result is still awaited",
         "brief": "a message"}


def new_id() -> str:
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"


class Drafts:
    def __init__(self, assistant):
        self.a = assistant

    @property
    def memory(self):
        return self.a.memory

    def style(self) -> str:
        text = self.memory.read("style.md").strip()
        return "How {{me}} writes (always follow):\n".replace("{{me}}", self.a.me) + text[:1500] if text else ""

    # ------------------------------------------------------------------ list, read, edit
    def list(self) -> list[dict]:
        out = []
        for p in sorted((self.memory.root / "drafts").glob("*.md"), reverse=True):
            meta, body = parse(p.read_text(encoding="utf-8"))
            if meta.get("status", "draft") == "draft":
                out.append({"id": p.stem, "body": body.strip(), **{k: str(meta.get(k) or "") for k in ("title", "to", "key", "kind", "link", "created", "outlook")}})
        return out

    def get(self, did: str) -> dict:
        if re.fullmatch(r"[0-9a-z-]+", did):
            for d in self.list():
                if d["id"] == did:
                    return d
        raise KeyError(did)

    def _write(self, d: dict, status: str = "draft") -> dict:
        meta = {"status": status, **{k: d.get(k, "") for k in ("title", "to", "key", "kind", "link", "created", "outlook")}}
        self.memory.write(f"drafts/{d['id']}.md", render(meta, "\n" + d["body"].strip() + "\n"), "draft")
        return d

    def save(self, did: str, body: str) -> dict:
        return self._write({**self.get(did), "body": body})

    def close(self, did: str, status: str) -> None:
        if status not in ("used", "dropped"):
            raise ValueError("status must be used or dropped")
        d = self.get(did)
        self._write(d, status)
        i = self.a.data["items"].get(d["key"])
        if i and i.get("draft") == did:
            i.pop("draft")
            self.a._save()

    # ------------------------------------------------------------------ the model
    def create(self, key: str, kind: str = "", note: str = "", priority: int = INTERACTIVE) -> dict:
        i = self.a.item(key)
        kind = kind or ("reminder" if self.a._cat(i) == "waiting" else "reply")
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        user = skills_text(i, self.a) + (f"\n\nWhat {self.a.me} wants to say: {note[:1500]}" if note.strip() else "")
        a = skills.DRAFT.run(self.a.llm, user, priority=priority, me=self.a.me, kind=KINDS[kind], style=self.style(),
                             language=skills.language(i["text"]))
        d = {"id": new_id(), "title": i["title"], "to": i.get("person") or i["people"],
             "key": key, "kind": kind, "link": i["link"], "created": time.strftime("%Y-%m-%d %H:%M"), "body": str(a["body"]).strip()}
        if not d["body"]:
            raise LLMError("the model wrote an empty draft")
        self._write(d)
        with self.a._lock:
            i["draft"] = d["id"]
        self.a._save()
        return d

    def new(self, title: str, body: str) -> dict:
        """A draft made of text that is already there (a brief, an agenda)."""
        return self._write({"id": new_id(), "title": title[:120], "kind": "brief",
                            "created": time.strftime("%Y-%m-%d %H:%M"), "body": body})

    def revise(self, did: str, remark: str, body: str = "") -> dict:
        """{draft, rule}: the corrected draft and a proposed style rule ('' when there is none)."""
        d = self.get(did)
        if not remark.strip():
            raise ValueError("say what is wrong with it")
        i = self.a.data["items"].get(d["key"])
        user = f"Draft:\n{body.strip() or d['body']}\n\n{self.a.me}'s remark: {remark[:1500]}" + \
            (f"\n\nThe conversation:\n{i['text'][-3000:]}" if i else "")
        a = skills.REVISE.run(self.a.llm, user, me=self.a.me, style=self.style())
        if str(a["body"]).strip():
            d["body"] = str(a["body"]).strip()
            self._write(d)
        return {"draft": d, "rule": " ".join(str(a["rule"]).split())[:200]}

    def add_rule(self, rule: str) -> None:
        rule = " ".join(rule.split())
        if not rule:
            raise ValueError("the rule is empty")
        text = self.memory.read("style.md") or "# How I write\n\n"
        if f"- {rule} (you)" not in text:
            self.memory.write("style.md", text.rstrip("\n") + f"\n- {rule} (you)\n", "style rule")

    # ------------------------------------------------------------------ Outlook (drafts folder only)
    def to_outlook(self, did: str) -> dict:
        d = self.get(did)
        i = self.a.data["items"].get(d["key"]) or {}
        if i.get("source") != "mail" or not i.get("msg_id"):
            raise ValueError("only an answer to a mail can go to the Outlook drafts")
        comment = html.escape(d["body"]).replace("\n", "<br>")
        made = self.a.graph.g.reply_draft(i["msg_id"], comment, everyone=i["last_from_me"] or len(i["people"].split(",")) > 1)
        d["outlook"] = made.get("webLink", "") or "yes"
        return self._write(d)


def skills_text(i: dict, a) -> str:
    from .service import cut

    text = cut(i["text"], 5000)
    if i.get("matter"):
        try:
            _, body = a.memory.matter(i["matter"])
            notes = [ln for s in ("State", "Decisions", "Notes") for ln in section(body, s)]
            text += "\n\nNotes on the matter (only for background):\n" + "\n".join(notes)[:1200]
        except KeyError:
            pass
    return text + f"\n\nWhat it is about: {i.get('summary', '')}\nNext step: {i.get('action', '')}"


__all__ = ["Drafts", "KINDS"]
