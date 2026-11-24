"""What came in: mail (Inbox tree and Sent), Teams chats, Jira issues of the user, finished meeting transcripts,
and the calendar ahead. Exclusions are applied here, before any text is kept or shown to a model.

Each source is a generator, so what was read before a failure is kept. One conversation that cannot be read is noted
(Reading) and the rest goes on.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from pathlib import Path

from .. import clock, export
from ..exclusions import Exclusions
from ..graph import mail, teams
from ..graph.client import GraphError

log = logging.getLogger("powertools.assistant")
MAX_THREADS = 600            # what does not fit into one update is said, never left out silently
MAX_CHATS = 200
MAX_ISSUES = 100
GONE = (403, 404, 410)       # left, deleted, not allowed: the next update would not get it either
CONTEXT = timedelta(days=4)  # chats: how far back before the range the model also sees
AUTOMATED = re.compile(r"no-?reply|do-?not-?reply|notification|mailer-daemon|postmaster|newsletter", re.I)
KEY = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{1,6}\b")
KINDS = {"oneOnOne": "1:1 chat", "group": "group chat", "meeting": "meeting chat"}


class Reading:
    """One source being read: what it does now, and the conversations that could not be read."""

    def __init__(self, step: Callable[[str], None] = lambda text: None):
        self.step = step
        self.notes: list[str] = []      # said to the user; the rest of the source was read
        self.again = False              # it may work next time: this range has to be read again

    def failed(self, what: str, e: GraphError) -> None:
        self.notes.append(f"{what}: {e}")
        self.again = self.again or e.status not in GONE

    def newest(self, rows: list, limit: int, what: str) -> list:
        if len(rows) > limit:
            self.notes.append(f"more than {limit} {what} in this range: only the newest {limit} were read (choose a shorter range)")
        return rows[:limit]


def short(text: str, n: int = 1500) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text or "").strip()
    return text if len(text) <= n else text[:n] + " [...]"


def issue_keys(*texts: str) -> list[str]:
    return list(dict.fromkeys(k for t in texts for k in KEY.findall(t or "")))[:10]


def names(i: dict) -> list[str]:
    """The other people of a conversation ("Last, First" is one person); items read before 0.16.1 have only the text."""
    return i["who"] if "who" in i else [x.strip() for x in i["people"].split(",") if x.strip()]


def _people(everyone: list[str], me: dict) -> dict:
    who = list(dict.fromkeys(n for n in everyone if n and n != me.get("displayName")))
    return {"who": who[:40], "people": ", ".join(who[:8])}


# ---------------------------------------------------------------- mail
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


def mail_items(g, me: dict, since: datetime, ex: Exclusions, r: Reading) -> Iterator[dict]:
    r.step("reading mail: finding threads")
    threads = mail.search_threads(g, folder_ids=folder_ids(g, ex), since=since, limit=MAX_THREADS + 1)
    threads = r.newest(threads, MAX_THREADS, "mail threads")
    for n, t in enumerate(threads, 1):
        r.step(f"reading mail: thread {n}/{len(threads)}")
        if ex.hit(t["subject"], *t["people"]):
            continue
        try:
            item = mail_item(g, me, t["id"], t["subject"], ex)
        except GraphError as e:
            r.failed(t["subject"][:80], e)
            continue
        if item:
            yield item


def _address(m: dict) -> str:
    return ((m.get("from") or {}).get("emailAddress") or {}).get("address", "").lower()


def _sent(m: dict) -> str:
    return m.get("receivedDateTime") or m.get("sentDateTime") or ""


def mail_item(g, me: dict, cid: str, subject: str, ex: Exclusions) -> dict | None:
    """One thread with its received and sent messages (that shows whether it was answered)."""
    mine = {a.lower() for a in (me.get("mail"), me.get("userPrincipalName")) if a}
    msgs = mail.fetch_thread(g, cid)
    everybody, addresses = [], []
    for m in msgs:
        everyone = [p for p in [m.get("from"), *(m.get("toRecipients") or []), *(m.get("ccRecipients") or [])] if p]
        everybody += mail.person_names(everyone)
        addresses += [(p.get("emailAddress") or {}).get("address", "") for p in everyone]
    if not msgs or ex.hit(*everybody, *addresses):
        return None
    last, lines = msgs[-1], [_mail_line(m) for m in msgs[-6:]]
    others = {_address(m) for m in msgs[-6:]} - mine
    text = f"E-mail thread. Subject: {subject}\n\n" + "\n\n".join(lines)
    return {
        "key": "mail:" + cid, "source": "mail", "title": subject, **_people(everybody, me), "last": _sent(last),
        "link": last.get("webLink", ""), "msg_id": last.get("id", ""), "count": len(msgs), "last_from_me": _address(last) in mine,
        "mine_at": max((_sent(m) for m in msgs if _address(m) in mine), default=""),
        "automated": bool(others) and all(AUTOMATED.search(s) for s in others), "keys": issue_keys(subject, text), "text": text,
    }


def _mail_line(m: dict) -> str:
    when = export.local(export.parse_time(_sent(m))).strftime("%Y-%m-%d %H:%M")
    frm = ", ".join(mail.person_names([m["from"]])) if m.get("from") else "?"
    to, cc = ", ".join(mail.person_names(m.get("toRecipients"))), ", ".join(mail.person_names(m.get("ccRecipients")))
    return f"--- {when} from {frm} <{_address(m)}> to {to}" + (f" cc {cc}" if cc else "") + f"\n{short(mail.body_text(m))}"


# ---------------------------------------------------------------- Teams
def chat_items(g, me: dict, since: datetime, until: datetime, ex: Exclusions, r: Reading) -> Iterator[dict]:
    r.step("reading Teams: listing chats")
    chats = [c for c in teams.list_chats(g, me.get("id", "")) if c["last"] and export.parse_time(c["last"]) >= since]
    chats = r.newest(chats, MAX_CHATS, "chats")
    for n, c in enumerate(chats, 1):
        r.step(f"reading Teams: chat {n}/{len(chats)}")
        if ex.hit(c["title"], *c["members"]):
            continue
        try:
            item = chat_item(g, me, c, since, until)
        except GraphError as e:
            r.failed(c["title"][:80], e)
            continue
        if item:
            yield item


def chat_item(g, me: dict, c: dict, since: datetime, until: datetime) -> dict | None:
    """One chat ({id, title, members, type, web}) with the messages since `since` and some days before; None: nothing new."""
    msgs = teams.fetch_messages(g, c["id"], since - CONTEXT, until)
    if not msgs or export.parse_time(msgs[-1]["createdDateTime"]) < since:
        return None
    lines = []
    for m in msgs[-15:]:
        t = export.local(export.parse_time(m["createdDateTime"])).strftime("%Y-%m-%d %H:%M")
        lines.append(f"{teams.sender(m)} {t}: {short(teams.render_message(m, {}), 600)}")
    when, what = teams.reaction(msgs[-1], me.get("id", ""))
    text = f"Teams {KINDS.get(c['type'], 'chat')}: {c['title']}\n\n" + "\n".join(lines)
    return {
        "key": "teams:" + c["id"], "source": "teams", "title": c["title"], "kind": c["type"], **_people(c["members"], me),
        "last": max(msgs[-1]["createdDateTime"], when), "reacted": what,
        "mine_at": max([m["createdDateTime"] for m in msgs if me.get("id") and teams.sender_id(m) == me["id"]] + [when]), "link": c.get("web", ""), "msg_id": "", "count": len(msgs),
        "last_from_me": bool(me.get("id")) and teams.sender_id(msgs[-1]) == me["id"], "automated": False,
        "keys": issue_keys(c["title"], text), "text": text,
    }


# ---------------------------------------------------------------- Jira, recorded meetings
def jira_items(client, since: datetime, ex: Exclusions, r: Reading) -> Iterator[dict]:
    """Issues the user is assigned to, watches or reported, changed in the range."""
    r.step("reading Jira")
    days = max(1, (datetime.now().astimezone() - since).days + 1)
    jql = (f"(assignee = currentUser() OR watcher = currentUser() OR reporter = currentUser()) AND updated >= -{days}d "
           "ORDER BY updated DESC")
    found = client.search(jql, "summary,status,assignee,reporter,updated,comment,duedate,priority", MAX_ISSUES + 1)
    for i in r.newest(list(found), MAX_ISSUES, "Jira issues"):
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
        yield {
            "key": "jira:" + i["key"], "source": "jira", "title": f"{i['key']} {f.get('summary', '')}", **_people(people, {}),
            "last": _utc(_jira_time(f["updated"])), "link": f"{client.base}/browse/{i['key']}", "msg_id": "",
            "count": len(comments), "last_from_me": False, "automated": False, "keys": [i["key"]], "text": "\n".join(lines),
        }


def meeting_items(sessions: list, since: datetime, ex: Exclusions) -> Iterator[dict]:
    """Recorded meetings whose summary is ready (the summary and key points, not the whole transcript)."""
    for s in sessions:
        m = s.meta
        done, summary = datetime.fromtimestamp(m.get("started", 0)).astimezone(), m.get("summary") or {}
        if m.get("summary_status") != "done" or done < since or ex.hit(m.get("title", ""), *m.get("attendees", [])):
            continue
        text = (f"Meeting {done:%Y-%m-%d %H:%M}: {m.get('title', '')}\nAttendees: {', '.join(m.get('attendees', [])[:20])}\n\n"
                f"{summary.get('summary', '')}\n" + "\n".join(f"- {p}" for p in summary.get("key_points", [])))
        yield {
            "key": "meeting:" + s.id, "source": "meeting", "title": m.get("title", "Meeting"), **_people(m.get("attendees", []), {}),
            "last": clock.iso(done), "link": "", "msg_id": "", "count": 1,
            "last_from_me": False, "automated": False, "keys": issue_keys(m.get("title", ""), text), "text": text,
            "file": str(Path(m["md"])) if m.get("md") else "",
        }


# ---------------------------------------------------------------- calendar, people
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
    return clock.iso(export.parse_time(t))


def _adf(body) -> str:
    """Plain text of a Jira comment (Cloud: Atlassian document, Data Center: text)."""
    if isinstance(body, str):
        return body
    if isinstance(body, dict):
        own = body.get("text", "") or (body.get("attrs") or {}).get("text", "")
        return own + "".join(_adf(c) for c in body.get("content") or []) + ("\n" if body.get("type") == "paragraph" else "")
    return ""
