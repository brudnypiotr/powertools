# powertools architecture

Status: draft after the feasibility run on the target laptop (see "Facts" below). Personal tool, Windows only,
user space only: no admin rights, no requests to IT, nothing that needs tenant consent.

## Facts from the feasibility checker

| Area | Result | Consequence |
|---|---|---|
| Runtime | Windows 11, PowerShell 5.1, not elevated, no proxy; PyPI, Hugging Face, GitHub, Graph reachable | Same bootstrap style as before: `.cmd` → `.ps1` → private Python + venv |
| Python | The private embeddable Python has no tkinter | Setup must prefer a full per-user Python (python.org installer, per-user) |
| GPU | NVIDIA RTX A1000, **6 GB** | Only one Whisper model on the GPU at a time |
| Whisper | Models already cached; turbo on CUDA loads in 4.6 s, RTF 0.12, Polish detected p=1.00 | Dictation keeps turbo loaded; large-v3 only for the final meeting pass |
| Audio | PyAudioWPatch records mic + WASAPI loopback together (10.2 s / 10.2 s); `soundcard` drops mic frames | PyAudioWPatch only; devices picked in settings |
| Meeting detection | HKCU CapabilityAccessManager shows `MSTeams` on the microphone during a call | Poll the registry; no admin, no Teams API |
| Graph (Graph Explorer token) | Chats 1:1/group/meeting, server-side time filter, chat files, mail (threads, sent, search, attachments), calendar with invite text: OK | These are the sources |
| Graph, admin-only | Channel messages (`ChannelMessage.Read.All`), online meetings + transcripts (`OnlineMeetings.Read`, `OnlineMeetingTranscript.Read.All`): 403 | **Out of scope for good.** Speakers are Me/Others; names come from the calendar attendee list |
| Shell | Tray icon + menu, local web page, global Alt+Q, Ctrl+V paste with clipboard restore: OK. pystray balloon toast: not shown | Own always-on-top tkinter popup instead of toasts |

## Shape

One process, started by `powertools.cmd` (autostart via a shortcut in the user's Startup folder):

```
main thread ........ tkinter root (hidden): recording dot, "Record this meeting?" popup
tray thread ........ pystray (run_detached): state icon + menu (Dictate, Record meeting, Open, Quit)
web thread ......... ThreadingHTTPServer on 127.0.0.1 (pattern of woynich-wiki kb serve): the window, as a browser tab
hotkey thread ...... keyboard: toggle dictation
whisper worker ..... owns the GPU model; a queue of jobs (dictation first, then live meeting chunks, then final passes)
meeting watcher .... every 5 s: registry (Teams on the mic?) + cached calendar event with a Teams link overlapping now
recorder (subproc) . PyAudioWPatch: mic.wav + others.wav in 30 s chunks; a crash never takes the app down
sync jobs .......... Graph exports, one at a time, on demand from the window
```

Modules (`powertools/`): `app` (wiring, tray, popup), `web` (routes + static UI), `dictation`, `meetings`
(`watcher`, `recorder`, `transcribe`), `graph` (`client`, `token`, `chats`, `mail`, `calendar`), `export`
(Markdown writer), `store` (SQLite: settings, history, sync cursors), `devices`.

## Features

**Dictation.** Toggle hotkey (Alt+Q by default). Record the selected mic → faster-whisper turbo with VAD →
replacement rules → paste with Ctrl+V → restore the clipboard. `initial_prompt` from the user's word list.
History (text only) in SQLite, with "add a correction" in the window. A post-processor hook exists for a future
**local** LLM and is off; no text leaves the laptop.

**Meetings.** Watcher sees Teams on the microphone → popup "Record 'Title'?" (title and attendees from the
calendar if an event with a Teams link is on now; otherwise "Teams call"). Two tracks: mic = **Me**, loopback of
the selected output device = **Others**. Live: turbo transcribes 30 s chunks into the Meetings tab, with
"mark moment". After the call: free the GPU, run large-v3 on both tracks, merge segments by time into
`Me:` / `Others:` lines, write Markdown. Audio kept for N days (setting), then deleted.

**Teams chats.** List of chats (1:1, group, meeting) sorted by last activity, searchable by member name.
Range: last 2 days / 10 days / since date / all, or "only new since the last export" (cursor per chat in
SQLite). Shared files are downloaded next to the Markdown; on failure the link stays.

**Mail.** Folder picker, person/subject search, same ranges and cursors. Threads merged by `conversationId`
(received + sent). File attachments saved next to the thread.

**Token.** Pasted from Graph Explorer into the window; stored with Windows DPAPI (CurrentUser, via ctypes),
never in the repo. On 401 the window asks for a new one and the job resumes from its cursor.

## Output

```
<output folder, default: the wiki inbox>/
  teams/<chat-slug>/<yyyy-mm-dd>_<from>_<to>.md   + files/
  mail/<thread-slug>/<yyyy-mm-dd>.md              + attachments/
  meetings/<yyyy-mm-dd-hhmm>_<title-slug>.md
```

Every file starts with frontmatter: `source` (teams-chat | mail | meeting), `title`, `participants`, `range`,
`exported`, `tool: powertools <version>`. Body is plain Markdown, oldest first.

## Setup

`powertools.cmd` → `setup/setup.ps1` (copied from woynich-wiki and trimmed): find a **full** Python 3.11+
(tkinter present), else install python.org per-user, else fall back to the embeddable one with the popup and
dot drawn in the browser tab instead; venv; `pip install`; Whisper models from the Hugging Face cache or
download; shortcut + Startup entry. Resets `PSModulePath` and runs modules through the `sys.path` bootstrap
(both lessons from the checker).

## Build order

1. **Dictation** (replaces letmetalk): app shell (tray, web, hotkey), whisper worker, settings, history.
2. **Teams chats + mail**: token, chat list, ranges, cursors, export.
3. **Meetings**: watcher, popup, recorder, live view, final pass.

Each step ships with unit tests that run on CI (`windows-latest`, dry) and a short manual check on the laptop.

## Out of scope

Team channels, official Teams transcripts and recordings, anything needing admin consent or IT, cloud LLMs.
