"""Teams chats (1:1, group, meeting) -> Markdown. Channels need admin consent and are out of scope."""

from __future__ import annotations

import base64
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from .. import export
from .client import Graph, GraphError

log = logging.getLogger("powertools.teams")
MAX_FILE_BYTES = 50 * 1024 * 1024


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def chat_title(chat: dict, me_id: str) -> str:
    if chat.get("topic"):
        return chat["topic"]
    others = [m.get("displayName") for m in chat.get("members", []) if m.get("userId") != me_id and m.get("displayName")]
    return ", ".join(others) or "(chat)"


def list_chats(g: Graph, me_id: str, limit: int = 400) -> list[dict]:
    params = {"$top": "50", "$expand": "members,lastMessagePreview",
              "$orderby": "lastMessagePreview/createdDateTime desc"}
    try:
        items = list(g.pages("/me/chats", params, max_items=limit))
    except GraphError as e:
        if e.status != 400:
            raise
        log.info("server-side sort refused (%s); sorting locally", e.code)
        items = list(g.pages("/me/chats", {"$top": "50", "$expand": "members"}, max_items=limit))
    chats = []
    for c in items:
        last = (c.get("lastMessagePreview") or {}).get("createdDateTime") or c.get("lastUpdatedDateTime") or ""
        chats.append({
            "id": c["id"],
            "type": c.get("chatType", ""),
            "title": chat_title(c, me_id),
            "members": [m.get("displayName") for m in c.get("members", []) if m.get("displayName")],
            "last": last,
        })
    chats.sort(key=lambda c: c["last"], reverse=True)
    return chats


def fetch_messages(g: Graph, chat_id: str, since: datetime | None, until: datetime) -> list[dict]:
    if since:
        # server-side filter on the modification time (checked on the laptop); creation time is checked below
        params = {"$top": "50", "$orderby": "lastModifiedDateTime desc",
                  "$filter": f"lastModifiedDateTime gt {iso(since)} and lastModifiedDateTime lt {iso(until)}"}
    else:
        params = {"$top": "50"}
    out = []
    for m in g.pages(f"/chats/{chat_id}/messages", params):
        if m.get("messageType") != "message" or m.get("deletedDateTime"):
            continue
        created = export.parse_time(m["createdDateTime"])
        if (since and created < since) or created > until:
            continue
        out.append(m)
    out.sort(key=lambda m: m["createdDateTime"])
    return out


def sender(m: dict) -> str:
    f = m.get("from") or {}
    for k in ("user", "application", "device"):
        if f.get(k) and f[k].get("displayName"):
            return f[k]["displayName"]
    return "(unknown)"


def share_id(url: str) -> str:
    return "u!" + base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")


def download_files(g: Graph, messages: list[dict], folder: Path, note) -> dict[str, str]:
    """Files shared in the chat (SharePoint/OneDrive links) -> folder/files/. Returns attachment id -> relative path."""
    saved: dict[str, str] = {}
    for m in messages:
        for a in m.get("attachments", []):
            url = a.get("contentUrl") or ""
            if a.get("contentType") != "reference" or not url.startswith("http"):
                continue
            name = export.safe_filename(a.get("name") or url.rsplit("/", 1)[-1])
            try:
                item = g.get(f"/shares/{share_id(url)}/driveItem", {"$select": "size,file,folder"})
                if item.get("folder"):
                    continue
                if item.get("size", 0) > MAX_FILE_BYTES:
                    note(f"skipped {name}: larger than 50 MB")
                    continue
                data = g.download(f"/shares/{share_id(url)}/driveItem/content")
            except GraphError as e:
                note(f"could not download {name}: HTTP {e.status}")
                continue
            path = export.unique(folder / "files" / name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            saved[a["id"]] = f"files/{path.name}"
    return saved


def render_message(m: dict, files: dict[str, str]) -> str:
    atts = {a["id"]: a for a in m.get("attachments", []) if a.get("id")}

    def link(aid: str) -> str:
        a = atts.get(aid)
        if not a or a.get("contentType") != "reference":
            return ""
        target = files.get(aid) or a.get("contentUrl", "")
        return f"[{a.get('name') or 'file'}]({target.replace(' ', '%20')})"

    body = m.get("body") or {}
    content = body.get("content") or ""
    # mark attachment placeholders before the HTML is flattened, then turn them into links
    content = re.sub(r'<attachment id="([^"]+)"\s*>\s*</attachment>', lambda x: f"\x00{x.group(1)}\x00", content)
    text = export.html_to_text(content) if body.get("contentType") == "html" else content.strip()
    used: set[str] = set()

    def place(x) -> str:
        used.add(x.group(1))
        return link(x.group(1))

    text = re.sub("\x00([^\x00]+)\x00", place, text).strip()
    extra = [f"File: {link(aid)}" for aid, a in atts.items() if a.get("contentType") == "reference" and aid not in used]
    quotes = []
    for a in atts.values():
        if a.get("contentType") == "messageReference":
            try:
                ref = json.loads(a.get("content") or "{}")
                who = ((ref.get("messageSender") or {}).get("user") or {}).get("displayName", "")
                quotes.append(f"> Re {who}: {ref.get('messagePreview', '').strip()}")
            except ValueError:
                pass
    return "\n".join(quotes + ([text] if text else []) + extra)


def chat_markdown(chat: dict, messages: list[dict], files: dict[str, str], since: datetime | None, until: datetime) -> str:
    participants = sorted({sender(m) for m in messages} | set(chat.get("members", [])))
    head = export.frontmatter({
        "source": "teams-chat",
        "title": chat["title"],
        "chat_type": chat.get("type", ""),
        "participants": participants,
        "range": export.range_label(since, until),
        "messages": len(messages),
        "exported": datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M"),
    })
    out, day = [head, f"# {chat['title']}", ""], ""
    for m in messages:
        t = export.local(export.parse_time(m["createdDateTime"]))
        if t.strftime("%Y-%m-%d") != day:
            day = t.strftime("%Y-%m-%d")
            out += [f"## {day}", ""]
        edited = " (edited)" if m.get("lastEditedDateTime") else ""
        out += [f"**{sender(m)}** {t.strftime('%H:%M')}{edited}", render_message(m, files) or "_(no text)_", ""]
    return "\n".join(out)


def export_chat(g: Graph, chat: dict, since: datetime | None, until: datetime, out_dir: Path, note) -> tuple[Path | None, str]:
    """Writes one Markdown file for the range. Returns (path or None when empty, newest createdDateTime)."""
    messages = fetch_messages(g, chat["id"], since, until)
    if not messages:
        return None, ""
    folder = out_dir / "teams" / export.slug(chat["title"])
    files = download_files(g, messages, folder, note)
    name = export.range_label(since, until).replace("..", "_") + ".md"
    path = export.write(export.unique(folder / name), chat_markdown(chat, messages, files, since, until))
    return path, messages[-1]["createdDateTime"]
