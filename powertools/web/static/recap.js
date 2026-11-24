"use strict";
// The Recap: Today, Matters, Meetings, Drafts, People, Ask. Uses $, api, esc, ago, toast, graphCall from app.js.
let R = null, recapView = "today", recapTimer = null, recapOpen = { matter: "", person: "", prep: "", file: "", edit: false };
const url = (u) => (/^https?:\/\//.test(u || "") ? esc(u) : "");
const SRC = { mail: "Mail", teams: "Teams", jira: "Jira", meeting: "Meeting" };
const CATS = [["todo", "To do"], ["reply", "To answer"], ["waiting", "Waiting for"], ["fyi", "Good to know"], ["ignore", "Ignore"]];
const clockOf = (iso) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const dayOf = (iso) => new Date(iso).toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" });
const plusDays = (n) => { const d = new Date(); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };
const busyBtn = async (b, fn) => {
  const label = b.textContent;
  b.disabled = true; b.textContent = "Working...";
  try { await fn(); } catch (e) { toast(e.message.slice(0, 200), 5000); }
  b.disabled = false; b.textContent = label;
};

// ---------------------------------------------------------------- load
async function loadRecap() {
  R = await api("GET", "/api/assistant");
  const running = R.job && ["queued", "running"].includes(R.job.state);
  renderRecapHead(running);
  if (!R.setup_done) renderSetup();
  else if (!typing()) await renderRecapView();
  clearTimeout(recapTimer);
  if (running) recapTimer = setTimeout(() => { if ($("#recap").classList.contains("active")) loadRecap(); }, 2000);
}
const typing = () => { const a = document.activeElement; return a && $("#recap-view").contains(a) && /INPUT|TEXTAREA|SELECT/.test(a.tagName) && (a.value || "") !== ""; };
function renderRecapHead(running) {
  const l = R.last || {}, llm = R.llm || {}, job = R.job || {};
  const where = llm.provider === "copilot" ? `<span class="where cloud" title="Text goes to GitHub Copilot">Copilot · ${esc(llm.model)}</span>`
    : `<span class="where" title="Nothing leaves the laptop">Local · ${esc(llm.model)}</span>`;
  const when = R.updated ? "Updated " + new Date(R.updated * 1000).toLocaleString([], { dateStyle: "short", timeStyle: "short" }) : "Not updated yet";
  $("#recap-status").innerHTML = `${where} <span>${esc(when)}${l.new !== undefined ? ` · ${l.new} new of ${l.found}, ${l.seconds} s${l.credits ? `, ${l.credits} credits` : ""}` : ""}</span>
    ${llm.provider === "copilot" ? `<span class="muted">${llm.credits_today} credits today</span>` : ""}
    ${R.reviewed ? `<span class="muted">you checked ${R.reviewed}, right ${R.agreed}</span>` : ""}`;
  const pct = job.total ? Math.round(100 * job.done / job.total) : 0;
  $("#recap-now").innerHTML = running
    ? `<div class="card recap-now"><b>Working:</b> ${esc(R.current || job.state)}${job.total ? `<div class="bar"><span style="width:${pct}%"></span></div>` : ""}
        ${job.state === "queued" && R.ahead.length ? `<div class="muted small">First: ${esc(R.ahead.join(", "))}</div>` : ""}</div>`
    : job.state === "needs_token" ? `<div class="card"><span class="warn">Paused: the token expired. Get a new one above and the Update goes on.</span></div>`
    : job.state === "error" ? `<div class="card"><span class="err">The Update stopped: ${esc(job.error)}</span></div>`
    : (l.problems || []).length ? `<div class="card warnbox">${l.problems.map((p) => `<div>${esc(p)}</div>`).join("")}</div>` : "";
  $("#recap-update").disabled = running;
  const counts = { today: R.decisions.length + R.mine.length, drafts: R.drafts, meetings: R.events.filter((e) => new Date(e.start).toDateString() === new Date().toDateString()).length };
  document.querySelectorAll("#recap-tabs button").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === recapView);
    b.querySelector("i").textContent = counts[b.dataset.view] || "";
  });
  $("#recap-bar").classList.toggle("hidden", !R.setup_done);
  $("#recap-tabs").classList.toggle("hidden", !R.setup_done);
}
async function renderRecapView() {
  await ({ today: renderToday, matters: renderMatters, meetings: renderAhead, drafts: renderDrafts, people: renderPeople, ask: renderAsk })[recapView]();
}
$("#recap-tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-view]");
  if (!b) return;
  recapView = b.dataset.view;
  document.activeElement.blur();
  loadRecap();
});
$("#recap-range").addEventListener("change", (e) => $("#recap-since").classList.toggle("hidden", e.target.value !== "since"));
$("#recap-update").addEventListener("click", async () => {
  const v = $("#recap-range").value;
  const range = v === "new" ? { kind: "new" } : v === "since" ? { kind: "since", date: $("#recap-since").value } : { kind: "days", days: Number(v) };
  try { await graphCall("POST", "/api/assistant/update", { range }); loadRecap(); } catch (e) { toast(e.message, 4000); }
});
$("#recap-briefs").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-brief]");
  if (b) busyBtn(b, async () => {
    const r = await api("POST", "/api/assistant/brief", { kind: b.dataset.brief });
    const title = { day: "Today", week: "The week", upward: "For your manager", leave: "Back from leave" }[r.kind];
    $("#recap-brief").innerHTML = `<div class="card brief"><div class="row"><h2>${title}</h2><button data-act="brief-draft" data-title="${title}">Make a draft</button>
      <button class="icon" data-act="brief-close" title="Close">✕</button></div><ul>${r.lines.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
      <div class="muted small">Saved in ${esc(r.file)}</div></div>`;
  });
});

// ---------------------------------------------------------------- Today
const matterSelect = (i) => `<select data-act="matter" data-key="${esc(i.key)}" title="Matter"><option value="">no matter</option>${R.matters.filter((m) => m.status !== "closed")
  .map((m) => `<option value="${m.id}" ${m.id === i.matter ? "selected" : ""}>${esc(m.title)}</option>`).join("")}</select>`;
function itemRow(i, kind) {
  const title = url(i.link) ? `<a href="${url(i.link)}" target="_blank" rel="noopener">${esc(i.title)}</a>` : `<b>${esc(i.title)}</b>`;
  return `<li data-key="${esc(i.key)}"><div class="recap-head"><span class="src">${SRC[i.source] || ""}</span>${title}
      <span class="muted">${esc(i.person || i.people)} · ${ago(i.last)}</span>
      ${i.quiet ? `<span class="quiet">quiet for ${i.days} days</span>` : ""}${i.matter_title ? `<span class="tag">${esc(i.matter_title)}</span>` : i.maybe_title ? `<button class="tag maybe" data-act="maybe" data-mid="${esc(i.maybe)}" title="Add it to this matter">${esc(i.maybe_title)}?</button>` : ""}
      ${i.orders ? `<span class="quiet" title="Check it yourself">this text gives orders to an AI</span>` : ""}</div>
    ${i.error ? `<div class="err small">Not sorted: ${esc(i.error)}</div>` : ""}
    ${i.summary ? `<div>${esc(i.summary)}</div>` : ""}
    ${i.action ? `<div class="action">${kind === "waiting" ? "Waiting for: " : "→ "}${esc(i.action)}${i.due ? ` <span class="warn">(${esc(i.due)})</span>` : ""}</div>` : ""}
    <div class="acts"><button class="mini" data-act="done">Done</button>
      ${["mail", "teams"].includes(i.source) && kind !== "fyi" ? `<button class="mini" data-act="draft">${i.draft ? "Open draft" : kind === "waiting" ? "Draft a reminder" : "Draft an answer"}</button>` : ""}
      <button class="mini" data-act="snooze" data-days="1">Tomorrow</button><button class="mini" data-act="snooze" data-days="7">In a week</button>
      <button class="mini" data-act="why">Why is this here?</button>
      <select data-act="category" title="Move to the right group">${CATS.map(([c, l]) => `<option value="${c}" ${c === i.category ? "selected" : ""}>${l}</option>`).join("")}</select>
      ${matterSelect(i)}<button class="mini" data-act="not_mine">Not mine</button><button class="mini" data-act="mute" title="Ignore this conversation from now on">Mute</button></div>
    <div class="whybox hidden">${i.quote ? `<blockquote>${esc(i.quote)}</blockquote>` : `<span class="muted">${i.by === "rule" ? "A rule: automatic sender." : i.by === "you" ? "You decided it." : "The model gave no sentence that proves it: check the source."}</span>`}
      <button class="mini" data-act="right">That is right</button></div></li>`;
}
const group = (title, list, kind, cls = "", open = true, note = "") => !list.length ? "" : `<details class="card recap-group ${cls}" ${open ? "open" : ""}><summary><b>${title}</b>
  <span class="muted">${list.length}</span>${note ? ` <span class="muted small">${note}</span>` : ""}</summary><ul class="recap-items">${list.map((i) => itemRow(i, kind)).join("")}</ul></details>`;
const promiseGroup = (title, list, mine) => !list.length ? "" : `<details class="card recap-group" open><summary><b>${title}</b> <span class="muted">${list.length}</span></summary><ul class="recap-items">
  ${list.map((p) => `<li data-promise="${p.id}"><div class="recap-head"><span class="src">${SRC[p.source] || ""}</span><b>${esc(p.what)}</b>
    <span class="muted">${mine ? "to " + esc(p.to || "?") : esc(p.who)} · ${ago(p.last)}</span>${p.due ? `<span class="warn">${esc(p.due)}</span>` : ""}</div>
    <div class="muted">“${esc(p.quote)}” ${url(p.link) ? `<a href="${url(p.link)}" target="_blank" rel="noopener">${esc(p.title)}</a>` : esc(p.title)}</div>
    <div class="acts"><button class="mini" data-act="promise" data-state="done">Done</button><button class="mini" data-act="promise" data-state="dropped">Not a promise</button></div></li>`).join("")}</ul></details>`;
function renderToday() {
  const today = R.events.filter((e) => new Date(e.start).toDateString() === new Date().toDateString());
  const empty = !R.updated && !R.job ? `<div class="card empty">Connect above and press <b>Update</b>: mail, Teams, Jira, the calendar and recorded meetings in one go.</div>` : "";
  $("#recap-view").innerHTML = empty +
    group("Decisions waiting for me", R.decisions, "mine", "todo") + group("Mine", R.mine, "mine", "todo") +
    group("Waiting for", R.waiting, "waiting", "wait", true, `quiet after ${R.silence_days} days`) +
    promiseGroup("Promised by me", R.promised, true) + promiseGroup("Promised to me", R.owed, false) +
    (today.length ? `<details class="card recap-group" open><summary><b>Meetings today</b> <span class="muted">${today.length}</span></summary>${today.map(eventRow).join("")}</details>` : "") +
    (R.suggested.length ? `<details class="card recap-group" open><summary><b>New matters?</b> <span class="muted">created only when you say so</span></summary><ul class="recap-items">${R.suggested.map((s) =>
      `<li data-key="${esc(s.items[0].key)}"><div class="recap-head"><input class="newname" value="${esc(s.name)}"><span class="muted">${esc(s.items.map((x) => x.title).join(" · "))}</span></div>
       <div class="acts"><button class="mini primary" data-act="new_matter">Create</button><button class="mini" data-act="no_matter">No</button></div></li>`).join("")}</ul></details>` : "") +
    group("Not sorted", R.unsorted, "fyi", "", true, "the model could not answer; the next Update tries again") +
    group("Changed", R.changed, "fyi", "", false) + group("Ignored", R.ignored, "fyi", "", false) +
    (R.ask_feedback ? `<form class="card feedback"><label>What did the Recap miss today? <input name="t" placeholder="one sentence; stays on this laptop"></label><button>Save</button></form>` : "");
}

// ---------------------------------------------------------------- clicks of the whole view
$("#recap").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-act]");
  if (!b || b.tagName === "SELECT") return;
  const act = b.dataset.act, li = b.closest("[data-key]"), key = li && li.dataset.key;
  const post = async (action, value) => { await api("POST", "/api/assistant/act", { key, action, value: value || "" }); await loadRecap(); };
  try {
    if (act === "why") return b.closest("li").querySelector(".whybox").classList.toggle("hidden");
    if (["done", "not_mine", "mute", "right", "no_matter", "reopen"].includes(act)) { await post(act); if (act === "right") toast("Noted"); return; }
    if (act === "maybe") return post("matter", b.dataset.mid);
    if (act === "snooze") return post("snooze", plusDays(Number(b.dataset.days)));
    if (act === "new_matter") return post("new_matter", li.querySelector(".newname").value);
    if (act === "promise") { await api("POST", "/api/assistant/promise", { id: b.closest("[data-promise]").dataset.promise, state: b.dataset.state }); return loadRecap(); }
    if (act === "draft") return busyBtn(b, async () => {
      const item = [...R.decisions, ...R.mine, ...R.waiting].find((i) => i.key === key) || {};
      if (!item.draft) await api("POST", "/api/drafts", { key });
      recapView = "drafts"; await loadRecap();
    });
    if (act === "brief-close") return ($("#recap-brief").innerHTML = "");
    if (act === "brief-draft") {
      const body = [...document.querySelectorAll("#recap-brief li")].map((x) => "- " + x.textContent).join("\n");
      await api("POST", "/api/drafts", { title: b.dataset.title, body }); recapView = "drafts"; return loadRecap();
    }
    if (act === "prep") return busyBtn(b, async () => { await openPrep(b.dataset.id, b.dataset.again === "1", b.dataset.person || ""); });
    if (act === "agenda-draft") { const c = recapOpen.prep; await api("POST", "/api/drafts", { title: c.title, body: "Agenda:\n" + c.agenda.map((x) => "- " + x).join("\n") }); recapView = "drafts"; return loadRecap(); }
    if (act === "file") { recapOpen.file = b.dataset.file; recapView = "matters"; return loadRecap(); }
    if (act === "undo") { const r = await api("POST", "/api/memory/undo", { id: b.dataset.id }); toast("Undone in " + r.file); b.remove(); return; }
    if (act === "accept") { await api("POST", "/api/memory/edit", { file: b.dataset.file, old: b.dataset.old, new: b.dataset.new }); toast("Saved"); b.remove(); return; }
  } catch (err) { toast(err.message, 5000); }
});
$("#recap").addEventListener("change", async (e) => {
  const s = e.target.closest("select[data-act]");
  if (!s) return;
  s.blur();
  try { await api("POST", "/api/assistant/act", { key: s.closest("[data-key]").dataset.key, action: s.dataset.act, value: s.value }); loadRecap(); }
  catch (err) { toast(err.message, 4000); }
});
$("#recap").addEventListener("submit", async (e) => {
  const f = e.target.closest("form.feedback");
  if (!f) return;
  e.preventDefault();
  await api("POST", "/api/assistant/feedback", { text: f.t.value });
  f.t.value = ""; toast("Thank you"); loadRecap();
});

// ---------------------------------------------------------------- Matters and the memory files
async function renderMatters() {
  const mem = await api("GET", "/api/memory");
  const row = (m) => `<li><button class="linkish" data-open-file="matters/${m.id}.md">${esc(m.title)}</button>
    <span class="muted">${esc(m.state)}</span>${m.silent ? `<span class="quiet">Still active? no news since ${esc(m.updated)}</span>` : ""}
    <span class="grow"></span>${m.reason ? `<span class="muted">${esc(m.reason)}</span>` : ""}
    <select data-mid="${m.id}">${["active", "waiting", "parked", "closed"].map((s) => `<option ${s === m.status ? "selected" : ""}>${s}</option>`).join("")}</select></li>`;
  const by = (...st) => R.matters.filter((m) => st.includes(m.status)).map(row).join("");
  $("#recap-view").innerHTML = `<form id="matter-new" class="row"><input name="title" placeholder="New matter: a project, a case, a problem you run"><button class="primary">Add</button>
      <button type="button" id="mem-open">Open folder</button></form>
    <ul class="rows matters">${by("active", "waiting") || `<li class="empty">No matters yet. Add one, or accept the ones the Recap suggests.</li>`}</ul>
    ${by("parked", "closed") ? `<details class="card"><summary><b>Parked and closed</b></summary><ul class="rows matters">${by("parked", "closed")}</ul></details>` : ""}
    <div class="row"><h2>Memory</h2><select id="mem-pick"><option value="">Open a file...</option>${mem.files.map((f) => `<option ${f === recapOpen.file ? "selected" : ""}>${esc(f)}</option>`).join("")}</select></div>
    <div id="mem-file"><div class="muted small">${esc(mem.root)}: plain Markdown. Edit it here or in any editor; lines with (you) are yours and stay.</div></div>`;
  $("#mem-pick").onchange = (e) => openFile(e.target.value);
  $("#mem-open").onclick = () => api("POST", "/api/memory/open");
  $("#matter-new").onsubmit = async (e) => {
    e.preventDefault();
    try { const r = await api("POST", "/api/matters", { title: e.target.title.value }); e.target.title.value = ""; recapOpen.file = `matters/${r.id}.md`; loadRecap(); } catch (err) { toast(err.message); }
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
  if (!name) return;
  const f = await api("GET", "/api/memory/file?name=" + encodeURIComponent(name));
  $("#mem-file").innerHTML = `<div class="card"><div class="row"><b>${esc(name)}</b><span class="grow"></span><button id="mem-save" class="primary">Save</button></div>
    <textarea id="mem-text" rows="22" spellcheck="false">${esc(f.text)}</textarea></div>`;
  $("#mem-save").onclick = async () => {
    try { await api("POST", "/api/memory/file", { name, text: $("#mem-text").value, opened: f.text }); f.text = $("#mem-text").value; toast("Saved"); } catch (err) { toast(err.message, 5000); }
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
  $("#recap-view").innerHTML = (recapOpen.prep ? prepCard(recapOpen.prep) : "") +
    (Object.entries(days).map(([d, l]) => `<h3>${esc(d)}</h3><div class="card">${l.map(eventRow).join("")}</div>`).join("") || `<div class="card empty">The calendar comes with the next Update.</div>`);
}

// ---------------------------------------------------------------- Drafts
async function renderDrafts() {
  const d = await api("GET", "/api/drafts");
  $("#recap-view").innerHTML = `<p class="muted small">Drafts stay here until you use them. The app never sends anything.</p>` + (d.drafts.map((x) => `<div class="card draft" data-did="${x.id}" data-key="${esc(x.key)}">
    <div class="recap-head"><span class="tag">${esc(x.kind)}</span><b>${esc(x.title)}</b><span class="muted">${x.to ? "to " + esc(x.to) : ""} · ${esc(x.created)}</span>
      ${url(x.link) ? `<a href="${url(x.link)}" target="_blank" rel="noopener">Open the conversation</a>` : ""}</div>
    <textarea rows="${Math.min(14, Math.max(4, x.body.split("\n").length + 2))}">${esc(x.body)}</textarea>
    <form class="row"><input name="remark" placeholder="What is wrong with it? e.g. shorter, no greeting, say Thursday (dictate with the hotkey)"><button>Correct it</button></form>
    <div class="rule"></div>
    <div class="acts"><button class="mini" data-d="copy">Copy</button>${x.key.startsWith("mail:") ? `<button class="mini" data-d="outlook" title="${d.outlook ? "" : "Needs Mail.ReadWrite in the token"}">${x.outlook ? "In Outlook drafts ✓" : "Put into Outlook drafts"}</button>` : ""}
      <button class="mini" data-d="used">Used</button><button class="mini" data-d="dropped">Throw away</button></div></div>`).join("") || `<div class="card empty">No drafts. Answers to questions are drafted during an Update, or press "Draft an answer" in Today.</div>`);
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
      } catch (err) { toast(err.message, 6000); }
    }));
  });
}

// ---------------------------------------------------------------- People
async function renderPeople() {
  const list = await api("GET", "/api/people");
  $("#recap-view").innerHTML = `<form id="person-new" class="row"><input name="name" placeholder="Add a person: name as in Outlook"><button class="primary">Add</button></form>
    <ul class="rows matters">${list.map((p) => `<li><button class="linkish" data-person="${esc(p.name)}">${esc(p.name)}</button><span class="muted">${esc(p.title)}</span><span class="grow"></span>
      <span class="muted">${p.mine} from me · ${p.theirs} from them${p.last ? " · " + ago(p.last) : ""}</span>${p.added ? `<button class="icon" data-remove="${esc(p.name)}" title="Remove">✕</button>` : ""}</li>`).join("") ||
      `<li class="empty">Your direct reports come with the first Update; add anyone else here.</li>`}</ul><div id="person-card"></div>`;
  $("#person-new").onsubmit = async (e) => { e.preventDefault(); try { await api("POST", "/api/people", { name: e.target.name.value }); e.target.name.value = ""; loadRecap(); } catch (err) { toast(err.message); } };
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
    <form class="row"><input name="note" placeholder="A note from your 1:1 (stays in ${esc(p.file)})"><button>Add</button></form></div>`;
  $("#person-card form").onsubmit = async (e) => { e.preventDefault(); try { await api("POST", "/api/people/note", { name, note: e.target.note.value }); showPerson(name); } catch (err) { toast(err.message); } };
}

// ---------------------------------------------------------------- Ask
const READY = ["What am I late with?", "Who am I waiting for, and since when?", "What changed this week?", "What should I do first today?"];
async function renderAsk() {
  const talk = await api("GET", "/api/assistant/talk");
  $("#recap-view").innerHTML = `<div class="talk">${talk.map((t) => `<div class="q">${esc(t.question)}</div><div class="a"><div class="text">${esc(t.answer)}</div>
      ${t.sources.length ? `<div class="muted small">${t.sources.map((s) => url(s.link) ? `<a href="${url(s.link)}" target="_blank" rel="noopener">${esc(s.title)}</a>` : s.file ? `<button class="linkish" data-act="file" data-file="${esc(s.file)}">${esc(s.title)}</button>` : esc(s.title)).join(" · ")}</div>` : ""}
      ${t.edits.map((x) => `<div class="edit ${x.state}"><b>${esc(x.file)}</b>${x.old ? `<div class="del">${esc(x.old)}</div>` : ""}${x.new ? `<div class="ins">${esc(x.new)}</div>` : ""}
        ${x.state === "applied" ? `<button class="mini" data-act="undo" data-id="${x.id}">Undo</button>` : x.state === "proposed" ? `<button class="mini" data-act="accept" data-file="${esc(x.file)}" data-old="${esc(x.old)}" data-new="${esc(x.new)}">Save this</button>` : `<span class="err">${esc(x.error)}</span>`}</div>`).join("")}</div>`).join("")}</div>
    <div class="chips">${READY.map((q) => `<button class="mini" data-q="${esc(q)}">${esc(q)}</button>`).join("")}${talk.length ? `<button class="mini" id="talk-clear">New conversation</button>` : ""}</div>
    <form id="ask-form" class="row"><textarea name="q" rows="2" placeholder="Ask about your work, or say what is different: &quot;no, the deadline is Thursday&quot;"></textarea><button class="primary">Ask</button></form>`;
  const ask = (q) => busyBtn($("#ask-form button"), async () => { if (!q.trim()) return; await api("POST", "/api/assistant/ask", { question: q }); $("#ask-form").q.value = ""; document.activeElement.blur(); await renderAsk(); });
  $("#ask-form").onsubmit = (e) => { e.preventDefault(); ask(e.target.q.value); };
  $("#ask-form").q.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(e.target.value); } };
  document.querySelectorAll("[data-q]").forEach((b) => (b.onclick = () => ask(b.dataset.q)));
  const c = $("#talk-clear");
  if (c) c.onclick = async () => { await api("DELETE", "/api/assistant/talk"); renderAsk(); };
}

// ---------------------------------------------------------------- first start: five questions
function renderSetup() {
  if ($("#setup-form")) return;
  $("#recap-view").innerHTML = `<form id="setup-form" class="card form"><h2>Five questions, three minutes</h2><p class="muted">You can dictate the answers. Everything can be changed later.</p>
    <label>1. What is your role, and what do people come to you for?<textarea name="role" rows="2"></textarea></label>
    <label>2. The 3 to 7 things you are running right now, one per line<textarea name="running" rows="4"></textarea></label>
    <label>3. Whose messages must you never miss?<textarea name="people" rows="2"></textarea></label>
    <label>4. What did you find out too late recently?<textarea name="late" rows="2"></textarea></label>
    <label>5. Anything this tool must never read? People, words in a title, or <code>folder: Name</code>; one per line<textarea name="never" rows="2"></textarea></label>
    <h3>Where the model runs</h3>
    <label class="check"><input type="radio" name="provider" value="copilot"> GitHub Copilot: good on every laptop, needs a Copilot licence. The text of your mail, chats and meetings is sent to GitHub Copilot.</label>
    <label class="check"><input type="radio" name="provider" value="local"> On this laptop (Ollama): nothing leaves it; needs a good GPU.</label>
    <div class="row"><button class="primary">Start</button><span class="err" id="setup-err"></span></div></form>`;
  $("#setup-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    const body = Object.fromEntries(["role", "running", "people", "late", "never", "provider"].map((k) => [k, f[k].value]));
    try {
      await api("POST", "/api/assistant/setup", body);
      f.remove();
      loadRecap();
      if (body.provider === "copilot") { const c = await api("GET", "/api/ai/copilot"); if (!c.ok) { toast("One more step: sign in to GitHub Copilot", 5000); showTab("ai"); } }
    } catch (err) { $("#setup-err").textContent = err.message; }
  };
}
