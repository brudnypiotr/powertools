// Today: what a click does. ON: the things that need no item; ITEM: the ones done with the open item (r).
import { $, api, busyBtn, plusDays, say, toast } from "../core.js";
import { tokenSaved, watchGraphToken } from "../graph.js";
import { showTab } from "../tabs.js";
import { ask } from "./ask.js";
import { renderBody, showItem } from "./body.js";
import { briefTitle, makeBrief } from "./brief.js";
import { openConnect, withToken } from "./connect.js";
import { rewrite, saveDraft } from "./draft.js";
import { find, next, renderToday } from "./list.js";
import { V, backToItem, loadRecap, opened, running, select } from "./store.js";

// ---------------------------------------------------------------- what leaves the list can be taken back
const UNDO = { done: "Done", not_mine: "Not yours", mute: "Ignored from now on", snooze: "Later", category: "Moved" };
const post = (key, action, value = "") => api("POST", "/api/assistant/act", { key, action, value });
const undoToast = (text, id, undo) => toast(text, 6000, ["Undo", async () => {
  try { await undo(); select(id); await loadRecap(); } catch (e) { say(e.message); }
}]);
async function act(key, action, value) {
  const leaves = !!UNDO[action], to = next(key);
  await post(key, action, value);
  if (leaves) select(to);
  await loadRecap();
  if (leaves) undoToast(UNDO[action], key, () => post(key, "undo"));
}
async function closePromise(r, state) {
  const to = next(r.id), set = (s) => api("POST", "/api/assistant/promise", { id: r.p.id, state: s });
  await set(state);
  select(to);
  await loadRecap();
  undoToast("Closed", r.id, () => set("open"));
}
async function closeDraft(r, status) {
  await api("POST", `/api/drafts/${$("#today-body .draftcard").dataset.did}/close`, { status });
  if (r.d) select("");
  await loadRecap();
  renderBody(true);
}
async function memoryEdit(b) {
  const [x] = await api("POST", "/api/memory/edit", { file: b.dataset.file, old: b.dataset.old, new: b.dataset.new });
  if (x && x.error) throw new Error(x.error);
  toast("Saved");
  b.remove();
}
async function newText(title, body) {   // a draft of the user's own, opened
  const d = await api("POST", "/api/drafts", { title, body });
  V.tab = "me";
  await loadRecap();
  return "d:" + d.id;
}
const ON = {
  brief: (b) => makeBrief(b.dataset.kind),
  "brief-close": () => { V.brief = null; renderBody(true); },
  "brief-draft": async () => showItem(await newText(briefTitle(V.brief.kind), V.brief.lines.map((x) => "- " + x).join("\n"))),
  meet: (b) => { V.meet = b.dataset.id; V.card = null; showTab("meetings"); },
  ask: (b) => ask(b.dataset.q),
  "ask-close": () => { backToItem(); renderToday(); },
  "ask-clear": async () => { await api("DELETE", "/api/assistant/talk"); renderBody(true); },
  file: (b) => { V.mFile = b.dataset.file; V.mTab = "files"; showTab("matters"); },
  "mem-undo": async (b) => { toast("Undone in " + (await api("POST", "/api/memory/undo", { id: b.dataset.id })).file); b.remove(); },
  "mem-accept": memoryEdit,
  "token-get": async () => { await api("POST", "/api/graph/token/fetch"); watchGraphToken(0); await loadRecap(); },
  "token-no": () => { V.pending = null; backToItem(); renderToday(); },
  check: () => withToken(async () => { await api("POST", "/api/assistant/check"); setTimeout(loadRecap, 600); }),
};
const ITEM = {
  "p-done": (r) => closePromise(r, "done"),
  "p-no": (r) => closePromise(r, "dropped"),
  wrong: () => { V.wrong = !V.wrong; renderBody(true); },
  draft: (r, b) => busyBtn(b, async () => { await api("POST", "/api/drafts", { key: r.id }); await loadRecap(); renderBody(true); }),
  copy: async () => { await saveDraft(); await navigator.clipboard.writeText($("#draft-text").value); toast("Copied"); },
  outlook: async () => {
    const d = await saveDraft();
    await withToken(async () => { await api("POST", `/api/drafts/${d.id}/outlook`); toast("In the Outlook drafts; you send it there", 4000); await loadRecap(); renderBody(true); });
  },
  drop: (r) => closeDraft(r, "dropped"),
  used: (r) => closeDraft(r, "used"),
  maybe: (r) => act(r.id, "matter", r.i.maybe),
  snooze: (r, b) => act(r.id, "snooze", plusDays(Number(b.dataset.days))),
  right: async (r) => { await act(r.id, "right"); toast("Noted"); },
};
async function todayClick(e) {
  const b = e.target.closest("[data-do]");
  if (!b) return;
  const a = b.dataset.do, menu = b.closest("details.menu"), r = V.mode === "item" && find(V.sel);
  if (menu) menu.open = false;
  try {
    if (ON[a]) return await ON[a](b);
    if (!r) return;
    if (ITEM[a]) return await ITEM[a](r, b);
    await saveDraft();
    await act(r.id, a, b.dataset.value);
  } catch (err) { if (err.message === "token") openConnect(); else say(err.message); }
}
$("#today-body").addEventListener("click", todayClick);
$("#today-bar").addEventListener("click", todayClick);
$("#today-body").addEventListener("change", async (e) => {
  const s = e.target.closest("[data-do-change]");
  if (s) try { s.blur(); await act(V.sel, s.dataset.doChange, s.value); } catch (err) { say(err.message); }
});
$("#today-body").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  if (f.id !== "token-form") return rewrite(f);
  try { await api("POST", "/api/graph/token", { token: f.token.value.trim() }); f.token.value = ""; tokenSaved(); } catch (err) { say(err.message); }
});

// ---------------------------------------------------------------- the list and its head
$("#today-rows").addEventListener("click", async (e) => {
  const row = e.target.closest("[data-row]"), fold = e.target.closest("[data-fold]"), b = e.target.closest("[data-do]"), s = b && b.closest(".suggest");
  if (row) return showItem(row.dataset.row);
  if (fold) { opened[fold.dataset.fold] = !opened[fold.dataset.fold]; return renderToday(); }
  if (s) try { return await act(s.dataset.key, b.dataset.do, s.querySelector("input").value); } catch (err) { return say(err.message); }
  todayClick(e);
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
$("#today-tabs").addEventListener("click", (e) => { const b = e.target.closest("[data-seg]"); if (b) { V.tab = b.dataset.seg; select(""); renderToday(); } });
$("#recap-range").addEventListener("change", (e) => $("#recap-since").classList.toggle("hidden", e.target.value !== "since"));
function range() {
  const v = $("#recap-range").value;
  return v === "new" ? { kind: "new" } : v === "since" ? { kind: "since", date: $("#recap-since").value } : { kind: "days", days: Number(v) };
}
$("#recap-update").addEventListener("click", async () => {
  try {
    if (running()) { await api("POST", "/api/assistant/stop"); toast("Stopping; what is sorted stays", 3000); }
    else { await api("POST", "/api/assistant/update", { range: range() }); V.asked = null; }
    setTimeout(loadRecap, 300);
  } catch (e) { say(e.message); }
});
addEventListener("click", (e) => document.querySelectorAll("details.menu[open]").forEach((m) => { if (!m.contains(e.target)) m.open = false; }));
addEventListener("keydown", (e) => { if (e.key === "Escape") document.querySelectorAll("details.menu[open]").forEach((m) => (m.open = false)); });

export { newText };
