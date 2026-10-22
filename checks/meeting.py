"""H: without admin rights we can tell that a Teams meeting is going on: Windows records which apps use
the microphone right now (HKCU CapabilityAccessManager), and the calendar says which meeting it is.

Run it twice: once during a Teams call, once outside one.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone

from .common import IS_WIN, OK, SKIP, WARN, Context

KEY = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"


def apps_using_mic() -> tuple[list[str], int]:
    """Returns (apps using the microphone now, number of apps that ever used it)."""
    import winreg

    active, total = [], 0

    def visit(path: str, label_from_key) -> None:
        nonlocal total
        try:
            root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            return
        i = 0
        while True:
            try:
                name = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            if name == "NonPackaged":
                continue
            try:
                with winreg.OpenKey(root, name) as k:
                    start = winreg.QueryValueEx(k, "LastUsedTimeStart")[0]
                    stop = winreg.QueryValueEx(k, "LastUsedTimeStop")[0]
            except OSError:
                continue
            total += 1
            if start and not stop:
                active.append(label_from_key(name))

    visit(KEY, lambda n: n.split("_")[0])  # packaged apps, e.g. MSTeams_8wekyb3d8bbwe -> MSTeams
    visit(KEY + r"\NonPackaged", lambda n: n.split("#")[-1])  # C:#Program Files#...#app.exe -> app.exe
    return active, total


def teams_processes() -> list[str]:
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return []
    names = {line.split('","')[0].strip('"') for line in out.splitlines() if line}
    return sorted(n for n in names if "teams" in n.lower())


def run(ctx: Context) -> None:
    if not IS_WIN:
        ctx.add("meeting", SKIP, "Windows only")
        return
    active, total = apps_using_mic()
    ctx.add("meeting.registry", OK if total else WARN, f"readable; {total} apps have used the microphone")
    teams_on_mic = [a for a in active if "teams" in a.lower()]
    ctx.add("meeting.mic_now", OK, f"using the microphone now: {', '.join(active) or 'nobody'}"
                                   + ("  -> Teams call detected" if teams_on_mic else ""))
    procs = teams_processes()
    ctx.add("meeting.teams_process", OK if procs else WARN, ", ".join(procs) or "Teams is not running")

    from .graph import Graph, current_event, load_token  # optional: only with a saved, valid token

    token = load_token(ctx, prompt=False)
    if not token:
        ctx.add("meeting.calendar_now", SKIP, "no saved Graph token (run the graph check first)")
        return
    st, ev = current_event(Graph(token), datetime.now(timezone.utc))
    if st != 200:
        ctx.add("meeting.calendar_now", WARN, f"calendar HTTP {st}")
        return
    if ev:
        online = bool((ev.get("onlineMeeting") or {}).get("joinUrl"))
        ctx.add("meeting.calendar_now", OK, f"a calendar event is on now (Teams link: {online}, "
                                            f"{len(ev.get('attendees', []))} attendees)")
    else:
        ctx.add("meeting.calendar_now", OK, "no calendar event right now")
