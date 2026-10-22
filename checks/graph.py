"""H: a token copied from Graph Explorer is enough for Teams chats/channels, mail with attachments, the
calendar, files shared in chats and official Teams meeting transcripts.

Only statuses and counts are reported. Content (names, subjects, text) is never printed or saved.
"""

from __future__ import annotations

import base64
import getpass
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

from .common import FAIL, OK, SKIP, TOKEN_FILE, WARN, Context

BASE = "https://graph.microsoft.com/v1.0"
GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

# probe id -> delegated permission to tick in Graph Explorer (Modify permissions) when it fails
SCOPES = {
    "me": "User.Read",
    "chats": "Chat.ReadBasic or Chat.Read",
    "chat.messages": "Chat.Read",
    "chat.file": "Files.Read.All (or Sites.Read.All)",
    "teams": "Team.ReadBasic.All",
    "channels": "Channel.ReadBasic.All",
    "channel.messages": "ChannelMessage.Read.All",
    "mail": "Mail.Read",
    "calendar": "Calendars.Read",
    "meetings": "OnlineMeetings.Read",
    "transcripts": "OnlineMeetingTranscript.Read.All",
    "drive": "Files.Read",
}


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def jwt_claims(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def load_token(ctx: Context, prompt: bool = True) -> str:
    token = os.environ.get("GRAPH_TOKEN", "").strip()
    if not token and TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        exp = jwt_claims(token).get("exp", 0)
        if exp and exp < time.time() + 120:
            print("   The saved token has expired.")
            token = ""
    if not token and prompt and ctx.interactive:
        print("   Graph Explorer (https://developer.microsoft.com/graph/graph-explorer): sign in with the work account,")
        print("   open the 'Access token' tab, copy it. Paste below (input is hidden, Enter when done).")
        token = getpass.getpass("   Token: ").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if token:
            TOKEN_FILE.write_text(token, encoding="utf-8")  # git-ignored
    return token


class Graph:
    def __init__(self, token: str):
        import requests  # imported here so the other checks run without it

        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})

    def get(self, path: str, params: dict | None = None, headers: dict | None = None, raw: bool = False):
        """Returns (status, json-or-bytes-or-None, error code + short message)."""
        url = path if path.startswith("http") else BASE + path
        try:
            r = self.s.get(url, params=params, headers=headers, timeout=60)
        except Exception as e:
            return 0, None, f"network {type(e).__name__}"
        if r.ok:
            if raw:
                return r.status_code, r.content, ""
            try:
                return r.status_code, r.json(), ""
            except ValueError:
                return r.status_code, None, ""
        try:
            err = r.json().get("error", {})
            msg = GUID.sub("<id>", str(err.get("message", "")))[:140]
            return r.status_code, None, f"{err.get('code', '')}: {msg}"
        except ValueError:
            return r.status_code, None, r.reason


def report(ctx: Context, id: str, scope_key: str, status: int, err: str, ok_detail: str | None) -> bool:
    """Adds a result for a failure; for a success only when ok_detail is given (else the caller adds it)."""
    if 200 <= status < 300:
        if ok_detail is not None:
            ctx.add(f"graph.{id}", OK, ok_detail)
        return True
    hint = f" -> needs {SCOPES[scope_key]}" if status in (401, 403) else ""
    ctx.add(f"graph.{id}", FAIL if status in (0, 401) else WARN, f"HTTP {status} {err}{hint}")
    return False


def run(ctx: Context) -> None:
    if not ctx.confirm_start("Graph", "You will paste a fresh token from Graph Explorer (valid about 1 hour).\n"
                                      "Tip: first tick extra permissions in Graph Explorer > Modify permissions:\n"
                                      "Chat.Read, ChannelMessage.Read.All, Mail.Read, Calendars.Read, Files.Read.All,\n"
                                      "OnlineMeetings.Read, OnlineMeetingTranscript.Read.All. Note which ones need admin."):
        ctx.add("graph", SKIP, "skipped by user")
        return
    token = load_token(ctx)
    if not token:
        ctx.add("graph.token", SKIP, "no token")
        return

    claims = jwt_claims(token)
    scopes = sorted(claims.get("scp", "").split())
    left = (claims.get("exp", 0) - time.time()) / 60
    ctx.add("graph.token", OK if scopes else WARN,
            f"app={claims.get('app_displayname', '?')}, aud={claims.get('aud', '?')}, expires in {left:.0f} min")
    ctx.add("graph.token.scopes", OK if scopes else WARN, " ".join(scopes) or "no scp claim")

    g = Graph(token)
    now = datetime.now(timezone.utc)

    st, _, err = g.get("/me", {"$select": "id"})
    if not report(ctx, "me", "me", st, err, "token accepted"):
        if st == 401:
            ctx.add("graph", FAIL, "token rejected; get a new one and run: check.cmd -Only graph")
            try:
                TOKEN_FILE.unlink()
            except OSError:
                pass
        return

    probe_chats(ctx, g, now)
    probe_channels(ctx, g)
    probe_mail(ctx, g, now)
    probe_calendar_and_transcripts(ctx, g, now)

    st, _, err = g.get("/me/drive/root", {"$select": "id"})
    report(ctx, "drive", "drive", st, err, "OneDrive reachable")


# ----------------------------------------------------------------------------------------------- Teams chats

def probe_chats(ctx: Context, g: Graph, now: datetime) -> None:
    st, data, err = g.get("/me/chats", {"$top": "50", "$expand": "members"})
    if not report(ctx, "chats.list", "chats", st, err, "listing works"):
        return
    chats = data.get("value", [])
    types: dict[str, int] = {}
    for c in chats:
        types[c.get("chatType", "?")] = types.get(c.get("chatType", "?"), 0) + 1
    named = sum(1 for c in chats if any(m.get("displayName") for m in c.get("members", [])))
    ctx.add("graph.chats.types", OK, f"{len(chats)} chats on page 1: {types}; with member names: {named}"
                                     f"; more pages: {'@odata.nextLink' in data}")

    st, _, err = g.get("/me/chats", {"$top": "20", "$expand": "lastMessagePreview",
                                     "$orderby": "lastMessagePreview/createdDateTime desc"})
    report(ctx, "chats.by_activity", "chats", st, err, "sort by last activity works (server side)")

    def pick(kind: str):
        return next((c for c in chats if c.get("chatType") == kind), None)

    first = pick("oneOnOne") or pick("group") or (chats[0] if chats else None)
    if not first:
        ctx.add("graph.chat.messages", SKIP, "no chats")
        return
    cid = first["id"]
    since = iso(now - timedelta(days=10))
    variants = {
        "plain": {"$top": "50"},
        "filter_modified_10d": {"$top": "50", "$orderby": "lastModifiedDateTime desc",
                                "$filter": f"lastModifiedDateTime gt {since} and lastModifiedDateTime lt {iso(now)}"},
        "order_created_desc": {"$top": "50", "$orderby": "createdDateTime desc",
                               "$filter": f"createdDateTime lt {iso(now)}"},
    }
    for name, params in variants.items():
        st, data, err = g.get(f"/chats/{cid}/messages", params)
        if report(ctx, f"chat.messages.{name}", "chat.messages", st, err, None):
            msgs = data.get("value", [])
            ctx.add(f"graph.chat.messages.{name}", OK, f"{len(msgs)} messages, more pages: {'@odata.nextLink' in data}")

    for kind in ("meeting", "group"):
        c = pick(kind)
        if c and c is not first:
            st, data, err = g.get(f"/chats/{c['id']}/messages", {"$top": "5"})
            report(ctx, f"chat.messages.{kind}", "chat.messages", st, err,
                   f"{len((data or {}).get('value', []))} messages")

    probe_chat_file(ctx, g, chats)


def probe_chat_file(ctx: Context, g: Graph, chats: list) -> None:
    """Find a file shared in a chat (SharePoint/OneDrive link) and check we can reach the file."""
    for c in chats[:15]:
        st, data, _ = g.get(f"/chats/{c['id']}/messages", {"$top": "50"})
        for m in (data or {}).get("value", []):
            for a in m.get("attachments", []):
                url = a.get("contentUrl") or ""
                if a.get("contentType") == "reference" and url.startswith("http"):
                    share = "u!" + base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
                    st, item, err = g.get(f"/shares/{share}/driveItem", {"$select": "id,size,file,parentReference"})
                    if not report(ctx, "chat.file.meta", "chat.file", st, err, None):
                        return
                    ctx.add("graph.chat.file.meta", OK, f"metadata OK, size {item.get('size', 0) // 1024} KB")
                    drive = item.get("parentReference", {}).get("driveId")
                    if drive:
                        st, _, err = g.get(f"/drives/{drive}/items/{item['id']}/content", raw=True,
                                           headers={"Range": "bytes=0-1023"})
                        report(ctx, "chat.file.download", "chat.file", st, err, "download works (first 1 KB)")
                    return
    ctx.add("graph.chat.file", SKIP, "no shared file found in the first 15 chats")


# -------------------------------------------------------------------------------------------- Teams channels

def probe_channels(ctx: Context, g: Graph) -> None:
    st, data, err = g.get("/me/joinedTeams", {"$select": "id"})
    if not report(ctx, "teams.joined", "teams", st, err, None):
        return
    teams = data.get("value", [])
    ctx.add("graph.teams.joined", OK, f"{len(teams)} teams")
    for t in teams[:5]:
        st, data, err = g.get(f"/teams/{t['id']}/channels", {"$select": "id,membershipType"})
        if not report(ctx, "teams.channels", "channels", st, err, None):
            return
        chans = data.get("value", [])
        ctx.add("graph.teams.channels", OK, f"{len(chans)} channels in the first team")
        if chans:
            st, data, err = g.get(f"/teams/{t['id']}/channels/{chans[0]['id']}/messages", {"$top": "5"})
            report(ctx, "teams.channel.messages", "channel.messages", st, err,
                   f"{len((data or {}).get('value', []))} posts")
            return


# ------------------------------------------------------------------------------------------------------ mail

def probe_mail(ctx: Context, g: Graph, now: datetime) -> None:
    st, data, err = g.get("/me/mailFolders", {"$top": "100", "$select": "id,childFolderCount,totalItemCount"})
    if not report(ctx, "mail.folders", "mail", st, err, None):
        return
    folders = data.get("value", [])
    ctx.add("graph.mail.folders", OK, (f"{len(folders)} top-level folders, "
                              f"{sum(f.get('childFolderCount', 0) for f in folders)} subfolders"))

    st, data, err = g.get("/me/mailFolders/sentitems/messages", {"$top": "5", "$select": "conversationId"})
    report(ctx, "mail.sent", "mail", st, err, f"{len((data or {}).get('value', []))} sent messages read")

    since = iso(now - timedelta(days=10))
    st, data, err = g.get("/me/messages", {"$top": "50", "$select": "conversationId,hasAttachments",
                                           "$filter": f"receivedDateTime ge {since}",
                                           "$orderby": "receivedDateTime desc"})
    if not report(ctx, "mail.last10d", "mail", st, err, None):
        return
    msgs = data.get("value", [])
    convs = {m.get("conversationId") for m in msgs}
    with_att = [m for m in msgs if m.get("hasAttachments")]
    ctx.add("graph.mail.last10d", OK, (f"{len(msgs)} messages, {len(convs)} threads, {len(with_att)} with attachments, "
                              f"more pages: {'@odata.nextLink' in data}"))

    if msgs:
        cid = msgs[0]["conversationId"].replace("'", "''")
        st, data, err = g.get("/me/messages", {"$filter": f"conversationId eq '{cid}'", "$select": "id"})
        report(ctx, "mail.thread", "mail", st, err, f"thread lookup: {len((data or {}).get('value', []))} messages")

    st, data, err = g.get("/me/messages", {"$search": '"meeting"', "$top": "5", "$select": "id"})
    report(ctx, "mail.search", "mail", st, err, f"$search works ({len((data or {}).get('value', []))} hits)")

    for m in with_att[:10]:
        st, data, err = g.get(f"/me/messages/{m['id']}/attachments", {"$select": "id,contentType,size,isInline"})
        if not 200 <= st < 300:
            report(ctx, "mail.attachments", "mail", st, err, None)
            return
        files = [a for a in data.get("value", [])
                 if a.get("@odata.type", "").endswith("fileAttachment") and not a.get("isInline")]
        if not files:
            continue
        kinds = sorted({a.get("contentType", "?") for a in files})
        ctx.add("graph.mail.attachments", OK, f"{len(files)} file attachments, types: {', '.join(kinds)}")
        st, blob, err = g.get(f"/me/messages/{m['id']}/attachments/{files[0]['id']}/$value", raw=True)
        report(ctx, "mail.attachment.download", "mail", st, err, f"downloaded {len(blob or b'') // 1024} KB (not saved)")
        return
    ctx.add("graph.mail.attachments", SKIP, "no file attachment in the last 10 days")


# --------------------------------------------------------------------------------- calendar + transcripts

def calendar_window(g: Graph, start: datetime, end: datetime):
    return g.get("/me/calendarView",
                  {"startDateTime": iso(start), "endDateTime": iso(end), "$top": "200",
                   "$select": "start,end,isOnlineMeeting,onlineMeeting,bodyPreview,attendees,isCancelled",
                   "$orderby": "start/dateTime"},
                  headers={"Prefer": 'outlook.timezone="UTC"'})


def parse_utc(s: str) -> datetime:
    return datetime.fromisoformat(s[:19]).replace(tzinfo=timezone.utc)


def current_event(g: Graph, now: datetime) -> tuple[int, dict | None]:
    st, data, _ = calendar_window(g, now - timedelta(hours=4), now + timedelta(hours=1))
    for e in (data or {}).get("value", []):
        if not e.get("isCancelled") and parse_utc(e["start"]["dateTime"]) <= now <= parse_utc(e["end"]["dateTime"]):
            return st, e
    return st, None


def probe_calendar_and_transcripts(ctx: Context, g: Graph, now: datetime) -> None:
    st, data, err = calendar_window(g, now - timedelta(days=14), now + timedelta(days=7))
    if not report(ctx, "calendar", "calendar", st, err, None):
        return
    events = [e for e in data.get("value", []) if not e.get("isCancelled")]
    online = [e for e in events if (e.get("onlineMeeting") or {}).get("joinUrl")]
    with_body = sum(1 for e in events if (e.get("bodyPreview") or "").strip())
    avg_att = sum(len(e.get("attendees", [])) for e in events) / max(len(events), 1)
    ctx.add("graph.calendar", OK, (f"{len(events)} events (-14d..+7d), {len(online)} Teams meetings, "
                              f"{with_body} with invite text, avg {avg_att:.1f} attendees"))
    _, ev = current_event(g, now)
    ctx.add("graph.calendar.now", OK, "a meeting is happening now" if ev else "no meeting right now")

    past = [e for e in online if parse_utc(e["end"]["dateTime"]) < now][::-1][:10]
    if not past:
        ctx.add("graph.meetings", SKIP, "no past Teams meetings in the last 14 days")
        return
    found_meeting = found_transcripts = 0
    last_err = ""
    for e in past:
        url = e["onlineMeeting"]["joinUrl"].replace("'", "''")
        st, data, err = g.get("/me/onlineMeetings", {"$filter": f"JoinWebUrl eq '{url}'"})
        if st in (401, 403):
            report(ctx, "meetings.lookup", "meetings", st, err, None)
            return
        meetings = (data or {}).get("value", [])
        if not meetings:
            last_err = err or "not found (only works for some meeting types)"
            continue
        found_meeting += 1
        mid = meetings[0]["id"]
        st, tdata, err = g.get(f"/me/onlineMeetings/{mid}/transcripts")
        if st in (401, 403):
            ctx.add("graph.meetings.lookup", OK, f"{found_meeting} of {len(past)} recent meetings resolved")
            report(ctx, "transcripts.list", "transcripts", st, err, None)
            return
        ts = (tdata or {}).get("value", [])
        if ts:
            found_transcripts += 1
            st, vtt, err = g.get(f"/me/onlineMeetings/{mid}/transcripts/{ts[0]['id']}/content",
                                 {"$format": "text/vtt"}, raw=True)
            if report(ctx, "transcripts.content", "transcripts", st, err, None):
                text = (vtt or b"").decode("utf-8", "replace")
                speakers = len(set(re.findall(r"<v ([^>]+)>", text)))
                ctx.add("graph.transcripts.content", OK, f"VTT {len(text) // 1024} KB, {speakers} distinct speakers (names not shown)")
            break
    ctx.add("graph.meetings.lookup", OK if found_meeting else WARN,
            f"{found_meeting} of {len(past)} recent meetings resolved" + ("" if found_meeting else f" ({last_err})"))
    ctx.add("graph.transcripts", OK if found_transcripts else WARN,
            "official Teams transcript available" if found_transcripts
            else "no transcripts found (transcription not used in these meetings, or no access)")
