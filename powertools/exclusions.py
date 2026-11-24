"""What the app must never read (Settings > Never read): checked before anything is kept, shown or given to a model."""

from __future__ import annotations

import re


class Exclusions:
    """Words from Settings ("folder: X" for a mail folder): a match in title, people or address keeps it out."""

    def __init__(self, entries: list[str]):
        rules = [e.strip().lower() for e in entries if e.strip()]
        self.folders = [e.split(":", 1)[1].strip() for e in rules if e.startswith("folder:")]
        self.words = [e for e in rules if not e.startswith("folder:")]

    def hit(self, *texts: str) -> bool:
        hay = " \n ".join(t for t in texts if t).lower()
        return any(re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", hay) for w in self.words)

    def folder(self, name: str) -> bool:
        return name.lower() in self.folders
