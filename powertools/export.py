"""Markdown output for the wiki inbox: Teams HTML to text, frontmatter, file names."""

from __future__ import annotations

import html
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from . import __version__


def slug(text: str, limit: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return (text[:limit].rstrip("-")) or "untitled"


def parse_time(value: str) -> datetime:
    """Graph timestamps (UTC, 'Z' or offset, up to 7 decimals) -> aware datetime."""
    value = value.replace("Z", "+00:00")
    value = re.sub(r"(\.\d{6})\d+", r"\1", value)
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def local(dt: datetime) -> datetime:
    return dt.astimezone()


def html_to_text(content: str, attachment_names: dict[str, str] | None = None) -> str:
    """Teams message HTML -> readable Markdown-ish text."""
    names = attachment_names or {}
    t = content or ""
    t = re.sub(r'<attachment id="([^"]+)"\s*>\s*</attachment>', lambda m: f"[{names.get(m.group(1), 'attachment')}]", t)
    t = re.sub(r"<at[^>]*>(.*?)</at>", r"@\1", t, flags=re.S | re.I)
    t = re.sub(r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>',
               lambda m: m.group(2) if m.group(2) == m.group(1) else f"[{m.group(2)}]({m.group(1)})", t, flags=re.S | re.I)
    t = re.sub(r"<img[^>]*>", "[image]", t, flags=re.I)
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.I)
    t = re.sub(r"<li[^>]*>", "\n- ", t, flags=re.I)
    t = re.sub(r"</(p|div|ul|ol|h\d|tr)>", "\n", t, flags=re.I)
    t = re.sub(r"<blockquote[^>]*>(.*?)</blockquote>",
               lambda m: "\n" + "\n".join("> " + ln for ln in re.sub(r"<[^>]+>", "", m.group(1)).splitlines() if ln.strip()) + "\n",
               t, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t).replace("\xa0", " ")
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def frontmatter(fields: dict) -> str:
    lines = ["---"]
    for k, v in fields.items():
        lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
    lines += [f"tool: {json.dumps('powertools ' + __version__)}", "---", ""]
    return "\n".join(lines)


def range_label(since: datetime | None, until: datetime) -> str:
    start = local(since).strftime("%Y-%m-%d") if since else "start"
    return f"{start}..{local(until).strftime('%Y-%m-%d')}"


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return path


def safe_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")
    return name[:150] or "file"


def unique(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 1000):
        p = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not p.exists():
            return p
    return path
