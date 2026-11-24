"use strict";
// Today, Matters and Meetings: a list on the left, one thing on the right. Uses $, api, esc, ago, toast, say from app.js.
let R = null, recapTimer = null, pending = null;        // pending: what goes on by itself when the Graph token is there
const V = { tab: "me", sel: "", mode: "brief", wrong: false, brief: null, mTab: "on", mSel: "", mFile: "", meetTab: "ahead", meet: "", card: null };
const opened = {};                                       // folded groups the user opened
const url = (u) => (/^https?:\/\//.test(u || "") ? esc(u) : "");
const SRC = { mail: "Mail", teams: "Teams", jira: "Jira", meeting: "Meeting" };
const clockOf = (iso) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const dayOf = (iso) => new Date(iso).toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" });
const isToday = (iso) => new Date(iso).toDateString() === new Date().toDateString();
const plusDays = (n) => { const d = new Date(); d.setDate(d.getDate() + n); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
const plain = (t) => String(t || "").replace(/^((re|fw|fwd|aw|odp|pd)\s*:\s*)+/i, "") || t;
const link = (u, text) => (url(u) ? `<a href="${url(u)}" target="_blank" rel="noopener">${esc(text)}</a>` : esc(text));
const cap = (t) => { t = String(t || ""); return t.charAt(0).toUpperCase() + t.slice(1); };
const list = (l) => (l.length ? `<ul>${l.map((x) => `<li>${x}</li>`).join("")}</ul>` : `<p class="muted">Nothing.</p>`);
const running = () => R && R.job && ["queued", "running"].includes(R.job.state);
const on = (name) => $("#" + name).classList.contains("active");
const busyBtn = async (b, fn) => {
  const label = b.textContent;
  b.disabled = true; b.textContent = "Working...";
  try { await fn(); } catch (e) { say(e.message.slice(0, 200)); }
  b.disabled = false; b.textContent = label;
};

// ---------------------------------------------------------------- drawing without disturbing
let pressed = false;
addEventListener("pointerdown", () => (pressed = true), true);
for (const ev of ["pointerup", "pointercancel"]) addEventListener(ev, () => (pressed = false), true);
const typingIn = (box) => { const a = document.activeElement; return a && box.contains(a) && (a.tagName === "SELECT" || (/INPUT|TEXTAREA/.test(a.tagName) && a.type !== "radio")); };
function draw(sel, html, force) {   // only when something changed, never under the user's hands; scroll and focus stay
  const box = $(sel);
  if (box.html === html || (!force && (pressed || typingIn(box)))) return false;
  const top = box.scrollTop, a = document.activeElement, again = a && box.contains(a) && a.dataset.k ? a.dataset.k : "";
  box.innerHTML = html;
  box.html = html;
  box.scrollTop = top;
  if (again) { const f = [...box.querySelectorAll("[data-k]")].find((x) => x.dataset.k === again); if (f) f.focus({ preventScroll: true }); }
  return true;
}
function bar(sel, html) { $(sel).classList.toggle("hidden", !html); draw(sel, html, true); }
const segments = (sel, rows, cur, attr) => draw(sel, rows.map(([id, name, n]) => `<button type="button" data-${attr}="${id}" data-k="seg-${id}" aria-pressed="${id === cur}">${name}${n ? ` <i>${n}</i>` : ""}</button>`).join(""));

// ---------------------------------------------------------------- load
async function loadRecap() {
  R = await api("GET", "/api/assistant");
  const today = R.events.filter((e) => isToday(e.start)).length, mine = R.decisions.length + R.mine.length;
  setText("#nav-today", mine ? String(mine) : "");
  setText("#nav-meetings", today ? String(today) : "");
  if (on("recap")) renderToday();
  if (on("matters")) renderMatters();
  if (on("meetings")) renderMeet();
  clearTimeout(recapTimer);
  recapTimer = setTimeout(() => { if (["recap", "matters", "meetings"].some(on)) loadRecap(); }, running() ? 2000 : 60000);
}

// ---------------------------------------------------------------- Today: the list
function rowsOf(tab) {   // [group name, rows, folded]
  const item = (kind) => (i) => ({ id: i.key, i, kind, title: i.error ? "Not sorted: " + plain(i.title) : cap(i.action || i.summary || plain(i.title)), sub: `${i.person || i.people || SRC[i.source] || ""}${i.person || i.people ? ", " : " "}${plain(i.title)}`, tag: kind === "closed" && i.done_by === "answer" ? "you answered" : i.due || ago(i.last), hot: kind !== "closed" && (!!i.due || i.quiet) });
  const prom = (mine) => (p) => ({ id: "p:" + p.id, p, kind: "promise", title: cap(p.what), sub: `${mine ? "You promised" + (p.to ? " " + p.to : "") : p.who + " promised"}, ${plain(p.title)}`, tag: p.due || ago(p.last), hot: !!p.due });
  if (tab === "me") return [["Decide", R.decisions.map(item("me"))], ["Do", [...R.mine.filter((i) => i.category === "todo").map(item("me")), ...R.promised.map(prom(true))]],
    ["Answer", R.mine.filter((i) => i.category === "reply").map(item("me"))], ["Not sorted yet", R.unsorted.map(item("chg"))],
    ["My texts", R.draft_list.filter((d) => !d.key).map((d) => ({ id: "d:" + d.id, d, kind: "text", title: d.title, sub: "Draft", tag: d.created.slice(5, 10), hot: false }))]];
  if (tab === "wait") return [["Quiet for long", R.waiting.filter((i) => i.quiet).map(item("wait"))], ["Still in time", R.waiting.filter((i) => !i.quiet).map(item("wait"))], ["Promised to me", R.owed.map(prom(false))]];
  return [["Changed", R.changed.map(item("chg"))], ["Ignored", R.ignored.map(item("chg")), true], ["Done and later", R.closed.map(item("closed")), true]];
}
const rowList = (tab) => rowsOf(tab).flatMap(([, rows]) => rows);
const count = (tab) => rowsOf(tab).filter(([, , folded]) => !folded).reduce((n, [, rows]) => n + rows.length, 0);
const find = (id) => ["me", "wait", "chg"].flatMap(rowList).find((r) => r.id === id);
const rowHtml = (r) => `<button type="button" class="prow" data-row="${esc(r.id)}" data-k="${esc(r.id)}" aria-current="${V.mode === "item" && V.sel === r.id}"><span class="ptext"><b>${esc(r.title)}</b><span>${esc(r.sub)}</span></span><span class="ptag ${r.hot ? "hot" : ""}">${esc(r.tag)}</span></button>`;
function renderToday() {
  const l = R.last || {}, job = R.job || {}, w = running();
  const when = R.updated ? new Date(R.updated * 1000) : null;
  const day = when && !isToday(when) ? when.toLocaleDateString([], { day: "numeric", month: "short" }) + " " : "";
  setText("#today-status", w ? (R.ahead.length && job.state === "queued" ? "After: " + R.ahead.join(", ") : R.current || "Starting") : when ? `As of ${day}${clockOf(when)}, ${l.new || 0} new` : "Nothing read yet");
  $("#today-status").title = [l.found !== undefined && `${l.found} conversations read in ${l.seconds} s`, l.credits && `${l.credits} AI credits`, R.reviewed && `you checked ${R.reviewed}, right ${R.agreed}`].filter(Boolean).join(", ");
  $("#recap-progress").classList.toggle("hidden", !w && job.state !== "needs_token");
  $("#recap-progress span").style.width = (job.total ? Math.round(100 * job.done / job.total) : 3) + "%";
  $("#recap-update").disabled = w;
  setText("#recap-update", w ? "Updating" : "Update");
  $("#recap-update").classList.toggle("primary", !R.updated && R.setup_done);
  $("#recap").classList.toggle("solo", !R.setup_done);
  if (!R.setup_done) return renderSetup();
  if (job.state === "needs_token" && V.mode !== "connect" && V.asked !== job.id) { V.asked = job.id; V.mode = "connect"; }
  segments("#today-tabs", [["me", "For me", count("me")], ["wait", "Waiting", count("wait")], ["chg", "Changed", count("chg")]], V.tab, "seg");
  const groups = rowsOf(V.tab).filter(([, rows]) => rows.length);
  const said = job.state === "error" ? [`The last Update stopped: ${job.error}. Nothing is lost, press Update again.`] : l.problems || [];
  const problems = !w && said.length ? `<div class="note">${said.map(esc).join("<br>")}</div>` : "";
  const sug = V.tab === "me" && R.suggested.length ? `<h2>A new matter?</h2>${R.suggested.map((s) => `<div class="suggest" data-key="${esc(s.items[0].key)}"><input aria-label="Name of the matter" value="${esc(s.name)}"><span class="muted small">${esc(s.items.map((x) => plain(x.title)).join(", "))}</span>
    <div><button type="button" data-do="new_matter">Create it</button><button type="button" class="quietbtn" data-do="no_matter">No</button></div></div>`).join("")}` : "";
  const brief = V.tab === "me" ? `<button type="button" class="prow" data-row="" data-k="brief" aria-current="${V.mode === "brief"}"><span class="ptext"><b>Morning brief</b><span>${mineFirst().length ? `${count("me")} for you, ${((n) => (n === 1 ? "1 meeting" : n + " meetings"))(R.events.filter((e) => isToday(e.start)).length)} today` : "The day at a glance"}</span></span></button>` : "";
  draw("#today-rows", problems + brief + groups.map(([name, rows, folded]) => (folded
    ? `<button type="button" class="foldbtn" data-fold="${esc(name)}" data-k="fold-${esc(name)}" aria-expanded="${!!opened[name]}">${esc(name)} <i>${rows.length}</i></button>${opened[name] ? rows.map(rowHtml).join("") : ""}`
    : `<h2>${esc(name)}</h2>${rows.map(rowHtml).join("")}`)).join("") + sug + (groups.length || !R.updated ? "" : `<div class="empty"><b>Nothing here.</b></div>`) +
    (V.tab === "chg" && R.ask_feedback ? `<form class="feedback"><label>What did the Recap miss today?<input name="t" placeholder="One sentence; stays on this laptop"></label><button>Save</button></form>` : ""));
  renderBody();
}
const mineFirst = () => [...R.decisions, ...R.mine].sort((a, b) => !!b.due - !!a.due).slice(0, 3);

// ---------------------------------------------------------------- Today: the right side
function renderBody(force) {
  if (V.mode === "item" && !find(V.sel)) V.mode = "brief";
  ({ brief: bodyBrief, item: bodyItem, ask: bodyAsk, connect: bodyConnect }[V.mode] || bodyBrief)(force);
}
function bodyBrief(force) {
  const top = mineFirst(), today = R.events.filter((e) => isToday(e.start)), b = V.brief;
  const lines = b && b.lines ? b.lines.map(esc) : top.map((i) => `${esc(i.action || i.summary)}${i.due ? ` <span class="ptag hot">${esc(i.due)}</span>` : ""} <span class="muted">${esc(i.person || "")}</span>`);
  const head = b && b.working ? "Writing..." : b ? { day: "Today", week: "The week", upward: "For my manager", leave: "While you were away" }[b.kind]
    : !R.updated ? "Press Update to see your day" : top.length ? (top.length === 3 ? "First these three" : top.length === 2 ? "First these two" : "One thing for you") : "Nothing waits for you";
  const intro = !R.updated ? `<p class="lead">It reads mail, Teams, Jira, the calendar and recorded meetings, and sorts them into what is yours, what you wait for and what changed.</p>`
    : !top.length && !b ? `<p class="lead">${R.waiting.length ? `You wait for ${R.waiting.length} answer${R.waiting.length === 1 ? "" : "s"}.` : "You wait for nobody."}</p>` : "";
  draw("#today-body", `<h2 class="big">${head}</h2>${intro}${b && b.working ? "" : `<ol class="lines">${lines.map((x) => `<li>${x}</li>`).join("")}</ol>`}
    ${b && b.lines ? `<p class="muted small">Saved in ${esc(b.file)}. <button type="button" class="inbtn" data-do="brief-draft" data-k="bd">Make a draft</button> <button type="button" class="inbtn" data-do="brief-close" data-k="bc">Back to the day</button></p>` : ""}
    ${today.length ? `<h3>Meetings</h3><div class="lined">${today.map((e) => `<div><time>${clockOf(e.start)}</time><span class="grow"><b>${esc(e.title)}</b> <span class="muted">${esc(e.people.slice(0, 3).join(", "))}</span></span><button type="button" class="inbtn" data-do="meet" data-id="${esc(e.id)}" data-k="m-${esc(e.id)}">${e.prepared ? "Open card" : "Prepare me"}</button></div>`).join("")}</div>` : ""}
    ${R.updated ? `<p class="writefor"><span class="muted">Write for me:</span> ${[["day", "Today in 5 lines"], ["week", "The week in 10 lines"], ["upward", "For my manager"], ["leave", "I was away"]].map(([k, n]) => `<button type="button" class="inbtn" data-do="brief" data-kind="${k}" data-k="b-${k}">${n}</button>`).join("")}</p>` : ""}`, force);
  bar("#today-bar", "");
}
const draftOf = (r) => (r.d ? r.d : r.i && r.i.draft ? R.draft_list.find((d) => d.id === r.i.draft) : null);
function bodyItem(force) {
  const r = find(V.sel), i = r.i, p = r.p, d = draftOf(r);
  if (p) {
    draw("#today-body", `<h2 class="big">${esc(cap(p.what))}</h2><p class="lead">${p.mine ? `You promised it${p.to ? " to " + esc(p.to) : ""}.` : `${esc(p.who)} promised it${p.to && !/^me$/i.test(p.to) ? " to " + esc(p.to) : ""}.`}</p>
      <dl><dt>Due</dt><dd>${esc(p.due || "no date")}</dd><dt>From</dt><dd>${SRC[p.source] || ""}, ${link(p.link, plain(p.title))}</dd><dt>Why here</dt><dd><q>${esc(p.quote)}</q></dd></dl>`, force);
    return bar("#today-bar", `<button type="button" class="primary" data-do="p-done" data-k="p1">Done</button><span class="grow"></span><button type="button" class="quietbtn" data-do="p-no" data-k="p2">Not a promise</button>`);
  }
  if (!i) {
    draw("#today-body", `<h2 class="big">${esc(d.title)}</h2>${draftCard(d)}`, force);
    return bar("#today-bar", `<button type="button" class="primary" data-do="copy" data-k="c">Copy</button><button type="button" data-do="used" data-k="u">Done</button>`);
  }
  const closed = r.kind === "closed", matters = R.matters.filter((m) => m.status !== "closed" || m.id === i.matter);
  draw("#today-body", `<h2 class="big">${esc(cap(i.action || i.summary || plain(i.title)))}</h2>${i.action && i.summary ? `<p class="lead">${esc(i.summary)}</p>` : ""}
    ${i.orders ? `<div class="note">This text gives orders to an AI. Check it yourself.</div>` : ""}
    <dl><dt>From</dt><dd><b>${esc(i.person || i.people || "")}</b>${i.person || i.people ? ", " : ""}${SRC[i.source] || ""}, ${link(i.link, plain(i.title))}</dd>
    <dt>When</dt><dd>${new Date(i.last).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}${i.due ? ` <span class="ptag hot">${esc(i.due)}</span>` : ""}${i.quiet ? ` <span class="ptag hot">quiet for ${i.days} days</span>` : ""}</dd>
    <dt>Matter</dt><dd><select data-do-change="matter" data-k="matter" aria-label="Matter"><option value="">none</option>${matters.map((m) => `<option value="${esc(m.id)}" ${m.id === i.matter ? "selected" : ""}>${esc(m.title)}</option>`).join("")}</select>
      ${i.maybe_title ? `<button type="button" class="inbtn" data-do="maybe" data-k="maybe">${esc(i.maybe_title)}?</button>` : ""}</dd>
    <dt>Why here</dt><dd>${i.error ? `<span class="err">${esc(i.error)}</span>` : i.quote ? `<q>${esc(i.quote)}</q>` : `<span class="muted">${i.by === "rule" ? "Automatic sender." : i.by === "you" ? "You decided it." : "The model gave no sentence that proves it: check the source."}</span>`}
      ${i.error ? "" : i.reviewed ? `<span class="muted small">Checked by you</span>` : `<button type="button" class="inbtn" data-do="right" data-k="right">That is right</button>`}</dd></dl>${d ? draftCard(d) : ""}`, force);
  const canDraft = ["mail", "teams"].includes(i.source) && ["me", "wait"].includes(r.kind);
  const first = closed ? `<button type="button" class="primary" data-do="reopen" data-k="a1">Bring it back</button>`
    : d && i.source === "mail" && d.outlook && d.same ? (url(d.outlook) ? `<a class="btnlink primary" href="${url(d.outlook)}" target="_blank" rel="noopener" data-k="a1">Open it in Outlook</a>` : `<button type="button" class="primary" disabled data-k="a1">In Outlook drafts</button>`)
    : d ? (i.source === "mail" ? `<button type="button" class="primary" data-do="outlook" data-k="a1">${d.outlook ? "Put the new text into Outlook drafts" : "Put into Outlook drafts"}</button>` : `<button type="button" class="primary" data-do="copy" data-k="a1">Copy for Teams</button>`)
    : canDraft ? `<button type="button" class="primary" data-do="draft" data-k="a1">${r.kind === "wait" ? "Write a reminder" : "Write a draft"}</button>` : `<button type="button" class="primary" data-do="done" data-k="a1">${r.kind === "chg" ? "Got it" : "Done"}</button>`;
  const second = closed ? "" : `${d || canDraft ? `<button type="button" data-do="done" data-k="a2">Done</button>` : ""}<details class="menu up"><summary data-k="later">Later</summary><div><button type="button" data-do="snooze" data-days="1">Tomorrow</button><button type="button" data-do="snooze" data-days="3">In 3 days</button><button type="button" data-do="snooze" data-days="7">In a week</button></div></details>`;
  const wrong = closed ? "" : `<span class="grow"></span><button type="button" class="quietbtn" data-do="wrong" aria-expanded="${V.wrong}" data-k="a4">This is wrong</button>`;
  const chips = V.wrong && !closed ? `<div class="chipsrow">${[["not_mine", "", "Not mine"], ["category", "todo", "For me to do"], ["category", "reply", "For me to answer"], ["category", "waiting", "I wait for it"], ["category", "fyi", "Only good to know"], ["mute", "", "Ignore this conversation"]]
    .filter(([a, c]) => a !== "category" || c !== i.category).map(([a, c, n]) => `<button type="button" class="pillbtn" data-do="${a}" data-value="${c}" data-k="w-${a}${c}">${n}</button>`).join("")}</div>` : "";
  bar("#today-bar", first + second + wrong + chips);
}
const draftCard = (d) => `<div class="draftcard" data-did="${d.id}"><div class="row0"><label for="draft-text"><b>Draft${d.to ? " to " + esc(d.to) : ""}</b></label><span class="grow"></span><button type="button" class="quietbtn" data-do="drop" data-k="drop">Throw away</button></div>
  <textarea id="draft-text" data-k="draft-text" rows="${Math.min(14, Math.max(4, d.body.split("\n").length + 1))}">${esc(d.body)}</textarea>
  <form class="fixline"><input name="remark" data-k="remark" aria-label="What to change" placeholder="What should change? Shorter, no greeting, say Thursday"><button data-k="fixit">Rewrite</button></form><div class="rule"></div></div>`
  .replace("<textarea", `${d.old ? `<div class="note">Written before the newest message. <button type="button" class="inbtn" data-do="draft" data-k="redraft">Write a new one</button></div>` : ""}<textarea`);
async function bodyAsk(force) {
  const talk = await api("GET", "/api/assistant/talk");
  if (V.mode !== "ask") return;
  const done = draw("#today-body", `${talk.length ? "" : `<h2 class="big">Ask about your work</h2><p class="lead">Answers come from your matters, mail, chats, calendar and notes, with sources. Say what is different and it is remembered.</p>`}
    ${talk.map((t) => `<div class="q">${esc(t.question)}</div><div class="a"><div class="text">${esc(t.answer)}</div>
      ${t.sources.length ? `<div class="muted small">${t.sources.map((s) => (s.file ? `<button type="button" class="inbtn" data-do="file" data-file="${esc(s.file)}">${esc(s.title)}</button>` : link(s.link, s.title))).join(", ")}</div>` : ""}
      ${t.edits.map((x) => `<div class="edit ${x.state}"><b>${esc(x.file)}</b>${x.old ? `<div class="del">${esc(x.old)}</div>` : ""}${x.new ? `<div class="ins">${esc(x.new)}</div>` : ""}
        ${x.state === "applied" ? `<button type="button" class="inbtn" data-do="mem-undo" data-id="${x.id}">Undo</button>` : x.state === "proposed" ? `<button type="button" class="inbtn" data-do="mem-accept" data-file="${esc(x.file)}" data-old="${esc(x.old)}" data-new="${esc(x.new)}">Save this</button>` : `<span class="err">${esc(x.error)}</span>`}</div>`).join("")}</div>`).join("")}
    <p class="writefor">${["What am I late with?", "Who am I waiting for, and since when?", "What changed this week?"].map((q) => `<button type="button" class="inbtn" data-do="ask" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</p>`, force);
  if (done) $("#today-body").scrollTop = $("#today-body").scrollHeight;
  bar("#today-bar", `<button type="button" data-do="ask-close" data-k="x1">Back to the list</button>${talk.length ? `<span class="grow"></span><button type="button" class="quietbtn" data-do="ask-clear" data-k="x2">New conversation</button>` : ""}`);
}

// ---------------------------------------------------------------- the Microsoft token: a step, not a state to watch
function openConnect(then) { if (then) pending = then; V.mode = "connect"; if (R && R.setup_done) renderToday(); else loadRecap(); }
function bodyConnect(force) {
  const g = R.graph, paused = R.job && R.job.state === "needs_token", waiting = g.watch && g.watch.active;
  const half = paused && R.paused_at.startsWith("reading") && !/profile/.test(R.paused_at), step = waiting ? 1 : 0;
  draw("#today-body", `<h2 class="big">${half ? "One step to finish the Update" : pending ? "One step to go on" : paused ? "One step to read your mail" : g.has_token ? "Renew the access" : "One step to read your mail"}</h2>
    <p class="lead">${half ? `Paused while ${esc(R.paused_at.split(":")[0])}. What was read before it is kept.` : "Microsoft gives access for about an hour at a time. It takes about ten seconds."}</p>
    <ol class="steps3">${["Graph Explorer opens; sign in if it asks", "Open the Access token tab there and press copy", paused || pending ? "Come back: it goes on by itself" : "Come back: you are connected"].map((t, n) => `<li class="${n === step ? "now" : n < step ? "past" : ""}"><i>${n + 1}</i>${t}</li>`).join("")}</ol>
    <p><button type="button" class="primary" data-do="token-get" data-k="t1" ${waiting ? "disabled" : ""}>${waiting ? "Waiting for the copy..." : "Open Graph Explorer"}</button> <button type="button" class="quietbtn" data-do="token-no" data-k="t2">Not now</button></p>
    <details ${V.paste ? "open" : ""} id="token-paste"><summary class="muted">Paste it myself</summary><form class="fixline" id="token-form"><input type="password" name="token" data-k="t3" aria-label="Access token" placeholder="Access token"><button>Connect</button></form></details>
    <p class="muted">Everything already read stays here: drafts, Ask, matters and meeting cards work without it.</p>`, force);
  bar("#today-bar", "");
}
async function recapToken() {   // called when a token was saved, here or in another tab
  const then = pending;
  pending = null; V.asked = null;
  if (V.mode === "connect") V.mode = V.sel ? "item" : "brief";
  toast("Connected");
  if (then) try { await then(); } catch (e) { say(e.message); }
  loadRecap();
}
async function withToken(fn) {   // runs it; without a valid token it waits for one and then runs
  try { await fn(); } catch (e) { if (e.message !== "token") throw e; openConnect(fn); }
}

// ---------------------------------------------------------------- Today: clicks
const UNDO = { done: "Done", not_mine: "Not yours", mute: "Ignored from now on", snooze: "Later", category: "Moved" };
function next(id) {   // the row after this one, so the work goes on
  const rows = rowList(V.tab).filter((r) => !rowsOf(V.tab).find(([, x, folded]) => folded && x.includes(r))), at = rows.findIndex((r) => r.id === id);
  return at < 0 ? "" : ((rows[at + 1] || rows[at - 1] || {}).id || "");
}
async function act(key, action, value) {
  const leaves = !!UNDO[action], to = leaves ? next(key) : key;
  await api("POST", "/api/assistant/act", { key, action, value: value || "" });
  if (leaves) { V.sel = to; V.mode = to ? "item" : "brief"; V.wrong = false; }
  await loadRecap();
  if (leaves) toast(UNDO[action], 6000, ["Undo", async () => { try { await api("POST", "/api/assistant/act", { key, action: "undo", value: "" }); V.sel = key; V.mode = "item"; loadRecap(); } catch (e) { say(e.message); } }]);
}
async function saveDraft() {
  const c = $("#today-body .draftcard"), t = c && c.querySelector("textarea"), d = c && R.draft_list.find((x) => x.id === c.dataset.did);
  if (d && t.value !== d.body) Object.assign(d, await api("POST", `/api/drafts/${d.id}`, { body: t.value }), { body: t.value });
  return d;
}
async function showItem(id) { await saveDraft().catch(() => {}); V.sel = id; V.mode = id ? "item" : "brief"; V.wrong = false; renderToday(); $("#today-body").scrollTop = 0; renderBody(true); }
$("#today-rows").addEventListener("click", async (e) => {
  const row = e.target.closest("[data-row]"), fold = e.target.closest("[data-fold]"), b = e.target.closest("[data-do]");
  if (row) return showItem(row.dataset.row);
  if (fold) { opened[fold.dataset.fold] = !opened[fold.dataset.fold]; return renderToday(); }
  if (b) { const s = b.closest(".suggest"); try { await act(s.dataset.key, b.dataset.do, s.querySelector("input").value); } catch (err) { say(err.message); } }
});
$("#today-rows").addEventListener("keydown", (e) => {   // arrows walk the list
  const d = { ArrowDown: 1, ArrowUp: -1 }[e.key], rows = [...$("#today-rows").querySelectorAll(".prow")], at = rows.indexOf(document.activeElement);
  if (!d || at < 0 || !rows[at + d]) return;
  e.preventDefault();
  rows[at + d].focus();
  showItem(rows[at + d].dataset.row);
});
$("#today-rows").addEventListener("submit", async (e) => {
  e.preventDefault();
  try { await api("POST", "/api/assistant/feedback", { text: e.target.t.value }); toast("Thank you"); document.activeElement.blur(); loadRecap(); } catch (err) { say(err.message); }
});
$("#today-tabs").addEventListener("click", (e) => { const b = e.target.closest("[data-seg]"); if (b) { V.tab = b.dataset.seg; V.sel = ""; V.mode = "brief"; renderToday(); } });
$("#recap-range").addEventListener("change", (e) => $("#recap-since").classList.toggle("hidden", e.target.value !== "since"));
$("#recap-update").addEventListener("click", async () => {
  const v = $("#recap-range").value;
  const range = v === "new" ? { kind: "new" } : v === "since" ? { kind: "since", date: $("#recap-since").value } : { kind: "days", days: Number(v) };
  try { await api("POST", "/api/assistant/update", { range }); V.asked = null; setTimeout(loadRecap, 300); } catch (e) { say(e.message); }
});
$("#ask-form").addEventListener("submit", (e) => { e.preventDefault(); ask(e.target.q.value, e.target.q); });
async function ask(q, field) {
  if (!q.trim()) { V.mode = "ask"; return renderBody(true); }
  const f = $("#ask-form").q;
  f.disabled = true;
  if (field) field.value = "";
  f.placeholder = "Thinking...";
  try { await saveDraft(); await api("POST", "/api/assistant/ask", { question: q }); V.mode = "ask"; await bodyAsk(true); renderToday(); }
  catch (err) { if (field) field.value = q; say(err.message); }
  f.disabled = false; f.placeholder = "Ask, or tell me what changed: the deadline is Thursday"; f.focus();
}
async function makeBrief(kind) {
  V.brief = { working: true }; V.mode = "brief"; renderBody(true);
  try { V.brief = await api("POST", "/api/assistant/brief", { kind }); } catch (err) { V.brief = null; say(err.message); }
  renderBody(true);
}
async function todayClick(e) {
  const b = e.target.closest("[data-do]");
  if (!b) return;
  const a = b.dataset.do, r = V.mode === "item" ? find(V.sel) : null, key = r && r.i && r.i.key;
  const menu = b.closest("details.menu");
  if (menu) menu.open = false;
  try {
    if (a === "brief") return makeBrief(b.dataset.kind);
    if (a === "brief-close") { V.brief = null; return renderBody(true); }
    if (a === "brief-draft") { const d = await api("POST", "/api/drafts", { title: { day: "Today", week: "The week", upward: "For my manager", leave: "While I was away" }[V.brief.kind], body: V.brief.lines.map((x) => "- " + x).join("\n") }); await loadRecap(); return showItem("d:" + d.id); }
    if (a === "meet") { V.meet = b.dataset.id; V.card = null; return showTab("meetings"); }
    if (a === "ask") return ask(b.dataset.q);
    if (a === "ask-close") { V.mode = V.sel ? "item" : "brief"; return renderToday(); }
    if (a === "ask-clear") { await api("DELETE", "/api/assistant/talk"); return renderBody(true); }
    if (a === "file") { V.mFile = b.dataset.file; V.mTab = "files"; return showTab("matters"); }
    if (a === "mem-undo") { const x = await api("POST", "/api/memory/undo", { id: b.dataset.id }); toast("Undone in " + x.file); return b.remove(); }
    if (a === "mem-accept") { const x = await api("POST", "/api/memory/edit", { file: b.dataset.file, old: b.dataset.old, new: b.dataset.new }); if (x[0] && x[0].error) throw new Error(x[0].error); toast("Saved"); return b.remove(); }
    if (a === "token-get") { await api("POST", "/api/graph/token/fetch"); watchGraphToken(0); return loadRecap(); }
    if (a === "token-no") { pending = null; V.mode = V.sel ? "item" : "brief"; return renderToday(); }
    if (!r) return;
    if (a === "p-done" || a === "p-no") {
      const id = r.p.id, to = next(r.id);
      await api("POST", "/api/assistant/promise", { id, state: a === "p-done" ? "done" : "dropped" });
      V.sel = to; V.mode = to ? "item" : "brief"; await loadRecap();
      return toast("Closed", 6000, ["Undo", async () => { await api("POST", "/api/assistant/promise", { id, state: "open" }); V.sel = "p:" + id; V.mode = "item"; loadRecap(); }]);
    }
    if (a === "wrong") { V.wrong = !V.wrong; return renderBody(true); }
    if (a === "draft") return busyBtn(b, async () => { await api("POST", "/api/drafts", { key }); await loadRecap(); renderBody(true); });
    if (a === "copy") { await saveDraft(); await navigator.clipboard.writeText($("#draft-text").value); return toast("Copied"); }
    if (a === "outlook") { const d = await saveDraft(); return withToken(async () => { await api("POST", `/api/drafts/${d.id}/outlook`); toast("In the Outlook drafts; you send it there", 4000); await loadRecap(); renderBody(true); }); }
    if (a === "drop" || a === "used") { const did = $("#today-body .draftcard").dataset.did; await api("POST", `/api/drafts/${did}/close`, { status: a === "drop" ? "dropped" : "used" }); if (r.d) { V.sel = ""; V.mode = "brief"; } await loadRecap(); return renderBody(true); }
    if (a === "maybe") return await act(key, "matter", r.i.maybe);
    if (a === "snooze") return await act(key, "snooze", plusDays(Number(b.dataset.days)));
    await saveDraft();
    await act(key, a, b.dataset.value);
    if (a === "right") toast("Noted");
  } catch (err) { if (err.message === "token") openConnect(); else say(err.message); }
}
$("#today-body").addEventListener("click", todayClick);
$("#today-bar").addEventListener("click", todayClick);
$("#today-body").addEventListener("change", async (e) => {
  const s = e.target.closest("[data-do-change]");
  try {
    if (s) { s.blur(); return await act(find(V.sel).i.key, s.dataset.doChange, s.value); }
    if (e.target.id === "draft-text") { await saveDraft(); renderToday(); }
  } catch (err) { say(err.message); }
});
$("#today-body").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  if (f.id === "token-form") {
    try { await api("POST", "/api/graph/token", { token: f.token.value.trim() }); f.token.value = ""; tokenSaved(true); } catch (err) { say(err.message); }
    return;
  }
  const c = f.closest(".draftcard"), ta = c.querySelector("textarea");
  if (!f.remark.value.trim()) return f.remark.focus();
  busyBtn(f.querySelector("button"), async () => {
    const r = await api("POST", `/api/drafts/${c.dataset.did}/revise`, { remark: f.remark.value, body: ta.value });
    ta.value = r.draft.body; f.remark.value = "";
    const d = R.draft_list.find((x) => x.id === c.dataset.did);
    if (d) d.body = r.draft.body;
    c.querySelector(".rule").innerHTML = r.rule ? `Always write like this? <b>${esc(r.rule)}</b> <button type="button" class="inbtn">Yes, remember</button>` : "";
    const y = c.querySelector(".rule button");
    if (y) y.onclick = async () => { await api("POST", "/api/style", { rule: r.rule }); c.querySelector(".rule").textContent = "Remembered."; };
  });
});
addEventListener("click", (e) => document.querySelectorAll("details.menu[open]").forEach((m) => { if (!m.contains(e.target)) m.open = false; }));
addEventListener("keydown", (e) => { if (e.key === "Escape") document.querySelectorAll("details.menu[open]").forEach((m) => (m.open = false)); });

// ---------------------------------------------------------------- Matters (and people, and the memory files)
const parts = (text) => { const out = {}; let cur = ""; for (const ln of text.split("\n")) { if (ln.startsWith("## ")) out[(cur = ln.slice(3).trim())] = []; else if (cur && ln.trim()) out[cur].push(ln.replace(/^- /, "")); } return out; };
const mdLine = (t) => { const m = t.match(/^(.*?)\s*\(\[source\]\((https?:[^)]+)\)\)\s*$/); return m ? `${esc(m[1])} <a href="${url(m[2])}" target="_blank" rel="noopener">source</a>` : esc(t); };
async function renderMatters() {
  const run = R.matters.filter((m) => ["active", "waiting"].includes(m.status)), off = R.matters.filter((m) => ["parked", "closed"].includes(m.status));
  segments("#matters-tabs", [["on", "Running", run.length], ["off", "Parked", off.length], ["people", "People", R.people.length], ["files", "Files"]], V.mTab, "seg");
  const f = $("#matters-new");
  f.classList.toggle("hidden", !["on", "people"].includes(V.mTab));
  f.name.placeholder = V.mTab === "people" ? "Add a person, as in Outlook" : "New matter: a project, a case, a problem";
  const row = (id, title, sub, tag, hot) => `<button type="button" class="prow" data-row="${esc(id)}" data-k="${esc(id)}" aria-current="${V.mSel === id}"><span class="ptext"><b>${esc(title)}</b><span>${esc(sub)}</span></span>${tag ? `<span class="ptag ${hot ? "hot" : ""}">${esc(tag)}</span>` : ""}</button>`;
  let rows;
  if (V.mTab === "people") rows = R.people.map((p) => row(p.name, p.name, p.title || `${p.mine} from me, ${p.theirs} from them`, p.last ? ago(p.last) : ""));
  else if (V.mTab === "files") rows = (await api("GET", "/api/memory")).files.filter((x) => !x.startsWith("drafts/")).map((x) => row(x, x.split("/").pop().replace(/\.md$/, ""), x));
  else rows = (V.mTab === "on" ? run : off).map((m) => { const n = [...R.decisions, ...R.mine].filter((i) => i.matter === m.id).length; return row(m.id, m.title, m.state || m.reason || "No news yet", m.silent ? "Still active?" : m.return_on && m.status === "parked" ? "back " + m.return_on.slice(5) : m.status === "closed" ? "closed" : n ? `${n} for me` : "", m.silent); });
  draw("#matters-rows", rows.join("") || `<div class="empty"><b>${V.mTab === "people" ? "Nobody yet." : V.mTab === "files" ? "No files yet." : "No matters yet."}</b><br>${V.mTab === "people" ? "Your direct reports come with an Update." : V.mTab === "on" ? "Add one, or accept the ones Today suggests." : ""}</div>`);
  if (V.mTab === "files") return V.mFile ? matterFile(V.mFile) : clearMatter();
  const ids = V.mTab === "people" ? R.people.map((p) => p.name) : (V.mTab === "on" ? run : off).map((m) => m.id);
  if (!ids.includes(V.mSel)) V.mSel = ids[0] || "";
  if (!V.mSel) return clearMatter();
  document.querySelectorAll("#matters-rows .prow").forEach((b) => b.setAttribute("aria-current", b.dataset.row === V.mSel));
  return V.mTab === "people" ? personBody(V.mSel) : matterBody(V.mSel);
}
function clearMatter() { draw("#matters-body", ""); bar("#matters-bar", ""); }
const openRows = (l, what) => list(l.map((i) => `<button type="button" class="inbtn" data-do="go" data-id="${esc(i.key)}">${esc(i.action || i.summary)}</button>${i.due ? ` <span class="ptag hot">${esc(i.due)}</span>` : ""}${what ? ` <span class="muted">${esc(i.person || "")}${i.days ? `, ${i.days} d` : ""}</span>` : ""}`));
async function matterBody(id) {
  const m = R.matters.find((x) => x.id === id), p = parts((await api("GET", "/api/memory/file?name=" + encodeURIComponent(`matters/${id}.md`))).text);
  if (V.mSel !== id || V.mTab === "files" || V.mTab === "people") return;
  const tl = [...(p.Timeline || [])].reverse();
  draw("#matters-body", `<h2 class="big">${esc(m.title)}</h2>${(p.State || []).map((x) => `<p class="lead">${esc(x)}</p>`).join("")}
    <div class="two"><div><h3>Mine</h3>${openRows([...R.decisions, ...R.mine].filter((i) => i.matter === id))}</div><div><h3>Waiting for</h3>${openRows(R.waiting.filter((i) => i.matter === id), true)}</div></div>
    <h3>Decided</h3>${list((p.Decisions || []).map(esc))}${(p.Notes || []).length ? `<h3>Notes</h3>${list(p.Notes.map(esc))}` : ""}
    <h3>What happened</h3>${tl.length ? `<div class="lined">${tl.slice(0, 30).map((x) => { const s = x.match(/^(\d{4}-\d\d-\d\d) · (.*)$/); return `<div>${s ? `<time>${s[1].slice(5)}</time>` : ""}<span class="grow">${mdLine(s ? s[2] : x)}</span></div>`; }).join("")}</div>` : `<p class="muted">Nothing yet.</p>`}`);
  const live = ["active", "waiting"].includes(m.status);
  bar("#matters-bar", `<form class="fixline grow" data-note="${esc(`matters/${id}.md`)}"><input name="note" data-k="mnote" aria-label="Note" placeholder="Add a note, or say what is different"><button data-k="mn">Add</button></form>${live ? `<button type="button" data-st="parked" data-k="park">Park</button><button type="button" class="quietbtn" data-st="closed" data-k="close">Close</button>` : `<button type="button" data-st="active" data-k="act">Make it active</button>`}<button type="button" class="quietbtn" data-file="${esc(`matters/${id}.md`)}" data-k="mf">Open the file</button>`);
}
async function personBody(name) {
  const p = await api("GET", "/api/people/card?name=" + encodeURIComponent(name));
  if (V.mSel !== name || V.mTab !== "people") return;
  const rows = (l) => list(l.map((x) => `${link(x.link, x.action || x.title)}${x.due ? ` <span class="ptag hot">${esc(x.due)}</span>` : ""}`));
  draw("#matters-body", `<h2 class="big">${esc(name)}</h2><p class="lead">${p.last ? "Last contact " + ago(p.last) : "No contact in the data yet."}</p><div class="two"><div><h3>I owe</h3>${rows(p.mine)}</div><div><h3>They owe</h3>${rows(p.theirs)}</div></div><h3>Notes</h3>${list(p.notes.map((n) => esc(n.replace(/^- /, ""))))}`);
  const added = R.people.find((x) => x.name === name && x.added);
  bar("#matters-bar", `<form class="fixline grow" data-person="${esc(name)}"><input name="note" data-k="pnote" aria-label="Note" placeholder="A note from your 1:1"><button data-k="pn">Add</button></form><button type="button" data-one="${esc(name)}" data-k="one">Prepare a 1:1</button>${added ? `<button type="button" class="quietbtn" data-rm="${esc(name)}" data-k="rm">Remove</button>` : ""}`);
}
async function matterFile(name) {
  if ($("#matters-body").file === name && typingIn($("#matters-body"))) return;
  const f = await api("GET", "/api/memory/file?name=" + encodeURIComponent(name));
  if (V.mFile !== name || V.mTab !== "files") return;
  document.querySelectorAll("#matters-rows .prow").forEach((b) => b.setAttribute("aria-current", b.dataset.row === name));
  if (draw("#matters-body", `<h2 class="big">${esc(name)}</h2><p class="muted">Plain Markdown. Lines with (you) are yours and stay.</p><textarea id="mem-text" data-k="mem" aria-label="File" rows="20" spellcheck="false">${esc(f.text)}</textarea>`)) { $("#matters-body").file = name; $("#matters-body").opened = f.text; }
  bar("#matters-bar", `<button type="button" class="primary" data-save="${esc(name)}" data-k="save">Save</button><span class="grow"></span><button type="button" class="quietbtn" data-folder="1" data-k="fold">Open the folder</button>`);
}
async function saveFile() {   // true when there is nothing left to save
  const box = $("#matters-body"), ta = $("#mem-text");
  if (!ta || ta.value === box.opened) return true;
  try { await api("POST", "/api/memory/file", { name: box.file, text: ta.value, opened: box.opened }); box.opened = ta.value; toast("Saved"); return true; }
  catch (err) { say(err.message); return false; }
}
$("#matters-tabs").addEventListener("click", async (e) => { const b = e.target.closest("[data-seg]"); if (b && await saveFile()) { V.mTab = b.dataset.seg; V.mSel = ""; renderMatters(); } });
$("#matters-rows").addEventListener("click", async (e) => { const b = e.target.closest("[data-row]"); if (!b || !await saveFile()) return; if (V.mTab === "files") V.mFile = b.dataset.row; else V.mSel = b.dataset.row; document.activeElement.blur(); renderMatters(); $("#matters-body").scrollTop = 0; });
$("#matters-new").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  try {
    if (V.mTab === "people") { await api("POST", "/api/people", { name: f.name.value }); V.mSel = f.name.value.trim(); } else V.mSel = (await api("POST", "/api/matters", { title: f.name.value })).id;
    f.name.value = "";
    loadRecap();
  } catch (err) { say(err.message); }
});
$("#matters-body").addEventListener("click", (e) => { const b = e.target.closest("[data-do=go]"); if (b) { const r = find(b.dataset.id); if (r) { V.tab = r.kind === "wait" ? "wait" : "me"; showTab("recap"); showItem(r.id); } } });
$("#matters-bar").addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b || b.form) return;
  const d = b.dataset;
  try {
    if (d.st) { await api("POST", `/api/matters/${encodeURIComponent(V.mSel)}/status`, { status: d.st, ...(d.st === "parked" ? { reason: "parked in the window", return_on: plusDays(30) } : {}) }); toast(d.st === "parked" ? "Parked; it comes back in 30 days" : d.st === "closed" ? "Closed" : "Active again"); return loadRecap(); }
    if (d.file) { V.mTab = "files"; V.mFile = d.file; return renderMatters(); }
    if (d.one) return busyBtn(b, async () => { V.card = await api("POST", "/api/assistant/prep", { person: d.one }); V.meetTab = "ahead"; V.meet = V.card.id; showTab("meetings"); });
    if (d.rm) { await api("POST", "/api/people/remove", { name: d.rm }); V.mSel = ""; return loadRecap(); }
    if (d.folder) return await api("POST", "/api/memory/open");
    if (d.save) await saveFile();
  } catch (err) { say(err.message); }
});
$("#matters-bar").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target, note = f.note.value.trim();
  if (!note) return f.note.focus();
  try {
    if (f.dataset.person) await api("POST", "/api/people/note", { name: f.dataset.person, note });
    else { const x = await api("POST", "/api/memory/edit", { file: f.dataset.note, old: "", new: note }); if (x[0] && x[0].error) throw new Error(x[0].error); }
    f.note.value = ""; toast("Noted"); document.activeElement.blur(); renderMatters();
  } catch (err) { say(err.message); }
});

// ---------------------------------------------------------------- Meetings: ahead (cards) and recorded
function renderMeet() {
  segments("#meet-tabs", [["ahead", "Ahead", R.events.length], ["rec", "Recorded"]], V.meetTab, "seg");
  const rec = V.meetTab === "rec";
  $("#meet-ahead").classList.toggle("hidden", rec);
  $("#meet-recorded").classList.toggle("hidden", !rec);
  if (rec) return draw("#meet-rows", `<p class="muted pad">Teams calls are noticed by themselves. A summary and the promises made go into Today.</p>`);
  const days = {};
  R.events.forEach((e) => (days[dayOf(e.start)] ||= []).push(e));
  const person = V.card && V.card.id.startsWith("person:") ? V.card : null;
  if (!person && !R.events.some((e) => e.id === V.meet)) V.meet = (R.events[0] || {}).id || "";
  draw("#meet-rows", (person ? `<h2>1:1</h2><button type="button" class="prow" data-row="${esc(person.id)}" data-k="${esc(person.id)}" aria-current="${V.meet === person.id}"><span class="ptext"><b>${esc(person.title)}</b><span>Card</span></span></button>` : "") +
    Object.entries(days).map(([d, l]) => `<h2>${esc(isToday(l[0].start) ? "Today" : d)}</h2>${l.map((e) => `<button type="button" class="prow" data-row="${esc(e.id)}" data-k="${esc(e.id)}" aria-current="${V.meet === e.id}"><time>${clockOf(e.start)}</time><span class="ptext"><b>${esc(e.title)}</b><span>${esc(e.people.slice(0, 3).join(", "))}${e.people.length > 3 ? ` +${e.people.length - 3}` : ""}</span></span>${e.prepared ? `<span class="ptag ready">Ready</span>` : ""}</button>`).join("")}`).join("") ||
    `<div class="empty"><b>No meetings to show.</b><br>The calendar of the next week comes with an Update.</div>`);
  meetBody();
}
async function meetBody() {
  const e = R.events.find((x) => x.id === V.meet), id = V.meet;
  if (!e && !(V.card && V.card.id === id)) { draw("#meet-body", ""); return bar("#meet-bar", ""); }
  if (e && e.prepared && (!V.card || V.card.id !== id)) { try { V.card = await api("POST", "/api/assistant/prep", { id }); } catch (err) { say(err.message); } if (V.meet !== id) return; }
  const c = V.card && V.card.id === id ? V.card : null, part = (t, l) => (l && l.length ? `<div><h3>${t}</h3>${list(l.map(esc))}</div>` : "");
  draw("#meet-body", `<h2 class="big">${esc(e ? e.title : c.title)}</h2>${e ? `<p class="lead">${isToday(e.start) ? "Today" : dayOf(e.start)} ${clockOf(e.start)}${e.people.length ? `, with ${esc(e.people.slice(0, 8).join(", "))}${e.people.length > 8 ? ` and ${e.people.length - 8} more` : ""}` : ""}</p>` : ""}
    ${c ? `${c.goal ? `<h3>Goal</h3><p class="goal">${esc(c.goal)}</p>` : ""}<div class="two">${part("Open from before", c.open)}${part("To ask", c.questions)}${part("What I know", c.known)}${part("Agenda", c.agenda)}</div>`
        : `<p>No card yet. It takes the invitation, what is open with these people, the last recording and your notes, and writes the goal, what to ask and a short agenda.</p>`}`, true);
  const join = e && url(e.join) ? `<a class="btnlink ${c ? "primary" : ""}" href="${url(e.join)}" target="_blank" rel="noopener" data-k="join">Join</a>` : "";
  bar("#meet-bar", c ? `${join}${c.agenda.length ? `<button type="button" data-m="agenda" data-k="ag">Draft the agenda</button>` : ""}<span class="grow"></span><button type="button" class="quietbtn" data-m="again" data-k="ga">Make it again</button><button type="button" class="quietbtn" data-m="file" data-k="gf">Edit the card</button>` : `<button type="button" class="primary" data-m="make" data-k="mk">Prepare me</button>${join}`);
}
$("#meet-tabs").addEventListener("click", (e) => { const b = e.target.closest("[data-seg]"); if (b) { V.meetTab = b.dataset.seg; renderMeet(); } });
$("#meet-rows").addEventListener("click", (e) => { const b = e.target.closest("[data-row]"); if (b) { V.meet = b.dataset.row; renderMeet(); $("#meet-body").scrollTop = 0; } });
$("#meet-bar").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-m]");
  if (!b) return;
  const a = b.dataset.m, c = V.card;
  if (a === "make" || a === "again") return busyBtn(b, async () => { const one = V.meet.startsWith("person:") ? V.meet.slice(7) : ""; V.card = await api("POST", "/api/assistant/prep", { id: one ? "" : V.meet, again: a === "again", person: one }); await loadRecap(); });
  try {
    if (a === "agenda") { const d = await api("POST", "/api/drafts", { title: c.title, body: "Agenda:\n" + c.agenda.map((x) => "- " + x).join("\n") }); V.tab = "me"; await loadRecap(); showTab("recap"); return showItem("d:" + d.id); }
    if (a === "file") { V.mTab = "files"; V.mFile = c.file; showTab("matters"); }
  } catch (err) { say(err.message); }
});

// ---------------------------------------------------------------- first start: three short steps
let setupStep = 0;
function renderSetup() {
  if ($("#setup-form")) return;
  bar("#today-bar", "");
  draw("#today-body", `<form id="setup-form" class="form setup"><div class="steps" aria-hidden="true"><i></i><i></i><i></i></div>
    <fieldset><h2 class="big">Where should the model run?</h2>
      <label class="pick"><input type="radio" name="provider" value="copilot"><span><b>GitHub Copilot</b><br>Good on every laptop, needs a Copilot licence. The text of your mail, chats and meetings, and what you check in Writing, is sent to GitHub Copilot.</span></label>
      <label class="pick"><input type="radio" name="provider" value="local"><span><b>This laptop (Ollama)</b><br>Nothing leaves it. Needs a good GPU.</span></label></fieldset>
    <fieldset><h2 class="big">About your work</h2><p class="muted">You can dictate the answers; all of it can be changed later.</p>
      <label>What is your role, and what do people come to you for?<textarea name="role" rows="2"></textarea></label>
      <label>The 3 to 7 things you are running right now, one per line<textarea name="running" rows="4"></textarea></label>
      <label>Whose messages must you never miss?<textarea name="people" rows="1"></textarea></label></fieldset>
    <fieldset><h2 class="big">Last two</h2>
      <label>What did you find out too late recently?<textarea name="late" rows="2"></textarea></label>
      <label>Anything this tool must never read?<textarea name="never" rows="3" placeholder="HR&#10;folder: Private"></textarea><small>People, words in a title, or folder: Name; one per line.</small></label></fieldset>
    <div class="row0"><button type="button" id="setup-back">Back</button><span class="err" id="setup-err" role="alert"></span><span class="grow"></span><button class="primary" id="setup-next">Next</button></div></form>`, true);
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
    e.stopPropagation();
    if (setupStep === 0 && !f.provider.value) return ($("#setup-err").textContent = "Choose one of the two.");
    if (setupStep < 2) return show(setupStep + 1);
    const body = Object.fromEntries(["role", "running", "people", "late", "never", "provider"].map((k) => [k, f[k].value]));
    try {
      await api("POST", "/api/assistant/setup", body);
      document.activeElement.blur();
      $("#today-body").html = "";
      await loadRecap();
      if (body.provider === "copilot") { const c = await api("GET", "/api/ai/copilot"); if (!c.ok) toast("One more step: sign in to GitHub Copilot", 15000, ["Sign in", () => showTab("ai")]); }
    } catch (err) { $("#setup-err").textContent = err.message; }
  };
}
