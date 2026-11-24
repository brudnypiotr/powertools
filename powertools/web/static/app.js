"use strict";
const $ = (s) => document.querySelector(s);
const api = async (method, path, body) => {
  const r = await fetch(path, {
    method,
    headers: { "X-Powertools": "1", "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json();
  if (r.status === 403 && data.error === "locked") locked();
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
};
let isLocked = false;
async function locked() {   // this window has no key (a cleared cookie, a first start after an update): the app opens one that has
  if (isLocked) return;
  isLocked = true;
  document.body.innerHTML = `<main class="main"><div class="empty"><b>powertools opens in a new window</b><p class="muted">This one has no access yet. You can close it.</p><p><button id="unlock" class="primary">Open it again</button></p></div></main>`;
  const ask = () => fetch("/api/unlock", { method: "POST", headers: { "X-Powertools": "1" }, body: "{}" }).catch(() => {});
  $("#unlock").onclick = ask;
  if (!sessionStorage.getItem("asked")) { sessionStorage.setItem("asked", "1"); ask(); }
  const back = setInterval(async () => { try { if ((await fetch("/api/state")).ok) { clearInterval(back); sessionStorage.removeItem("asked"); location.reload(); } } catch { /* the app restarts */ } }, 2000);
}
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

// ---------------------------------------------------------------- tabs
document.querySelectorAll("nav button[data-tab]").forEach((b) =>
  b.addEventListener("click", () => {
    if (b.disabled) return;
    if (b.closest("#more-nav")) openMore(true);
    document.querySelectorAll("nav button, .tab").forEach((e) => e.classList.remove("active"));
    b.classList.add("active");
    document.querySelectorAll("nav button[data-tab]").forEach((e) => e.toggleAttribute("aria-current", e === b));
    $(".main").scrollTop = 0;
    $("#" + b.dataset.tab).classList.add("active");
    if (b.dataset.tab === "settings") loadSettings();
    if (b.dataset.tab === "diagnostics") loadDiagnostics();
    if (b.dataset.tab === "teams" || b.dataset.tab === "mail") { renderGraphBar(); loadJobs(); }
    if (b.dataset.tab === "teams" && !chats.length) loadChats(false);
    if (b.dataset.tab === "mail" && !foldersLoaded) loadFolders();
    document.querySelector(".main").classList.toggle("full", $("#" + b.dataset.tab).classList.contains("panes"));
    if (b.dataset.tab === "meetings") loadMeetings();
    if (b.dataset.tab === "org") { renderGraphBar(); loadJobs(); loadOrgSaved(); if (!orgChain.length) loadOrgChain(); }
    if (b.dataset.tab === "writing") loadWording();
    if (["recap", "matters", "meetings"].includes(b.dataset.tab)) loadRecap();
    if (b.dataset.tab === "ai") loadAI();
    if (b.dataset.tab === "jira") { renderJiraBar(false); loadJiraDownloaded(); loadJobs(); loadJiraFields(); }
  }));

function openMore(on) { $("#more-nav").classList.toggle("hidden", !on); $("#more").setAttribute("aria-expanded", on); }
$("#more").addEventListener("click", () => openMore($("#more-nav").classList.contains("hidden")));

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
  if (isLocked) return;
  let s;
  try { s = await api("GET", "/api/state"); failures = 0; }
  catch {
    if (isLocked) return;
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
  renderSide(s.llm || {}, s.graph || {});
  gateLocalAI(s.llm);
  followFocus(s);
  setTimeout(poll, s.dictation === "recording" ? 150 : 700);
}
$("#toggle").addEventListener("click", () => api("POST", "/api/toggle"));

// ---------------------------------------------------------------- bottom left: the model, its tokens, when the access ends
const modelName = (m) => String(m || "").split("-").map((w) => (/^gpt$/i.test(w) ? "GPT" : /^[a-z]/.test(w) ? w[0].toUpperCase() + w.slice(1) : w)).join(" ").replace(/^GPT /, "GPT-");
const thousands = (n) => (n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1000 ? Math.round(n / 1000) + "k" : String(n || 0));
const setText = (sel, t) => { const e = $(sel); if (e.textContent !== t) e.textContent = t; };
function setLabel(sel, text, kind) { setText(sel, text); const e = $(sel); if (e.dataset.kind !== kind) { e.dataset.kind = kind; e.className = "label " + kind; } }
function renderSide(llm, g) {
  const cloud = llm.provider === "copilot";
  setText("#side-model", cloud ? modelName(llm.model) : llm.model || "");
  setText("#side-tokens", cloud ? `${thousands(llm.tokens_today)} tokens today` : "on this laptop");
  const q = cloud && llm.quota, left = q && q.left !== null && q.left !== undefined, few = (n) => Number(n).toLocaleString([], { maximumFractionDigits: n < 100 ? 1 : 0 });
  setText("#side-credits", !cloud ? "nothing leaves it" : `${(llm.credits_today || 0).toFixed(1)}${llm.limit ? ` of ${llm.limit}` : ""} credits today`);
  $("#side-quota").classList.toggle("hidden", !q);
  if (q) {
    setText("#side-unit", q.unit);
    setText("#side-pct", q.unlimited ? "unlimited" : left ? Math.round(q.used_pct) + "% used" : "?");
    $("#side-fill").style.width = left ? Math.max(2, Math.min(100, q.used_pct)) + "%" : "0";
    $("#side-fill").className = !left ? "" : q.used_pct >= 90 ? "bad" : q.used_pct >= 70 ? "warn" : "";
    setText("#side-left", left ? `${few(q.left)} of ${few(q.entitlement)} left` : "");
    setText("#side-reset", `new in ${q.days_left} day${q.days_left === 1 ? "" : "s"} (${q.work_days_left} working)`);
    $("#side-quota").title = `${q.plan ? `Plan ${q.plan}. ` : ""}Read from GitHub at ${new Date(q.checked * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}, less what powertools used since; powertools this month: ${few(llm.credits_month || 0)}. Click to read it now.`;
  }
  $("#side-bar").classList.toggle("hidden", !cloud || !llm.limit || !!left);
  $("#side-bar span").style.width = Math.min(100, 100 * (llm.credits_today || 0) / (llm.limit || 1)) + "%";
  const m = g.expires_min || 0;
  setLabel("#side-graph", g.has_token ? `${m} min` : "expired", !g.has_token ? "bad" : m < 10 ? "warn" : "good");
  const d = llm.github_days, known = cloud && d !== null && d !== undefined;
  $("#side-github-row").classList.toggle("hidden", !known);
  $("#side-github-renew").classList.toggle("hidden", !known || d > 7);
  if (known) setLabel("#side-github", d > 0 ? `${d} day${d === 1 ? "" : "s"}` : "expired", d < 1 ? "bad" : d < 8 ? "warn" : "good");
}
const askQuota = () => api("POST", "/api/ai/quota").then(() => toast("Asking GitHub...")).catch((e) => say(e.message));
$("#side-quota").addEventListener("click", askQuota);
$("#side-quota").addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); askQuota(); } });
$("#side-renew").addEventListener("click", () => { showTab("recap"); openConnect(); });
$("#side-github-renew").addEventListener("click", () => { showTab("ai"); $("#copilot-card details").open = true; });

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
const toasts = Object.assign(document.createElement("div"), { className: "toasts" });
toasts.setAttribute("role", "status");
document.body.appendChild(toasts);
function toast(text, ms = 1800, action) {  // action: [label, function]
  const t = Object.assign(document.createElement("div"), { className: "toast", textContent: text });
  if (action) {
    const b = Object.assign(document.createElement("button"), { textContent: action[0] });
    b.onclick = () => { t.remove(); action[1](); };
    t.appendChild(b);
  }
  toasts.appendChild(t);
  while (toasts.children.length > 3) toasts.firstChild.remove();
  setTimeout(() => t.remove(), ms);
}
const say = (text) => toast(text, 6000);  // an error or a hint: never a dialog that blocks the window
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
  for (const k of ["meeting_mic", "meeting_output", "meeting_final_model", "meeting_keep_audio_days", "meeting_mute_hotkey"]) f[k].value = s[k];
  f.meeting_prompt.checked = s.meeting_prompt;
  f.meeting_mute_sync.checked = s.meeting_mute_sync;
  f.meeting_summary.checked = s.meeting_summary;
  api("GET", "/api/meetings").then((m) => { $("#teams-link").textContent = m.teams && m.teams.connected ? teamsLink(m.teams) : ""; }).catch(() => {});
  f.notifications.checked = s.notifications;
  f.input_device.innerHTML = `<option value="">Windows default</option>` +
    [...new Set([...devices, s.input_device].filter(Boolean))].map((d) => `<option>${esc(d)}</option>`).join("");
  for (const k of ["hotkey", "model", "language", "input_device", "beam_size", "output_dir", "jira_url", "jira_email",
    "jira_max_attachment_mb", "memory_dir", "silence_days", "matter_quiet_days", "copilot_daily_credits", "copilot_effort", "llm_gpu", "llm_keep_alive_min", "wording_hotkey", "wording_mode"]) f[k].value = s[k];
  for (const k of ["paste", "restore_clipboard", "open_window_on_start", "mail_attachments", "app_window", "jira_attachments",
    "small_on_cpu", "dictation_cleanup", "auto_drafts", "check_answers"]) f[k].checked = s[k];
  const llmModels = (installed, suggested) => {
    const names = [...new Set([...installed, ...suggested, s.llm_model].filter(Boolean))];
    f.llm_model.innerHTML = names.map((m) => `<option value="${esc(m)}">${esc(m)}${installed.includes(m) ? "" : " (not downloaded)"}</option>`).join("");
    f.llm_model.value = s.llm_model;
  };
  llmModels([s.llm_model], []);
  api("GET", "/api/llm/status").then((st) => llmModels((st.models || []).map((m) => m.name), st.suggested || [])).catch(() => {});
  f.vocabulary.value = s.vocabulary.join("\n");
  f.exclude.value = s.exclude.join("\n");
  f.replacements.value = s.replacements.map((r) => `${r.from} => ${r.to}`).join("\n");
}
const settingsForm = $("#settings-form");
$("#settings-nav").innerHTML = [...settingsForm.querySelectorAll("h3")].map((h, n) => { h.id = "set-" + n; return `<button type="button" data-to="set-${n}">${esc(h.textContent)}</button>`; }).join("");
$("#settings-nav").addEventListener("click", (e) => { const b = e.target.closest("[data-to]"); if (b) document.getElementById(b.dataset.to).scrollIntoView({ block: "start" }); });
settingsForm.addEventListener("input", (e) => { if (!e.target.closest("#look")) $("#saved").textContent = "Not saved yet"; });
settingsForm.addEventListener("submit", async (e) => {
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
    meeting_mute_sync: f.meeting_mute_sync.checked, meeting_summary: f.meeting_summary.checked, meeting_mute_hotkey: f.meeting_mute_hotkey.value.trim(),
    exclude: lines(f.exclude.value), memory_dir: f.memory_dir.value.trim(), auto_drafts: f.auto_drafts.checked, check_answers: f.check_answers.checked, silence_days: Number(f.silence_days.value),
    matter_quiet_days: Number(f.matter_quiet_days.value), copilot_daily_credits: Number(f.copilot_daily_credits.value), copilot_effort: f.copilot_effort.value,
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
  const waiting = s.watch && s.watch.active ? `<div class="token-wait">Waiting for the token: in the Graph Explorer window sign in if asked,
    open the <b>Access token</b> tab and click its copy button. It is saved here automatically.</div>` : "";
  const fields = `<button class="token-get ${s.has_token ? "" : "primary"}">Get ${s.has_token ? "a new" : "a"} token</button>
    <input type="password" placeholder="...or paste the access token here" aria-label="Graph access token" class="token-input">
    <button class="token-save">${s.has_token ? "Replace" : "Connect"}</button>`;
  document.querySelectorAll(".graphbar").forEach((bar) => {
    if (bar.contains(document.activeElement) && document.activeElement.value) return;  // a token is being pasted
    bar.innerHTML = s.has_token
      ? `<span class="ok">Connected${s.user ? " as " + esc(s.user) : ""}</span> <span class="muted">${s.expires_min} min left</span>
         <details class="token-more" ${waiting ? "open" : ""}><summary>New token</summary><div class="row">${fields}</div></details>${waiting}`
      : `<div class="token-why"><b>Connect to your mail, Teams and calendar.</b> <span class="muted">A token from
         Microsoft Graph Explorer, valid for about an hour: press Get a token, sign in, copy it there.</span></div>${fields}${waiting}`;
    bar.querySelector(".token-get").onclick = async () => {
      try { await api("POST", "/api/graph/token/fetch"); watchGraphToken(s.has_token ? s.expires_min : -1); renderGraphBar(); }
      catch (e) { say(e.message); }
    };
    bar.querySelector(".token-save").onclick = async () => {
      const token = bar.querySelector(".token-input").value.trim();
      if (!token) return;
      try {
        const r = await api("POST", "/api/graph/token", { token });
        bar.querySelector(".token-input").value = "";
        renderGraphBar();
        tokenSaved(r.resumed_jobs);
      } catch (e) { say(e.message); }
    };
  });
}
function tokenSaved(resumed) {
  if (resumed) loadJobs();
  recapToken();
  if ($("#teams").classList.contains("active")) loadChats(true);
  if ($("#mail").classList.contains("active") && !foldersLoaded) loadFolders();
  if ($("#org").classList.contains("active")) loadOrgChain();
}
let graphWatch = null;
function watchGraphToken(before) {  // poll until the copied token is caught (or the 5 minutes run out)
  clearTimeout(graphWatch);
  graphWatch = setTimeout(async () => {
    const s = await api("GET", "/api/graph/status");
    if (s.watch.active) return watchGraphToken(before);
    renderGraphBar();
    if (s.watch.result === "saved") {
      toast("Connected");
      tokenSaved(true);
    } else if (s.watch.result && s.watch.result !== "timeout") say(s.watch.result);
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
  if (!ids.length) return say("Select chats first.");
  try { await graphCall("POST", "/api/teams/export", { ids, range: rangeOf("teams") }); loadJobs(); }
  catch (e) { say(e.message); }
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
  if (!items.length) return say("Select threads first.");
  try { await graphCall("POST", "/api/mail/export", { items, range: rangeOf("mail") }); loadJobs(); }
  catch (e) { say(e.message); }
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
    catch (e) { say(e.message); }
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
  catch (e) { say(e.message); }
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
  if (!keys.length) return say("Select issues first.");
  jiraDownload({ mode: "keys", value: keys.join(",") });
});
$("#jira-dl-all").addEventListener("click", () => {
  if (!jiraLast.jql) return say("Search first.");
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
  } catch (e) { say(e.message); }
}
$("#jira-refresh-sel").addEventListener("click", () => {
  const keys = [...document.querySelectorAll("#jira-downloaded input:checked")].map((c) => c.value);
  if (!keys.length) return say("Select downloaded issues first.");
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
  if (!orgRoot) return say("Pick a person first: one of your managers or a search result.");
  try {
    const job = await graphCall("POST", "/api/org/export", { id: orgRoot.id, depth: Number($("#org-depth").value), above: $("#org-above").checked });
    loadJobs(); waitOrgJob(job.id);
  } catch (e) { say(e.message); }
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
  const card = (j) => `<div class="card job ${j.state}">
      <div class="title"><b>${esc(j.title)}</b> · ${j.state.replace("_", " ")} ${j.total ? `· ${j.done}/${j.total}` : ""}</div>
      ${j.state === "running" || j.state === "queued" ? `<div class="bar"><span style="width:${j.total ? (100 * j.done / j.total) : 5}%"></span></div>` : ""}
      ${j.error ? `<div>${esc(j.error)}</div>` : ""}
      ${j.files.length || j.notes.length ? `<ul>${j.files.map((f) => `<li class="file"><span title="${esc(f)}">${esc(f.split(/[\\/]/).pop())}</span>
          <button class="mini" data-open="${esc(f)}">Open</button><button class="mini" data-reveal="${esc(f)}">Open folder</button></li>`).join("")}
        ${j.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}</div>`;
  document.querySelectorAll(".jobs").forEach((el) => {  // each tab lists only its own exports
    const mine = jobs.filter((j) => !el.dataset.kind || j.kind === el.dataset.kind);
    el.innerHTML = mine.length ? `<div class="row"><h2>Exports</h2><button class="open-out" data-kind="${el.dataset.kind || ""}">Open export folder</button></div>` +
      mine.map(card).join("") : "";
  });
  document.querySelectorAll(".jobs .open-out").forEach((b) => (b.onclick = () => api("POST", "/api/open-output", { kind: b.dataset.kind })));
  document.querySelectorAll(".jobs [data-open]").forEach((b) => (b.onclick = () => api("POST", "/api/open-path", { path: b.dataset.open })));
  document.querySelectorAll(".jobs [data-reveal]").forEach((b) => (b.onclick = () => api("POST", "/api/open-path", { path: b.dataset.reveal, folder: true })));
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
function teamsLink(t) {
  if (!t) return "";
  if (!t.connected) return "Now: Teams is not reachable (closed, or the API is off).";
  if (!t.paired) return "Now: connected to Teams, not paired yet (happens in the next call).";
  return "Now: connected and paired" + (t.in_meeting ? (t.muted ? ", in a call, muted." : ", in a call.") : ".");
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
        <span class="timer"></span><span class="pill transcribing mutedp hidden" title="Me muted: your microphone is not recorded">Me muted</span><span class="lv">Me <span class="meter"><span class="lme"></span></span></span>
        <span class="lv">Others <span class="meter"><span class="lot"></span></span></span></div>
        <div class="meetbar" style="margin-top:10px"><input class="note" placeholder="Note for this moment (optional)">
        <button class="mark">Mark moment</button><button class="mute"></button><button class="stop rec">Stop</button></div>`;
      now.querySelector(".mute").onclick = async () => { await api("POST", "/api/meetings/mute", {}); loadMeetings(); };
      now.querySelector(".mark").onclick = async () => { await api("POST", "/api/meetings/mark", { note: now.querySelector(".note").value }); now.querySelector(".note").value = ""; };
      now.querySelector(".stop").onclick = async () => { await api("POST", "/api/meetings/stop"); setTimeout(loadMeetings, 500); };
    }
    now.querySelector(".mtitle").textContent = cur.title;
    now.querySelector(".timer").textContent = clock(Date.now() / 1000 - cur.started);
    now.querySelector(".mutedp").classList.toggle("hidden", !m.muted);
    now.querySelector(".mute").textContent = m.muted ? "Unmute Me" : "Mute Me";
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
        <small>Records your microphone (Me) and what you hear (Others). Teams calls are noticed automatically.
        <span class="tlink"></span></small>`;
      now.querySelector(".start").onclick = async () => {
        try { await api("POST", "/api/meetings/start", { title: now.querySelector(".title").value }); loadMeetings(); }
        catch (e) { say(e.message); }
      };
    }
  }
  const tl = now.querySelector(".tlink");
  if (tl) tl.textContent = m.mute_hotkey ? `${m.mute_hotkey.toUpperCase()} mutes Teams and "Me" in the recording together.` : "";
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
        ${s.summary_status === "running" ? `<div class="muted">Summary: ${esc(s.summary_step || "writing...")}</div>` : ""}
        ${s.summary_status === "error" ? `<div class="warn">Summary failed: ${esc(s.summary_error || "")}</div>` : ""}
        <div class="mdetail">
          ${s.summary ? `<div><span class="muted">Summary</span> ${esc(s.summary.summary)}</div>
            ${s.summary.key_points.length ? `<ul class="kp">${s.summary.key_points.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>` : ""}` : ""}
          ${s.organizer ? `<div><span class="muted">Organizer</span> ${esc(s.organizer)}</div>` : ""}
          ${people.length ? `<div><span class="muted">Invited</span> ${people.map(esc).join(", ")}</div>`
            : `<div class="muted">No attendees yet. <button data-act="cal">Fill from calendar</button> (needs the Graph token from the Teams tab)</div>`}
          ${talk ? `<div><span class="muted">Speaking</span> Me ${mins(me.seconds || 0)} (${me.words || 0} words) · Others ${mins(ot.seconds || 0)} (${ot.words || 0} words)</div>` : ""}
          ${(s.marks || []).length ? `<div><span class="muted">Marked moments</span> ${s.marks.length}</div>` : ""}
          ${(s.muted || []).length ? `<div><span class="muted">Me muted</span> ${mins((s.muted || []).reduce((a, [x, y]) => a + ((y ?? s.seconds) - x), 0))} (not recorded)</div>` : ""}
          ${s.transcript_model ? `<div><span class="muted">Transcript</span> ${esc(s.transcript_model)}</div>` : ""}
          ${s.devices ? `<div><span class="muted">Devices</span> mic: ${esc(s.devices.mic || "default")} · others: ${esc(s.devices.output || "default")}</div>` : ""}
          ${s.invite ? `<details><summary class="muted">Invitation</summary><pre>${esc(s.invite)}</pre></details>` : ""}
          ${s.md ? `<div class="muted small">${esc(s.md)}</div>` : ""}
        </div></div>
      <div class="mact">
        ${s.md ? `<button data-act="open">Open</button>` : ""}
        ${(s.status === "interrupted" || s.status === "done") && s.audio ? `<button data-act="final">Transcribe again</button>` : ""}
        ${s.status === "done" && s.md && s.summary_status !== "running" ? `<button data-act="sum">${s.summary ? "Summarize again" : "Summarize"}</button>` : ""}
        <button data-act="del">Delete</button></div></li>`;
  }).join("") || "<li>No recordings yet.</li>";
  document.querySelectorAll(".open-out:not([data-kind])").forEach((b) => (b.onclick = () => api("POST", "/api/open-output")));
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
  if (act === "open") { try { await api("POST", `/api/meetings/${li.dataset.id}/open`, {}); } catch (err) { say(err.message); } return; }
  if (act === "cal") {
    try { const r = await api("POST", `/api/meetings/${li.dataset.id}/calendar`, {});
      if (!r.calendar) say("No Teams meeting on your calendar at that time (an ad-hoc call has no attendee list)."); }
    catch (err) { say(err.message); }
    loadMeetings(); return;
  }
  if (act === "sum") {
    try { await api("POST", `/api/meetings/${li.dataset.id}/summarize`, {}); } catch (err) { say(err.message); }
    loadMeetings(); return;
  }
  if (act === "del" && !confirm("Delete this recording (audio and live transcript)? The exported Markdown stays.")) return;
  try {
    await api(act === "del" ? "DELETE" : "POST", `/api/meetings/${li.dataset.id}` + (act === "final" ? "/finalize" : ""));
  } catch (err) { say(err.message); }
  loadMeetings();
});

$("#update-all").onclick = async () => {
  const b = $("#update-all"); b.disabled = true;
  try { const r = await api("POST", "/api/meetings/update-all", {});
    say(`${r.updated} transcripts updated, ${r.with_attendees} with attendees from the calendar.` +
          (r.token ? "" : " Set a Graph token in the Teams tab to fill in the attendees."));
  } catch (err) { say(err.message); }
  b.disabled = false; loadMeetings();
};

// ---------------------------------------------------------------- writing (wording hotkey + pasted text)
let focusSeq = null, wordingSeen = null;
function gateLocalAI(llm) {  // Writing needs a model: greyed out until there is one
  const ok = !llm || llm.installed || llm.running;
  const b = document.querySelector('nav button[data-tab="writing"]');
  b.disabled = !ok;
  b.title = ok ? "" : "Needs Ollama or GitHub Copilot: see the AI tab";
  if (!ok && b.classList.contains("active")) showTab("ai");
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
const STYLE_LABELS = { professional: "More professional", casual: "More casual", shorter: "Shorter" };
function renderWording(w) {
  const box = $("#w-result");
  if (!w || !w.state) { box.innerHTML = ""; return; }
  if (w.original && w.original !== $("#w-text").value && w.mode !== "window") $("#w-text").value = w.original;
  if (w.state === "working") { $("#w-status").textContent = "Checking with the model..."; box.innerHTML = ""; return; }
  $("#w-status").textContent = w.stats ? `${w.stats.model} · ${w.stats.total_s} s` : "";
  if (w.state === "error") { box.innerHTML = `<div class="card"><span class="err">${esc(w.error)}</span></div>`; return; }
  const issues = w.style ? "" : `<h3>Mistakes</h3>` + (w.issues.length ? `<ul class="issues">${w.issues.map((i) => `<li><span class="wrong">${esc(i.wrong)}</span> →
      <span class="better">${esc(i.better)}</span><span class="muted"> · ${esc(i.why)}</span></li>`).join("")}</ul>`
    : `<div class="ok">${w.language && !/^en/i.test(w.language) ? `Not English (${esc(w.language)}): nothing to correct.` : "No mistakes found."}</div>`);
  box.innerHTML = `<div class="card">${issues}
    ${w.changed ? `<h3>${w.style ? esc(STYLE_LABELS[w.style] || w.style) : "Corrected"}</h3><textarea id="w-better" rows="6">${esc(w.improved)}</textarea>
    <div class="row"><span class="muted small">${w.replaced ? "Already pasted over the selection." : "Edit if needed, then copy."}</span>
      <button id="w-copy" class="primary">Copy</button></div>` : ""}</div>`;
  const copy = $("#w-copy");
  if (copy) copy.onclick = () => { navigator.clipboard.writeText($("#w-better").value); toast("Copied"); };
}
async function runWording(style) {
  const text = $("#w-text").value.trim();
  if (!text) return $("#w-text").focus();
  const buttons = document.querySelectorAll("#w-check, .w-style");
  buttons.forEach((b) => (b.disabled = true));
  $("#w-status").textContent = style ? "Rewriting with the model..." : "Checking with the model...";
  $("#w-result").innerHTML = "";
  try { renderWording(await api("POST", "/api/wording", { text, style })); }
  catch (e) { $("#w-status").textContent = ""; $("#w-result").innerHTML = `<div class="card"><span class="err">${esc(e.message)}</span></div>`; }
  buttons.forEach((b) => (b.disabled = false));
}
$("#w-check").addEventListener("click", () => runWording(""));
document.querySelectorAll(".w-style").forEach((b) => b.addEventListener("click", () => runWording(b.dataset.style)));

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
  if (install) install.onclick = async () => { install.disabled = true; try { await api("POST", "/api/llm/install"); } catch (err) { say(err.message); } loadAI(); };
  const start = $("#ai-start");
  if (start) start.onclick = async () => { start.disabled = true; try { await api("POST", "/api/llm/start"); } catch (err) { say(err.message); } loadAI(); };
  document.querySelectorAll(".ai-pull").forEach((b) => (b.onclick = async () => {
    try { await api("POST", "/api/llm/pull", { model: b.dataset.model }); } catch (err) { say(err.message); } loadAI();
  }));
  document.querySelectorAll(".ai-delete").forEach((b) => (b.onclick = async () => {
    if (!confirm(`Delete ${b.dataset.model}? You can download it again here.`)) return;
    b.disabled = true;
    try { await api("POST", "/api/llm/delete", { model: b.dataset.model }); } catch (err) { say(err.message); } loadAI();
  }));
  const sel = $("#ai-model"), cur = sel.value || s.model;
  sel.innerHTML = [...new Set([s.model, ...(s.models || []).map((m) => m.name)])].map((m) => `<option>${esc(m)}</option>`).join("");
  sel.value = cur;
  renderProvider();
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

// ---------------------------------------------------------------- provider, GitHub Copilot, comparison
async function renderProvider() {
  const u = await api("GET", "/api/ai/usage");
  document.querySelectorAll('input[name="ai-provider"]').forEach((r) => {
    r.checked = r.value === (u.provider || "local");
    r.onchange = async () => { await api("POST", "/api/settings", { ai_provider: r.value }); renderProvider(); };
  });
  const by = Object.entries(u.today.by || {}).map(([k, v]) => `${esc(k)} ${v.credits.toFixed(1)} (${v.calls})`).join(", ");
  $("#ai-usage").innerHTML = `Copilot today: ${u.today.credits.toFixed(1)} of ${u.limit || "∞"} credits in ${u.today.calls} calls${by ? ": " + by : ""}. This month: ${u.month}.`;
}
function renderCopilot(c) {
  $("#copilot-status").innerHTML = c.ok ? `<span class="ok">Signed in${c.login ? " as " + esc(c.login) : ""}</span>${c.token ? " with a token" : ""}${c.detail ? ` · <span class="warn">${esc(c.detail)}</span>` : ""}`
    : `<span class="warn">${esc(c.detail || "not signed in")}</span>`;
  const sel = $("#copilot-model");
  sel.innerHTML = [...new Set([c.model, ...c.models])].map((m) => `<option ${m === c.model ? "selected" : ""}>${esc(m)}</option>`).join("");
  sel.onchange = () => api("POST", "/api/settings", { copilot_model: sel.value });
}
$("#copilot-check").addEventListener("click", async (e) => {
  e.target.disabled = true; $("#copilot-status").textContent = "Checking (the first time downloads the Copilot runtime)...";
  try { renderCopilot(await api("GET", "/api/ai/copilot")); } catch (err) { $("#copilot-status").textContent = err.message; }
  e.target.disabled = false;
});
$("#copilot-token").addEventListener("submit", async (e) => {
  e.preventDefault(); $("#copilot-status").textContent = "Checking the token...";
  try { renderCopilot(await api("POST", "/api/ai/copilot/token", { token: e.target.token.value })); e.target.token.value = ""; } catch (err) { $("#copilot-status").innerHTML = `<span class="err">${esc(err.message)}</span>`; }
});
$("#copilot-forget").addEventListener("click", async () => { await api("DELETE", "/api/ai/copilot/token"); $("#copilot-status").textContent = "Token removed."; });
$("#copilot-test").addEventListener("click", async (e) => {
  e.target.disabled = true; $("#copilot-result").innerHTML = `<div class="muted">Asking...</div>`;
  try { const t = await api("POST", "/api/ai/copilot/test"); $("#copilot-result").innerHTML = `<div class="ok">${esc(t.model)} answered in ${t.total_s} s, ${t.credits == null ? "credits unknown" : t.credits.toFixed(2) + " credits"}</div><div class="muted small">${esc(t.improved)}</div>`; }
  catch (err) { $("#copilot-result").innerHTML = `<div class="err">${esc(err.message)}</div>`; }
  e.target.disabled = false; renderProvider();
});
async function pollCompare() {
  const c = await api("GET", "/api/ai/compare"), r = c.result || {};
  $("#cmp-run").disabled = c.state === "running";
  if (c.state === "running") { $("#cmp-result").innerHTML = `<div class="muted">Sorting again (after the running job)...</div>`; return setTimeout(pollCompare, 2000); }
  $("#cmp-result").innerHTML = c.state === "error" ? `<div class="err">${esc(c.error)}</div>` : r.items === undefined ? "" : `<div class="verdict"><b>${r.items} sorted in ${r.seconds} s${r.credits ? `, ${r.credits} credits` : ""}</b>;
    right in ${r.right} of ${r.reviewed} you checked; the same as before in ${r.same}${r.errors ? `; <span class="err">${r.errors} failed: ${esc(r.error)}</span>` : ""}</div>
    <table class="list"><tr><th>Conversation</th><th>Now</th><th>Before</th><th>You</th></tr>${r.rows.filter((x) => x.now !== x.before || x.you).map((x) => `<tr><td>${esc(x.title)}</td><td>${x.now}</td><td>${x.before}</td><td>${x.you}</td></tr>`).join("")}</table>`;
}
$("#cmp-run").addEventListener("click", async () => { try { await api("POST", "/api/ai/compare", { provider: $("#cmp-provider").value, limit: Number($("#cmp-limit").value) }); pollCompare(); } catch (err) { toast(err.message, 4000); } });

// ---------------------------------------------------------------- look (name, colours, logo of an organisation)
const LOOK = ["accent", "sidebar", "sidebar_active"], lookPicked = new Set();
async function loadLook(reload) {
  const b = await api("GET", "/api/branding");
  $("#brand-name").textContent = b.name;
  const logo = $("#brand-logo");
  logo.classList.toggle("hidden", !b.files.logo);
  if (b.files.logo) logo.src = "/branding/logo?" + b.files.logo;
  if (reload) $("#branding-css").href = "/branding.css?" + Date.now();
  $("#look-name").value = b.name === "powertools" ? "" : b.name;
  lookPicked.clear();
  const shown = { accent: "--accent", sidebar: "--side", sidebar_active: "--side2" }, css = getComputedStyle(document.documentElement);
  for (const k of LOOK) $("#look-" + k).value = b.colors[k] || css.getPropertyValue(shown[k]).trim();
}
for (const k of LOOK) $("#look-" + k).addEventListener("input", () => lookPicked.add(k));
const look = async (fn, done) => { try { const r = await fn(); await loadLook(true); toast(done || r.path, 2500); } catch (e) { toast(e.message, 5000); } };
$("#look-save").addEventListener("click", () => look(() => api("POST", "/api/branding", { powertools_name: $("#look-name").value.trim(),
  colors: Object.fromEntries([...lookPicked].map((k) => [k, $("#look-" + k).value])) }), "Applied"));
$("#look-reset").addEventListener("click", () => look(() => api("DELETE", "/api/branding"), "Back to the default look"));
$("#look-pack").addEventListener("click", () => look(() => api("POST", "/api/branding/pack")));
$("#look-zip").addEventListener("change", (e) => {
  const f = e.target.files[0];
  e.target.value = "";
  if (f) look(async () => {
    const r = await fetch("/api/branding/zip", { method: "POST", headers: { "X-Powertools": "1" }, body: f }), d = await r.json();
    if (!r.ok) throw new Error(d.error);
    return d;
  }, "Imported");
});
loadLook();

// ---------------------------------------------------------------- ideas
const ideaBox = $("#idea-box");
const ideaForm = ideaBox.querySelector("form");
let pictures = [];
const drawPictures = () => { $("#idea-pics").innerHTML = pictures.map((p, n) => `<button type="button" data-n="${n}" title="Remove"><img src="${p}" alt="Picture ${n + 1}"></button>`).join(""); };
$("#idea").addEventListener("click", async () => {
  $("#idea-done").innerHTML = ""; ideaForm.querySelector(".err").textContent = "";
  ideaBox.showModal();
  try {
    const i = await api("GET", "/api/feedback");
    $("#idea-report").textContent = i.report;
    $("#idea-github").classList.toggle("hidden", !i.issues);
  } catch (err) { ideaForm.querySelector(".err").textContent = err.message; }
});
ideaBox.querySelector(".close").addEventListener("click", () => ideaBox.close());
ideaBox.addEventListener("paste", (e) => {
  for (const it of e.clipboardData.items) if (/^image\/(png|jpeg|webp)$/.test(it.type) && pictures.length < 6) {
    const r = new FileReader();
    r.onload = () => { pictures.push(r.result); drawPictures(); };
    r.readAsDataURL(it.getAsFile());
  }
});
$("#idea-pics").addEventListener("click", (e) => { const b = e.target.closest("[data-n]"); if (b) { pictures.splice(Number(b.dataset.n), 1); drawPictures(); } });
ideaForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target, text = f.text.value;
  try {
    const r = await api("POST", "/api/feedback/package", { text, diagnostics: f.diagnostics.checked, pictures });
    const note = encodeURIComponent("powertools feedback: " + text.split("\n")[0].slice(0, 100));
    $("#idea-done").innerHTML = `<b>${esc(r.name)}</b> is on your Desktop. Send it to whoever looks after powertools.
      <span class="row"><button type="button" id="idea-show">Show the file</button>${r.contact ? `<a class="btnlink" target="_blank" rel="noopener" href="https://teams.microsoft.com/l/chat/0/0?users=${encodeURIComponent(r.contact)}&message=${note}">Message in Teams</a> <a class="btnlink" href="mailto:${encodeURIComponent(r.contact)}?subject=${note}">E-mail</a>` : ""}</span>`;
    $("#idea-show").onclick = () => api("POST", "/api/feedback/show", { name: r.name }).catch((err) => say(err.message));
    f.text.value = ""; pictures = []; drawPictures();
  } catch (err) { f.querySelector(".err").textContent = err.message; }
});
$("#idea-github").addEventListener("click", async () => {
  try { const r = await api("POST", "/api/idea", { text: ideaForm.text.value, diagnostics: ideaForm.diagnostics.checked }); window.open(r.url, "_blank", "noopener"); ideaForm.text.value = ""; ideaBox.close(); }
  catch (err) { ideaForm.querySelector(".err").textContent = err.message; }
});

poll();
loadHistory();
addEventListener("DOMContentLoaded", () => { $(".main").classList.add("full"); loadRecap(); });

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
    ["Python", a.python], ["Folder", a.root], ["Updates from", a.remote || "?"]];
  $("#about-info").innerHTML = rows.map(([k, v]) => `<span>${esc(k)}</span><span>${esc(v)}</span>`).join("");
  $("#about-log").innerHTML = logItems(a.changelog || []);
  const pending = a.pending || [];
  $("#about-pending").innerHTML = pending.length ? `<h3>New in the update</h3><ul class="changelog new">${logItems(pending)}</ul>` : "";
  if (a.error) $("#about-status").textContent = a.git ? `Updates unavailable: ${a.error}` : "Updates need git (not found).";
  else if (pending.length) {
    $("#about-status").textContent = a.busy ? `${pending.length} new change(s). Update after: ${a.busy}.` : `${pending.length} new change(s) available.`;
    $("#about-do").classList.toggle("hidden", !!a.busy);
  } else $("#about-status").textContent = a.fetched ? "You have the latest version." : "";
  const prev = a.previous || {};
  $("#about-back").classList.toggle("hidden", !prev.version || !!a.busy);
  $("#about-back").textContent = `Back to ${prev.version || ""}`;
  appInstalled = !!a.installed;
  renderInstall();
}
$("#engine-foot").addEventListener("click", () => { dlg.showModal(); loadAbout(true); });
dlg.querySelector(".close").addEventListener("click", () => dlg.close());
dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
$("#about-source").addEventListener("submit", async (e) => {
  e.preventDefault(); $("#about-source-note").textContent = "Checking the repository (it may ask you to sign in to GitHub)...";
  try { const r = await api("POST", "/api/update/source", { remote: e.target.remote.value.trim() }); $("#about-source-note").textContent = "Updates now come from " + r.remote; loadAbout(true); }
  catch (err) { $("#about-source-note").textContent = err.message; }
});
$("#about-check").addEventListener("click", () => loadAbout(true));
$("#about-do").addEventListener("click", () => otherVersion("/api/update", "Updating..."));
$("#about-back").addEventListener("click", () => otherVersion("/api/update/rollback", "Going back..."));
async function otherVersion(path, doing) {
  $("#about-do").classList.add("hidden");
  $("#about-back").classList.add("hidden");
  updating = true;
  $("#about-status").textContent = doing;
  let r;
  try { r = await api("POST", path); } catch (e) { updating = false; $("#about-status").textContent = e.message; return; }
  if (!r.restarting) { updating = false; loadAbout(false); return; }
  $("#about-status").textContent = `${r.before} → ${r.after}, a backup is made. Restarting (setup may run first)...`;
  const started = Date.now();
  await new Promise((ok) => setTimeout(ok, 3000));
  const wait = async () => {
    try { await api("GET", "/api/ping"); location.reload(); }
    catch { if (Date.now() - started < 300000) setTimeout(wait, 1500); else $("#about-status").textContent = "Restart takes long: start powertools from the shortcut (see setup.log)."; }
  };
  wait();
}

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
