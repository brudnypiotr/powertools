"""What came in: mail (Inbox tree and Sent), Teams chats, Jira issues of the user, finished meeting transcripts,
and the calendar ahead. Exclusions are applied here, before any text is kept or shown to a model."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import export
from ..graph import mail, teams
from ..graph.client import GraphError

log = logging.getLogger("powertools.assistant")
MAX_CHATS = 80
MAX_ISSUES = 25
CONTEXT = timedelta(days=4)   # chats: how far back before the range the model also sees
AUTOMATED = re.compile(r"no-?reply|do-?not-?reply|notification|mailer-daemon|postmaster|newsletter", re.I)
KEY = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{1,6}\b")


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


def short(text: str, n: int = 1500) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text or "").strip()
    return text if len(text) <= n else text[:n] + " [...]"


def issue_keys(*texts: str) -> list[str]:
    return list(dict.fromkeys(k for t in texts for k in KEY.findall(t or "")))[:10]


def folder_ids(g, ex: Exclusions) -> list[str]:
    """Inbox with every subfolder, and Sent, without the excluded folders."""
    ids, todo = ["inbox"], ["inbox"]
    while todo:
        params = {"$top": "100", "$select": "id,displayName,childFolderCount"}
        for f in g.pages(f"/me/mailFolders/{todo.pop()}/childFolders", params):
            if ex.folder(f.get("displayName", "")):
                continue
            ids.append(f["id"])
            if f.get("childFolderCount"):
                todo.append(f["id"])
    return ids + ["sentitems"]


def mail_items(g, me: dict, since: datetime, ex: Exclusions, step=lambda text: None) -> list[dict]:
    mine = {a.lower() for a in (me.get("mail"), me.get("userPrincipalName")) if a}
    items = []
    step("reading mail: finding threads")
    for n, t in enumerate(threads := mail.search_threads(g, folder_ids=folder_ids(g, ex), since=since, limit=400), 1):
        step(f"reading mail: thread {n}/{len(threads)}")
        if ex.hit(t["subject"], *t["people"]):
            continue
        msgs = mail.fetch_thread(g, t["id"])   # received and sent: shows whether it was answered
        if not msgs:
            continue
        lines, senders, names, addresses = [], set(), [], []
        for m in msgs:
            everyone = [m.get("from")] + (m.get("toRecipients") or []) + (m.get("ccRecipients") or [])
            names += mail.person_names([r for r in everyone if r])
            addresses += [((r or {}).get("emailAddress") or {}).get("address", "") for r in everyone]
        if ex.hit(*names, *addresses):
            continue
        for m in msgs[-6:]:
            addr = ((m.get("from") or {}).get("emailAddress") or {}).get("address", "")
            senders.add(addr.lower())
            when = export.local(export.parse_time(m.get("receivedDateTime") or m.get("sentDateTime"))).strftime("%Y-%m-%d %H:%M")
            frm = ", ".join(mail.person_names([m.get("from")])) if m.get("from") else "?"
            to, cc = ", ".join(mail.person_names(m.get("toRecipients"))), ", ".join(mail.person_names(m.get("ccRecipients")))
            lines.append(f"--- {when} from {frm} <{addr}> to {to}" + (f" cc {cc}" if cc else "") +
                         f"\n{short(mail.body_text(m))}")
        last = msgs[-1]
        last_addr = ((last.get("from") or {}).get("emailAddress") or {}).get("address", "").lower()
        text = f"E-mail thread. Subject: {t['subject']}\n\n" + "\n\n".join(lines)
        items.append({
            "key": "mail:" + t["id"], "source": "mail", "title": t["subject"],
            "people": ", ".join(list(dict.fromkeys(n for n in names if n and n != me.get("displayName")))[:8]),
            "last": last.get("receivedDateTime") or last.get("sentDateTime") or "", "link": last.get("webLink", ""),
            "msg_id": last.get("id", ""), "count": len(msgs), "last_from_me": last_addr in mine,
            "automated": bool(senders - mine) and all(AUTOMATED.search(s) for s in senders - mine),
            "keys": issue_keys(t["subject"], text), "text": text,
        })
    return items


def chat_items(g, me: dict, since: datetime, until: datetime, ex: Exclusions, step=lambda text: None) -> list[dict]:
    items = []
    step("reading Teams: listing chats")
    chats = [c for c in teams.list_chats(g, me.get("id", "")) if c["last"] and export.parse_time(c["last"]) >= since]
    for n, c in enumerate(chats[:MAX_CHATS], 1):
        step(f"reading Teams: chat {n}/{min(len(chats), MAX_CHATS)}")
        if ex.hit(c["title"], *c["members"]):
            continue
        msgs = teams.fetch_messages(g, c["id"], since - CONTEXT, until)
        if not msgs or export.parse_time(msgs[-1]["createdDateTime"]) < since:
            continue
        lines = []
        for m in msgs[-15:]:
            t = export.local(export.parse_time(m["createdDateTime"])).strftime("%Y-%m-%d %H:%M")
            lines.append(f"{teams.sender(m)} {t}: {short(teams.render_message(m, {}), 600)}")
        last_user = ((msgs[-1].get("from") or {}).get("user") or {}).get("id", "")
        kind = {"oneOnOne": "1:1 chat", "group": "group chat", "meeting": "meeting chat"}.get(c["type"], "chat")
        text = f"Teams {kind}: {c['title']}\n\n" + "\n".join(lines)
        items.append({
            "key": "teams:" + c["id"], "source": "teams", "title": c["title"],
            "people": ", ".join([m for m in c["members"] if m != me.get("displayName")][:8]),
            "last": msgs[-1]["createdDateTime"], "link": c.get("web", ""), "msg_id": "", "count": len(msgs),
            "last_from_me": bool(last_user) and last_user == me.get("id"), "automated": False,
            "keys": issue_keys(c["title"], text), "text": text,
        })
    return items


def jira_items(client, since: datetime, ex: Exclusions, step=lambda text: None) -> list[dict]:
    """Issues the user is assigned to, watches or reported, changed in the range."""
    step("reading Jira")
    days = max(1, (datetime.now().astimezone() - since).days + 1)
    jql = (f"(assignee = currentUser() OR watcher = currentUser() OR reporter = currentUser()) AND updated >= -{days}d "
           "ORDER BY updated DESC")
    items = []
    for i in client.search(jql, "summary,status,assignee,reporter,updated,comment,duedate,priority", MAX_ISSUES):
        f = i.get("fields") or {}
        if not f.get("updated") or export.parse_time(_jira_time(f["updated"])) < since or ex.hit(f.get("summary", "")):
            continue
        comments = ((f.get("comment") or {}).get("comments") or [])[-4:]
        lines = [f"Jira issue {i['key']}: {f.get('summary', '')}", f"Status: {(f.get('status') or {}).get('name', '')}",
                 f"Assignee: {_name(f.get('assignee'))}", f"Reporter: {_name(f.get('reporter'))}", f"Due: {f.get('duedate') or ''}"]
        lines += [f"Comment {str(c.get('created', ''))[:16].replace('T', ' ')} {_name(c.get('author'))}: {short(_adf(c.get('body')), 700)}"
                  for c in comments]
        people = [_name(f.get("assignee")), _name(f.get("reporter"))] + [_name(c.get("author")) for c in comments]
        if ex.hit(*people):
            continue
        items.append({
            "key": "jira:" + i["key"], "source": "jira", "title": f"{i['key']} {f.get('summary', '')}",
            "people": ", ".join(dict.fromkeys(p for p in people if p)), "last": _utc(_jira_time(f["updated"])),
            "link": f"{client.base}/browse/{i['key']}", "msg_id": "", "count": len(comments), "last_from_me": False,
            "automated": False, "keys": [i["key"]], "text": "\n".join(lines),
        })
    return items


def meeting_items(sessions: list, since: datetime, ex: Exclusions) -> list[dict]:
    """Recorded meetings whose summary is ready (the summary and key points, not the whole transcript)."""
    items = []
    for s in sessions:
        m = s.meta
        done, summary = datetime.fromtimestamp(m.get("started", 0)).astimezone(), m.get("summary") or {}
        if m.get("summary_status") != "done" or done < since or ex.hit(m.get("title", ""), *m.get("attendees", [])):
            continue
        text = (f"Meeting {done:%Y-%m-%d %H:%M}: {m.get('title', '')}\nAttendees: {', '.join(m.get('attendees', [])[:20])}\n\n"
                f"{summary.get('summary', '')}\n" + "\n".join(f"- {p}" for p in summary.get("key_points", [])))
        items.append({
            "key": "meeting:" + s.id, "source": "meeting", "title": m.get("title", "Meeting"),
            "people": ", ".join(m.get("attendees", [])[:8]), "last": done.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "link": "", "msg_id": "", "count": 1, "last_from_me": False, "automated": False,
            "keys": issue_keys(m.get("title", ""), text), "text": text, "file": str(Path(m["md"])) if m.get("md") else "",
        })
    return items


def events(g, start: datetime, end: datetime, ex: Exclusions) -> list[dict]:
    """The calendar ahead (cancelled and excluded ones left out)."""
    out = []
    for e in g.pages("/me/calendarView", {
            "startDateTime": start.isoformat(timespec="seconds"), "endDateTime": end.isoformat(timespec="seconds"),
            "$top": "50", "$orderby": "start/dateTime",
            "$select": "id,seriesMasterId,subject,start,end,isCancelled,isAllDay,onlineMeeting,attendees,organizer,body,webLink"},
            {"Prefer": 'outlook.timezone="UTC", outlook.body-content-type="text"'}, 200):
        people = [(a.get("emailAddress") or {}).get("name", "") for a in e.get("attendees", [])]
        addresses = [(a.get("emailAddress") or {}).get("address", "") for a in e.get("attendees", [])]
        if e.get("isCancelled") or ex.hit(e.get("subject", ""), *people, *addresses):
            continue
        out.append({"id": e.get("id", ""), "series": e.get("seriesMasterId") or "", "title": e.get("subject") or "(no title)",
                    "start": e["start"]["dateTime"][:19] + "Z", "end": e["end"]["dateTime"][:19] + "Z",
                    "all_day": bool(e.get("isAllDay")), "people": [p for p in people if p][:40],
                    "organizer": ((e.get("organizer") or {}).get("emailAddress") or {}).get("name", ""),
                    "link": e.get("webLink", ""), "join": (e.get("onlineMeeting") or {}).get("joinUrl", "") or "",
                    "invite": short(((e.get("body") or {}).get("content") or ""), 3000)})
    return out


def direct_reports(g) -> list[dict]:
    try:
        return [{"name": p.get("displayName", ""), "title": p.get("jobTitle") or "", "mail": p.get("mail") or "", "added": False}
                for p in g.pages("/me/directReports", {"$select": "id,displayName,jobTitle,mail", "$top": "100"})
                if p.get("displayName")]
    except GraphError as e:
        log.info("no direct reports: %s", e)
        return []


def _name(p) -> str:
    return (p or {}).get("displayName", "") if isinstance(p, dict) else ""


def _jira_time(t: str) -> str:
    return re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", t)


def _utc(t: str) -> str:
    return export.parse_time(t).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _adf(body) -> str:
    """Plain text of a Jira comment (Cloud: Atlassian document, Data Center: text)."""
    if isinstance(body, str):
        return body
    if isinstance(body, dict):
        own = body.get("text", "") or (body.get("attrs") or {}).get("text", "")
        return own + "".join(_adf(c) for c in body.get("content") or []) + ("\n" if body.get("type") == "paragraph" else "")
    return ""
