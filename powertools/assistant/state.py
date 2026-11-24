"""What the assistant knows between updates: items with their text, promises, calendar, people, the cursor (assistant.json)."""

from __future__ import annotations

import threading
from datetime import date, datetime, timezone
from pathlib import Path

from .. import export, files
from .text import alike, promise_id

OPEN = ("todo", "reply", "waiting")
EMPTY = {"items": {}, "promises": {}, "events": [], "preps": {}, "people": [], "cursor": "", "updated": 0, "checked": 0, "last": {}, "me": {},
         "corrections": [], "muted": [], "missed": []}


def cat(i: dict) -> str:
    """The category that counts: the user's word before the model's."""
    return i.get("user_category") or i.get("category") or "fyi"


def age_days(i: dict) -> int:
    return max(0, (datetime.now(timezone.utc) - export.parse_time(i["last"])).days) if i.get("last") else 0


class State:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.data: dict = {**{k: type(v)(v) if isinstance(v, (dict, list)) else v for k, v in EMPTY.items()}, **files.load_json(path)}

    def save(self) -> None:
        with self.lock:
            files.save_json(self.path, self.data)

    @property
    def me(self) -> str:
        return self.data["me"].get("displayName") or "the user"

    def is_me(self, who: str) -> bool:
        who, me = who.strip().lower(), self.me.lower()
        return bool(who) and (who in ("me", "i") or who == me or who == me.split()[0])

    # ------------------------------------------------------------------ items
    def item(self, key: str) -> dict:
        return self.data["items"][key]

    def all(self) -> list[dict]:
        """Every item, safe to go through while an Update adds some."""
        with self.lock:
            return list(self.data["items"].values())

    def open(self) -> list[dict]:
        """Copies of what is open now, the newest first, with the category that counts."""
        today = date.today().isoformat()
        out = []
        for i in self.all():
            snoozed = i["status"] == "snoozed" and str(i.get("until", "")) > today
            if i["status"] in ("open", "snoozed") and not snoozed and cat(i) in OPEN:
                out.append({**i, "category": cat(i)})
        return sorted(out, key=lambda i: i["last"], reverse=True)

    # ------------------------------------------------------------------ promises
    def promise_state(self, key: str, c: dict) -> str:
        """Closed stays closed when the model words the same promise differently after the next message."""
        kept = self.data["promises"]
        direct = kept.get(promise_id(key, c))
        if direct:
            return direct if isinstance(direct, str) else direct["state"]
        for p in kept.values():
            if isinstance(p, dict) and p["key"] == key and p["who"].lower() == c["who"].lower() and (
                    alike(p["what"], c["what"]) or alike(p["quote"], c["quote"])):
                return p["state"]
        return "open"

    def promises(self, every: bool = False) -> list[dict]:
        """Commitments with a proving quote: mine and other people's."""
        out = []
        for i in self.all():
            if i["status"] == "dropped":
                continue
            for c in i.get("commitments") or []:
                pid, state = promise_id(i["key"], c), self.promise_state(i["key"], c)
                if every or state == "open":
                    out.append({**c, "id": pid, "state": state, "mine": self.is_me(c["who"]), "key": i["key"], "title": i["title"],
                                "source": i["source"], "link": i["link"], "last": i["last"], "days": age_days(i),
                                "matter": i.get("matter", "")})
        return sorted(out, key=lambda p: p["last"], reverse=True)

    def open_promises(self, i: dict) -> bool:
        return i["status"] != "dropped" and any(self.promise_state(i["key"], c) == "open" for c in i.get("commitments") or [])

    def close_promise(self, pid: str, state: str) -> None:
        if state not in ("open", "done", "dropped"):
            raise ValueError("state must be open, done or dropped")
        p = next((p for p in self.promises(every=True) if p["id"] == pid), None)
        if p is None:
            raise KeyError(pid)
        with self.lock:
            # every earlier wording of it too, else an old "done" would close it again
            for old in self.data["promises"].values():
                if isinstance(old, dict) and old["key"] == p["key"] and (alike(old["what"], p["what"]) or alike(old["quote"], p["quote"])):
                    old["state"] = state
            self.data["promises"][pid] = {"state": state, **{k: p[k] for k in ("key", "who", "what", "quote")}}
            self.save()
