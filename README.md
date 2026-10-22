# powertools

Personal Windows toolkit (work in progress): dictation with local Whisper, meeting recording with
transcription, and exporting Teams chats and mail (Microsoft Graph) as Markdown sources for a wiki.
Everything runs in user space: no admin rights, PowerShell 5.1, private Python.

## Dictation (step 1)

Double-click `powertools.cmd`. The first start runs the setup (no admin rights): a Python with tkinter
(found or installed per-user), `.venv`, faster-whisper with the CUDA libraries (~1.5 GB), shortcuts on the
desktop, in the Start menu and in Startup (`setup\setup.ps1 -NoAutostart` to skip that one).

- **Alt+Q** (anywhere) starts recording; **Alt+Q** again stops, transcribes locally and pastes into the
  active window. The previous clipboard comes back afterwards.
- A small pill at the bottom of the screen shows the state: red dot + level bars while recording, amber
  dots while transcribing. The tray icon has the same colours.
- The window (tray icon > Open window) has the history, "Fix a word" (adds a replacement rule and the word
  to the vocabulary) and the settings: hotkey, model, language, microphone, vocabulary, replacements.
- Nothing leaves the laptop: audio is never saved, history is text only in
  `%LOCALAPPDATA%\powertools\history.sqlite`.

`powertools.cmd --console` runs with the log in the window; `powertools.cmd --setup` repeats the setup.

## Step 0: feasibility checker

Before building, `check.cmd` tests every assumption on the target laptop and writes a report that holds
only statuses, counts, HTTP codes and device names (no message content, names or subjects).

```
check.cmd                                    all checks (Whisper: cache scan only)
check.cmd -Only meeting                      run during a Teams call, then again outside one
check.cmd -Only graph,audio                  pick checks: env, whisper, meeting, graph, audio, ui
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
