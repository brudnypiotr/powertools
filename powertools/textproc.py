"""Text after Whisper: the user's replacement rules and the vocabulary prompt."""

from __future__ import annotations

import re


def apply_replacements(text: str, rules: list[dict]) -> str:
    """Whole-word, case-insensitive replacements, applied in order. Phrases (with spaces) work too."""
    for r in rules:
        src, dst = r.get("from", ""), r.get("to", "")
        if not src:
            continue
        pattern = r"(?<!\w)" + re.escape(src) + r"(?!\w)"
        text = re.sub(pattern, lambda _m: dst, text, flags=re.IGNORECASE)
    return text


def vocabulary_prompt(words: list[str], limit: int = 800) -> str | None:
    """Whisper's initial_prompt biases spelling towards these words. Kept short: long prompts hurt accuracy."""
    words = [w for w in words if w]
    if not words:
        return None
    prompt = "Glossary: " + ", ".join(words) + "."
    return prompt[:limit]


def tidy(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
