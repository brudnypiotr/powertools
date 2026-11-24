"""Small questions about texts, answered by code."""

from __future__ import annotations

import hashlib
import re

MAX_CHARS = 6000             # text per conversation sent to the model (the newest part)
LABELS = {"mail": "Mail", "teams": "Teams", "jira": "Jira", "meeting": "Meeting"}


def cut(text: str, n: int = MAX_CHARS) -> str:
    return text if len(text) <= n else text[:600] + "\n[... older part cut ...]\n" + text[-(n - 600):]


def shares_word(title: str, text: str, people: str) -> bool:
    """A significant word of the matter's name (not a person's name) is in the text."""
    names = {w.lower() for w in re.findall(r"\w+", people)}
    words = {w.lower() for w in re.findall(r"\w{4,}", title)} - names - {"with", "from", "project", "matter"}
    hay = {w.lower() for w in re.findall(r"\w{4,}", text)}
    return any(h.startswith(w[:max(4, len(w) - 2)]) for w in words for h in hay)


def tells(text: str) -> bool:
    """Is it (also) a statement to remember, not only a question?"""
    return "?" not in text or bool(re.search(r"\b(remember|note that|actually|is wrong|not true|has changed|correct that)\b", text, re.I))


def said(new: str, old: str, question: str) -> bool:
    """Do at least half of the words an edit adds come from what the user wrote?"""
    def words(t: str) -> set[str]:
        return {w.lower() for w in re.findall(r"\w{3,}", t)}
    added, mine = words(new) - words(old), words(question)
    return sum(w in mine for w in added) * 2 >= len(added) if added else bool(words(old) & mine)


def promise_id(key: str, c: dict) -> str:
    return hashlib.sha1(f"{key}|{c['what'].lower()}".encode()).hexdigest()[:12]


def alike(a: str, b: str) -> bool:
    """Do two short texts say the same (two thirds of the words of the shorter one)?"""
    x, y = ({w.lower() for w in re.findall(r"\w{3,}", t)} for t in (a, b))
    return bool(x and y) and len(x & y) * 3 >= min(len(x), len(y)) * 2


def one_line(value, limit: int = 0) -> str:
    """The model's text as one line."""
    line = " ".join(str(value).split())
    return line[:limit] if limit else line


def lines(values, limit: int) -> list[str]:
    """Non-empty one-line texts out of a list the model gave."""
    return [x for x in (one_line(v) for v in values[:limit]) if x] if isinstance(values, list) else []
