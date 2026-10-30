"""Window API for meeting recording. `h.app` is the App; `h.app.meetings` the MeetingManager."""

from __future__ import annotations

import threading

from .server import route


@route("GET", "/api/meetings")
def meetings(h):
    m = h.app.meetings
    h.send(200, {"current": m.current.view(with_live=True) if m.current else None, "levels": m.levels,
                 "pending_prompt": {k: v for k, v in h.app.pending_prompt.items() if k != "event"},
                 "sessions": [s.view() for s in m.sessions() if not m.current or s.id != m.current.id]})


@route("POST", "/api/meetings/start")
def meeting_start(h):
    b = h.json_body()
    event = h.app.pending_prompt.get("event") if b.get("from_prompt") else None
    h.send(200, h.app.start_meeting(str(b.get("title", "")), event, auto=bool(b.get("from_prompt"))).view())


@route("POST", "/api/meetings/dismiss")
def meeting_dismiss(h):
    h.app.pending_prompt = {}
    h.send(200, {"ok": True})


@route("POST", "/api/meetings/stop")
def meeting_stop(h):
    if not h.app.meetings.current:
        raise ValueError("no meeting is being recorded")
    threading.Thread(target=h.app.meetings.stop, daemon=True).start()  # waits for the recorder to flush
    h.send(200, {"stopping": True})


@route("POST", "/api/meetings/mark")
def meeting_mark(h):
    h.send(200, h.app.meetings.mark(str(h.json_body().get("note", ""))))


@route("POST", r"/api/meetings/([\w-]+)/finalize")
def meeting_finalize(h, sid):
    s = h.app.meetings.get(sid)
    if s.meta.get("status") not in ("interrupted", "done"):
        raise ValueError("only interrupted or finished meetings can be transcribed again")
    threading.Thread(target=h.app.meetings.finalize, args=(s,), daemon=True).start()
    h.send(200, {"finalizing": sid})


@route("POST", r"/api/meetings/([\w-]+)/open")
def meeting_open(h, sid):
    """Open the meeting's Markdown file (or its folder) with the default app."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    md = h.app.meetings.get(sid).meta.get("md") or ""
    if not md or not Path(md).exists():
        raise ValueError("no transcript file yet")
    target = str(Path(md).parent) if h.json_body().get("folder") else md
    if sys.platform == "win32":
        os.startfile(target)  # noqa: S606 (the user's own file)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    h.send(200, {"opened": target})


def _calendar_at(h, s) -> dict:
    from datetime import datetime
    if not h.app.graph.tokens.get():
        raise ValueError("set a Graph token in the Teams tab first: attendees come from your calendar")
    return h.app.graph.current_event(datetime.fromtimestamp(s.meta["started"] + 60))


@route("POST", r"/api/meetings/([\w-]+)/calendar")
def meeting_calendar(h, sid):
    """Attendees, organizer and title from the calendar for an earlier recording, and the compact layout."""
    s = h.app.meetings.get(sid)
    h.send(200, h.app.meetings.update_file(s, _calendar_at(h, s)))


@route("POST", "/api/meetings/update-all")
def meetings_update_all(h):
    """Every finished recording: attendees from the calendar where they are missing (needs the Graph token), and
    every transcript file in the compact layout."""
    m, token = h.app.meetings, bool(h.app.graph.tokens.get())
    done = []
    for s in m.sessions(limit=1000):
        if s is m.current or s.meta.get("status") != "done":
            continue
        event = None
        if token and not s.meta.get("attendees"):
            try:
                event = _calendar_at(h, s)
            except Exception:  # expired token, network: the file is still made compact
                event = None
        done.append(m.update_file(s, event))
    h.send(200, {"updated": len(done), "with_attendees": sum(1 for d in done if d["calendar"]), "token": token})


@route("DELETE", r"/api/meetings/([\w-]+)")
def meeting_delete(h, sid):
    h.app.meetings.delete(sid)
    h.send(200, {"deleted": sid})


@route("GET", "/api/meetings/devices")
def meeting_devices(h):
    h.send(200, h.app.meeting_devices())
