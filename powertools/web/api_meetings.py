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


@route("DELETE", r"/api/meetings/([\w-]+)")
def meeting_delete(h, sid):
    h.app.meetings.delete(sid)
    h.send(200, {"deleted": sid})


@route("GET", "/api/meetings/devices")
def meeting_devices(h):
    h.send(200, h.app.meeting_devices())
