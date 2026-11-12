"""Wording: English corrections for a selected text (hotkey) or a pasted one (Writing tab), from the local model.

Hotkey: the selection is copied with Ctrl+C (the previous clipboard comes back afterwards), then either
- "review": the window shows the mistakes and a better version (copy it, or edit and copy), or
- "replace": the better version is pasted over the selection right away (the selection stays active meanwhile).
"""

from __future__ import annotations

import itertools
import logging
import threading
import time
from typing import Callable

from .llm import LLMError, parse_json

log = logging.getLogger("powertools.wording")
MAX_CHARS = 6000
_ids = itertools.count(1)

SYSTEM = """You proofread English written by a non-native speaker (Polish): work e-mails and chat messages.
Fix only real mistakes: grammar, spelling, articles, prepositions, tenses, word order, and words or phrases that a
native speaker would find wrong or confusing (for example calques from Polish).
Do NOT rewrite for style. Keep the author's words, tone, register, sentence structure and length wherever they are
correct, even if you would phrase them differently. Do not make the text more formal, more polite or more fluent, do
not change what happened or when, and do not drop or add content.
For each mistake give the fragment exactly as written ("wrong"), the corrected fragment ("better") and a short reason,
at most 12 words ("why").
"improved": the original text with only those fixes applied; everything else stays exactly as written, including
names, codes, numbers, links, greetings, formatting and line breaks.
If there are no mistakes, return no issues and the text unchanged.
If the text is not in English, set "language" to its language, return no issues and the text unchanged."""

# Rewrites on request (Writing tab buttons): here changing the style is the point.
STYLES = {
    "professional": "more professional: clear, polite and confident, as in a work e-mail to a colleague in another team",
    "casual": "more casual: friendly and relaxed, as in a Teams chat with a colleague you know well",
    "shorter": "shorter: keep every fact, request and deadline, drop the rest",
}
STYLE_SYSTEM = """You rewrite an English work message by a non-native speaker (Polish) so that it is {goal}.
Keep the meaning, the facts, names, codes, numbers, links and the language (English). Fix any mistakes on the way.
Do not invent content. Keep the greeting and the sign-off if there are any.
Return the rewritten text as "improved", "issues" empty, "language" "English"."""

SCHEMA = {
    "type": "object",
    "properties": {
        "language": {"type": "string"},
        "issues": {"type": "array", "items": {"type": "object", "properties": {
            "wrong": {"type": "string"}, "better": {"type": "string"}, "why": {"type": "string"}},
            "required": ["wrong", "better", "why"]}},
        "improved": {"type": "string"},
    },
    "required": ["language", "issues", "improved"],
}


def clean_answer(text: str, answer: dict) -> dict:
    """Drops non-changes and keeps the original when the model returned nothing usable."""
    issues = [{"wrong": str(i.get("wrong", "")).strip(), "better": str(i.get("better", "")).strip(),
               "why": str(i.get("why", "")).strip()} for i in answer.get("issues") or [] if isinstance(i, dict)]
    issues = [i for i in issues if i["wrong"] and i["wrong"] != i["better"]]
    improved = str(answer.get("improved") or "").strip() or text
    return {"language": str(answer.get("language", "")), "issues": issues, "improved": improved,
            "changed": improved.strip() != text.strip()}


def copy_selection(timeout: float = 0.8) -> str:
    """The selected text of the focused window (Ctrl+C), then the previous clipboard back."""
    import keyboard
    import pyperclip

    t0 = time.time()  # with Alt still held, Ctrl+C would arrive as Ctrl+Alt+C
    while any(keyboard.is_pressed(k) for k in ("alt", "ctrl", "shift", "windows")) and time.time() - t0 < 1.5:
        time.sleep(0.02)
    try:
        previous = pyperclip.paste()
    except Exception:
        previous = None
    pyperclip.copy("")
    keyboard.send("ctrl+c")
    text, t0 = "", time.time()
    while time.time() - t0 < timeout:
        time.sleep(0.05)
        text = pyperclip.paste()
        if text:
            break
    if previous is not None:
        pyperclip.copy(previous)
    return text


class Wording:
    def __init__(self, settings, llm, deliver: Callable, on_state: Callable[[str], None], show: Callable[[], None],
                 copy: Callable[[], str] = copy_selection):
        self.settings, self.llm = settings, llm
        self.deliver, self.on_state, self.show, self.copy = deliver, on_state, show, copy
        self.last: dict = {}
        self._lock = threading.Lock()

    def hotkey(self) -> None:
        if not self._lock.acquire(blocking=False):
            return  # one check at a time
        try:
            a = self.llm.available()
            if not (a["installed"] or a["running"]):
                # no Ollama on this laptop: only the red pill, no window and no Ctrl+C into the focused app
                self.last = {"id": next(_ids), "state": "error", "original": "",
                             "error": "Wording needs Ollama (Local AI tab)."}
                self.on_state("error")
                return
            text = self.copy().strip()
            mode = self.settings["wording_mode"]
            if not text:
                self.last = {"id": next(_ids), "state": "error", "error": "Nothing selected: select text, then press "
                             f"{self.settings['wording_hotkey']}. Or paste it here.", "original": ""}
                self.on_state("error")
                if mode == "review":
                    self.show()
                return
            if mode == "review":
                self.last = {"id": next(_ids), "state": "working", "original": text[:MAX_CHARS]}
                self.show()
            self.on_state("thinking")
            self._run(text, mode)
            self.on_state("error" if self.last.get("state") == "error" else "idle")
        finally:
            self._lock.release()

    def check(self, text: str, style: str = "") -> dict:
        """From the Writing tab: runs here and returns the result. `style` asks for a rewrite (STYLES) instead."""
        text = (text or "").strip()
        if not text:
            raise ValueError("paste a text first")
        if style and style not in STYLES:
            raise ValueError(f"style must be one of {', '.join(STYLES)}")
        with self._lock:
            self._run(text, "window", style)
        return self.last

    def _run(self, text: str, mode: str, style: str = "") -> None:
        item = {"id": self.last.get("id") if self.last.get("state") == "working" else next(_ids),
                "original": text[:MAX_CHARS], "mode": mode, "style": style}
        system = STYLE_SYSTEM.format(goal=STYLES[style]) if style else SYSTEM
        try:
            content, stats = self.llm.chat(system, text[:MAX_CHARS], SCHEMA, max_tokens=max(400, len(text) // 2))
            item.update(clean_answer(text, parse_json(content)), state="done", stats=stats)
        except LLMError as e:
            log.warning("wording failed: %s", e)
            self.last = {**item, "state": "error", "error": str(e)}
            return
        if mode == "replace" and item["changed"]:
            try:
                self.deliver(item["improved"], paste=True, restore=True)
                item["replaced"] = True
            except Exception as e:
                log.exception("paste failed")
                item["error"] = f"paste: {e}"
        self.last = item
