"""Drafts live in the app (memory/drafts/*.md). Nothing is ever sent: the most the app can do is put a reply into the
Outlook Drafts folder when the user clicks it, and there is no code path that sends."""

from __future__ import annotations

import hashlib
import html
import re
import secrets
import time

from ..llm import INTERACTIVE, LLMError
from . import skills
from .collect import names
from .kit import Part
from .memory import parse, render, section
from .state import cat
from .text import cut, one_line

KINDS = {"reply": "a reply to the latest message", "reminder": "a short friendly reminder that the answer or result is still awaited",
         "brief": "a message"}


FIELDS = ("title", "to", "key", "kind", "link", "created", "outlook", "outlook_text", "edited", "old")


def fingerprint(text: str) -> str:
    return hashlib.sha1(text.strip().encode()).hexdigest()[:12]


def new_id() -> str:
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"


class Drafts(Part):
    @property
    def memory(self):
        return self.kit.memory

    def style(self) -> str:
        text = self.memory.read("style.md").strip()
        return "How {{me}} writes (always follow):\n".replace("{{me}}", self.state.me) + text[:1500] if text else ""

    # ------------------------------------------------------------------ list, read, edit
    def list(self) -> list[dict]:
        out = []
        for p, text in reversed(self.memory.listed("drafts")):
            meta, body = parse(text)
            if meta.get("status", "draft") == "draft":
                d = {"id": p.stem, "body": body.strip(), **{k: str(meta.get(k) or "") for k in FIELDS}}
                out.append({**d, "same": d["outlook_text"] in ("", fingerprint(d["body"]))})   # Outlook has this very text
        return out

    def get(self, did: str) -> dict:
        if re.fullmatch(r"[0-9a-z-]+", did):
            for d in self.list():
                if d["id"] == did:
                    return d
        raise KeyError(did)

    def _write(self, d: dict, status: str = "draft") -> dict:
        meta = {"status": status, **{k: d.get(k, "") for k in FIELDS}}
        self.memory.write(f"drafts/{d['id']}.md", render(meta, "\n" + d["body"].strip() + "\n"), "draft")
        return d

    def save(self, did: str, body: str) -> dict:
        d = self.get(did)
        self._write({**d, "body": body, "edited": "yes" if body.strip() != d["body"] else d["edited"]})
        return self.get(did)

    def overtaken(self, did: str, by_me: bool) -> bool:
        """The conversation went on. The user's own answer ends the draft; a new message ends one the user did not touch
        (the Update writes a new one); one with the user's words stays and is marked. True when it stays."""
        try:
            d = self.get(did)
        except KeyError:
            return False
        if by_me or not d["edited"]:
            self._write(d, "used" if by_me else "dropped")
            return False
        self._write({**d, "old": "yes"})
        return True

    def forget(self, key: str) -> None:
        """Deletes every draft of a conversation that must not be read any more, closed ones too."""
        gone = [p for p, text in self.memory.listed("drafts") if parse(text)[0].get("key") == key]
        for p in gone:
            p.unlink(missing_ok=True)
        names_gone = {f"drafts/{p.name}" for p in gone}
        if gone:
            self.memory.purge_history(lambda e: e["file"] in names_gone)

    def close(self, did: str, status: str) -> None:
        if status not in ("used", "dropped"):
            raise ValueError("status must be used or dropped")
        d = self.get(did)
        self._write(d, status)
        i = self.state.data["items"].get(d["key"])
        if i and i.get("draft") == did:
            i.pop("draft")
            self.state.save()

    # ------------------------------------------------------------------ the model
    def create(self, key: str, kind: str = "", note: str = "", priority: int = INTERACTIVE) -> dict:
        i = self.state.item(key)
        kind = kind or ("reminder" if cat(i) == "waiting" else "reply")
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        user = self._conversation(i) + (f"\n\nWhat {self.state.me} wants to say: {note[:1500]}" if note.strip() else "")
        a = skills.DRAFT.run(self.kit.llm, user, priority=priority, me=self.state.me, kind=KINDS[kind], style=self.style(),
                             language=skills.language(i["text"]))
        d = {"id": new_id(), "title": i["title"], "to": i.get("person") or i["people"],
             "key": key, "kind": kind, "link": i["link"], "created": time.strftime("%Y-%m-%d %H:%M"), "body": str(a["body"]).strip()}
        if not d["body"]:
            raise LLMError("the model wrote an empty draft")
        self._write(d)
        if i.get("draft"):
            self.overtaken(i["draft"], by_me=True)     # this one takes its place
        with self.state.lock:
            i["draft"] = d["id"]
        self.state.save()
        return d

    def _conversation(self, i: dict) -> str:
        text = cut(i["text"], 5000)
        if i.get("matter"):
            try:
                _, body = self.memory.matter(i["matter"])
                notes = [ln for s in ("State", "Decisions", "Notes") for ln in section(body, s)]
                text += "\n\nNotes on the matter (only for background):\n" + "\n".join(notes)[:1200]
            except KeyError:
                pass
        return text + f"\n\nWhat it is about: {i.get('summary', '')}\nNext step: {i.get('action', '')}"

    def new(self, title: str, body: str) -> dict:
        """A draft made of text that is already there (a brief, an agenda)."""
        return self._write({"id": new_id(), "title": title[:120], "kind": "brief",
                            "created": time.strftime("%Y-%m-%d %H:%M"), "body": body})

    def revise(self, did: str, remark: str, body: str = "") -> dict:
        """{draft, rule}: the corrected draft and a proposed style rule ('' when there is none)."""
        d = self.get(did)
        if not remark.strip():
            raise ValueError("say what is wrong with it")
        i = self.state.data["items"].get(d["key"])
        user = f"Draft:\n{body.strip() or d['body']}\n\n{self.state.me}'s remark: {remark[:1500]}" + \
            (f"\n\nThe conversation:\n{i['text'][-3000:]}" if i else "")
        a = skills.REVISE.run(self.kit.llm, user, me=self.state.me, style=self.style())
        if str(a["body"]).strip():
            d.update(body=str(a["body"]).strip(), edited="yes", old="")
            self._write(d)
        return {"draft": d, "rule": one_line(a["rule"], 200)}

    def add_rule(self, rule: str) -> None:
        rule = one_line(rule)
        if not rule:
            raise ValueError("the rule is empty")
        text = self.memory.read("style.md") or "# How I write\n\n"
        if f"- {rule} (you)" not in text:
            self.memory.write("style.md", text.rstrip("\n") + f"\n- {rule} (you)\n", "style rule")

    # ------------------------------------------------------------------ Outlook (drafts folder only)
    def to_outlook(self, did: str) -> dict:
        d = self.get(did)
        i = self.state.data["items"].get(d["key"]) or {}
        if i.get("source") != "mail" or not i.get("msg_id"):
            raise ValueError("only an answer to a mail can go to the Outlook drafts")
        text = fingerprint(d["body"])
        if d["outlook"] and d["outlook_text"] == text:
            return d                      # this very text is there already
        comment = html.escape(d["body"]).replace("\n", "<br>")
        made = self.kit.graph.g.reply_draft(i["msg_id"], comment, everyone=i["last_from_me"] or len(names(i)) > 1)
        d.update(outlook=made.get("webLink", "") or "yes", outlook_text=text)
        return self._write(d)
