// The chat in the corner, on every tab: answers with sources, what it changed in the memory, dictation; it can grow.
import { $, api, button, draw, esc, link, say, toast } from "./core.js";
import { undoMemory } from "./edit.js";
import { micButton } from "./mic.js";
import { on, showTab } from "./tabs.js";
import { showItem } from "./recap/body.js";
import { newText } from "./recap/clicks.js";
import { saveDraft } from "./recap/draft.js";
import { find } from "./recap/list.js";
import { R, V, loadRecap, onLoaded } from "./recap/store.js";

const box = $("#chat"), form = $("#chat-form"), q = form.q;
const IDEAS = ["What am I late with?", "Who am I waiting for, and since when?", "What changed this week?"];
let talk = [], waiting = null, noContext = "";        // waiting: the question being answered; noContext: the item the user does not mean

// ---------------------------------------------------------------- the window: open, big, its size
const keep = { open: false, big: false, w: 400, h: 560 };
try { Object.assign(keep, JSON.parse(localStorage.getItem("chat") || "{}")); } catch { /* the default size */ }
function place() {
  box.classList.toggle("hidden", !keep.open);
  box.classList.toggle("big", keep.big);
  $("#chat-open").classList.toggle("hidden", keep.open || !R || !R.setup_done);
  box.style.setProperty("--w", keep.w + "px");
  box.style.setProperty("--h", keep.h + "px");
  $("#chat-big").setAttribute("aria-pressed", keep.big);
  try { localStorage.setItem("chat", JSON.stringify(keep)); } catch { /* private window */ }
}
async function open(yes) {
  keep.open = yes;
  place();
  if (!yes) return $("#chat-open").focus();
  $("#chat-open .dot").classList.add("hidden");
  await load();
  q.focus();
}
$("#chat-open").addEventListener("click", () => open(true));
$("#chat-close").addEventListener("click", () => open(false));
$("#chat-big").addEventListener("click", () => { keep.big = !keep.big; place(); bottom(); });
$("#chat-clear").addEventListener("click", async () => { try { talk = await api("DELETE", "/api/assistant/talk"); render(); } catch (e) { say(e.message); } });
addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); if (R && R.setup_done) open(!keep.open || !box.contains(document.activeElement)); }
  if (e.key === "Escape" && box.contains(document.activeElement)) open(false);
});
$("#chat-grip").addEventListener("pointerdown", (e) => {   // drag the corner
  e.preventDefault();
  const r = box.getBoundingClientRect(), move = (m) => {
    keep.big = false;
    keep.w = Math.max(320, Math.min(innerWidth - 40, Math.round(r.right - m.clientX)));
    keep.h = Math.max(320, Math.min(innerHeight - 40, Math.round(r.bottom - m.clientY)));
    place();
  };
  addEventListener("pointermove", move);
  addEventListener("pointerup", () => removeEventListener("pointermove", move), { once: true });
});

// ---------------------------------------------------------------- what is said
async function load() { try { talk = await api("GET", "/api/assistant/talk"); render(); } catch (e) { say(e.message); } }
const source = (s) => (s.file ? button(s.title, "inbtn", { c: "file", file: s.file }) : link(s.link, s.title));
function edit(x) {
  const act = { applied: button("Undo", "inbtn", { c: "undo", id: x.id }), proposed: button("Save this", "inbtn", { c: "accept", file: x.file, old: x.old, new: x.new }) }[x.state];
  return `<div class="edit ${esc(x.state)}"><b>${esc(x.file)}</b>${x.old ? `<div class="del">${esc(x.old)}</div>` : ""}${x.new ? `<div class="ins">${esc(x.new)}</div>` : ""}${act || `<span class="err">${esc(x.error)}</span>`}</div>`;
}
const TURN = [["again", "✎", "Edit the question"], ["copy", "⧉", "Copy the answer"], ["draft", "↳", "Make a draft of it"], ["del", "✕", "Delete"]];
const turn = (t) => `<div class="turn" data-id="${esc(t.id)}"><div class="q">${t.about ? `<small>${esc(t.about)}</small>` : ""}${esc(t.question)}</div>
  <div class="a"><div class="text">${esc(t.answer)}</div>${t.sources.length ? `<div class="muted small">${t.sources.map(source).join(", ")}</div>` : ""}${t.edits.map(edit).join("")}
  <span class="tools">${TURN.map(([c, sign, label]) => `<button type="button" data-c="${c}" title="${label}" aria-label="${label}">${sign}</button>`).join("")}</span></div></div>`;
const hello = () => `<p class="lead">Answers come from your matters, mail, chats, calendar and notes, with sources. Say what is different and it is remembered.</p>
  <p class="writefor">${IDEAS.map((x) => button(x, "inbtn", { c: "ask", q: x })).join("")}</p>`;
function render() {
  const asking = waiting ? `<div class="turn"><div class="q">${esc(waiting)}</div><div class="a muted">Thinking...</div></div>` : "";
  if (draw("#chat-body", (talk.length || waiting ? talk.map(turn).join("") : hello()) + asking, true)) bottom();
  $("#chat-clear").classList.toggle("hidden", !talk.length);
  $("#chat-send").textContent = waiting ? "Stop" : "Ask";
  renderAbout();
}
const bottom = () => { const b = $("#chat-body"); b.scrollTop = b.scrollHeight; };
function about() {   // the item the user is looking at
  const r = on("recap") && V.mode === "item" ? find(V.sel) : null;
  return r && r.i && r.id !== noContext ? r : null;
}
function renderAbout() {
  const r = about();
  draw("#chat-about", r ? `<span class="chip">About: ${esc(r.title)} <button type="button" data-c="nocontext" title="Not about this" aria-label="Not about this">✕</button></span>` : "", true);
}
async function ask(text) {
  if (waiting) return api("POST", "/api/ai/stop").catch((e) => say(e.message));
  if (!text.trim()) return q.focus();
  const item = about();
  waiting = text;
  q.value = ""; grow();
  render();
  try {
    await saveDraft().catch(() => {});
    await api("POST", "/api/assistant/ask", { question: text, about: item ? item.id : "" });
    if (!keep.open) $("#chat-open .dot").classList.remove("hidden");
  } catch (e) { if (!q.value) q.value = text; say(e.message === "stopped" ? "Stopped" : e.message); }
  waiting = null;
  await load();
  loadRecap().catch(() => {});
}
function grow() { q.style.height = "auto"; q.style.height = Math.min(160, q.scrollHeight) + "px"; }
q.addEventListener("input", grow);
q.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (!waiting) ask(q.value); } });
form.addEventListener("submit", (e) => { e.preventDefault(); ask(q.value); });
q.insertAdjacentHTML("afterend", micButton("Dictate the question"));

const CLICK = {
  ask: (b) => ask(b.dataset.q),
  file: (b) => { V.mFile = b.dataset.file; V.mTab = "files"; showTab("matters"); },
  undo: async (b) => { await undoMemory(b.dataset.id); b.remove(); },
  accept: async (b) => {
    const [x] = await api("POST", "/api/memory/edit", { file: b.dataset.file, old: b.dataset.old, new: b.dataset.new });
    if (x && x.error) throw new Error(x.error);
    toast("Saved");
    b.remove();
  },
  nocontext: () => { noContext = V.sel; renderAbout(); },
  again: (_b, t) => { q.value = t.question; grow(); q.focus(); },
  copy: async (_b, t) => { await navigator.clipboard.writeText(t.answer); toast("Copied"); },
  draft: async (_b, t) => { const id = await newText(t.question.slice(0, 80), t.answer); showTab("recap"); showItem(id); },
  del: async (_b, t) => { talk = await api("DELETE", "/api/assistant/talk/" + t.id); render(); },
};
box.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-c]"), row = b && b.closest(".turn");
  if (!b) return;
  try { await CLICK[b.dataset.c](b, row && talk.find((t) => t.id === row.dataset.id)); } catch (err) { say(err.message); }
});
onLoaded(() => { if (V.sel !== noContext) noContext = ""; place(); if (keep.open) { if (!talk.length && !waiting) load(); else renderAbout(); } });
place();
