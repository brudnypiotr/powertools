"""Outlook mail: folders, thread search, thread export (received + sent, new text only) with attachments."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from .. import export
from .client import Graph, GraphError

log = logging.getLogger("powertools.mail")
TEXT = {"Prefer": 'outlook.body-content-type="text"'}
LIST_FIELDS = "id,subject,conversationId,receivedDateTime,from,toRecipients,hasAttachments"
FULL_FIELDS = LIST_FIELDS + ",ccRecipients,sentDateTime,uniqueBody,body,isDraft"
MAX_FILE_BYTES = 50 * 1024 * 1024


def folders(g: Graph, depth: int = 3) -> list[dict]:
    out: list[dict] = []

    def walk(path: str, prefix: str, level: int) -> None:
        for f in g.pages(path, {"$top": "100", "$select": "id,displayName,totalItemCount,childFolderCount"}):
            name = f"{prefix}{f['displayName']}"
            out.append({"id": f["id"], "name": name, "total": f.get("totalItemCount", 0)})
            if f.get("childFolderCount") and level < depth:
                walk(f"/me/mailFolders/{f['id']}/childFolders", name + " / ", level + 1)

    walk("/me/mailFolders", "", 1)
    return out


def _kql(value: str) -> str:
    value = value.replace('"', "")
    return f'"{value}"' if " " in value else value


def person_names(rcps: list) -> list[str]:
    return [(r.get("emailAddress") or {}).get("name") or (r.get("emailAddress") or {}).get("address", "") for r in rcps or []]


def search_threads(g: Graph, folder_id: str = "", person: str = "", subject: str = "",
                   since: datetime | None = None, limit: int = 300) -> list[dict]:
    base = f"/me/mailFolders/{folder_id}/messages" if folder_id else "/me/messages"
    if person or subject:
        terms = []
        if since:
            terms.append(f"received>={since.astimezone().strftime('%Y-%m-%d')}")
        if person:
            terms.append(f"participants:{_kql(person)}")
        if subject:
            terms.append(f"subject:{_kql(subject)}")
        params = {"$search": '"' + " AND ".join(terms).replace('"', '\\"') + '"', "$top": "50", "$select": LIST_FIELDS}
    else:
        params = {"$top": "50", "$select": LIST_FIELDS, "$orderby": "receivedDateTime desc"}
        if since:
            params["$filter"] = f"receivedDateTime ge {since.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
    threads: dict[str, dict] = {}
    for m in g.pages(base, params, max_items=limit):
        cid = m.get("conversationId")
        if not cid:
            continue
        t = threads.setdefault(cid, {"id": cid, "subject": m.get("subject") or "(no subject)", "count": 0,
                                     "last": "", "people": set(), "attachments": False})
        t["count"] += 1
        t["attachments"] = t["attachments"] or bool(m.get("hasAttachments"))
        if m.get("receivedDateTime", "") > t["last"]:
            t["last"], t["subject"] = m["receivedDateTime"], m.get("subject") or t["subject"]
        t["people"].update(person_names([m.get("from")] if m.get("from") else []))
    result = sorted(threads.values(), key=lambda t: t["last"], reverse=True)
    for t in result:
        t["people"] = sorted(p for p in t["people"] if p)[:8]
    return result


def fetch_thread(g: Graph, conversation_id: str) -> list[dict]:
    cid = conversation_id.replace("'", "''")
    msgs = [m for m in g.pages("/me/messages", {"$filter": f"conversationId eq '{cid}'", "$select": FULL_FIELDS,
                                                "$top": "50"}, TEXT) if not m.get("isDraft")]
    msgs.sort(key=lambda m: m.get("receivedDateTime") or m.get("sentDateTime") or "")
    return msgs


def save_attachments(g: Graph, m: dict, folder: Path, note) -> list[str]:
    if not m.get("hasAttachments"):
        return []
    saved = []
    for a in g.pages(f"/me/messages/{m['id']}/attachments", {"$select": "id,name,contentType,size,isInline"}):
        if a.get("isInline") or not a.get("@odata.type", "").endswith("fileAttachment"):
            continue
        name = export.safe_filename(a.get("name") or "attachment")
        if a.get("size", 0) > MAX_FILE_BYTES:
            note(f"skipped {name}: larger than 50 MB")
            continue
        try:
            data = g.download(f"/me/messages/{m['id']}/attachments/{a['id']}/$value")
        except GraphError as e:
            note(f"could not download {name}: HTTP {e.status}")
            continue
        path = export.unique(folder / "attachments" / name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        saved.append(f"attachments/{path.name}")
    return saved


def body_text(m: dict) -> str:
    for key in ("uniqueBody", "body"):
        b = m.get(key) or {}
        text = (b.get("content") or "").strip()
        if text:
            return export.html_to_text(text) if b.get("contentType") == "html" else text.replace("\r\n", "\n")
    return ""


def thread_markdown(subject: str, msgs: list[dict], files: dict[str, list[str]], since: datetime | None, until: datetime) -> str:
    people = set()
    for m in msgs:
        people.update(person_names([m.get("from")] if m.get("from") else []))
        people.update(person_names(m.get("toRecipients")) + person_names(m.get("ccRecipients")))
    head = export.frontmatter({
        "source": "mail",
        "title": subject,
        "participants": sorted(p for p in people if p),
        "range": export.range_label(since, until),
        "messages": len(msgs),
        "attachments": [f for fs in files.values() for f in fs],
        "exported": datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M"),
    })
    out = [head, f"# {subject}", ""]
    for m in msgs:
        when = export.local(export.parse_time(m.get("receivedDateTime") or m.get("sentDateTime"))).strftime("%Y-%m-%d %H:%M")
        frm = ", ".join(person_names([m.get("from")])) if m.get("from") else "?"
        to = ", ".join(person_names(m.get("toRecipients")))
        cc = ", ".join(person_names(m.get("ccRecipients")))
        out.append(f"## {when}: {frm} -> {to}" + (f" (cc: {cc})" if cc else ""))
        out += ["", body_text(m) or "_(no text)_", ""]
        for f in files.get(m["id"], []):
            out.append(f"Attachment: [{f.split('/', 1)[1]}]({f.replace(' ', '%20')})")
        if files.get(m["id"]):
            out.append("")
    return "\n".join(out)


def export_thread(g: Graph, conversation_id: str, since: datetime | None, until: datetime, out_dir: Path,
                  note, attachments: bool = True) -> tuple[Path | None, str]:
    msgs = fetch_thread(g, conversation_id)
    msgs = [m for m in msgs if not since or export.parse_time(m.get("receivedDateTime") or m["sentDateTime"]) >= since]
    if not msgs:
        return None, ""
    subject = msgs[-1].get("subject") or msgs[0].get("subject") or "(no subject)"
    clean = subject
    for prefix in ("RE:", "Re:", "FW:", "Fw:", "AW:", "ODP:", "PD:"):
        while clean.strip().startswith(prefix):
            clean = clean.strip()[len(prefix):]
    folder = out_dir / "mail" / export.slug(clean.strip() or subject)
    files = {m["id"]: save_attachments(g, m, folder, note) for m in msgs} if attachments else {}
    name = export.range_label(since, until).replace("..", "_") + ".md"
    path = export.write(export.unique(folder / name), thread_markdown(subject, msgs, files, since, until))
    newest = msgs[-1].get("receivedDateTime") or msgs[-1].get("sentDateTime") or ""
    return path, newest
