# powertools

Personal Windows toolkit (work in progress): dictation with local Whisper, meeting recording with
transcription, exporting Teams chats and mail (Microsoft Graph) and Jira issues as Markdown sources for a wiki, and a
**Recap** for people who run many things at once through other people: what is mine, what I wait for, what changed, with
matters, drafts, meeting cards and a memory in Markdown. The language model is the local one (Ollama) or GitHub Copilot,
as you choose.
Everything runs in user space: no admin rights, PowerShell 5.1, private Python.

## Dictation (step 1)

Double-click `powertools.cmd` (a git clone, or the ZIP from GitHub unpacked anywhere: the setup turns a ZIP copy into a clone of github.com/brudnypiotr/powertools so Update works). The first start runs the setup (no admin rights): a Python with tkinter
(found or installed per-user), `.venv`, faster-whisper (with an NVIDIA GPU also the CUDA libraries, ~1.5 GB together,
else ~350 MB), shortcuts on the
desktop, in the Start menu and in Startup (`setup\setup.ps1 -NoAutostart` to skip that one).

- **Alt+Q** (anywhere) starts recording; **Alt+Q** again stops, transcribes locally and pastes into the
  active window. The previous clipboard comes back afterwards.
- A small pill at the bottom of the screen shows the state: red dot + level bars while recording, amber
  dots while transcribing (drawn with Win32 layered windows: no tkinter needed, never takes the focus).
  The tray icon has the same colours.
- Quit: tray icon > Quit, Diagnostics > Quit, or Ctrl+C in `powertools.cmd --console`.
- The window (tray icon > Open window) has the history, "Fix a word" (adds a replacement rule and the word
  to the vocabulary) and the settings: hotkey, model, language, microphone, vocabulary, replacements.
- Speech stays on the laptop: audio is never saved, history is text only in
  `%LOCALAPPDATA%\powertools\history.sqlite`. (Dictation clean-up, off by default, uses the model chosen in the AI tab.)

`powertools.cmd --console` runs with the log in the window; `powertools.cmd --setup` repeats the setup.

### Without a (big enough) GPU

Whisper tries the GPU in float16, then the GPU in the memory-saving int8_float16 mode (about a third less VRAM), then
the CPU (int8). It steps down when loading fails and also when a transcription fails on the GPU (out of memory), and
stays down. On the CPU the big models are too slow, so by default turbo runs as small and large-v3 as turbo
(Settings: "Smaller models without a usable GPU"). The footer and the Dictation tab say when it runs on the CPU.
Rough VRAM (float16): large-v3 about 4.5 GB, turbo 2-2.5 GB, small about 1 GB.

### Parakeet V3

Settings → Model → Parakeet V3: NVIDIA's parakeet-tdt-0.6b-v3 (25 European languages, Polish and English among
them) through onnx-asr, int8 on the CPU (~670 MB, downloaded from Hugging Face on the first load). Fast enough for
dictation without the GPU, which stays free for the meeting's Whisper. It detects the language itself and takes no
prompt: Vocabulary does nothing, Replacements still apply.

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
- "Me" muted = your microphone is not recorded: the recorder writes silence for Me and drops any Me text from
  those minutes. While recording, **Ctrl+Shift+M** (Teams' own mute key, passed on to Teams) mutes Teams and Me
  together; the Mute Me button (Meetings tab, tray) does it by hand. Muting with the mouse in Teams is not seen.
  Every recording starts unmuted. Where Teams' Third-party app API is allowed (Settings > Privacy), the real Teams
  mute button is followed instead.
- After the final transcript the local model (Local AI tab) writes a summary and the key points above the
  transcript in the same file; long meetings are summarized in parts first. "Summarize" in the list redoes it, also
  for older recordings.
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
- **Export org chart** writes to `org/` in the export folder: `<date>_<name>.html`, an **interactive chart** in one
  file (opens in any browser, offline, can be sent to anyone), `.md` (an indented tree for the wiki), `.csv` (one row
  per person for Excel) and `.json` (the chart's data). The branch is walked level by level, 20 people per request:
  about 80 people a second. The chart opens in the tab when the export is done; earlier ones under **Chart**.
- **The chart:** drag to move, wheel or +/− to zoom, click a person for their details and whom they report to, the
  number on a card to unfold that team. **Search down** finds everyone below by name, title, department, office or
  e-mail (all words must match), unfolds the way to them and dims the rest; Enter / ↓ ↑ go through the matches.
  **Departments** colour the cards; click one to light it up with the way to its heads.

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

## Local AI (step 5)

A small local model through [Ollama](https://github.com/ollama/ollama/releases/latest): the AI tab downloads the
official Windows ZIP from GitHub and unpacks it to `%LOCALAPPDATA%\powertools\ollama` (no installer, no admin rights).
With this model nothing leaves the laptop: powertools talks to Ollama on 127.0.0.1 only.

- **AI tab**: the NVIDIA GPU and its VRAM (total, free now), one-click local Ollama setup from the GitHub ZIP
  (or Start it when already unpacked), the installed models, Download buttons for `gemma4:e2b` and `gemma4:e4b`, and
  **Test the model**: it loads the model, corrects a short English message once and reports tokens/s, load time, how
  much of the model sits on the GPU and the VRAM before/after, with a verdict (fast / usable / slow).
- **GPU sharing** (6 GB): before Whisper loads a model on the GPU it asks Ollama to unload; a server started by
  powertools keeps 1 GB of VRAM free (`OLLAMA_GPU_OVERHEAD`); Settings > "CPU only" leaves the GPU to
  Whisper; Parakeet V3 for dictation leaves the GPU to the model. "Keep the model loaded" (default 5 min).
- **Dictation clean-up** (Settings, off by default): after Whisper the model removes fillers and repetitions and fixes
  punctuation. An answer much shorter or longer than the transcript, an error or a timeout keeps the transcript. The
  raw transcript stays in the history (click a row).
- **Wording** (**Alt+W**, Settings > Wording hotkey): select an English text anywhere and press it. The selection is
  copied (the clipboard comes back), the window opens on the **Writing** tab with the mistakes (wrong → better · why)
  and a better version to edit and copy. "Replace the selection right away" pastes the better version over the
  selection instead. The Writing tab also checks pasted text. Ctrl+W works too, but then it no longer closes tabs.

## GitHub Copilot (optional)

AI tab > "Where the model runs". Never a default: until you choose it, everything stays with the local model. With
Copilot the text of mail, chats, Jira issues and meeting summaries is sent to GitHub Copilot; the menu always shows
which model is used. **Prerequisite: a GitHub account with a Copilot licence.**

- Sign-in: a fine-grained GitHub token with **Copilot Requests** (steps in the AI tab, kept with DPAPI), else the user
  already signed in to the Copilot CLI. The SDK downloads its runtime from GitHub on first use and checks its SHA-256.
- Every question is a session of its own without tools, skills, MCP or custom instructions, in an empty folder, deleted
  afterwards. The model can only answer with text.
- The AI tab counts calls and AI credits by task; Settings has a daily limit (default 80). **Compare** sorts
  conversations of the last updates on the other model: how often it agrees with you, how long, how many credits.

## Recap

First start: five questions (role, what you run, who you must not miss, what came too late, what must never be read).
Then one button, **Update**: mail (Inbox with subfolders, and Sent), Teams chats, Jira issues you are assigned to, watch
or reported, the calendar of the next week and finished meeting summaries; only what is new since the last full Update.

The window is a list on the left and one thing on the right, with at most one filled button.

- **Today**: *For me* (decide, do, answer), *Waiting* ("quiet for N days", also threads only you wrote in), *Changed*
  (with Ignored and Done and later). An item shows who, when, its matter and the sentence that decided it; its draft sits
  under it. **Done**, **Later** and **This is wrong** (not mine, another group, ignore the conversation) can be undone, the
  next item opens by itself, and what you correct goes into the next prompts. The morning brief is the first row;
  *Write for me*: today in 5 lines, the week in 10, for my manager, I was away.
- **Ask** is the field above: answers from the memory and the items, with sources. Say what is different ("no, the deadline
  is Thursday") and it changes the memory file, marked `(you)`, with Undo. A change that does not come from your words is
  only proposed.
- **Drafts** are written in the app and stay there; say what should change, and it also proposes a style rule you can
  accept. **Nothing is ever sent**: the Graph client refuses every write except a reply put into the Outlook *Drafts* folder
  (needs Mail.ReadWrite in the token), and only when you click it.
- **Matters** (a project, a case, a problem): created only by you; Today suggests. A conversation belongs to a matter by its
  Jira key or thread, else by the model when a word of the matter's name is in the text, else it asks. Running, parked
  (comes back on a date), closed. **People** (your direct reports and anyone you add) and the memory **Files** are here too:
  what is open both ways, your notes, a 1:1 card; no scores, no response times.
- **Meetings**: *Ahead* ("Prepare me" makes a card: goal, open from before, what to ask, agenda) and *Recorded*.
- **Bottom left**: the model in use and its tokens and credits today, how long the Graph token (and, with Copilot, the GitHub
  token) is valid, and the version (About, Update). The Graph token is asked for when it is needed: an Update without one
  waits and goes on by itself when the token is there, also when it runs out in the middle.

Memory: Markdown in `Documents\powertools\memory` (Settings > Memory folder), readable and editable without the app.
Lines with `(you)` are never overwritten; every write can be undone. Items and their text are in
`%LOCALAPPDATA%\powertools\assistant.json`. **Never read** (Settings): people, words in a title, `folder: Name`; checked
before anything is kept or shown to a model. Quotes and promises the model gives are checked against the source word by
word. **I have an idea** opens a filled-in GitHub issue; you read it and submit it there.

## Your own copy for an organisation

See [docs/DISTRIBUTION.md](docs/DISTRIBUTION.md).

