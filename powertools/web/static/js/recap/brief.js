// Today, the right side without an item: the day at a glance, or a brief the model wrote.
import { $, api, bar, button, clockOf, draw, esc, say, tag } from "../core.js";
import { mineFirst } from "./list.js";
import { R, V, meetingsToday } from "./store.js";

const KINDS = { day: ["Today", "Today in 5 lines"], week: ["The week", "The week in 10 lines"], upward: ["For my manager", "For my manager"], leave: ["While you were away", "I was away"] };
const briefTitle = (kind) => KINDS[kind][0];
const shownBrief = () => V.brief || (V.noBrief ? null : R.brief);      // asked for now, or the one that came with today's Update
function headline(top) {
  const b = shownBrief();
  if (b) return b.working ? "Writing..." : briefTitle(b.kind);
  if (!R.updated) return "Press Start my day";
  return ["Nothing waits for you", "One thing for you", "First these two", "First these three"][top.length];
}
function intro(top) {
  if (!R.updated) return "It reads mail, Teams, Jira, the calendar and recorded meetings, and sorts them into what is yours, what you wait for and what changed.";
  if (top.length || shownBrief()) return "";
  const n = R.waiting.length;
  return n ? `You wait for ${n} answer${n === 1 ? "" : "s"}.` : "You wait for nobody.";
}
const topLine = (i) => `${esc(i.action || i.summary)} ${tag(i.due, "hot")} <span class="muted">${esc(i.person || "")}</span>`;
const meetingLine = (e) => `<div><time>${clockOf(e.start)}</time><span class="grow"><b>${esc(e.title)}</b> <span class="muted">${esc(e.people.slice(0, 3).join(", "))}</span></span>
  ${button(e.prepared ? "Open card" : "Prepare me", "inbtn", { do: "meet", id: e.id, k: "m-" + e.id })}</div>`;
function bodyBrief(force) {
  const top = mineFirst(), today = meetingsToday(), b = shownBrief(), said = intro(top);
  const lines = b ? (b.lines || []).map(esc) : top.map(topLine);
  const saved = b && b.lines ? `<p class="muted small">Saved in ${esc(b.file)}. ${button("Make a draft", "inbtn", { do: "brief-draft", k: "bd" })} ${button("Back to the day", "inbtn", { do: "brief-close", k: "bc" })}</p>` : "";
  const write = Object.entries(KINDS).map(([k, [, label]]) => button(label, "inbtn", { do: "brief", kind: k, k: "b-" + k })).join("");
  draw("#today-body", `<h2 class="big">${headline(top)}</h2>${said ? `<p class="lead">${said}</p>` : ""}
    ${b && b.working ? "" : `<ol class="lines">${lines.map((x) => `<li>${x}</li>`).join("")}</ol>`}${saved}
    ${today.length ? `<h3>Meetings</h3><div class="lined">${today.map(meetingLine).join("")}</div>` : ""}
    ${R.updated ? `<p class="writefor"><span class="muted">Write for me:</span> ${write}</p>` : ""}`, force);
  bar("#today-bar", "");
}
async function makeBrief(kind) {
  $("#ai-stop").classList.remove("hidden");
  V.brief = { working: true }; V.mode = "brief"; bodyBrief(true);
  try { V.brief = await api("POST", "/api/assistant/brief", { kind }); }
  catch (err) { V.brief = null; say(err.message === "stopped" ? "Stopped" : err.message); }
  $("#ai-stop").classList.add("hidden");
  if (V.mode === "brief") bodyBrief(true);
}

export { bodyBrief, briefTitle, makeBrief, shownBrief };
