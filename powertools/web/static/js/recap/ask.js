// Ask: the field above every item, answers with sources, and what was changed in the memory because of it.
import { $, GAP, api, bar, button, draw, esc, link, say } from "../core.js";
import { saveDraft } from "./draft.js";
import { renderToday } from "./list.js";
import { V } from "./store.js";

const IDEAS = ["What am I late with?", "Who am I waiting for, and since when?", "What changed this week?"];
const WELCOME = `<h2 class="big">Ask about your work</h2><p class="lead">Answers come from your matters, mail, chats, calendar and notes, with sources. Say what is different and it is remembered.</p>`;
const source = (s) => (s.file ? button(s.title, "inbtn", { do: "file", file: s.file }) : link(s.link, s.title));
function edit(x) {
  const act = { applied: button("Undo", "inbtn", { do: "mem-undo", id: x.id }), proposed: button("Save this", "inbtn", { do: "mem-accept", file: x.file, old: x.old, new: x.new }) }[x.state];
  return `<div class="edit ${esc(x.state)}"><b>${esc(x.file)}</b>${x.old ? `<div class="del">${esc(x.old)}</div>` : ""}${x.new ? `<div class="ins">${esc(x.new)}</div>` : ""}${act || `<span class="err">${esc(x.error)}</span>`}</div>`;
}
const turn = (t) => `<div class="q">${esc(t.question)}</div><div class="a"><div class="text">${esc(t.answer)}</div>
  ${t.sources.length ? `<div class="muted small">${t.sources.map(source).join(", ")}</div>` : ""}${t.edits.map(edit).join("")}</div>`;
async function bodyAsk(force) {
  const talk = await api("GET", "/api/assistant/talk"), body = $("#today-body");
  if (V.mode !== "ask") return;
  const ideas = IDEAS.map((q) => button(q, "inbtn", { do: "ask", q })).join("");
  if (draw("#today-body", `${talk.length ? talk.map(turn).join("") : WELCOME}<p class="writefor">${ideas}</p>`, force)) body.scrollTop = body.scrollHeight;
  bar("#today-bar", button("Back to the list", "", { do: "ask-close", k: "x1" }) + (talk.length ? GAP + button("New conversation", "quietbtn", { do: "ask-clear", k: "x2" }) : ""));
}
async function ask(q, typed) {   // typed: the question is in the field
  const f = $("#ask-form").q, hint = f.placeholder;
  if (!q.trim()) { V.mode = "ask"; return bodyAsk(true); }
  f.disabled = true;
  if (typed) f.value = "";
  f.placeholder = "Thinking...";
  $("#ai-stop").classList.remove("hidden");
  try { await saveDraft(); await api("POST", "/api/assistant/ask", { question: q }); V.mode = "ask"; await bodyAsk(true); renderToday(); }
  catch (err) { if (typed) f.value = q; say(err.message === "stopped" ? "Stopped" : err.message); }
  $("#ai-stop").classList.add("hidden");
  f.disabled = false; f.placeholder = hint; f.focus();
}
$("#ask-form").addEventListener("submit", (e) => { e.preventDefault(); ask(e.target.q.value, true); });
$("#ai-stop").addEventListener("click", () => api("POST", "/api/ai/stop").catch((e) => say(e.message)));

export { ask, bodyAsk };
