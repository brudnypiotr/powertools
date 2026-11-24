"use strict";
// The Recap: Today, Matters, Meetings, Drafts, People, Ask. Uses $, api, esc, ago, toast, graphCall from app.js.
let R = null, recapView = "today", recapTimer = null, recapOpen = { person: "", prep: "", file: "", token: false, brief: null };
const openItems = new Set(), openGroups = {};   // what is unfolded: kept when the list is drawn again
const url = (u) => (/^https?:\/\//.test(u || "") ? esc(u) : "");
const SRC = { mail: "Mail", teams: "Teams", jira: "Jira", meeting: "Meeting" };
const CATS = [["todo", "To do"], ["reply", "To answer"], ["waiting", "Waiting for"], ["fyi", "Good to know"], ["ignore", "Ignore"]];
const clockOf = (iso) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const dayOf = (iso) => new Date(iso).toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" });
const plusDays = (n) => { const d = new Date(); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };
const busyBtn = async (b, fn) => {
  const label = b.textContent;
  b.disabled = true; b.textContent = "Working...";
  try { await fn(); } catch (e) { say(e.message.slice(0, 200)); }
  b.disabled = false; b.textContent = label;
};

// ---------------------------------------------------------------- load
async function loadRecap() {
  R = await api("GET", "/api/assistant");
  const running = R.job && ["queued", "running"].includes(R.job.state);
  renderRecapHead(running);
  if (!R.setup_done) renderSetup();
  else if (!inUse()) await renderRecapView();
  clearTimeout(recapTimer);
  if ($("#recap").classList.contains("active")) recapTimer = setTimeout(() => { if ($("#recap").classList.contains("active")) loadRecap(); }, running ? 2000 : 60000);
}
// never redraw under the user's hands: a field with text, an open list, a pressed mouse button
let pressed = false;
addEventListener("pointerdown", () => (pressed = true), true);
for (const ev of ["pointerup", "pointercancel"]) addEventListener(ev, () => (pressed = false), true);
const inUse = () => {
  const a = document.activeElement;
  return pressed || (a && $("#recap-view").contains(a) && (a.tagName === "SELECT" || (/INPUT|TEXTAREA/.test(a.tagName) && a.value !== "")));
};
function draw(html) {  // same place, same scroll, same focus
  const box = $("#recap-view"), main = $(".main"), top = main.scrollTop, a = document.activeElement;
  const again = a && box.contains(a) && a.dataset.act && a.closest("[data-key]") ? `[data-key="${CSS.escape(a.closest("[data-key]").dataset.key)}"] [data-act="${a.dataset.act}"]` : "";
  if (box.lastHtml === html && box.lastView === recapView) return;
  const same = box.lastView === recapView;
  box.innerHTML = html;
  box.lastHtml = html; box.lastView = recapView;
  main.scrollTop = same ? top : 0;
  if (again && box.querySelector(again)) box.querySelector(again).focus({ preventScroll: true });
}
function renderRecapHead(running) {
  const l = R.last || {}, llm = R.llm || {}, job = R.job || {}, g = R.graph || {};
  $("#recap-where").innerHTML = llm.provider === "copilot"
    ? `<span class="chip cloud" title="The text goes to GitHub Copilot (${esc(llm.model)}). ${llm.credits_today} AI credits today.">Copilot</span>`
    : `<span class="chip ok" title="Nothing leaves the laptop (${esc(llm.model)})">Local</span>`;
  const when = R.updated ? new Date(R.updated * 1000) : null;
  const day = when && when.toDateString() !== new Date().toDateString() ? when.toLocaleDateString([], { day: "numeric", month: "short" }) + " " : "";
  const pct = job.total ? Math.round(100 * job.done / job.total) : 3;
  $("#recap-status").textContent = running ? (R.ahead.length && job.state === "queued" ? "After: " + R.ahead.join(", ") : R.current || "Starting...")
    : when ? `Updated ${day}${when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} · ${l.new || 0} new` : "Not updated yet";
  $("#recap-status").title = running ? "" : [l.found !== undefined && `${l.found} conversations read in ${l.seconds} s`, l.credits && `${l.credits} AI credits`,
    R.reviewed && `you checked ${R.reviewed}, right ${R.agreed}`].filter(Boolean).join(" · ");
  $("#recap-progress").classList.toggle("hidden", !running);
  $("#recap-progress span").style.width = pct + "%";
  const conn = $("#recap-conn"), low = g.has_token && g.expires_min < 5;
  conn.className = "chip " + (!g.has_token ? "bad" : low ? "warn" : "");
  conn.textContent = g.has_token ? `Connected, ${g.expires_min} min` : "Not connected";
  conn.title = g.has_token ? "Microsoft Graph token; click for a new one" : "Click to connect";
  const paused = job.state === "needs_token", show = R.setup_done && (!g.has_token || paused || recapOpen.token);
  if (show && $("#recap-token").classList.contains("hidden")) renderGraphBar();
  $("#recap-token").classList.toggle("hidden", !show);
  conn.setAttribute("aria-expanded", show);
  $("#recap-now").innerHTML = paused ? `<div class="card warnbox">Paused: the token expired. Get a new one above and the Update goes on.</div>`
    : job.state === "error" ? `<div class="card"><span class="err">The Update stopped: ${esc(job.error)}</span></div>`
    : !running && (l.problems || []).length ? `<div class="card warnbox">${l.problems.map((p) => `<div>${esc(p)}</div>`).join("")}</div>` : "";
  $("#recap-update").disabled = running;
  $("#recap-update").textContent = running ? "Updating..." : "Update";
  const counts = { today: R.decisions.length + R.mine.length, drafts: R.drafts, meetings: R.events.filter((e) => new Date(e.start).toDateString() === new Date().toDateString()).length };
  document.querySelectorAll("#recap-tabs button").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === recapView);
    b.setAttribute("aria-selected", b.dataset.view === recapView);
    b.querySelector("i").textContent = counts[b.dataset.view] || "";
  });
  for (const id of ["#recap-bar", "#recap-tabs"]) $(id).classList.toggle("hidden", !R.setup_done);
}
async function renderRecapView() {
  await ({ today: renderToday, matters: renderMatters, meetings: renderAhead, drafts: renderDrafts, people: renderPeople, ask: renderAsk })[recapView]();
}
function go(view) { recapView = view; if (document.activeElement) document.activeElement.blur(); return loadRecap(); }
$("#recap-tabs").addEventListener("click", (e) => { const b = e.target.closest("button[data-view]"); if (b) go(b.dataset.view); });
$("#recap-tabs").addEventListener("keydown", (e) => {
  const d = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
  if (!d) return;
  const all = [...document.querySelectorAll("#recap-tabs button")], n = all[(all.findIndex((b) => b.dataset.view === recapView) + d + all.length) % all.length];
  n.focus(); recapView = n.dataset.view; loadRecap();
});
$("#recap-conn").addEventListener("click", () => { recapOpen.token = !recapOpen.token; renderRecapHead(R.job && ["queued", "running"].includes(R.job.state)); });
$("#recap-range").addEventListener("change", (e) => $("#recap-since").classList.toggle("hidden", e.target.value !== "since"));
$("#recap-update").addEventListener("click", async () => {
  const v = $("#recap-range").value;
  const range = v === "new" ? { kind: "new" } : v === "since" ? { kind: "since", date: $("#recap-since").value } : { kind: "days", days: Number(v) };
  try { await graphCall("POST", "/api/assistant/update", { range }); loadRecap(); }
  catch (e) { if (R.graph && !R.graph.has_token) recapOpen.token = true; say(e.message); loadRecap(); }
});
const briefs = $("#recap-briefs");
addEventListener("click", (e) => { if (!briefs.contains(e.target)) briefs.open = false; });
addEventListener("keydown", (e) => { if (e.key === "Escape") briefs.open = false; });
briefs.addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-brief]");
  if (!b) return;
  briefs.open = false;
  recapOpen.brief = { working: b.textContent };
  await go("today");
  try { recapOpen.brief = await api("POST", "/api/assistant/brief", { kind: b.dataset.brief }); } catch (err) { recapOpen.brief = null; say(err.message); }
  loadRecap();
});

// ---------------------------------------------------------------- Today
const plain = (t) => String(t || "").replace(/^((re|fw|fwd|aw|odp|pd)\s*:\s*)+/i, "") || t;
const matterSelect = (i) => `<label>Matter <select data-act="matter"><option value="">none</option>${R.matters.filter((m) => m.status !== "closed")
  .map((m) => `<option value="${m.id}" ${m.id === i.matter ? "selected" : ""}>${esc(m.title)}</option>`).join("")}</select></label>`;
function itemRow(i, kind) {
  const link = url(i.link) ? `<a href="${url(i.link)}" target="_blank" rel="noopener" title="${esc(i.title)}">${esc(plain(i.title))}</a>` : esc(plain(i.title));
  const person = i.person || i.people, isOpen = openItems.has(i.key), closed = kind === "closed";
  const head = i.error ? "Not sorted yet" : kind === "waiting" && i.action ? `${esc(i.person || "Others")}: ${esc(i.action)}` : esc(i.action || i.summary || plain(i.title));
  const draft = !closed && ["mail", "teams"].includes(i.source) && kind !== "fyi"
    ? `<button class="mini" data-act="draft">${i.draft ? "Open draft" : kind === "waiting" ? "Remind" : "Draft"}</button>` : "";
  const until = i.status === "snoozed" ? `until ${esc(i.until)}` : i.status === "dropped" ? "not mine" : "done";
  return `<li class="item" data-key="${esc(i.key)}"><div class="what">
      <div class="action">${head}${i.due ? ` <span class="due">${esc(i.due)}</span>` : ""}</div>
      ${i.action && i.summary ? `<div class="sum">${esc(i.summary)}</div>` : ""}
      <div class="meta"><span>${SRC[i.source] || ""}</span> · ${link}${person ? ` · ${esc(person)}` : ""} · ${ago(i.last)}${closed ? ` · <b>${until}</b>` : ""}
        ${i.quiet ? `<span class="quiet">quiet for ${i.days} days</span>` : ""}${i.matter_title ? `<span class="tag">${esc(i.matter_title)}</span>` : i.maybe_title ? `<button class="tag maybe" data-act="maybe" data-mid="${esc(i.maybe)}" title="Add it to this matter">${esc(i.maybe_title)}?</button>` : ""}
        ${i.orders ? `<span class="quiet" title="Check it yourself">this text gives orders to an AI</span>` : ""}</div></div>
    <div class="do">${closed ? `<button class="mini" data-act="reopen">Reopen</button>` : `${draft}<button class="mini" data-act="done">Done</button>
      <button class="mini more" data-act="more" aria-expanded="${isOpen}" aria-label="More for: ${esc(plain(i.title))}">⋯</button>`}</div>
    ${isOpen && !closed ? `<div class="detail"><div class="why"><b>Why is this here?</b> ${i.error ? `<span class="err">${esc(i.error)}</span>` : i.quote ? `<q>${esc(i.quote)}</q>`
        : `<span class="muted">${i.by === "rule" ? "A rule: automatic sender." : i.by === "you" ? "You decided it." : "The model gave no sentence that proves it: check the source."}</span>`}
        ${i.error ? "" : `<button class="mini" data-act="right">That is right</button>`}</div>
      <div class="acts"><span class="muted">Later:</span><button class="mini" data-act="snooze" data-days="1">Tomorrow</button><button class="mini" data-act="snooze" data-days="7">In a week</button>
        <label>Group <select data-act="category">${CATS.map(([c, n]) => `<option value="${c}" ${c === i.category ? "selected" : ""}>${n}</option>`).join("")}</select></label>${matterSelect(i)}
        <button class="mini" data-act="not_mine">Not mine</button><button class="mini" data-act="mute" title="Ignore this conversation from now on">Mute the conversation</button></div></div>` : ""}</li>`;
}
const fold = (id, title, n, inner, cls = "", open = true, note = "") => `<details class="card recap-group ${cls}" data-g="${id}" ${(openGroups[id] ?? open) ? "open" : ""}><summary><b>${title}</b>
  <span class="count">${n}</span>${note ? ` <span class="muted small">${note}</span>` : ""}</summary>${inner}</details>`;
const group = (id, title, list, kind, cls, open, note) => (list.length ? fold(id, title, list.length, `<ul class="recap-items">${list.map((i) => itemRow(i, kind)).join("")}</ul>`, cls, open, note) : "");
const promiseGroup = (id, title, list, mine) => (!list.length ? "" : fold(id, title, list.length, `<ul class="recap-items">${list.map((p) => `<li class="item" data-promise="${p.id}"><div class="what">
    <div class="action">${mine ? "" : esc(p.who) + ": "}${esc(p.what)}${p.due ? ` <span class="due">${esc(p.due)}</span>` : ""}</div><div class="sum">“${esc(p.quote)}”</div>
    <div class="meta">${SRC[p.source] || ""} · ${url(p.link) ? `<a href="${url(p.link)}" target="_blank" rel="noopener">${esc(plain(p.title))}</a>` : esc(plain(p.title))}${mine && p.to ? ` · to ${esc(p.to)}` : ""} · ${ago(p.last)}</div></div>
    <div class="do"><button class="mini" data-act="promise" data-state="dropped">Not a promise</button><button class="mini" data-act="promise" data-state="done">Done</button></div></li>`).join("")}</ul>`));
const BRIEF = { day: "Today", week: "The week", upward: "For my manager", leave: "While you were away" };
function briefCard() {
  const r = recapOpen.brief;
  if (!r) return "";
  if (r.working) return `<div class="card brief muted">Writing: ${esc(r.working)}...</div>`;
  return `<div class="card brief" id="brief"><div class="row"><h2>${BRIEF[r.kind]}</h2><button class="mini" data-act="brief-draft">Make a draft</button>
    <button class="icon" data-act="brief-close" aria-label="Close the brief">✕</button></div><ul>${r.lines.map((x) => `<li>${esc(x)}</li>`).join("")}</ul><div class="muted small">Saved in ${esc(r.file)}</div></div>`;
}
function renderToday() {
  const today = R.events.filter((e) => new Date(e.start).toDateString() === new Date().toDateString());
  const nothing = !R.decisions.length && !R.mine.length && !R.waiting.length && !R.promised.length;
  const empty = !R.updated && !R.job ? `<div class="card empty"><b>Press Update to see your day.</b><br>It reads mail, Teams, Jira, the calendar and recorded meetings, and sorts them into what is yours, what you wait for and what changed.</div>`
    : nothing && R.updated ? `<div class="card empty"><b>Nothing is waiting for you.</b><br>${R.ignored.length} ignored and ${R.changed.length} changed below.</div>` : "";
  draw(briefCard() + empty +
    group("dec", "Decisions waiting for me", R.decisions, "mine", "hot") + group("mine", "Mine", R.mine, "mine", "hot") +
    (today.length ? fold("meet", "Meetings today", today.length, today.map(eventRow).join("")) : "") +
    group("wait", "Waiting for", R.waiting, "waiting", "wait") +
    promiseGroup("prom", "Promised by me", R.promised, true) + promiseGroup("owed", "Promised to me", R.owed, false) +
    (R.suggested.length ? fold("sug", "New matters?", R.suggested.length, `<ul class="recap-items">${R.suggested.map((s) =>
      `<li class="item" data-key="${esc(s.items[0].key)}"><div class="what"><input class="newname" aria-label="Name of the matter" value="${esc(s.name)}"><div class="meta">${esc(s.items.map((x) => plain(x.title)).join(" · "))}</div></div>
       <div class="do"><button class="mini" data-act="no_matter">No</button><button class="mini" data-act="new_matter">Create</button></div></li>`).join("")}</ul>`, "", true, "created only when you say so") : "") +
    group("uns", "Not sorted", R.unsorted, "fyi", "wait", true, "the model could not answer; the next Update tries again") +
    group("chg", "Changed", R.changed, "fyi", "", false) + group("ign", "Ignored", R.ignored, "fyi", "", false) + group("done", "Done and later", R.closed, "closed", "", false) +
    (R.ask_feedback ? `<form class="card feedback"><label>What did the Recap miss today? <input name="t" placeholder="one sentence; stays on this laptop"></label><button>Save</button></form>` : ""));
}
document.addEventListener("toggle", (e) => { if (e.target.dataset && e.target.dataset.g) { openGroups[e.target.dataset.g] = e.target.open; $("#recap-view").lastHtml = ""; } }, true);

// ---------------------------------------------------------------- clicks of the whole view
const UNDO = { done: "Done", not_mine: "Not yours", mute: "Muted", snooze: "Later", category: "Moved" };
async function act(key, action, value) {
  await api("POST", "/api/assistant/act", { key, action, value: value || "" });
  openItems.delete(key);
  await loadRecap();
  if (UNDO[action]) toast(UNDO[action], 6000, ["Undo", async () => { try { await api("POST", "/api/assistant/act", { key, action: "undo", value: "" }); loadRecap(); } catch (e) { say(e.message); } }]);
}
$("#recap").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-act]");
  if (!b || b.tagName === "SELECT") return;
  const a = b.dataset.act, li = b.closest("[data-key]"), key = li && li.dataset.key;
  try {
    if (a === "more") { openItems.has(key) ? openItems.delete(key) : openItems.add(key); return renderToday(); }
    if (a === "right") { await act(key, "right"); return toast("Noted"); }
    if (["done", "not_mine", "mute", "no_matter", "reopen"].includes(a)) return await act(key, a);
    if (a === "maybe") return await act(key, "matter", b.dataset.mid);
    if (a === "snooze") return await act(key, "snooze", plusDays(Number(b.dataset.days)));
    if (a === "new_matter") return await act(key, "new_matter", li.querySelector(".newname").value);
    if (a === "promise") { await api("POST", "/api/assistant/promise", { id: b.closest("[data-promise]").dataset.promise, state: b.dataset.state });
      const id = b.closest("[data-promise]").dataset.promise; await loadRecap();
      return toast("Closed", 6000, ["Undo", async () => { await api("POST", "/api/assistant/promise", { id, state: "open" }); loadRecap(); }]); }
    if (a === "draft") return busyBtn(b, async () => {
      const item = [...R.decisions, ...R.mine, ...R.waiting].find((i) => i.key === key) || {};
      recapOpen.draft = item.draft || (await api("POST", "/api/drafts", { key })).id;
      await go("drafts");
    });
    if (a === "brief-close") { recapOpen.brief = null; return renderToday(); }
    if (a === "brief-draft") { const r = recapOpen.brief; recapOpen.draft = (await api("POST", "/api/drafts", { title: BRIEF[r.kind], body: r.lines.map((x) => "- " + x).join("\n") })).id; return go("drafts"); }
    if (a === "prep") return busyBtn(b, async () => { await openPrep(b.dataset.id, b.dataset.again === "1", b.dataset.person || ""); });
    if (a === "agenda-draft") { const c = recapOpen.prep; recapOpen.draft = (await api("POST", "/api/drafts", { title: c.title, body: "Agenda:\n" + c.agenda.map((x) => "- " + x).join("\n") })).id; return go("drafts"); }
    if (a === "file") { recapOpen.file = b.dataset.file; return go("matters"); }
    if (a === "undo") { const r = await api("POST", "/api/memory/undo", { id: b.dataset.id }); toast("Undone in " + r.file); b.remove(); return; }
    if (a === "accept") { await api("POST", "/api/memory/edit", { file: b.dataset.file, old: b.dataset.old, new: b.dataset.new }); toast("Saved"); b.remove(); return; }
  } catch (err) { say(err.message); }
});
$("#recap").addEventListener("change", async (e) => {
  const s = e.target.closest("select[data-act]");
  if (!s) return;
  s.blur();
  try { await act(s.closest("[data-key]").dataset.key, s.dataset.act, s.value); } catch (err) { say(err.message); }
});
$("#recap").addEventListener("submit", async (e) => {
  const f = e.target.closest("form.feedback");
  if (!f) return;
  e.preventDefault();
  try { await api("POST", "/api/assistant/feedback", { text: f.t.value }); f.t.value = ""; toast("Thank you"); loadRecap(); } catch (err) { say(err.message); }
});

// ---------------------------------------------------------------- Matters and the memory files
async function renderMatters() {
  const mem = await api("GET", "/api/memory");
  const row = (m) => `<li><button class="linkish" data-open-file="matters/${m.id}.md">${esc(m.title)}</button>
    <span class="muted">${esc(m.state)}</span>${m.silent ? `<span class="quiet">Still active? no news since ${esc(m.updated)}</span>` : ""}
    <span class="grow"></span>${m.reason ? `<span class="muted">${esc(m.reason)}</span>` : ""}
    <select data-mid="${m.id}" aria-label="State of ${esc(m.title)}">${["active", "waiting", "parked", "closed"].map((s) => `<option ${s === m.status ? "selected" : ""}>${s}</option>`).join("")}</select></li>`;
  const by = (...st) => R.matters.filter((m) => st.includes(m.status)).map(row).join("");
  const sig = JSON.stringify([R.matters, mem, recapOpen.file]), box = $("#recap-view");
  if (box.lastView === "matters" && box.lastHtml === sig) return;
  box.lastHtml = sig; box.lastView = "matters";
  box.innerHTML = `<form id="matter-new" class="row"><input name="title" aria-label="New matter" placeholder="New matter: a project, a case, a problem you run"><button class="primary">Add</button>
      <button type="button" id="mem-open">Open folder</button></form>
    <ul class="rows matters">${by("active", "waiting") || `<li class="empty">No matters yet. Add one, or accept the ones the Recap suggests.</li>`}</ul>
    ${by("parked", "closed") ? `<details class="card"><summary><b>Parked and closed</b></summary><ul class="rows matters">${by("parked", "closed")}</ul></details>` : ""}
    <div class="row"><h2>Memory</h2><select id="mem-pick" aria-label="Open a memory file"><option value="">Open a file...</option>${mem.files.map((f) => `<option ${f === recapOpen.file ? "selected" : ""}>${esc(f)}</option>`).join("")}</select></div>
    <div id="mem-file"><div class="muted small">${esc(mem.root)}: plain Markdown. Edit it here or in any editor; lines with (you) are yours and stay.</div></div>`;
  $("#mem-pick").onchange = (e) => openFile(e.target.value);
  $("#mem-open").onclick = () => api("POST", "/api/memory/open");
  $("#matter-new").onsubmit = async (e) => {
    e.preventDefault();
    try { const r = await api("POST", "/api/matters", { title: e.target.title.value }); e.target.title.value = ""; recapOpen.file = `matters/${r.id}.md`; loadRecap(); } catch (err) { say(err.message); }
  };
  document.querySelectorAll("[data-open-file]").forEach((b) => (b.onclick = () => openFile(b.dataset.openFile)));
  document.querySelectorAll("select[data-mid]").forEach((s) => (s.onchange = async () => {
    const extra = {};
    if (s.value === "parked") { extra.reason = "parked in the window"; extra.return_on = plusDays(30); }
    s.blur();
    await api("POST", `/api/matters/${s.dataset.mid}/status`, { status: s.value, ...extra });
    if (s.value === "parked") toast("Parked; it comes back in 30 days");
    loadRecap();
  }));
  if (recapOpen.file) openFile(recapOpen.file);
}
async function openFile(name) {
  recapOpen.file = name;
  $("#recap-view").lastHtml = "";
  if (!name) return;
  const f = await api("GET", "/api/memory/file?name=" + encodeURIComponent(name));
  $("#mem-file").innerHTML = `<div class="card"><div class="row"><b>${esc(name)}</b><span class="grow"></span><button id="mem-save" class="primary">Save</button></div>
    <textarea id="mem-text" aria-label="File" rows="16" spellcheck="false">${esc(f.text)}</textarea></div>`;
  $("#mem-save").onclick = async () => {
    try { await api("POST", "/api/memory/file", { name, text: $("#mem-text").value, opened: f.text }); f.text = $("#mem-text").value; toast("Saved"); } catch (err) { say(err.message); }
  };
}

// ---------------------------------------------------------------- Meetings ahead
const eventRow = (e) => `<div class="event"><time>${clockOf(e.start)}</time><div class="grow"><b>${esc(e.title)}</b> <span class="muted">${esc(e.people.slice(0, 6).join(", "))}${e.people.length > 6 ? ` +${e.people.length - 6}` : ""}</span></div>
  ${url(e.join) ? `<a class="btn" href="${url(e.join)}" target="_blank" rel="noopener">Join</a>` : ""}
  <button class="mini" data-act="prep" data-id="${esc(e.id)}">${e.prepared ? "Open card" : "Prepare me"}</button></div>`;
async function openPrep(id, again, person) {
  recapOpen.prep = await api("POST", "/api/assistant/prep", { id, again, person });
  recapView = "meetings";
  await loadRecap();
}
function prepCard(c) {
  const part = (t, l) => (l.length ? `<h3>${t}</h3><ul>${l.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "");
  return `<div class="card brief"><div class="row"><h2>${esc(c.title)}</h2><button class="mini" data-act="prep" data-id="${esc(c.id)}" data-again="1" ${c.id.startsWith("person:") ? `data-person="${esc(c.id.slice(7))}"` : ""}>Make again</button>
      ${c.agenda.length ? `<button class="mini" data-act="agenda-draft">Draft the agenda</button>` : ""}<button class="mini" data-act="file" data-file="${esc(c.file)}">Edit</button></div>
    ${c.goal ? `<p><b>Goal:</b> ${esc(c.goal)}</p>` : ""}${part("What I know", c.known)}${part("Open from before", c.open)}${part("To ask", c.questions)}${part("Agenda", c.agenda)}</div>`;
}
function renderAhead() {
  const days = {};
  R.events.forEach((e) => (days[dayOf(e.start)] ||= []).push(e));
  draw((recapOpen.prep ? prepCard(recapOpen.prep) : "") +
    (Object.entries(days).map(([d, l]) => `<h3>${esc(d)}</h3><div class="card">${l.map(eventRow).join("")}</div>`).join("") || `<div class="card empty"><b>No meetings to show.</b><br>The calendar of the next week comes with an Update.</div>`));
}

// ---------------------------------------------------------------- Drafts
async function renderDrafts() {
  const d = await api("GET", "/api/drafts"), sig = JSON.stringify(d);
  if ($("#recap-view").lastView === "drafts" && $("#recap-view").lastHtml === sig) return;
  d.drafts.sort((a, b) => (b.id === recapOpen.draft) - (a.id === recapOpen.draft));
  $("#recap-view").lastHtml = sig; $("#recap-view").lastView = "drafts"; $(".main").scrollTop = 0;
  $("#recap-view").innerHTML = `<p class="muted small">Drafts stay here until you use them. The app never sends anything.</p>` + (d.drafts.map((x) => `<div class="card draft ${x.id === recapOpen.draft ? "now" : ""}" data-did="${x.id}" data-key="${esc(x.key)}">
    <div class="action" title="${esc(x.title)}">${esc(plain(x.title))}</div>
    <div class="meta">${{ reply: "Answer", reminder: "Reminder", brief: "Text" }[x.kind] || esc(x.kind)}${x.to ? " to " + esc(x.to) : ""} · ${esc(x.created.slice(5))}${url(x.link) ? ` · <a href="${url(x.link)}" target="_blank" rel="noopener">Open the conversation</a>` : ""}</div>
    <textarea aria-label="Draft" rows="${Math.min(14, Math.max(3, x.body.split("\n").length + 1))}">${esc(x.body)}</textarea>
    <form class="row"><input name="remark" aria-label="What is wrong with it" placeholder="What is wrong with it? e.g. shorter, no greeting, say Thursday"><button>Correct it</button></form>
    <div class="rule"></div>
    <div class="acts">${x.key.startsWith("mail:") ? `<button class="mini primary" data-d="outlook" title="${d.outlook ? "A reply in the Outlook Drafts folder; you send it there" : "Needs Mail.ReadWrite in the token"}">${x.outlook ? "In Outlook drafts ✓" : "Put into Outlook drafts"}</button><button class="mini" data-d="copy">Copy</button>`
        : `<button class="mini primary" data-d="copy">Copy</button>`}
      <span class="grow"></span><button class="mini" data-d="used" title="You sent it: the draft goes away">I used it</button><button class="mini" data-d="dropped">Throw away</button></div></div>`).join("") || `<div class="card empty"><b>No drafts.</b><br>An Update drafts answers to questions; or press Draft next to an item in Today.</div>`);
  document.querySelectorAll(".draft").forEach((el) => {
    const did = el.dataset.did, ta = el.querySelector("textarea");
    const save = () => api("POST", `/api/drafts/${did}`, { body: ta.value });
    ta.onchange = save;
    el.querySelector("form").onsubmit = (e) => {
      e.preventDefault();
      const f = e.target;
      busyBtn(f.querySelector("button"), async () => {
        const r = await api("POST", `/api/drafts/${did}/revise`, { remark: f.remark.value, body: ta.value });
        ta.value = r.draft.body; f.remark.value = "";
        el.querySelector(".rule").innerHTML = r.rule ? `Always write like this? <b>${esc(r.rule)}</b> <button class="mini" type="button">Yes, remember</button>` : "";
        const y = el.querySelector(".rule button");
        if (y) y.onclick = async () => { await api("POST", "/api/style", { rule: r.rule }); el.querySelector(".rule").textContent = "Remembered in style.md."; };
      });
    };
    el.querySelectorAll("[data-d]").forEach((b) => (b.onclick = async () => {
      const a = b.dataset.d;
      try {
        if (a === "copy") { await save(); await navigator.clipboard.writeText(ta.value); return toast("Copied"); }
        if (a === "outlook") { await save(); await graphCall("POST", `/api/drafts/${did}/outlook`); toast("It is in the Outlook drafts"); return loadRecap(); }
        await api("POST", `/api/drafts/${did}/close`, { status: a }); loadRecap();
      } catch (err) { say(err.message); }
    }));
  });
}

// ---------------------------------------------------------------- People
async function renderPeople() {
  const list = await api("GET", "/api/people"), sig = JSON.stringify(list), box = $("#recap-view");
  if (box.lastView === "people" && box.lastHtml === sig) return;
  box.lastHtml = sig; box.lastView = "people";
  box.innerHTML = `<form id="person-new" class="row"><input name="name" aria-label="Add a person" placeholder="Add a person: name as in Outlook"><button class="primary">Add</button></form>
    <ul class="rows matters">${list.map((p) => `<li><button class="linkish" data-person="${esc(p.name)}">${esc(p.name)}</button><span class="muted">${esc(p.title)}</span><span class="grow"></span>
      <span class="muted">${p.mine} from me · ${p.theirs} from them${p.last ? " · " + ago(p.last) : ""}</span>${p.added ? `<button class="icon" data-remove="${esc(p.name)}" title="Remove">✕</button>` : ""}</li>`).join("") ||
      `<li class="empty">Your direct reports come with the first Update; add anyone else here.</li>`}</ul><div id="person-card"></div>`;
  $("#person-new").onsubmit = async (e) => { e.preventDefault(); try { await api("POST", "/api/people", { name: e.target.name.value }); e.target.name.value = ""; loadRecap(); } catch (err) { say(err.message); } };
  document.querySelectorAll("button[data-person]").forEach((b) => { if (!b.dataset.act) b.onclick = () => showPerson(b.dataset.person); });
  document.querySelectorAll("button[data-remove]").forEach((b) => (b.onclick = async () => { await api("POST", "/api/people/remove", { name: b.dataset.remove }); loadRecap(); }));
  if (recapOpen.person && list.some((p) => p.name === recapOpen.person)) showPerson(recapOpen.person);
}
async function showPerson(name) {
  recapOpen.person = name;
  const p = await api("GET", "/api/people/card?name=" + encodeURIComponent(name));
  const part = (t, l) => `<h3>${t}</h3>${l.length ? `<ul>${l.map((x) => `<li>${url(x.link) ? `<a href="${url(x.link)}" target="_blank" rel="noopener">${esc(x.title)}</a>` : esc(x.title)} <span class="muted">${esc(x.action || x.summary || "")} ${esc(x.due || "")}</span></li>`).join("")}</ul>` : `<div class="muted">nothing open</div>`}`;
  $("#person-card").innerHTML = `<div class="card brief"><div class="row"><h2>${esc(p.name)}</h2><button class="mini" data-act="prep" data-id="" data-person="${esc(p.name)}">Prepare a 1:1</button></div>
    ${part("I owe", p.mine)}${part("They owe", p.theirs)}<h3>Notes</h3><ul>${p.notes.map((n) => `<li>${esc(n.replace(/^- /, ""))}</li>`).join("")}</ul>
    <form class="row"><input name="note" aria-label="Note" placeholder="A note from your 1:1 (stays in ${esc(p.file)})"><button>Add</button></form></div>`;
  $("#person-card form").onsubmit = async (e) => { e.preventDefault(); try { await api("POST", "/api/people/note", { name, note: e.target.note.value }); showPerson(name); } catch (err) { say(err.message); } };
}

// ---------------------------------------------------------------- Ask
const READY = ["What am I late with?", "Who am I waiting for, and since when?", "What changed this week?", "What should I do first today?"];
async function renderAsk() {
  const talk = await api("GET", "/api/assistant/talk"), sig = JSON.stringify(talk), box = $("#recap-view");
  if (box.lastView === "ask" && box.lastHtml === sig) return;
  box.lastHtml = sig; box.lastView = "ask";
  box.innerHTML = `<div class="askpage"><div class="talk" aria-live="polite">${talk.length ? "" : `<div class="empty muted"><b>Ask about your work.</b><br>Answers come from your matters, mail, chats, calendar and notes, with sources. Say what is different and it is remembered.</div>`}${talk.map((t) => `<div class="q">${esc(t.question)}</div><div class="a"><div class="text">${esc(t.answer)}</div>
      ${t.sources.length ? `<div class="muted small">${t.sources.map((s) => url(s.link) ? `<a href="${url(s.link)}" target="_blank" rel="noopener">${esc(s.title)}</a>` : s.file ? `<button class="linkish" data-act="file" data-file="${esc(s.file)}">${esc(s.title)}</button>` : esc(s.title)).join(" · ")}</div>` : ""}
      ${t.edits.map((x) => `<div class="edit ${x.state}"><b>${esc(x.file)}</b>${x.old ? `<div class="del">${esc(x.old)}</div>` : ""}${x.new ? `<div class="ins">${esc(x.new)}</div>` : ""}
        ${x.state === "applied" ? `<button class="mini" data-act="undo" data-id="${x.id}">Undo</button>` : x.state === "proposed" ? `<button class="mini" data-act="accept" data-file="${esc(x.file)}" data-old="${esc(x.old)}" data-new="${esc(x.new)}">Save this</button>` : `<span class="err">${esc(x.error)}</span>`}</div>`).join("")}</div>`).join("")}</div>
    <div class="askbar"><div class="chips">${READY.map((q) => `<button class="mini" data-q="${esc(q)}">${esc(q)}</button>`).join("")}${talk.length ? `<button class="mini" id="talk-clear">New conversation</button>` : ""}</div>
    <form id="ask-form" class="row"><textarea name="q" rows="2" aria-label="Your question" placeholder="Ask, or say what is different: &quot;no, the deadline is Thursday&quot;"></textarea><button class="primary">Ask</button></form></div></div>`;
  $(".main").scrollTop = talk.length ? $(".main").scrollHeight : 0;
  const ask = (q) => busyBtn($("#ask-form button"), async () => { if (!q.trim()) return; await api("POST", "/api/assistant/ask", { question: q }); $("#ask-form").q.value = ""; await renderAsk(); $("#ask-form").q.focus(); });
  $("#ask-form").onsubmit = (e) => { e.preventDefault(); ask(e.target.q.value); };
  $("#ask-form").q.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(e.target.value); } };
  document.querySelectorAll("[data-q]").forEach((b) => (b.onclick = () => ask(b.dataset.q)));
  const c = $("#talk-clear");
  if (c) c.onclick = async () => { await api("DELETE", "/api/assistant/talk"); renderAsk(); };
}

// ---------------------------------------------------------------- first start: three short steps
let setupStep = 0;
function renderSetup() {
  if ($("#setup-form")) return;
  $("#recap-view").innerHTML = `<form id="setup-form" class="card form setup"><div class="steps" aria-hidden="true"><i></i><i></i><i></i></div>
    <fieldset><h2>Where should the model run?</h2>
      <label class="pick"><input type="radio" name="provider" value="copilot"><span><b>GitHub Copilot</b><br>Good on every laptop, needs a Copilot licence. The text of your mail, chats and meetings is sent to GitHub Copilot.</span></label>
      <label class="pick"><input type="radio" name="provider" value="local"><span><b>This laptop (Ollama)</b><br>Nothing leaves it. Needs a good GPU.</span></label></fieldset>
    <fieldset><h2>About your work</h2><p class="muted">You can dictate the answers; all of it can be changed later.</p>
      <label>What is your role, and what do people come to you for?<textarea name="role" rows="2"></textarea></label>
      <label>The 3 to 7 things you are running right now, one per line<textarea name="running" rows="4"></textarea></label>
      <label>Whose messages must you never miss?<textarea name="people" rows="1"></textarea></label></fieldset>
    <fieldset><h2>Last two</h2>
      <label>What did you find out too late recently?<textarea name="late" rows="2"></textarea></label>
      <label>Anything this tool must never read?<textarea name="never" rows="3" placeholder="HR&#10;folder: Private"></textarea><small>People, words in a title, or folder: Name; one per line.</small></label></fieldset>
    <div class="row"><button type="button" id="setup-back">Back</button><span class="err" id="setup-err" role="alert"></span><span class="grow"></span><button class="primary" id="setup-next">Next</button></div></form>`;
  const f = $("#setup-form"), sets = [...f.querySelectorAll("fieldset")];
  const show = (n) => {
    setupStep = n;
    sets.forEach((x, k) => x.classList.toggle("hidden", k !== n));
    f.querySelectorAll(".steps i").forEach((x, k) => x.classList.toggle("on", k <= n));
    $("#setup-back").classList.toggle("hidden", n === 0);
    $("#setup-next").textContent = n === 2 ? "Start" : "Next";
    $("#setup-err").textContent = "";
    const first = sets[n].querySelector("textarea");
    if (first) first.focus();
  };
  show(0);
  $("#setup-back").onclick = () => show(setupStep - 1);
  f.onsubmit = async (e) => {
    e.preventDefault();
    if (setupStep === 0 && !f.provider.value) return ($("#setup-err").textContent = "Choose one of the two.");
    if (setupStep < 2) return show(setupStep + 1);
    const body = Object.fromEntries(["role", "running", "people", "late", "never", "provider"].map((k) => [k, f[k].value]));
    try {
      await api("POST", "/api/assistant/setup", body);
      f.remove();
      await loadRecap();
      if (body.provider === "copilot") { const c = await api("GET", "/api/ai/copilot"); if (!c.ok) toast("One more step: sign in to GitHub Copilot", 15000, ["Sign in", () => showTab("ai")]); }
    } catch (err) { $("#setup-err").textContent = err.message; }
  };
}
