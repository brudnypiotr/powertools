// Meetings ahead and their cards.
import { $, GAP, api, bar, busyBtn, button, clockOf, dayOf, draw, esc, isToday, list, outLink, rowHtml, say, segments, url } from "../core.js";
import { done } from "../edit.js";
import { showItem } from "./body.js";
import { newText } from "./clicks.js";
import { R, V, loadRecap, onRecap } from "./store.js";
import { showTab } from "../tabs.js";

function renderMeet() {
  segments("#meet-tabs", [["ahead", "Ahead", R.events.length], ["rec", "Recorded"]], V.meetTab, "seg");
  const rec = V.meetTab === "rec";
  $("#meet-ahead").classList.toggle("hidden", rec);
  $("#meet-recorded").classList.toggle("hidden", !rec);
  if (rec) return draw("#meet-rows", `<p class="muted pad">Teams calls are noticed by themselves. A summary and the promises made go into Today.</p>`);
  const days = {}, person = V.card && V.card.id.startsWith("person:") ? V.card : null;
  R.events.forEach((e) => (days[dayOf(e.start)] ||= []).push(e));
  if (!person && !R.events.some((e) => e.id === V.meet)) V.meet = (R.events[0] || {}).id || "";
  const one = person ? `<h2>1:1</h2>${rowHtml({ id: person.id, title: person.title, sub: "Card", current: V.meet === person.id })}` : "";
  const ahead = Object.entries(days).map(([d, l]) => `<h2>${esc(isToday(l[0].start) ? "Today" : d)}</h2>${l.map(meetingRow).join("")}`).join("");
  draw("#meet-rows", one + ahead || `<div class="empty"><b>No meetings to show.</b><br>The calendar of the next week comes with an Update.</div>`);
  meetBody();
}
const some = (people, n, more) => people.slice(0, n).join(", ") + (people.length > n ? more(people.length - n) : "");
const meetingRow = (e) => rowHtml({ id: e.id, time: clockOf(e.start), title: e.title, sub: some(e.people, 3, (n) => ` +${n}`), tag: e.prepared ? "Ready" : "", kind: "ready", current: V.meet === e.id });
const NO_CARD = "<p>No card yet. It takes the invitation, what is open with these people, the last recording and your notes, and writes the goal, what to ask and a short agenda.</p>";
function cardHtml(c) {
  const part = (title, l) => (l && l.length ? `<div><h3>${title}</h3>${list(l.map(esc))}</div>` : "");
  return `${c.goal ? `<h3>Goal</h3><p class="goal">${esc(c.goal)}</p>` : ""}<div class="two">${part("Open from before", c.open)}${part("To ask", c.questions)}${part("What I know", c.known)}${part("Agenda", c.agenda)}</div>`;
}
const whenWith = (e) => `<p class="lead">${isToday(e.start) ? "Today" : dayOf(e.start)} ${clockOf(e.start)}${e.people.length ? `, with ${esc(some(e.people, 8, (n) => ` and ${n} more`))}` : ""}</p>`;
async function loadCard(id) {
  try { V.card = await api("POST", "/api/assistant/prep", { id }); } catch (err) { say(err.message); }
}
async function meetBody() {
  const id = V.meet, e = R.events.find((x) => x.id === id), mine = () => V.card && V.card.id === id;
  if (e && e.prepared && !mine()) await loadCard(id);
  if (V.meet !== id) return;
  const c = mine() ? V.card : null;
  if (!e && !c) { draw("#meet-body", ""); return bar("#meet-bar", ""); }
  draw("#meet-body", `<h2 class="big">${esc(e ? e.title : c.title)}</h2>${e ? whenWith(e) : ""}${c ? cardHtml(c) : NO_CARD}`, true);
  const join = e && url(e.join) ? outLink(e.join, "Join", c ? "primary" : "") : "";
  bar("#meet-bar", c ? join + (c.agenda.length ? button("Draft the agenda", "", { m: "agenda", k: "ag" }) : "") + GAP + button("Make it again", "quietbtn", { m: "again", k: "ga" }) + button("Edit the card", "quietbtn", { m: "file", k: "gf" }) + button("Throw it away", "quietbtn", { m: "drop", k: "gd" })
    : button("Prepare me", "primary", { m: "make", k: "mk" }) + join);
}
$("#meet-tabs").addEventListener("click", (e) => { const b = e.target.closest("[data-seg]"); if (b) { V.meetTab = b.dataset.seg; renderMeet(); } });
$("#meet-rows").addEventListener("click", (e) => { const b = e.target.closest("[data-row]"); if (b) { V.meet = b.dataset.row; renderMeet(); $("#meet-body").scrollTop = 0; } });
$("#meet-bar").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-m]");
  if (!b) return;
  const a = b.dataset.m, c = V.card;
  if (a === "make" || a === "again") return busyBtn(b, async () => {
    const person = V.meet.startsWith("person:") ? V.meet.slice(7) : "";
    V.card = await api("POST", "/api/assistant/prep", { id: person ? "" : V.meet, again: a === "again", person });
    await loadRecap();
  });
  try {
    if (a === "agenda") { const id = await newText(c.title, "Agenda:\n" + c.agenda.map((x) => "- " + x).join("\n")); showTab("recap"); return showItem(id); }
    if (a === "drop") {
      const e2 = await api("DELETE", "/api/assistant/prep?id=" + encodeURIComponent(c.id));
      V.card = null;
      await loadRecap();
      return done("Thrown away", e2.id);
    }
    if (a === "file") { V.mTab = "files"; V.mFile = c.file; showTab("matters"); }
  } catch (err) { say(err.message); }
});

onRecap("meetings", renderMeet);
