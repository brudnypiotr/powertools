# powertools

Personal Windows toolkit (work in progress): dictation with local Whisper, meeting recording with
transcription, and exporting Teams chats and mail (Microsoft Graph) and Jira issues as Markdown sources for a wiki.
Everything runs in user space: no admin rights, PowerShell 5.1, private Python.

## Dictation (step 1)

Double-click `powertools.cmd` (a git clone, or the ZIP from GitHub unpacked anywhere: the setup turns a ZIP copy into a clone of github.com/brudnypiotr/powertools so Update works). The first start runs the setup (no admin rights): a Python with tkinter
(found or installed per-user), `.venv`, faster-whisper with the CUDA libraries (~1.5 GB), shortcuts on the
desktop, in the Start menu and in Startup (`setup\setup.ps1 -NoAutostart` to skip that one).

- **Alt+Q** (anywhere) starts recording; **Alt+Q** again stops, transcribes locally and pastes into the
  active window. The previous clipboard comes back afterwards.
- A small pill at the bottom of the screen shows the state: red dot + level bars while recording, amber
  dots while transcribing (drawn with Win32 layered windows: no tkinter needed, never takes the focus).
  The tray icon has the same colours.
- Quit: tray icon > Quit, Diagnostics > Quit, or Ctrl+C in `powertools.cmd --console`.
- The window (tray icon > Open window) has the history, "Fix a word" (adds a replacement rule and the word
  to the vocabulary) and the settings: hotkey, model, language, microphone, vocabulary, replacements.
- Nothing leaves the laptop: audio is never saved, history is text only in
  `%LOCALAPPDATA%\powertools\history.sqlite`.

`powertools.cmd --console` runs with the log in the window; `powertools.cmd --setup` repeats the setup.

### Without a (big enough) GPU

Whisper tries the GPU in float16, then the GPU in the memory-saving int8_float16 mode (about a third less VRAM), then
the CPU (int8). It steps down when loading fails and also when a transcription fails on the GPU (out of memory), and
stays down. On the CPU the big models are too slow, so by default turbo runs as small and large-v3 as turbo
(Settings: "Smaller models without a usable GPU"). The footer and the Dictation tab say when it runs on the CPU.
Rough VRAM (float16): large-v3 about 4.5 GB, turbo 2-2.5 GB, small about 1 GB.

## Teams chats and mail (step 2)

Tabs **Teams** and **Mail** in the window. **Get token** opens
[Graph Explorer](https://developer.microsoft.com/graph/graph-explorer) in its own Edge window (your profile, so you are
usually signed in already); open its "Access token" tab and click copy: powertools catches the token from the clipboard
(for 5 minutes, only Graph tokens), saves it and clears the clipboard. Pasting by hand works too. The token is valid
about an hour and stored encrypted for your Windows user (DPAPI). Graph Explorer cannot be shown inside the window
itself: Microsoft forbids embedding it (X-Frame-Options). Only what Graph Explorer can read without admin
consent: 1:1, group and meeting chats with shared files, and mail; team channels are out of scope.

- Pick chats or search mail threads (folder, person, subject), choose a range (last 2/10/30 days, since a
  date, everything, or **only new since the last export**) and export.
- Output (Settings > Export folder, point it at the wiki inbox): `teams/<chat>/<from>_<to>.md` with
  `files/`, `mail/<subject>/<from>_<to>.md` with `attachments/`. Every file has frontmatter (source, title,
  participants, range, counts). Mail uses only the new part of each reply, so quoted history is not repeated.
- When the token expires mid-export, the job pauses; paste a new token and it continues.

## Meetings (step 3)

- When Teams starts using the microphone, a card in the bottom-right corner asks **Record 'Title'?**
  (title and attendees from the calendar when a Graph token is set). The card does not take the focus.
  Tray menu > Record meeting, or the Meetings tab, starts a recording by hand.
- Two tracks: your microphone = **Me**, the sound of the output device (headphones or speakers) =
  **Others**. Pick both devices in Settings > Meetings.
- Live transcript every 30 s in the Meetings tab, with "Mark moment". After the call (Teams leaves the
  microphone for 20 s, or Stop) the accurate model (large-v3) transcribes both tracks and writes
  `meetings/<date>_<title>.md`: invitation text, attendees, marked moments, `Me:` / `Others:` lines.
- Dictation keeps working during a meeting and goes first in the Whisper queue.
- The Meetings tab lists each recording with date, start-end, duration, how many were invited, your share of the
  speaking time; click a row for the organizer, attendees, speaking time and words per track, marked moments, model,
  devices and the invitation. **Open** opens the transcript; **Transcribe again** reruns the final pass from the kept
  audio (kept for "Keep meeting audio" days).
- Others is one voice track: Teams transcripts with names need admin consent, so they are out of scope.

## Jira (step 4)

Tab **Jira**. Set the site and e-mail in Settings > Jira. Token candidates, tried in this order: one pasted in the tab
(kept encrypted for your Windows user, DPAPI), `JIRA_API_TOKEN`, the Windows Credential Manager entries `jira-token`
(service `woynich-wiki`) and `copilot-jira`, both read live, nothing copied. **Check** tries each one, shows the result
per token (last 4 characters, e-mail, Jira's answer) and uses the first one Jira accepts. Jira Cloud: API token +
Atlassian e-mail of the same account; Data Center: personal access token. Read-only.

- **Download** issue keys or browse links, an **epic with all its issues**, or everything matching a JQL.
- **Search**: project, epic, labels, text, updated within N days, any number of field filters (field name with
  autocomplete, operator `= != ~ > < in is ...`, value) and free JQL; the JQL used is shown. Download the selected
  issues or all matches.
- **Linked issues** to 0-5 levels, following links, sub-tasks, parent/epic and/or an epic's issues; "Max issues"
  caps the walk. Attachments on or off (Settings: default and size limit); already downloaded attachments are kept.
- Output: `jira/<KEY>.md` (frontmatter with key, status, people, labels, sprints, links...; description, other
  fields, links, sub-tasks, attachments, comments) and `jira/<KEY>/` with attachments; inline images point at them.
- **Update** (selected or all downloaded): one search finds what changed in Jira since the download, only those are
  fetched again. A changed file is overwritten with `download_status: "changed"`, a `changes:` list in the
  frontmatter and a "Changed since the last download" note (fields, description, new/edited comments, attachments,
  custom fields, plus Jira's own history since then); `## Downloads` keeps the history. Unchanged files are not touched.

## Org chart (step 5)

The **Org** tab exports who reports to whom, with the Graph token of the Teams and Mail tabs (no admin rights):

- **Start from** one of your managers (the whole chain comes in one request) or search anyone by name or e-mail.
- **Levels below**: 1, 2, 3 or all; **managers above** adds the chain above that person.
- **Export org chart** writes `org/<date>_<name>.md` (an indented tree with job title, department and office, and the
  number of people under each manager) and `.csv` (one row per person: level, manager, job title, department, office,
  e-mail) to the export folder. The branch is walked level by level, 20 people per request: about 80 people a second.

Measured on the laptop first (`check.cmd -Only org`): the directory listed with managers returns none, and Active
Directory is not reachable, so the tree is always walked from a chosen person.

## Step 0: feasibility checker

Before building, `check.cmd` tests every assumption on the target laptop and writes a report that holds
only statuses, counts, HTTP codes and device names (no message content, names or subjects).

```
check.cmd                                    all checks (Whisper: cache scan only)
check.cmd -Only meeting                      run during a Teams call, then again outside one
check.cmd -Only graph,audio                  pick checks: env, whisper, meeting, graph, org, audio, ui
check.cmd -Only org                          org chart: which approach works here (Graph chain, branch walk plain vs $batch, directory with managers, Active Directory)
check.cmd -Only whisper -Whisper             install faster-whisper + CUDA libs (~1.5 GB) and test a load
check.cmd -WhisperVenv C:\path\to\.venv      reuse an existing venv with faster-whisper instead
```

| Check | Hypothesis |
|---|---|
| env | Runs without admin; Python starts from LOCALAPPDATA; GPU visible; Graph, PyPI, Hugging Face, GitHub reachable |
| whisper | faster-whisper models are already cached or downloadable; the model loads on the GPU and transcribes fast |
| meeting | Windows shows which app uses the microphone now (HKCU), so a Teams call can be detected; the calendar names it |
| graph | A Graph Explorer token reads chats (1:1, group, meeting), channels, mail threads + attachments, calendar, chat files, Teams transcripts |
| audio | Microphone ("Me") and system sound loopback ("Others") record at the same time, also on headphones |
| ui | Tray icon + menu, toast, local web page calling back, global Alt+Q, paste through the clipboard |

Needs Python 3.10+ (a per-user install or one installed earlier by another setup). Packages go to
`checks\.venv`; the Graph token is kept in `checks\.token`; recordings and reports in `checks\out\`
(all git-ignored). Reports: `checks\out\report-<time>.txt`.
