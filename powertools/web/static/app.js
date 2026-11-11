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
    if (b.dataset.tab === "org") { renderGraphBar(); loadJobs(); loadOrgSaved(); if (!orgChain.length) loadOrgChain(); }
    if (b.dataset.tab === "writing") loadWording();
    if (b.dataset.tab === "recap") { renderGraphBar(); loadRecap(); loadJobs(); }
    if (b.dataset.tab === "ai") loadAI();
    if (b.dataset.tab === "jira") { renderJiraBar(false); loadJiraDownloaded(); loadJobs(); loadJiraFields(); }
  }));

// ---------------------------------------------------------------- status
let lastId = null;
const HINTS = {
  idle: (s) => `Press ${s.hotkey} anywhere to dictate.`,
  recording: (s) => `Listening... press ${s.hotkey} again to stop.`,
  transcribing: () => "Transcribing...",
  error: () => "Something went wrong, see below.",
};
let failures = 0, updating = false;
function stopped() {
  document.body.innerHTML = `<main class="main"><p class="muted">powertools has stopped. Start it again from the
    shortcut or the Start menu.</p></main>`;
  window.close();  // allowed for an app window opened with one page
}
async function poll() {
  let s;
  try { s = await api("GET", "/api/state"); failures = 0; }
  catch {
    if (++failures >= 3 && !updating) return stopped();  // the app quit (tray > Quit): close this window too
    return setTimeout(poll, 2000);
  }
  const e = s.engine;
  const shown = e.state === "ready" || s.dictation !== "idle" ? s.dictation : e.state === "error" ? "error" : "loading";
  const pill = $("#pill");
  pill.className = "pill " + shown;
  pill.textContent = shown;
  $("#hint").textContent = shown === "loading" ? `Loading ${e.model}...`
    : e.state === "error" && s.dictation === "idle" ? `Model failed: ${e.error || ""}`
    : (HINTS[shown] || (() => ""))(s) + (shown === "idle" && e.note ? ` (${e.note})` : "");
  $("#toggle").classList.toggle("on", s.dictation === "recording");
  $("#engine-foot").innerHTML = `${esc(e.model)}${e.device ? " · " + esc(e.device === "cpu" ? "CPU" : e.compute === "int8_float16" ? "cuda (saving)" : e.device) : ""}<br>${esc(s.hotkey)} · v${esc(s.version)}`;
  $("#engine-foot").title = e.note ? e.note : "About, changelog and updates";
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
  gateLocalAI(s.llm);
  followFocus(s);
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
      <div class="body"><div class="text">${esc(i.text)}</div>${i.raw && i.raw !== i.text
        ? `<div class="raw muted small">Whisper: ${esc(i.raw)}</div>` : ""}</div>
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
  f.notifications.checked = s.notifications;
  f.input_device.innerHTML = `<option value="">Windows default</option>` +
    [...new Set([...devices, s.input_device].filter(Boolean))].map((d) => `<option>${esc(d)}</option>`).join("");
  for (const k of ["hotkey", "model", "language", "input_device", "beam_size", "output_dir", "jira_url", "jira_email",
    "jira_max_attachment_mb", "llm_gpu", "llm_keep_alive_min", "wording_hotkey", "wording_mode"]) f[k].value = s[k];
  for (const k of ["paste", "restore_clipboard", "open_window_on_start", "mail_attachments", "app_window", "jira_attachments",
    "small_on_cpu", "dictation_cleanup"]) f[k].checked = s[k];
  const llmModels = (installed, suggested) => {
    const names = [...new Set([...installed, ...suggested, s.llm_model].filter(Boolean))];
    f.llm_model.innerHTML = names.map((m) => `<option value="${esc(m)}">${esc(m)}${installed.includes(m) ? "" : " (not downloaded)"}</option>`).join("");
    f.llm_model.value = s.llm_model;
  };
  llmModels([s.llm_model], []);
  api("GET", "/api/llm/status").then((st) => llmModels((st.models || []).map((m) => m.name), st.suggested || [])).catch(() => {});
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
    open_window_on_start: f.open_window_on_start.checked, small_on_cpu: f.small_on_cpu.checked, app_window: f.app_window.checked, vocabulary: lines(f.vocabulary.value),
    output_dir: f.output_dir.value.trim(), mail_attachments: f.mail_attachments.checked,
    jira_url: f.jira_url.value.trim(), jira_email: f.jira_email.value.trim(), jira_attachments: f.jira_attachments.checked,
    jira_max_attachment_mb: Number(f.jira_max_attachment_mb.value),
    meeting_prompt: f.meeting_prompt.checked, notifications: f.notifications.checked, meeting_mic: f.meeting_mic.value, meeting_output: f.meeting_output.value,
    meeting_final_model: f.meeting_final_model.value, meeting_keep_audio_days: Number(f.meeting_keep_audio_days.value),
    llm_model: f.llm_model.value, llm_gpu: f.llm_gpu.value, llm_keep_alive_min: Number(f.llm_keep_alive_min.value),
    dictation_cleanup: f.dictation_cleanup.checked, wording_hotkey: f.wording_hotkey.value, wording_mode: f.wording_mode.value,
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
  stopped();
});

// ---------------------------------------------------------------- Graph token bar (Teams + Mail)
async function renderGraphBar() {
  const s = await api("GET", "/api/graph/status");
  const status = s.has_token
    ? `<span class="ok">Connected${s.user ? " as " + esc(s.user) : ""}</span> <span>token valid ${s.expires_min} min</span>`
    : `<span class="warn">No valid token.</span>`;
  document.querySelectorAll(".graphbar").forEach((bar) => {
    bar.innerHTML = `${status}
      <button class="token-get primary">${s.has_token ? "Get a new token" : "Get token"}</button>
      <input type="password" placeholder="...or paste the access token here" class="token-input">
      <button class="token-save">${s.has_token ? "Replace token" : "Connect"}</button>
      ${s.watch && s.watch.active ? `<div class="token-wait">Waiting for the token: in the Graph Explorer window sign in if asked,
        open the <b>Access token</b> tab and click its copy button. It is saved here automatically.</div>` : ""}`;
    bar.querySelector(".token-get").onclick = async () => {
      try { await api("POST", "/api/graph/token/fetch"); watchGraphToken(s.has_token ? s.expires_min : -1); renderGraphBar(); }
      catch (e) { alert(e.message); }
    };
    bar.querySelector(".token-save").onclick = async () => {
      const token = bar.querySelector(".token-input").value.trim();
      if (!token) return;
      try {
        const r = await api("POST", "/api/graph/token", { token });
        renderGraphBar();
        if (r.resumed_jobs) loadJobs();
        if ($("#teams").classList.contains("active")) loadChats(true);
        if ($("#mail").classList.contains("active") && !foldersLoaded) loadFolders();
        if ($("#org").classList.contains("active")) loadOrgChain();
      } catch (e) { alert(e.message); }
    };
  });
}
let graphWatch = null;
function watchGraphToken(before) {  // poll until the copied token is caught (or the 5 minutes run out)
  clearTimeout(graphWatch);
  graphWatch = setTimeout(async () => {
    const s = await api("GET", "/api/graph/status");
    if (s.watch.active) return watchGraphToken(before);
    renderGraphBar();
    if (s.watch.result === "saved") {
      toast("Token saved");
      loadJobs();
      if ($("#teams").classList.contains("active")) loadChats(true);
      if ($("#mail").classList.contains("active") && !foldersLoaded) loadFolders();
      if ($("#org").classList.contains("active")) loadOrgChain();
    } else if (s.watch.result && s.watch.result !== "timeout") alert(s.watch.result);
  }, 1500);
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

// ---------------------------------------------------------------- Jira
async function renderJiraBar(check) {
  const bar = $(".jirabar");
  if (check) bar.querySelectorAll("button").forEach((b) => (b.disabled = true));
  const busy = bar.querySelector(".token-check");
  if (check && busy) busy.textContent = "Checking...";
  let s;
  try { s = await api("GET", "/api/jira/status" + (check ? "?check=1" : "")); }
  catch (e) { bar.textContent = e.message; return; }
  const status = !s.site ? `<span class="warn">No Jira site: set it in Settings > Jira.</span>`
    : !s.has_token ? `<span class="warn">No Jira token: paste one here.</span>`
    : s.error ? `<span class="warn">${esc(s.error)}</span>`
    : `<span class="ok">Connected${s.user ? " as " + esc(s.user) : ""}</span>`;
  const when = s.checked_at ? ` · checked ${new Date(s.checked_at * 1000).toLocaleTimeString()}` : "";
  const mode = s.site ? (s.cloud ? "Jira Cloud: e-mail + API token" : "Jira Data Center: personal access token") : "";
  const rows = (s.checks || []).map((c) => `<tr><td class="${c.ok ? "ok" : c.ok === false ? "warn" : "muted"}">${
    c.ok ? "✓" : c.ok === false ? "✗" : "·"}</td><td>${esc(c.source)}</td><td class="muted">${esc(c.token)}</td>
    <td class="muted">${esc(c.email)}</td><td>${esc(c.detail)}</td></tr>`).join("");
  bar.innerHTML = `<div class="jira-line">${status} <span class="muted">${esc(s.site)} · ${esc(mode)}${when}</span></div>
    ${rows ? `<table class="list checks"><thead><tr><th></th><th>Token from</th><th>Token</th><th>E-mail</th><th>Result</th></tr></thead><tbody>${rows}</tbody></table>` : ""}
    <div class="jira-line"><input type="password" placeholder="Paste a Jira API token (Cloud) or personal access token (Data Center)" class="token-input">
    <button class="token-save">${s.token_source === "saved in powertools" ? "Replace token" : "Save token"}</button><button class="token-check">Check</button>
    ${s.token_source === "saved in powertools" ? `<button class="token-clear">Forget token</button>` : ""}</div>`;
  bar.querySelector(".token-check").onclick = () => renderJiraBar(true);
  bar.querySelector(".token-save").onclick = async () => {
    const input = bar.querySelector(".token-input"), token = input.value.trim();
    if (!token) { input.focus(); return toast("Paste a token into the box first"); }
    const btn = bar.querySelector(".token-save");
    btn.disabled = true; btn.textContent = "Checking...";
    try { await api("POST", "/api/jira/token", { token }); toast("Token saved"); }
    catch (e) { alert(e.message); }
    renderJiraBar(false);
  };
  const clr = bar.querySelector(".token-clear");
  if (clr) clr.onclick = async () => { await api("DELETE", "/api/jira/token"); renderJiraBar(false); };
  if (check) toast(s.error ? "Check failed, see the table" : "Jira accepted the token");
}
let jiraFieldsLoaded = false, jiraLast = { jql: "", issues: [] }, jiraDone = [];
async function loadJiraFields() {
  if (jiraFieldsLoaded) return;
  try {
    const fields = await api("GET", "/api/jira/fields");
    $("#jira-field-names").innerHTML = fields.map((f) => `<option value="${esc(f.name)}">${esc(f.id)}</option>`).join("");
    jiraFieldsLoaded = true;
  } catch { /* not connected yet */ }
}
const OPS = ["=", "!=", "~", "!~", ">", ">=", "<", "<=", "in", "not in", "is", "is not"];
$("#jira-add-filter").addEventListener("click", () => {
  const row = document.createElement("div");
  row.className = "filter";
  row.innerHTML = `<input class="f-field" list="jira-field-names" placeholder="field, e.g. Fix versions">
    <select class="f-op">${OPS.map((o) => `<option>${o}</option>`).join("")}</select>
    <input class="f-value" placeholder="value (in: a, b; is: EMPTY)"><button type="button" class="icon" title="Remove">✕</button>`;
  row.querySelector("button").onclick = () => row.remove();
  $("#jira-filters").appendChild(row);
});
function jiraOptions() {
  const o = $(".jira-opts");
  return { depth: Number(o.querySelector("[name=depth]").value),
    follow: [...o.querySelectorAll("[name=follow]:checked")].map((c) => c.value),
    attachments: o.querySelector("[name=attachments]").checked,
    max_issues: Number(o.querySelector("[name=max_issues]").value) };
}
async function jiraDownload(seed) {
  try { await api("POST", "/api/jira/download", { seed, options: jiraOptions() }); loadJobs(); watchJira(); }
  catch (e) { alert(e.message); }
}
$("#jira-quick").addEventListener("submit", (e) => {
  e.preventDefault();
  const f = e.target;
  if (!f.value.value.trim()) return;
  jiraDownload({ mode: f.mode.value, value: f.value.value });
});
$("#jira-search").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const form = { project: f.project.value, epic: f.epic.value, labels: f.labels.value, text: f.text.value,
    updated_days: f.updated_days.value ? Number(f.updated_days.value) : 0, jql: f.jql.value,
    filters: [...document.querySelectorAll("#jira-filters .filter")].map((r) => ({
      field: r.querySelector(".f-field").value, op: r.querySelector(".f-op").value, value: r.querySelector(".f-value").value })) };
  $("#jira-results").innerHTML = `<tr><td colspan="7">Searching...</td></tr>`;
  try { jiraLast = await api("POST", "/api/jira/search", { form }); }
  catch (err) { $("#jira-results").innerHTML = `<tr><td colspan="7">${esc(err.message)}</td></tr>`; $("#jira-jql").textContent = ""; return; }
  $("#jira-jql").textContent = `JQL: ${jiraLast.jql} · ${jiraLast.issues.length} issue(s)`;
  $("#jira-results").innerHTML = jiraLast.issues.map((i) => `<tr>
      <td><input type="checkbox" value="${esc(i.key)}"></td><td>${esc(i.key)}</td><td>${esc(i.summary)}</td>
      <td class="muted">${esc(i.type)}</td><td class="muted">${esc(i.status)}</td><td class="muted">${ago(i.updated)}</td>
      <td class="muted">${i.downloaded ? ago(i.downloaded) + (i.changed ? ' <span class="warn">changed</span>' : "") : ""}</td></tr>`).join("")
    || `<tr><td colspan="7">Nothing found.</td></tr>`;
});
$("#jira-all").addEventListener("change", (e) => document.querySelectorAll("#jira-results input").forEach((c) => (c.checked = e.target.checked)));
$("#jira-dl-sel").addEventListener("click", () => {
  const keys = [...document.querySelectorAll("#jira-results input:checked")].map((c) => c.value);
  if (!keys.length) return alert("Select issues first.");
  jiraDownload({ mode: "keys", value: keys.join(",") });
});
$("#jira-dl-all").addEventListener("click", () => {
  if (!jiraLast.jql) return alert("Search first.");
  jiraDownload({ mode: "jql", value: jiraLast.jql });
});
async function loadJiraDownloaded() {
  try { jiraDone = await api("GET", "/api/jira/downloaded"); } catch { jiraDone = []; }
  renderJiraDownloaded();
}
function renderJiraDownloaded() {
  const q = $("#jira-dl-filter").value.toLowerCase();
  const rows = jiraDone.filter((r) => !q || (r.key + " " + r.summary + " " + r.status).toLowerCase().includes(q));
  $("#jira-downloaded").innerHTML = rows.map((r) => `<tr>
      <td><input type="checkbox" value="${esc(r.key)}"></td><td>${esc(r.key)}</td><td>${esc(r.summary)}</td>
      <td class="muted">${esc(r.status)}</td><td class="muted">${ago(r.updated)}</td><td class="muted">${ago(r.downloaded)}</td>
      <td class="${r.last_result === "changed" ? "warn" : "muted"}">${esc(r.last_result)}</td></tr>`).join("")
    || `<tr><td colspan="7">Nothing downloaded yet.</td></tr>`;
}
$("#jira-dl-filter").addEventListener("input", renderJiraDownloaded);
$("#jira-dl-check").addEventListener("change", (e) => document.querySelectorAll("#jira-downloaded input").forEach((c) => (c.checked = e.target.checked)));
async function jiraRefresh(keys) {
  try {
    await api("POST", "/api/jira/refresh", { keys, options: { attachments: $(".jira-opts [name=attachments]").checked } });
    loadJobs(); watchJira();
  } catch (e) { alert(e.message); }
}
$("#jira-refresh-sel").addEventListener("click", () => {
  const keys = [...document.querySelectorAll("#jira-downloaded input:checked")].map((c) => c.value);
  if (!keys.length) return alert("Select downloaded issues first.");
  jiraRefresh(keys);
});
$("#jira-refresh-all").addEventListener("click", () => jiraRefresh([]));
let jiraWatch = null;
function watchJira() {  // refresh the Downloaded list while a Jira job runs
  clearTimeout(jiraWatch);
  jiraWatch = setTimeout(async () => {
    const jobs = await api("GET", "/api/jobs");
    loadJiraDownloaded();
    if (jobs.some((j) => j.title.startsWith("Jira") && (j.state === "running" || j.state === "queued"))) watchJira();
  }, 2000);
}

// ---------------------------------------------------------------- org chart
let orgChain = [], orgRoot = null;
const personLine = (p) => `<b>${esc(p.name)}</b>${p.title ? ` <span class="muted">${esc(p.title)}</span>` : ""}`;
async function loadOrgChain() {
  try { orgChain = await graphCall("GET", "/api/org/chain"); }
  catch (e) { $("#org-chain").textContent = e.message; return; }
  $("#org-chain").innerHTML = orgChain.map((p, i) =>
    `<button type="button" data-i="${i}" title="${esc(p.department)}">${i ? "↑" + i + " " : ""}${esc(p.name)}</button>`).join(" ");
  $("#org-chain").querySelectorAll("button").forEach((b) => (b.onclick = () => pickOrg(orgChain[Number(b.dataset.i)])));
}
function pickOrg(p) {
  orgRoot = p;
  $("#org-root").innerHTML = `From ${personLine(p)}${p.department ? ` · ${esc(p.department)}` : ""}`;
}
$("#org-search").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = e.target.q.value.trim();
  if (q.length < 2) return;
  $("#org-found").innerHTML = `<li class="muted">Searching...</li>`;
  let found;
  try { found = await graphCall("GET", "/api/org/search?q=" + encodeURIComponent(q)); }
  catch (err) { $("#org-found").innerHTML = `<li>${esc(err.message)}</li>`; return; }
  $("#org-found").innerHTML = found.map((p, i) => `<li><button type="button" data-i="${i}">Pick</button> ${personLine(p)}
    ${p.department ? `<span class="muted">· ${esc(p.department)}</span>` : ""}</li>`).join("") || `<li class="muted">Nobody found.</li>`;
  $("#org-found").querySelectorAll("button").forEach((b) => (b.onclick = () => pickOrg(found[Number(b.dataset.i)])));
});
$("#org-export").addEventListener("click", async () => {
  if (!orgRoot) return alert("Pick a person first: one of your managers or a search result.");
  try {
    const job = await graphCall("POST", "/api/org/export", { id: orgRoot.id, depth: Number($("#org-depth").value), above: $("#org-above").checked });
    loadJobs(); waitOrgJob(job.id);
  } catch (e) { alert(e.message); }
});
function waitOrgJob(id) {  // open the chart as soon as the export is done
  setTimeout(async () => {
    const j = (await api("GET", "/api/jobs")).find((x) => x.id === id);
    if (!j) return;
    if (j.state === "running" || j.state === "queued" || j.state === "needs_token") return waitOrgJob(id);
    if (j.state === "done" && j.result) { await loadOrgSaved(); openOrg(j.result); }
  }, 1000);
}
async function loadOrgSaved() {
  const list = await api("GET", "/api/org/saved");
  const sel = $("#org-saved"), keep = sel.value;
  sel.innerHTML = `<option value="">Open a saved chart...</option>` + list.map((c) =>
    `<option value="${esc(c.name)}">${esc(c.root)} · ${c.people} people · ${esc(c.generated)}</option>`).join("");
  sel.value = keep;
}
async function openOrg(name) {
  if (!name) return;
  $("#org-saved").value = name;
  try { OrgChart($("#org-view"), await api("GET", "/api/org/saved/" + encodeURIComponent(name))); }
  catch (e) { $("#org-view").textContent = e.message; }
}
$("#org-saved").addEventListener("change", (e) => openOrg(e.target.value));

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
const openMeetings = new Set();  // meetings whose details are unfolded (kept across refreshes)
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
  const hm = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const mins = (sec) => sec >= 3600 ? `${Math.floor(sec / 3600)} h ${Math.round(sec % 3600 / 60)} min` : `${Math.max(1, Math.round(sec / 60))} min`;
  $("#meeting-list").innerHTML = m.sessions.map((s) => {
    const people = s.attendees || [], st = s.stats || {}, me = st.me || {}, ot = st.others || {};
    const talk = (me.seconds || 0) + (ot.seconds || 0);
    const share = talk ? ` · you spoke ${Math.round(100 * (me.seconds || 0) / talk)}%` : "";
    const open = openMeetings.has(s.id);
    return `<li data-id="${esc(s.id)}" class="${open ? "open" : ""}">
      <div class="grow"><div class="mhead"><b>${esc(s.title)}</b> <span class="pill ${s.status === "done" ? "ready" : s.status === "finalizing" ? "transcribing" : "error"}">${status[s.status] || esc(s.status)}</span></div>
        <div class="muted">${new Date(s.started * 1000).toLocaleDateString()} ${hm(s.started)}–${hm(s.started + (s.seconds || 0))} · ${mins(s.seconds || 0)}
          · ${people.length ? `${people.length} invited` : "attendees unknown"}${share}</div>
        ${s.error ? `<div class="warn">${esc(s.error)}</div>` : ""}
        <div class="mdetail">
          ${s.organizer ? `<div><span class="muted">Organizer</span> ${esc(s.organizer)}</div>` : ""}
          ${people.length ? `<div><span class="muted">Invited</span> ${people.map(esc).join(", ")}</div>`
            : `<div class="muted">No attendees yet. <button data-act="cal">Fill from calendar</button> (needs the Graph token from the Teams tab)</div>`}
          ${talk ? `<div><span class="muted">Speaking</span> Me ${mins(me.seconds || 0)} (${me.words || 0} words) · Others ${mins(ot.seconds || 0)} (${ot.words || 0} words)</div>` : ""}
          ${(s.marks || []).length ? `<div><span class="muted">Marked moments</span> ${s.marks.length}</div>` : ""}
          ${s.transcript_model ? `<div><span class="muted">Transcript</span> ${esc(s.transcript_model)}</div>` : ""}
          ${s.devices ? `<div><span class="muted">Devices</span> mic: ${esc(s.devices.mic || "default")} · others: ${esc(s.devices.output || "default")}</div>` : ""}
          ${s.invite ? `<details><summary class="muted">Invitation</summary><pre>${esc(s.invite)}</pre></details>` : ""}
          ${s.md ? `<div class="muted small">${esc(s.md)}</div>` : ""}
        </div></div>
      <div class="mact">
        ${s.md ? `<button data-act="open">Open</button>` : ""}
        ${(s.status === "interrupted" || s.status === "done") && s.audio ? `<button data-act="final">Transcribe again</button>` : ""}
        <button data-act="del">Delete</button></div></li>`;
  }).join("") || "<li>No recordings yet.</li>";
  document.querySelectorAll(".open-out").forEach((b) => (b.onclick = () => api("POST", "/api/open-output")));
  if ($("#meetings").classList.contains("active")) meetTimer = setTimeout(loadMeetings, cur ? 1000 : 4000);
}
$("#meeting-list").addEventListener("click", async (e) => {
  const act = e.target.dataset.act, li = e.target.closest("li");
  if (!li || !li.dataset.id) return;
  if (!act) {  // click on the row: show / hide the details
    if (e.target.closest("details, a")) return;
    li.classList.toggle("open");
    li.classList.contains("open") ? openMeetings.add(li.dataset.id) : openMeetings.delete(li.dataset.id);
    return;
  }
  if (act === "open") { try { await api("POST", `/api/meetings/${li.dataset.id}/open`, {}); } catch (err) { alert(err.message); } return; }
  if (act === "cal") {
    try { const r = await api("POST", `/api/meetings/${li.dataset.id}/calendar`, {});
      if (!r.calendar) alert("No Teams meeting on your calendar at that time (an ad-hoc call has no attendee list)."); }
    catch (err) { alert(err.message); }
    loadMeetings(); return;
  }
  if (act === "del" && !confirm("Delete this recording (audio and live transcript)? The exported Markdown stays.")) return;
  try {
    await api(act === "del" ? "DELETE" : "POST", `/api/meetings/${li.dataset.id}` + (act === "final" ? "/finalize" : ""));
  } catch (err) { alert(err.message); }
  loadMeetings();
});

$("#update-all").onclick = async () => {
  const b = $("#update-all"); b.disabled = true;
  try { const r = await api("POST", "/api/meetings/update-all", {});
    alert(`${r.updated} transcripts updated, ${r.with_attendees} with attendees from the calendar.` +
          (r.token ? "" : " Set a Graph token in the Teams tab to fill in the attendees."));
  } catch (err) { alert(err.message); }
  b.disabled = false; loadMeetings();
};

// ---------------------------------------------------------------- writing (wording hotkey + pasted text)
let focusSeq = null, wordingSeen = null;
function gateLocalAI(llm) {  // Writing and Recap need Ollama: greyed out until it is installed
  const ok = !llm || llm.installed || llm.running;
  for (const tab of ["writing", "recap"]) {
    const b = document.querySelector(`nav button[data-tab="${tab}"]`);
    b.disabled = !ok;
    b.title = ok ? "" : "Needs Ollama: see the Local AI tab";
    if (!ok && b.classList.contains("active")) showTab("ai");
  }
}
function showTab(name) { const b = document.querySelector(`nav button[data-tab="${name}"]`); if (b && !b.classList.contains("active")) b.click(); }
function followFocus(s) {
  const f = s.focus || {}, w = s.wording || {};
  if (focusSeq === null ? w.state === "working" : f.seq !== focusSeq) { if (f.tab) showTab(f.tab); }
  focusSeq = f.seq;
  const seen = `${w.id}:${w.state}`;
  if (w.id && seen !== wordingSeen) { wordingSeen = seen; if ($("#writing").classList.contains("active")) loadWording(); }
}
async function loadWording() {
  const st = await api("GET", "/api/state");
  $("#w-hint").innerHTML = `Select an English text anywhere and press <b>${esc(st.wording_hotkey)}</b>, or paste it here.`;
  renderWording(await api("GET", "/api/wording"));
}
function renderWording(w) {
  const box = $("#w-result");
  if (!w || !w.state) { box.innerHTML = ""; return; }
  if (w.original && w.original !== $("#w-text").value && w.mode !== "window") $("#w-text").value = w.original;
  if (w.state === "working") { $("#w-status").textContent = "Checking with the local model..."; box.innerHTML = ""; return; }
  $("#w-status").textContent = w.stats ? `${w.stats.model} · ${w.stats.total_s} s` : "";
  if (w.state === "error") { box.innerHTML = `<div class="card"><span class="err">${esc(w.error)}</span></div>`; return; }
  const issues = w.issues.length ? `<ul class="issues">${w.issues.map((i) => `<li><span class="wrong">${esc(i.wrong)}</span> →
      <span class="better">${esc(i.better)}</span><span class="muted"> · ${esc(i.why)}</span></li>`).join("")}</ul>`
    : `<div class="ok">${w.language && !/^en/i.test(w.language) ? `Not English (${esc(w.language)}): nothing to correct.` : "No mistakes found."}</div>`;
  box.innerHTML = `<div class="card"><h3>Mistakes</h3>${issues}
    ${w.changed ? `<h3>Better version</h3><textarea id="w-better" rows="6">${esc(w.improved)}</textarea>
    <div class="row"><span class="muted small">${w.replaced ? "Already pasted over the selection." : "Edit if needed, then copy."}</span>
      <button id="w-copy" class="primary">Copy</button></div>` : ""}</div>`;
  const copy = $("#w-copy");
  if (copy) copy.onclick = () => { navigator.clipboard.writeText($("#w-better").value); toast("Copied"); };
}
$("#w-check").addEventListener("click", async () => {
  const text = $("#w-text").value.trim();
  if (!text) return $("#w-text").focus();
  $("#w-check").disabled = true; $("#w-status").textContent = "Checking with the local model..."; $("#w-result").innerHTML = "";
  try { renderWording(await api("POST", "/api/wording", { text })); }
  catch (e) { $("#w-status").textContent = ""; $("#w-result").innerHTML = `<div class="card"><span class="err">${esc(e.message)}</span></div>`; }
  $("#w-check").disabled = false;
});

// ---------------------------------------------------------------- recap
const GROUPS = [["todo", "To do"], ["reply", "To answer"], ["fyi", "Good to know"], ["ignore", "Ignore"]];
let recapTimer = null;
async function loadRecap() {
  const r = await api("GET", "/api/recap");
  const running = r.job && (r.job.state === "running" || r.job.state === "queued");
  const rng = r.range || {};
  const when = r.created ? new Date(r.created * 1000).toLocaleString([], { dateStyle: "short", timeStyle: "short" }) : "";
  $("#recap-status").textContent = !r.items.length && !running ? "No recap yet: pick a range and press Recap."
    : `${when ? "Made " + when : ""}${rng.kind === "days" ? `, last ${rng.days} day(s)` : rng.date ? `, since ${rng.date}` : ""}` +
      `${r.model ? " · " + r.model : ""}${running ? ` · sorting ${r.job.done}/${r.job.total || "?"}...` : ""}` +
      (r.reviewed ? ` · you checked ${r.reviewed}: the model was right in ${r.agreed}` : " · move an item to the right group when the model is wrong");
  $("#recap-list").innerHTML = GROUPS.map(([cat, label]) => {
    const items = r.items.filter((i) => (i.user_category || i.category) === cat);
    if (!items.length) return "";
    return `<details class="card recap-group ${cat}" ${cat === "ignore" ? "" : "open"}><summary><b>${label}</b> <span class="muted">${items.length}</span></summary>
      <ul class="recap-items">${items.map((i) => `<li>
        <div class="recap-head"><span class="src">${i.source === "mail" ? "Mail" : "Teams"}</span>
          ${i.link ? `<a href="${esc(i.link)}" target="_blank" rel="noopener">${esc(i.title)}</a>` : `<b>${esc(i.title)}</b>`}
          <span class="muted">${esc(i.people)} · ${ago(i.last)}${i.last_from_me ? " · last message yours" : ""}</span>
          <select data-key="${esc(i.key)}" title="Move to the right group">${GROUPS.map(([c, l]) =>
            `<option value="${c}" ${c === (i.user_category || i.category) ? "selected" : ""}>${l}</option>`).join("")}</select></div>
        ${i.summary ? `<div>${esc(i.summary)}</div>` : ""}
        ${i.action ? `<div class="action">→ ${esc(i.action)}${i.due ? ` <span class="warn">(${esc(i.due)})</span>` : ""}</div>` : ""}
        ${i.error ? `<div class="err small">${esc(i.error)}</div>` : ""}
        ${i.user_category && i.user_category !== i.category ? `<div class="muted small">model said: ${esc(i.category)}</div>` : ""}</li>`).join("")}</ul></details>`;
  }).join("");
  clearTimeout(recapTimer);
  if (running) recapTimer = setTimeout(() => { if ($("#recap").classList.contains("active")) loadRecap(); }, 2000);
}
$("#recap-form").days.addEventListener("change", (e) => $("#recap-form .since").classList.toggle("hidden", e.target.value !== "since"));
$("#recap-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const range = f.days.value === "since" ? { kind: "since", date: f.since.value } : { kind: "days", days: Number(f.days.value) };
  const sources = [f.mail.checked && "mail", f.teams.checked && "teams"].filter(Boolean);
  try { await graphCall("POST", "/api/recap", { range, sources }); loadRecap(); loadJobs(); }
  catch (err) { alert(err.message); }
});
$("#recap-list").addEventListener("change", async (e) => {
  const sel = e.target.closest("select[data-key]");
  if (!sel) return;
  await api("POST", "/api/recap/correct", { key: sel.dataset.key, category: sel.value });
  loadRecap();
});

// ---------------------------------------------------------------- local AI: GPU, Ollama, models, test
let aiTimer = null;
const mb = (v) => (v / 1024).toFixed(1) + " GB";
async function loadAI() {
  const s = await api("GET", "/api/llm/status");
  const g = s.gpu || {}, e = s.engine || {};
  const gpu = g.name ? `<b>${esc(g.name)}</b> · ${mb(g.total_mb)} VRAM, ${mb(g.free_mb)} free now (${mb(g.used_mb)} in use)`
    : `<span class="warn">No NVIDIA GPU found${g.error ? ": " + esc(g.error) : ""}.</span> The model can still run on the CPU, slowly.`;
  const whisper = `Whisper: ${esc(e.model || "?")}${e.device ? " on " + esc(e.device) : ""} (${esc(e.state || "?")})` +
    (e.device === "cuda" ? ". Choosing Parakeet V3 for dictation leaves the whole GPU to the model." : "");
  const inst = s.install || {};
  const instProg = inst.total
    ? ` ${Math.round(100 * inst.completed / inst.total)}% (${(inst.completed / 1e9).toFixed(2)} / ${(inst.total / 1e9).toFixed(2)} GB)`
    : inst.completed ? ` ${(inst.completed / 1e9).toFixed(2)} GB` : "";
  const ollama = inst.state === "running"
    ? `<span class="warn">Setting up Ollama locally (${esc(inst.status)}${instProg})...</span>`
    : !s.installed && !s.running
    ? `<span class="warn">Ollama is not installed.</span> <button id="ai-install" class="primary">Download &amp; set up Ollama</button>
       <span class="muted small">(GitHub ZIP → %LOCALAPPDATA%\\powertools\\ollama, no installer, no admin rights)</span>`
    : !s.running ? `<span class="warn">Ollama is installed${s.exe ? ` (${esc(s.exe)})` : ""} but not running.</span> <button id="ai-start" class="primary">Start it</button>`
    : `<span class="ok">Ollama ${esc(s.version)} is running</span>${s.exe ? ` <span class="muted small">(${esc(s.exe)})</span>` : ""}.`;
  const instErr = inst.state === "error" ? `<div class="err">Ollama setup failed: ${esc(inst.error)}</div>` : "";
  const models = (s.models || []).map((m) => `<li><b>${esc(m.name)}</b> <span class="muted">${m.size_gb} GB · ${esc(m.params)} · ${esc(m.quant)}</span>
    <button class="ai-delete" data-model="${esc(m.name)}">Delete</button></li>`).join("");
  const loaded = (s.loaded || []).map((m) => `${esc(m.name)}: ${m.gpu_share}% on the GPU (${m.vram_gb} of ${m.size_gb} GB)`).join("; ");
  const missing = s.running ? s.suggested.filter((m) => !(s.models || []).some((x) => x.name === m)) : [];
  const p = s.pull || {};
  const pullProg = p.total
    ? ` ${Math.round(100 * p.completed / p.total)}% (${(p.completed / 1e9).toFixed(2)} / ${(p.total / 1e9).toFixed(2)} GB)`
    : p.completed ? ` ${(p.completed / 1e9).toFixed(2)} GB` : "";
  const pull = p.state === "running" ? `<div>Downloading ${esc(p.model)}: ${esc(p.status)}${pullProg}</div>`
    : p.state === "error" ? `<div class="err">Download of ${esc(p.model)} failed: ${esc(p.error)}</div>`
    : p.state === "done" ? `<div class="ok">${esc(p.model)} downloaded.</div>` : "";
  $("#ai-status").innerHTML = `<h3>GPU</h3><div>${gpu}</div><div class="muted small">${whisper}</div>
    <h3>Ollama</h3><div>${ollama} <button id="ai-refresh">Check again</button></div>${instErr}
    ${s.running ? `<h3>Models</h3>${models ? `<ul class="plain">${models}</ul>` : `<div class="muted">No models yet.</div>`}
      ${loaded ? `<div class="muted small">Loaded now: ${loaded}</div>` : ""}
      <div class="row">${missing.map((m) => `<button class="ai-pull" data-model="${esc(m)}">Download ${esc(m)}</button>`).join("")}</div>${pull}` : ""}`;
  $("#ai-refresh").onclick = loadAI;
  const install = $("#ai-install");
  if (install) install.onclick = async () => { install.disabled = true; try { await api("POST", "/api/llm/install"); } catch (err) { alert(err.message); } loadAI(); };
  const start = $("#ai-start");
  if (start) start.onclick = async () => { start.disabled = true; try { await api("POST", "/api/llm/start"); } catch (err) { alert(err.message); } loadAI(); };
  document.querySelectorAll(".ai-pull").forEach((b) => (b.onclick = async () => {
    try { await api("POST", "/api/llm/pull", { model: b.dataset.model }); } catch (err) { alert(err.message); } loadAI();
  }));
  document.querySelectorAll(".ai-delete").forEach((b) => (b.onclick = async () => {
    if (!confirm(`Delete ${b.dataset.model}? You can download it again here.`)) return;
    b.disabled = true;
    try { await api("POST", "/api/llm/delete", { model: b.dataset.model }); } catch (err) { alert(err.message); } loadAI();
  }));
  const sel = $("#ai-model"), cur = sel.value || s.model;
  sel.innerHTML = [...new Set([s.model, ...(s.models || []).map((m) => m.name)])].map((m) => `<option>${esc(m)}</option>`).join("");
  sel.value = cur;
  gateLocalAI(s);
  clearTimeout(aiTimer);
  if ((p.state === "running" || inst.state === "running") && $("#ai").classList.contains("active")) aiTimer = setTimeout(loadAI, 1500);
}
$("#ai-test").addEventListener("click", async () => {
  const btn = $("#ai-test");
  btn.disabled = true;
  $("#ai-result").innerHTML = `<div class="muted">Loading ${esc($("#ai-model").value)} and running the test (up to a minute on the first run)...</div>`;
  try {
    const t = await api("POST", "/api/llm/test", { model: $("#ai-model").value });
    const vram = (x) => x && x.total_mb ? `${mb(x.free_mb)} free` : "?";
    $("#ai-result").innerHTML = `<div class="verdict"><b>${esc(t.verdict)}</b></div>
      <div class="about-grid"><span>Speed</span><span>${t.tok_s} tokens/s writing, ${t.prompt_tok_s} tokens/s reading</span>
      <span>Took</span><span>${t.took_s} s (loading ${t.load_s} s)</span>
      <span>On the GPU</span><span>${t.gpu_share}% of ${t.size_gb} GB (${t.vram_gb} GB in VRAM)</span>
      <span>VRAM</span><span>before ${vram(t.gpu_before)}, after ${vram(t.gpu_after)}</span>
      <span>Answer</span><span>${t.json_ok ? `valid, ${t.issues} mistake(s) found` : '<span class="warn">not in the expected format</span>'}</span></div>
      ${t.improved ? `<div class="muted small">Better version: ${esc(t.improved)}</div>` : ""}`;
  } catch (e) { $("#ai-result").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
  btn.disabled = false;
  loadAI();
});

poll();
loadHistory();

// ---------------------------------------------------------------- about + update
const dlg = $("#about");
const logItems = (items) => items.map((c) => `<li><span>${esc(c.date)}</span><span>${esc(c.subject)}</span></li>`).join("");
async function loadAbout(fetchRemote) {
  $("#about-status").textContent = fetchRemote ? "Checking for updates..." : "";
  $("#about-do").classList.add("hidden");
  let a;
  try { a = await api("GET", "/api/about" + (fetchRemote ? "?fetch=1" : "")); }
  catch (e) { $("#about-status").textContent = e.message; return; }
  $("#about-version").textContent = `v${a.version}${a.commit ? " · " + a.commit : ""}${a.branch ? " · " + a.branch : ""}`;
  const e = a.engine || {};
  const rows = [["Whisper", `${e.model || "?"}${e.device ? " on " + e.device + (e.compute ? "/" + e.compute : "") : ""} (${e.state || "?"})`],
    ["Python", a.python], ["Folder", a.root]];
  $("#about-info").innerHTML = rows.map(([k, v]) => `<span>${esc(k)}</span><span>${esc(v)}</span>`).join("");
  $("#about-log").innerHTML = logItems(a.changelog || []);
  const pending = a.pending || [];
  $("#about-pending").innerHTML = pending.length ? `<h3>New in the update</h3><ul class="changelog new">${logItems(pending)}</ul>` : "";
  if (a.error) $("#about-status").textContent = a.git ? `Updates unavailable: ${a.error}` : "Updates need git (not found).";
  else if (pending.length) {
    $("#about-status").textContent = a.busy ? `${pending.length} new change(s). Update after: ${a.busy}.` : `${pending.length} new change(s) available.`;
    $("#about-do").classList.toggle("hidden", !!a.busy);
  } else $("#about-status").textContent = a.fetched ? "You have the latest version." : "";
  appInstalled = !!a.installed;
  renderInstall();
}
$("#engine-foot").addEventListener("click", () => { dlg.showModal(); loadAbout(true); });
dlg.querySelector(".close").addEventListener("click", () => dlg.close());
dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
$("#about-check").addEventListener("click", () => loadAbout(true));
$("#about-do").addEventListener("click", async () => {
  $("#about-do").classList.add("hidden");
  updating = true;
  $("#about-status").textContent = "Updating...";
  let r;
  try { r = await api("POST", "/api/update"); } catch (e) { updating = false; $("#about-status").textContent = e.message; return; }
  if (!r.restarting) { updating = false; loadAbout(false); return; }
  $("#about-status").textContent = `Updated ${r.before} → ${r.after}. Restarting (setup may run first)...`;
  const started = Date.now();
  await new Promise((ok) => setTimeout(ok, 3000));
  const wait = async () => {
    try { await api("GET", "/api/ping"); location.reload(); }
    catch { if (Date.now() - started < 300000) setTimeout(wait, 1500); else $("#about-status").textContent = "Restart takes long: start powertools from the shortcut (see setup.log)."; }
  };
  wait();
});

// ---------------------------------------------------------------- install as an app (own taskbar icon, pinning)
let installEvent = null, appInstalled = false;
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
function renderInstall() {
  if (appInstalled) {
    $("#install-text").textContent = "Installed as an app: pin it from the taskbar.";
    $("#install-do").classList.add("hidden");
    return;
  }
  $("#install-do").classList.toggle("hidden", !installEvent);
  $("#install-text").textContent = installEvent ? "Install the window as an app to get its own taskbar icon and pinning."
    : `To get its own taskbar icon: open ${location.origin} in Edge, then ⋯ menu > Apps > Install this site as an app.`;
}
window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); installEvent = e; renderInstall(); });
window.addEventListener("appinstalled", () => { appInstalled = true; $("#install-text").textContent = "Installed. Next time the app window opens with its own icon."; $("#install-do").classList.add("hidden"); });
$("#install-do").addEventListener("click", async () => { if (installEvent) { installEvent.prompt(); installEvent = null; renderInstall(); } });
renderInstall();
