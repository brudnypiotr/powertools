// Today, the right side: one item, a promise, or a text of the user's own. First the next step, last "this is wrong".
import { GAP, SRC, bar, button, cap, draw, esc, link, outLink, plain, tag, url } from "../core.js";
import { draftCard, draftOf } from "./draft.js";
import { find } from "./list.js";
import { R, V } from "./store.js";

const BODY = "#today-body", BAR = "#today-bar";
function bodyItem(force) {
  const r = find(V.sel);
  return r.p ? promiseBody(r.p, force) : r.i ? conversationBody(r, force) : textBody(r.d, force);
}
function promiseBody(p, force) {
  const to = p.to && !/^me$/i.test(p.to) ? " to " + esc(p.to) : "";
  draw(BODY, `<h2 class="big">${esc(cap(p.what))}</h2><p class="lead">${p.mine ? "You promised it" : `${esc(p.who)} promised it`}${to}.</p>
    <dl><dt>Due</dt><dd>${esc(p.due || "no date")}</dd><dt>From</dt><dd>${SRC[p.source] || ""}, ${link(p.link, plain(p.title))}</dd><dt>Why here</dt><dd><q>${esc(p.quote)}</q></dd></dl>`, force);
  bar(BAR, button("Done", "primary", { do: "p-done", k: "p1" }) + GAP + button("Not a promise", "quietbtn", { do: "p-no", k: "p2" }));
}
function textBody(d, force) {
  draw(BODY, `<h2 class="big">${esc(d.title)}</h2>${draftCard(d)}`, force);
  bar(BAR, button("Copy", "primary", { do: "copy", k: "c" }) + button("Done", "", { do: "used", k: "u" }));
}

// ---------------------------------------------------------------- a conversation
function conversationBody(r, force) {
  const i = r.i, d = draftOf(r), who = i.person || i.people || "";
  draw(BODY, `<h2 class="big">${esc(cap(i.action || i.summary || plain(i.title)))}</h2>${i.action && i.summary ? `<p class="lead">${esc(i.summary)}</p>` : ""}
    ${i.orders ? `<div class="note">This text gives orders to an AI. Check it yourself.</div>` : ""}
    <dl><dt>From</dt><dd>${who ? `<b>${esc(who)}</b>, ` : ""}${SRC[i.source] || ""}, ${link(i.link, plain(i.title))}</dd>
    <dt>When</dt><dd>${whenOf(i.last)} ${tag(i.due, "hot")} ${i.quiet ? tag(`quiet for ${i.days} days`, "hot") : ""}</dd>
    <dt>Matter</dt><dd>${matterPicker(i)}</dd><dt>Why here</dt><dd>${why(i)}</dd></dl>${d ? draftCard(d) : ""}`, force);
  bar(BAR, r.kind === "closed" ? button("Bring it back", "primary", { do: "reopen", k: "a1" }) : actions(r, d));
}
const whenOf = (iso) => new Date(iso).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
function matterPicker(i) {
  const matters = R.matters.filter((m) => m.status !== "closed" || m.id === i.matter);
  const options = matters.map((m) => `<option value="${esc(m.id)}" ${m.id === i.matter ? "selected" : ""}>${esc(m.title)}</option>`).join("");
  return `<select data-do-change="matter" data-k="matter" aria-label="Matter"><option value="">none</option>${options}</select>
    ${i.maybe_title ? button(i.maybe_title + "?", "inbtn", { do: "maybe", k: "maybe" }) : ""}`;
}
const BY = { rule: "Automatic sender.", you: "You decided it." };
function why(i) {
  if (i.error) return `<span class="err">${esc(i.error)}</span>`;
  const proof = i.quote ? `<q>${esc(i.quote)}</q>` : `<span class="muted">${BY[i.by] || "The model gave no sentence that proves it: check the source."}</span>`;
  return `${proof} ${i.reviewed ? `<span class="muted small">Checked by you</span>` : button("That is right", "inbtn", { do: "right", k: "right" })}`;
}
function actions(r, d) {
  const i = r.i, canDraft = ["mail", "teams"].includes(i.source) && ["me", "wait"].includes(r.kind);
  const done = d || canDraft ? button("Done", "", { do: "done", k: "a2" }) : "";
  return firstAction(r, d, canDraft) + done + LATER + GAP + button("This is wrong", "quietbtn", { do: "wrong", k: "a4" }, `aria-expanded="${V.wrong}"`) + (V.wrong ? wrongChips(i) : "");
}
function firstAction(r, d, canDraft) {
  const main = (text, what) => button(text, "primary", { do: what, k: "a1" });
  if (d && r.i.source === "mail") {
    if (!d.outlook) return main("Put into Outlook drafts", "outlook");
    if (!d.same) return main("Put the new text into Outlook drafts", "outlook");
    return url(d.outlook) ? outLink(d.outlook, "Open it in Outlook", "primary") : `<button type="button" class="primary" disabled>In Outlook drafts</button>`;
  }
  if (d) return main("Copy for Teams", "copy");
  if (canDraft) return main(r.kind === "wait" ? "Write a reminder" : "Write a draft", "draft");
  return main(r.kind === "chg" ? "Got it" : "Done", "done");
}
const LATER = `<details class="menu up"><summary data-k="later">Later</summary><div>${[["Tomorrow", 1], ["In 3 days", 3], ["In a week", 7]]
  .map(([label, days]) => button(label, "", { do: "snooze", days })).join("")}</div></details>`;
const WRONG = [["not_mine", "", "Not mine"], ["category", "todo", "For me to do"], ["category", "reply", "For me to answer"], ["category", "waiting", "I wait for it"],
  ["category", "fyi", "Only good to know"], ["mute", "", "Ignore this conversation"]];
const wrongChips = (i) => `<div class="chipsrow">${WRONG.filter(([a, c]) => a !== "category" || c !== i.category)
  .map(([a, c, label]) => button(label, "pillbtn", { do: a, value: c, k: `w-${a}${c}` })).join("")}</div>`;

export { bodyItem };
