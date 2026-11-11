"""Dictation clean-up by the local model: fillers, repetitions, punctuation. The words and the meaning stay.

Any failure, a timeout or an answer that is much shorter or longer than the transcript keeps the transcript as it
was: the model must never eat a sentence or add one.
"""

from __future__ import annotations

import logging

from .llm import LLMError

log = logging.getLogger("powertools.cleanup")
MIN_WORDS = 4
RATIO = (0.6, 1.15)   # words in the answer / words in the transcript

SYSTEM = """You clean up text from speech recognition (dictation).
Remove filler words and sounds (um, uh, er, yyy, eee, hmm, and words used only as fillers), stutters and accidental
repetitions. Fix punctuation, capitalization and sentence breaks. Fix a misrecognized word only when you are certain.
Keep the language as it is: Polish stays Polish, English stays English, a mix stays a mix. Keep the wording, the word
order and the meaning. Do not translate, summarize, shorten, answer or add anything.
Return only the cleaned text, without quotes or comments."""


def clean(llm, text: str, vocabulary: list[str] | None = None, timeout: float = 25) -> tuple[str, str]:
    """(text to use, note). The note says why the transcript was kept, or is ''."""
    words = len(text.split())
    if words < MIN_WORDS:
        return text, ""
    system = SYSTEM + (f"\nSpell these words exactly like this: {', '.join(vocabulary)}." if vocabulary else "")
    try:
        out, _stats = llm.chat(system, text, max_tokens=int(words * 2.5) + 40, timeout=timeout)
    except LLMError as e:
        log.warning("clean-up skipped: %s", e)
        return text, f"clean-up skipped: {e}"
    out = out.strip().strip('"').strip()
    n = len(out.split())
    if not out or not RATIO[0] <= n / words <= RATIO[1]:
        log.info("clean-up rejected (%d -> %d words)", words, n)
        return text, f"clean-up rejected ({words} -> {n} words)"
    return out, ""
