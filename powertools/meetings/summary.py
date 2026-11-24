"""A summary and the key points of a meeting, written by the chosen model (local or Copilot) after the final transcript.

Long meetings do not fit the model's context: the transcript is cut into parts of about CHUNK_CHARS, each part gets
its own notes, and one more call joins them. The result goes into the Markdown file above "## Transcript".
"""

from __future__ import annotations

import re
import time

from ..llm import BACKGROUND, LLMError, parse_json

CHUNK_CHARS = 12000
NUM_CTX = 8192
SPEAKERS = {"me": "Me", "others": "Others"}
LINE = re.compile(r"^- \*\*\[(\d\d):(\d\d):(\d\d)\] ([^*:]+):\*\* ?(.*)$")
SECTION = re.compile(r"## Summary\n.*?(?=## Transcript)", re.S)

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "key_points": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "key_points"],
}

RULES = """The transcript comes from speech recognition: expect misheard words and names. "Me" is the person who
recorded the meeting; "Others" is everyone else on one track (not split by person). Use only what the transcript
says, never invent names, numbers or dates. Write in the language of the transcript. Answer with JSON:
"summary": what the meeting was about and how it went, 3 to 6 sentences;
"key_points": the most important points: decisions, agreed next steps (who does what, when), open questions,
problems and important facts; one short sentence each, most important first, at most 10."""


def system_prompt(meta: dict, part: str = "") -> str:
    head = f'You take notes of a work meeting: "{meta.get("title") or "meeting"}".'
    if meta.get("attendees"):
        head += " Invited: " + ", ".join(meta["attendees"][:20]) + "."
    if part:
        head += f" This is {part} of the transcript; another step joins the notes of all parts."
    return head + "\n" + RULES


def transcript_text(segments: list[dict]) -> str:
    out = []
    for s in segments:
        t = int(s["start"])
        out.append(f"[{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}] "
                   f"{SPEAKERS.get(s['speaker'], s['speaker'])}: {s['text']}")
    return "\n".join(out)


def split(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """Parts of at most `size` characters, cut between lines."""
    parts, cur = [], ""
    for line in text.splitlines():
        if cur and len(cur) + len(line) + 1 > size:
            parts.append(cur)
            cur = ""
        cur = (cur + "\n" + line) if cur else line[:size]
    return parts + [cur] if cur else parts


def segments_from_md(md: str) -> list[dict]:
    """The turns of an older transcript file (no transcript.json next to its audio)."""
    names = {v: k for k, v in SPEAKERS.items()}
    out = []
    for line in md.partition("## Transcript")[2].splitlines():
        m = LINE.match(line)
        if m:
            h, mi, s, who, text = m.groups()
            out.append({"speaker": names.get(who.strip(), who.strip()), "start": int(h) * 3600 + int(mi) * 60 + int(s),
                        "text": text})
    return out


def _ask(llm, system: str, user: str) -> dict:
    content, _ = llm.chat(system, user, SCHEMA, priority=BACKGROUND, max_tokens=1200, num_ctx=NUM_CTX, timeout=900,
                          task="meeting summary")
    data = parse_json(content)
    points = [str(p).strip() for p in data.get("key_points") or [] if str(p).strip()]
    return {"summary": str(data.get("summary") or "").strip(), "key_points": points}


def summarize(llm, meta: dict, segments: list[dict], step=lambda text: None) -> dict:
    """{"summary", "key_points", "model", "seconds"}. Raises LLMError."""
    text = transcript_text(segments)
    if not text.strip():
        raise LLMError("the transcript is empty")
    t0 = time.time()
    parts = split(text)
    if len(parts) == 1:
        step("summarizing")
        result = _ask(llm, system_prompt(meta), text)
    else:
        notes = []
        for i, p in enumerate(parts, 1):
            step(f"summarizing part {i}/{len(parts)}")
            n = _ask(llm, system_prompt(meta, f"part {i} of {len(parts)}"), p)
            notes.append(f"Part {i}:\nSummary: {n['summary']}\nPoints:\n" + "\n".join(f"- {x}" for x in n["key_points"]))
        step("joining the parts")
        result = _ask(llm, system_prompt(meta) + "\nYou get the notes of consecutive parts of the meeting, not the "
                      "transcript: join them into one summary and one list of key points for the whole meeting.",
                      "\n\n".join(notes))
    return {**result, "model": getattr(llm, "model", ""),
            "seconds": round(time.time() - t0, 1)}


def section(result: dict) -> str:
    out = ["## Summary", "", result["summary"], "", "## Key points", ""]
    out += [f"- {p}" for p in result["key_points"]] or ["_(none)_"]
    note = f"_Written by the model{' ' + result['model'] if result.get('model') else ''} from the transcript "
    return "\n".join(out + ["", note + "below; check it against the transcript._", "", ""])


def insert(md: str, result: dict) -> str:
    """The file with the summary above "## Transcript" (an earlier summary is replaced)."""
    block = section(result)
    if SECTION.search(md):
        return SECTION.sub(lambda _: block, md, count=1)
    head, sep, body = md.partition("## Transcript")
    return head + block + sep + body if sep else md.rstrip() + "\n\n" + block
