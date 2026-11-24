// Matters, people and the memory files.
import { $, GAP, ago, api, attrs, bar, busyBtn, button, draw, esc, link, list, plusDays, rowHtml, say, segments, tag, toast, typingIn, url } from "../core.js";
import { showItem } from "./body.js";
import { find } from "./list.js";
import { R, V, loadRecap, onRecap, openOfMine } from "./store.js";
import { showTab } from "../tabs.js";

const parts = (text) => { const out = {}; let cur = ""; for (const ln of text.split("\n")) { if (ln.startsWith("## ")) out[(cur = ln.slice(3).trim())] = []; else if (cur && ln.trim()) out[cur].push(ln.replace(/^- /, "")); } return out; };
const mdLine = (t) => { const m = t.match(/^(.*?)\s*\(\[source\]\((https?:[^)]+)\)\)\s*$/); return m ? `${esc(m[1])} <a href="${url(m[2])}" target="_blank" rel="noopener">source</a>` : esc(t); };
const LIVE = ["active", "waiting"];
const matters = (live) => R.matters.filter((m) => LIVE.includes(m.status) === live);
const forMe = (id) => openOfMine().filter((i) => i.matter === id);
function matterTag(m) {
  if (m.silent) return "Still active?";
  if (m.status === "closed") return "closed";
  if (m.status === "parked") return m.return_on ? "back " + m.return_on.slice(5) : "";
  const n = forMe(m.id).length;
  return n ? `${n} for me` : "";
}
const matterRow = (m) => ({ id: m.id, title: m.title, sub: m.state || m.reason || "No news yet", tag: matterTag(m), kind: m.silent ? "hot" : "" });
const personRow = (p) => ({ id: p.name, title: p.name, sub: p.title || `${p.mine} from me, ${p.theirs} from them`, tag: p.last ? ago(p.last) : "" });
const fileRow = (f) => ({ id: f, title: f.split("/").pop().replace(/\.md$/, ""), sub: f });
// each tab: its rows, what to say when there are none, and the right side for the chosen one
const TABS = {
  on: { rows: () => matters(true).map(matterRow), none: ["No matters yet.", "Add one, or accept the ones Today suggests."], body: (id) => matterBody(id), add: "New matter: a project, a case, a problem" },
  off: { rows: () => matters(false).map(matterRow), none: ["No matters yet.", ""], body: (id) => matterBody(id) },
  people: { rows: () => R.people.map(personRow), none: ["Nobody yet.", "Your direct reports come with an Update."], body: (id) => personBody(id), add: "Add a person, as in Outlook" },
  files: { rows: async () => (await api("GET", "/api/memory")).files.filter((x) => !x.startsWith("drafts/")).map(fileRow), none: ["No files yet.", ""], body: (id) => matterFile(id) },
};
async function renderMatters() {
  const tab = TABS[V.mTab], f = $("#matters-new"), asked = V.mTab;
  segments("#matters-tabs", [["on", "Running", matters(true).length], ["off", "Parked", matters(false).length], ["people", "People", R.people.length], ["files", "Files"]], V.mTab, "seg");
  f.classList.toggle("hidden", !tab.add);
  f.name.placeholder = tab.add || "";
  const rows = await tab.rows();
  if (V.mTab !== asked) return;
  if (V.mTab !== "files" && !rows.some((r) => r.id === V.mSel)) V.mSel = (rows[0] || {}).id || "";
  const chosen = V.mTab === "files" ? V.mFile : V.mSel;
  draw("#matters-rows", rows.map((r) => rowHtml({ ...r, current: r.id === chosen })).join("") || `<div class="empty"><b>${tab.none[0]}</b><br>${tab.none[1]}</div>`);
  return chosen ? tab.body(chosen) : clearMatter();
}
function clearMatter() { draw("#matters-body", ""); bar("#matters-bar", ""); }
const openRows = (l, withWho) => list(l.map((i) => [button(i.action || i.summary, "inbtn", { do: "go", id: i.key }), tag(i.due, "hot"),
  withWho ? `<span class="muted">${esc(i.person || "")}${i.days ? `, ${i.days} d` : ""}</span>` : ""].join(" ")));
function happened(line) {
  const s = line.match(/^(\d{4}-\d\d-\d\d) · (.*)$/);
  return `<div>${s ? `<time>${s[1].slice(5)}</time>` : ""}<span class="grow">${mdLine(s ? s[2] : line)}</span></div>`;
}
const noteForm = (data, hint) => `<form class="fixline grow"${attrs(data)}><input name="note" data-k="note" aria-label="Note" placeholder="${hint}"><button data-k="add">Add</button></form>`;
async function matterBody(id) {
  const m = R.matters.find((x) => x.id === id), file = `matters/${id}.md`, p = parts((await api("GET", "/api/memory/file?name=" + encodeURIComponent(file))).text);
  if (V.mSel !== id || !["on", "off"].includes(V.mTab)) return;
  const tl = [...(p.Timeline || [])].reverse().slice(0, 30);
  draw("#matters-body", `<h2 class="big">${esc(m.title)}</h2>${(p.State || []).map((x) => `<p class="lead">${esc(x)}</p>`).join("")}
    <div class="two"><div><h3>Mine</h3>${openRows(forMe(id))}</div><div><h3>Waiting for</h3>${openRows(R.waiting.filter((i) => i.matter === id), true)}</div></div>
    <h3>Decided</h3>${list((p.Decisions || []).map(esc))}${(p.Notes || []).length ? `<h3>Notes</h3>${list(p.Notes.map(esc))}` : ""}
    <h3>What happened</h3>${tl.length ? `<div class="lined">${tl.map(happened).join("")}</div>` : `<p class="muted">Nothing yet.</p>`}`);
  const status = LIVE.includes(m.status) ? button("Park", "", { st: "parked", k: "park" }) + button("Close", "quietbtn", { st: "closed", k: "close" }) : button("Make it active", "", { st: "active", k: "act" });
  bar("#matters-bar", noteForm({ note: file }, "Add a note, or say what is different") + status + button("Open the file", "quietbtn", { file, k: "mf" }));
}
async function personBody(name) {
  const p = await api("GET", "/api/people/card?name=" + encodeURIComponent(name));
  if (V.mSel !== name || V.mTab !== "people") return;
  const rows = (l) => list(l.map((x) => `${link(x.link, x.action || x.title)} ${tag(x.due, "hot")}`));
  draw("#matters-body", `<h2 class="big">${esc(name)}</h2><p class="lead">${p.last ? "Last contact " + ago(p.last) : "No contact in the data yet."}</p>
    <div class="two"><div><h3>I owe</h3>${rows(p.mine)}</div><div><h3>They owe</h3>${rows(p.theirs)}</div></div>
    <h3>Notes</h3>${list(p.notes.map((n) => esc(n.replace(/^- /, ""))))}`);
  const added = R.people.some((x) => x.name === name && x.added);
  bar("#matters-bar", noteForm({ person: name }, "A note from your 1:1") + button("Prepare a 1:1", "", { one: name, k: "one" }) + (added ? button("Remove", "quietbtn", { rm: name, k: "rm" }) : ""));
}
async function matterFile(name) {
  if ($("#matters-body").file === name && typingIn($("#matters-body"))) return;
  const f = await api("GET", "/api/memory/file?name=" + encodeURIComponent(name));
  if (V.mFile !== name || V.mTab !== "files") return;
  const drawn = draw("#matters-body", `<h2 class="big">${esc(name)}</h2><p class="muted">Plain Markdown. Lines with (you) are yours and stay.</p>
    <textarea id="mem-text" data-k="mem" aria-label="File" rows="20" spellcheck="false">${esc(f.text)}</textarea>`);
  if (drawn) Object.assign($("#matters-body"), { file: name, opened: f.text });
  bar("#matters-bar", button("Save", "primary", { save: name, k: "save" }) + GAP + button("Open the folder", "quietbtn", { folder: 1, k: "fold" }));
}
async function saveFile() {   // true when there is nothing left to save
  const box = $("#matters-body"), ta = $("#mem-text");
  if (!ta || ta.value === box.opened) return true;
  try { await api("POST", "/api/memory/file", { name: box.file, text: ta.value, opened: box.opened }); box.opened = ta.value; toast("Saved"); return true; }
  catch (err) { say(err.message); return false; }
}
$("#matters-tabs").addEventListener("click", async (e) => { const b = e.target.closest("[data-seg]"); if (b && await saveFile()) { V.mTab = b.dataset.seg; V.mSel = ""; renderMatters(); } });
$("#matters-rows").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-row]");
  if (!b || !await saveFile()) return;
  V[V.mTab === "files" ? "mFile" : "mSel"] = b.dataset.row;
  document.activeElement.blur();
  renderMatters();
  $("#matters-body").scrollTop = 0;
});
$("#matters-new").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  try {
    if (V.mTab === "people") { await api("POST", "/api/people", { name: f.name.value }); V.mSel = f.name.value.trim(); } else V.mSel = (await api("POST", "/api/matters", { title: f.name.value })).id;
    f.name.value = "";
    loadRecap();
  } catch (err) { say(err.message); }
});
$("#matters-body").addEventListener("click", (e) => {
  const b = e.target.closest("[data-do=go]"), r = b && find(b.dataset.id);
  if (r) { V.tab = r.kind === "wait" ? "wait" : "me"; showTab("recap"); showItem(r.id); }
});
$("#matters-bar").addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b || b.form) return;
  const d = b.dataset;
  try {
    if (d.st) {
      const parked = d.st === "parked" ? { reason: "parked in the window", return_on: plusDays(30) } : {};
      await api("POST", `/api/matters/${encodeURIComponent(V.mSel)}/status`, { status: d.st, ...parked });
      toast({ parked: "Parked; it comes back in 30 days", closed: "Closed", active: "Active again" }[d.st]);
      return loadRecap();
    }
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

onRecap("matters", renderMatters);
