"use strict";
const $ = (s) => document.querySelector(s);
const api = async (method, path, body) => {
  const r = await fetch(path, {
    method,
    headers: { "X-Powertools": "1", "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
};
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

// ---------------------------------------------------------------- tabs
document.querySelectorAll("nav button[data-tab]").forEach((b) =>
  b.addEventListener("click", () => {
    if (b.disabled) return;
    document.querySelectorAll("nav button, .tab").forEach((e) => e.classList.remove("active"));
    b.classList.add("active");
    $("#" + b.dataset.tab).classList.add("active");
    if (b.dataset.tab === "settings") loadSettings();
    if (b.dataset.tab === "diagnostics") loadDiagnostics();
    if (b.dataset.tab === "teams" || b.dataset.tab === "mail") { renderGraphBar(); loadJobs(); }
    if (b.dataset.tab === "teams" && !chats.length) loadChats(false);
    if (b.dataset.tab === "mail" && !foldersLoaded) loadFolders();
    if (b.dataset.tab === "meetings") loadMeetings();
  }));

// ---------------------------------------------------------------- status
let lastId = null;
const HINTS = {
  idle: (s) => `Press ${s.hotkey} anywhere to dictate.`,
  recording: (s) => `Listening... press ${s.hotkey} again to stop.`,
  transcribing: () => "Transcribing...",
  error: () => "Something went wrong, see below.",
};
async function poll() {
  let s;
  try { s = await api("GET", "/api/state"); } catch { return setTimeout(poll, 2000); }
  const e = s.engine;
  const shown = e.state === "ready" || s.dictation !== "idle" ? s.dictation : e.state === "error" ? "error" : "loading";
  const pill = $("#pill");
  pill.className = "pill " + shown;
  pill.textContent = shown;
  $("#hint").textContent = shown === "loading" ? `Loading Whisper ${e.model}...`
    : e.state === "error" && s.dictation === "idle" ? `Whisper failed: ${e.error || ""}` : (HINTS[shown] || (() => ""))(s);
  $("#toggle").classList.toggle("on", s.dictation === "recording");
  $("#engine-foot").innerHTML = `Whisper ${esc(e.model)}${e.device ? " · " + esc(e.device) : ""}<br>${esc(s.hotkey)} · v${esc(s.version)}`;
  $("#level").style.width = Math.min(100, s.level * 600) + "%";
  const last = s.last || {};
  const box = $("#last");
  if (last.error) { box.innerHTML = `<span class="err">${esc(last.error)}</span>`; }
  else if (last.id && last.id !== lastId) {
    lastId = last.id;
    box.innerHTML = `${esc(last.text)}<span class="meta">${last.audio_s}s → ${last.took_s}s · ${esc(last.language)}</span>`;
    loadHistory();
  }
  renderPrompt(s.pending_prompt);
  setTimeout(poll, s.dictation === "recording" ? 150 : 700);
}
$("#toggle").addEventListener("click", () => api("POST", "/api/toggle"));

// ---------------------------------------------------------------- history
async function loadHistory() {
  const items = await api("GET", "/api/history?limit=100&q=" + encodeURIComponent($("#search").value));
  const when = (ts) => {
    const d = new Date(ts * 1000), today = new Date().toDateString() === d.toDateString();
    return today ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : d.toLocaleDateString([], { day: "2-digit", month: "short" }) + " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  };
  $("#history").innerHTML = items.map((i) => `
    <li data-id="${i.id}" title="${esc(i.language)} · ${i.audio_s}s audio · ${i.took_s}s">
      <time>${when(i.ts)}</time>
      <div class="body"><div class="text">${esc(i.text)}</div></div>
      <span class="actions">
        <button class="icon" data-act="copy" title="Copy">${ICONS.copy}</button>
        <button class="icon" data-act="fix" title="Fix a word (adds a replacement rule)">${ICONS.fix}</button>
        <button class="icon" data-act="del" title="Delete">${ICONS.del}</button>
      </span>
    </li>`).join("") || `<li class="empty">Nothing yet. Press the hotkey anywhere and speak.</li>`;
}
$("#search").addEventListener("input", () => loadHistory());
const ICONS = {
  copy: '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/></svg>',
  fix: '<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13 7 4 4"/></svg>',
  del: '<svg viewBox="0 0 24 24"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>',
};
function toast(text) {
  const t = Object.assign(document.createElement("div"), { className: "toast", textContent: text });
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 1400);
}
$("#history").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("[data-act]");
  const li = ev.target.closest("li");
  if (!li) return;
  if (!btn) { if (!ev.target.closest("form")) li.classList.toggle("open"); return; }
  const act = btn.dataset.act;
  const text = li.querySelector(".text").textContent;
  if (act === "copy") { navigator.clipboard.writeText(text); toast("Copied"); }
  if (act === "del") { await api("DELETE", "/api/history/" + li.dataset.id); loadHistory(); }
  if (act === "fix" && !li.querySelector(".fix")) {
    const sel = window.getSelection().toString().trim();
    li.querySelector(".body").insertAdjacentHTML("beforeend", `<form class="fix"><input name="wrong" placeholder="heard" value="${esc(sel)}">
      <input name="right" placeholder="should be"><button class="primary">Add rule</button></form>`);
    li.querySelector(".fix").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = e.target;
      await api("POST", "/api/correction", { wrong: f.wrong.value, right: f.right.value });
      f.remove();
      toast("Rule added");
    });
  }
});

// ---------------------------------------------------------------- settings
async function loadSettings() {
  const [s, devices, md] = await Promise.all([api("GET", "/api/settings"), api("GET", "/api/devices"),
    api("GET", "/api/meetings/devices")]);
  const f = $("#settings-form");
  const opts = (list, cur, def) => `<option value="">${def}</option>` +
    [...new Set([...list, cur].filter(Boolean))].map((d) => `<option>${esc(d)}</option>`).join("");
  f.meeting_mic.innerHTML = opts(md.inputs, s.meeting_mic, "Windows default microphone");
  f.meeting_output.innerHTML = opts(md.outputs, s.meeting_output, "Windows default output");
  for (const k of ["meeting_mic", "meeting_output", "meeting_final_model", "meeting_keep_audio_days"]) f[k].value = s[k];
  f.meeting_prompt.checked = s.meeting_prompt;
  f.input_device.innerHTML = `<option value="">Windows default</option>` +
    [...new Set([...devices, s.input_device].filter(Boolean))].map((d) => `<option>${esc(d)}</option>`).join("");
  for (const k of ["hotkey", "model", "language", "input_device", "beam_size", "output_dir"]) f[k].value = s[k];
  for (const k of ["paste", "restore_clipboard", "open_window_on_start", "mail_attachments", "app_window"]) f[k].checked = s[k];
  f.vocabulary.value = s.vocabulary.join("\n");
  f.replacements.value = s.replacements.map((r) => `${r.from} => ${r.to}`).join("\n");
}
$("#settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const lines = (t) => t.split("\n").map((l) => l.trim()).filter(Boolean);
  const body = {
    hotkey: f.hotkey.value, model: f.model.value, language: f.language.value, input_device: f.input_device.value,
    beam_size: Number(f.beam_size.value), paste: f.paste.checked, restore_clipboard: f.restore_clipboard.checked,
    open_window_on_start: f.open_window_on_start.checked, app_window: f.app_window.checked, vocabulary: lines(f.vocabulary.value),
    output_dir: f.output_dir.value.trim(), mail_attachments: f.mail_attachments.checked,
    meeting_prompt: f.meeting_prompt.checked, meeting_mic: f.meeting_mic.value, meeting_output: f.meeting_output.value,
    meeting_final_model: f.meeting_final_model.value, meeting_keep_audio_days: Number(f.meeting_keep_audio_days.value),
    replacements: lines(f.replacements.value).filter((l) => l.includes("=>"))
      .map((l) => { const [a, ...b] = l.split("=>"); return { from: a.trim(), to: b.join("=>").trim() }; }),
  };
  try {
    const r = await api("POST", "/api/settings", body);
    $("#saved").textContent = r.changed.length ? "Saved: " + r.changed.join(", ") : "No changes.";
  } catch (err) { $("#saved").textContent = err.message; }
});

// ---------------------------------------------------------------- diagnostics
async function loadDiagnostics() {
  $("#diag").textContent = JSON.stringify(await api("GET", "/api/state"), null, 2);
  $("#log").textContent = (await api("GET", "/api/log?lines=200")).lines.join("\n");
}
$("#quit").addEventListener("click", async () => {
  if (!confirm("Quit powertools? The hotkey stops working until you start it again.")) return;
  await api("POST", "/api/quit");
  document.body.innerHTML = "<main><p>powertools has stopped. Start it again from the desktop shortcut.</p></main>";
});

// ---------------------------------------------------------------- Graph token bar (Teams + Mail)
async function renderGraphBar() {
  const s = await api("GET", "/api/graph/status");
  const status = s.has_token
    ? `<span class="ok">Connected${s.user ? " as " + esc(s.user) : ""}</span> <span>token valid ${s.expires_min} min</span>`
    : `<span class="warn">No valid token.</span>`;
  document.querySelectorAll(".graphbar").forEach((bar) => {
    bar.innerHTML = `${status}
      <input type="password" placeholder="Paste the access token from Graph Explorer" class="token-input">
      <button class="token-save">${s.has_token ? "Replace token" : "Connect"}</button>
      <a href="https://developer.microsoft.com/graph/graph-explorer" target="_blank" rel="noopener">Graph Explorer</a>`;
    bar.querySelector(".token-save").onclick = async () => {
      const token = bar.querySelector(".token-input").value.trim();
      if (!token) return;
      try {
        const r = await api("POST", "/api/graph/token", { token });
        renderGraphBar();
        if (r.resumed_jobs) loadJobs();
        if ($("#teams").classList.contains("active")) loadChats(true);
        if ($("#mail").classList.contains("active") && !foldersLoaded) loadFolders();
      } catch (e) { alert(e.message); }
    };
  });
}
async function graphCall(method, path, body) {
  try { return await api(method, path, body); }
  catch (e) { if (e.message === "token") { renderGraphBar(); throw new Error("Paste a fresh token above."); } throw e; }
}

// ---------------------------------------------------------------- range picker
const RANGES = [["days:2", "Last 2 days"], ["days:10", "Last 10 days"], ["days:30", "Last 30 days"],
  ["new", "Only new since last export"], ["since", "Since date..."], ["all", "Everything"]];
document.querySelectorAll(".range").forEach((el) => {
  el.innerHTML = `<select>${RANGES.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select>
    <input type="date" hidden>`;
  const sel = el.querySelector("select");
  if (el.dataset.for !== "mail-search") sel.value = "new";
  else sel.querySelector('option[value="new"]').remove();
  sel.onchange = () => { el.querySelector("input").hidden = sel.value !== "since"; };
});
function rangeOf(name) {
  const el = document.querySelector(`.range[data-for="${name}"]`);
  const v = el.querySelector("select").value;
  if (v.startsWith("days:")) return { kind: "days", days: Number(v.slice(5)) };
  if (v === "since") return { kind: "since", date: el.querySelector("input").value };
  return { kind: v };
}
const ago = (iso) => {
  if (!iso) return "";
  const d = new Date(iso), days = Math.floor((Date.now() - d) / 864e5);
  return days < 1 ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : days < 7 ? `${days} d ago` : d.toLocaleDateString();
};
const TYPES = { oneOnOne: "1:1", group: "group", meeting: "meeting" };

// ---------------------------------------------------------------- Teams
let chats = [];
async function loadChats(refresh) {
  $("#chats").innerHTML = `<tr><td colspan="5">Loading chats...</td></tr>`;
  try { chats = await graphCall("GET", "/api/teams/chats" + (refresh ? "?refresh=1" : "")); }
  catch (e) { $("#chats").innerHTML = `<tr><td colspan="5">${esc(e.message)}</td></tr>`; return; }
  renderChats();
}
function renderChats() {
  const q = $("#chat-filter").value.toLowerCase(), type = $("#chat-type").value;
  const rows = chats.filter((c) => (!type || c.type === type) &&
    (!q || c.title.toLowerCase().includes(q) || c.members.join(" ").toLowerCase().includes(q)));
  $("#chats").innerHTML = rows.map((c) => `<tr>
      <td><input type="checkbox" value="${esc(c.id)}"></td><td>${esc(c.title)}</td>
      <td class="muted">${TYPES[c.type] || esc(c.type)}</td><td class="muted">${ago(c.last)}</td>
      <td class="muted">${c.exported ? ago(c.exported) : ""}</td></tr>`).join("")
    || `<tr><td colspan="5">No chats match.</td></tr>`;
}
$("#chat-filter").addEventListener("input", renderChats);
$("#chat-type").addEventListener("change", renderChats);
$("#chat-refresh").addEventListener("click", () => loadChats(true));
$("#chat-all").addEventListener("change", (e) => document.querySelectorAll("#chats input").forEach((c) => (c.checked = e.target.checked)));
$("#chat-export").addEventListener("click", async () => {
  const ids = [...document.querySelectorAll("#chats input:checked")].map((c) => c.value);
  if (!ids.length) return alert("Select chats first.");
  try { await graphCall("POST", "/api/teams/export", { ids, range: rangeOf("teams") }); loadJobs(); }
  catch (e) { alert(e.message); }
});

// ---------------------------------------------------------------- Mail
let foldersLoaded = false, threads = [];
async function loadFolders() {
  try {
    const folders = await graphCall("GET", "/api/mail/folders");
    $("#mail-search").folder.innerHTML = `<option value="">All mail</option>` +
      folders.map((f) => `<option value="${esc(f.id)}">${esc(f.name)} (${f.total})</option>`).join("");
    foldersLoaded = true;
  } catch (e) { /* no token yet */ }
}
$("#mail-search").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  $("#threads").innerHTML = `<tr><td colspan="6">Searching...</td></tr>`;
  try {
    threads = await graphCall("POST", "/api/mail/threads",
      { folder: f.folder.value, person: f.person.value, subject: f.subject.value, range: rangeOf("mail-search") });
  } catch (err) { $("#threads").innerHTML = `<tr><td colspan="6">${esc(err.message)}</td></tr>`; return; }
  $("#threads").innerHTML = threads.map((t, i) => `<tr>
      <td><input type="checkbox" value="${i}"></td><td>${esc(t.subject)}${t.attachments ? " 📎" : ""}</td>
      <td class="muted">${esc(t.people.join(", "))}</td><td class="muted">${t.count}</td>
      <td class="muted">${ago(t.last)}</td><td class="muted">${t.exported ? ago(t.exported) : ""}</td></tr>`).join("")
    || `<tr><td colspan="6">Nothing found.</td></tr>`;
});
$("#mail-all").addEventListener("change", (e) => document.querySelectorAll("#threads input").forEach((c) => (c.checked = e.target.checked)));
$("#mail-export").addEventListener("click", async () => {
  const items = [...document.querySelectorAll("#threads input:checked")].map((c) => threads[Number(c.value)])
    .map((t) => ({ id: t.id, subject: t.subject }));
  if (!items.length) return alert("Select threads first.");
  try { await graphCall("POST", "/api/mail/export", { items, range: rangeOf("mail") }); loadJobs(); }
  catch (e) { alert(e.message); }
});

// ---------------------------------------------------------------- export jobs
let jobsTimer = null;
async function loadJobs() {
  const jobs = await api("GET", "/api/jobs");
  const html = jobs.length ? `<div class="row"><h2>Exports</h2><button class="open-out">Open export folder</button></div>` +
    jobs.map((j) => `<div class="card job ${j.state}">
      <div class="title"><b>${esc(j.title)}</b> · ${j.state.replace("_", " ")} ${j.total ? `· ${j.done}/${j.total}` : ""}</div>
      ${j.state === "running" || j.state === "queued" ? `<div class="bar"><span style="width:${j.total ? (100 * j.done / j.total) : 5}%"></span></div>` : ""}
      ${j.error ? `<div>${esc(j.error)}</div>` : ""}
      ${j.files.length || j.notes.length ? `<ul>${j.files.map((f) => `<li>${esc(f)}</li>`).join("")}
        ${j.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}</div>`).join("") : "";
  document.querySelectorAll(".jobs").forEach((el) => (el.innerHTML = html));
  document.querySelectorAll(".open-out").forEach((b) => (b.onclick = () => api("POST", "/api/open-output")));
  if (jobs.some((j) => j.state === "needs_token")) renderGraphBar();
  clearTimeout(jobsTimer);
  if (jobs.some((j) => j.state === "running" || j.state === "queued")) jobsTimer = setTimeout(loadJobs, 1500);
}

// ---------------------------------------------------------------- meetings
function renderPrompt(p) {
  const el = $("#prompt");
  if (!p || !p.title) { el.classList.add("hidden"); return; }
  if (!el.classList.contains("hidden")) return;
  el.innerHTML = `<b>Teams call</b><span class="grow">Record '${esc(p.title)}'? (${esc(p.detail)})</span>
    <button class="rec">Record</button><button class="no">Not now</button>`;
  el.classList.remove("hidden");
  el.querySelector(".rec").onclick = async () => { el.classList.add("hidden"); await api("POST", "/api/meetings/start", { from_prompt: true }); loadMeetings(); };
  el.querySelector(".no").onclick = () => { el.classList.add("hidden"); api("POST", "/api/meetings/dismiss"); };
}
const clock = (s) => [Math.floor(s / 3600), Math.floor(s / 60) % 60, Math.floor(s) % 60].map((n) => String(n).padStart(2, "0")).join(":");
let meetTimer = null, liveCount = -1;
async function loadMeetings() {
  clearTimeout(meetTimer);
  let m;
  try { m = await api("GET", "/api/meetings"); } catch { return; }
  const cur = m.current, now = $("#meeting-now"), live = $("#meeting-live");
  if (cur) {
    if (!now.querySelector(".timer")) {
      now.innerHTML = `<div class="meetbar"><span class="pill recording">recording</span><b class="mtitle"></b>
        <span class="timer"></span><span class="lv">Me <span class="meter"><span class="lme"></span></span></span>
        <span class="lv">Others <span class="meter"><span class="lot"></span></span></span></div>
        <div class="meetbar" style="margin-top:10px"><input class="note" placeholder="Note for this moment (optional)">
        <button class="mark">Mark moment</button><button class="stop rec">Stop</button></div>`;
      now.querySelector(".mark").onclick = async () => { await api("POST", "/api/meetings/mark", { note: now.querySelector(".note").value }); now.querySelector(".note").value = ""; };
      now.querySelector(".stop").onclick = async () => { await api("POST", "/api/meetings/stop"); setTimeout(loadMeetings, 500); };
    }
    now.querySelector(".mtitle").textContent = cur.title;
    now.querySelector(".timer").textContent = clock(Date.now() / 1000 - cur.started);
    now.querySelector(".lme").style.width = Math.min(100, m.levels.me * 600) + "%";
    now.querySelector(".lot").style.width = Math.min(100, m.levels.others * 600) + "%";
    live.classList.remove("hidden");
    if (cur.live.length !== liveCount) {
      liveCount = cur.live.length;
      const stick = live.scrollTop + live.clientHeight >= live.scrollHeight - 30;
      live.innerHTML = cur.live.map((s) => `<p><span class="t">${clock(s.start)}</span><span class="who">${s.speaker === "me" ? "Me" : "Others"}:</span> ${esc(s.text)}</p>`).join("")
        || "<p class='t'>The live transcript appears every 30 seconds.</p>";
      if (stick) live.scrollTop = live.scrollHeight;
    }
  } else {
    liveCount = -1;
    live.classList.add("hidden");
    if (!now.querySelector(".start")) {
      now.innerHTML = `<div class="meetbar"><input class="title" placeholder="Title (empty: from the calendar)">
        <button class="start rec">Start recording</button></div>
        <small>Records your microphone (Me) and what you hear (Others). Teams calls are noticed automatically.</small>`;
      now.querySelector(".start").onclick = async () => {
        try { await api("POST", "/api/meetings/start", { title: now.querySelector(".title").value }); loadMeetings(); }
        catch (e) { alert(e.message); }
      };
    }
  }
  const status = { done: "done", finalizing: "transcribing...", interrupted: "interrupted", recording: "recording" };
  $("#meeting-list").innerHTML = m.sessions.map((s) => `<li data-id="${esc(s.id)}">
      <div class="grow"><b>${esc(s.title)}</b><div class="muted">${new Date(s.started * 1000).toLocaleString()} ·
        ${Math.round((s.seconds || 0) / 60)} min · ${status[s.status] || esc(s.status)}${s.error ? " · " + esc(s.error) : ""}</div>
        ${s.md ? `<div class="muted">${esc(s.md)}</div>` : ""}</div>
      ${s.status === "interrupted" || s.status === "done" ? `<button data-act="final">Transcribe again</button>` : ""}
      <button data-act="del">Delete</button></li>`).join("") || "<li>No recordings yet.</li>";
  document.querySelectorAll(".open-out").forEach((b) => (b.onclick = () => api("POST", "/api/open-output")));
  if ($("#meetings").classList.contains("active")) meetTimer = setTimeout(loadMeetings, cur ? 1000 : 4000);
}
$("#meeting-list").addEventListener("click", async (e) => {
  const act = e.target.dataset.act, li = e.target.closest("li");
  if (!act || !li) return;
  if (act === "del" && !confirm("Delete this recording (audio and live transcript)? The exported Markdown stays.")) return;
  try {
    await api(act === "del" ? "DELETE" : "POST", `/api/meetings/${li.dataset.id}` + (act === "final" ? "/finalize" : ""));
  } catch (err) { alert(err.message); }
  loadMeetings();
});

poll();
loadHistory();
