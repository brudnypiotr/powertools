// The draft under an item (or a text of the user's own): shown, saved when the user leaves it, rewritten on a remark.
import { $, api, busyBtn, button, GAP, esc } from "../core.js";
import { renderToday } from "./list.js";
import { R } from "./store.js";

const draftOf = (r) => (r.d ? r.d : r.i && r.i.draft ? R.draft_list.find((d) => d.id === r.i.draft) : null);
const rowsFor = (text) => Math.min(14, Math.max(4, text.split("\n").length + 1));
function draftCard(d) {
  const old = d.old ? `<div class="note">Written before the newest message. ${button("Write a new one", "inbtn", { do: "draft", k: "redraft" })}</div>` : "";
  return `<div class="draftcard" data-did="${esc(d.id)}"><div class="row0"><label for="draft-text"><b>Draft${d.to ? " to " + esc(d.to) : ""}</b></label>${GAP}${button("Throw away", "quietbtn", { do: "drop", k: "drop" })}</div>${old}
    <textarea id="draft-text" data-k="draft-text" rows="${rowsFor(d.body)}">${esc(d.body)}</textarea>
    <form class="fixline"><input name="remark" data-k="remark" aria-label="What to change" placeholder="What should change? Shorter, no greeting, say Thursday"><button data-k="fixit">Rewrite</button></form><div class="rule"></div></div>`;
}
async function saveDraft() {   // what is in the field now; the draft, or undefined when there is none on the screen
  const c = $("#today-body .draftcard"), ta = c && c.querySelector("textarea"), d = c && R.draft_list.find((x) => x.id === c.dataset.did);
  if (d && ta.value !== d.body) Object.assign(d, await api("POST", `/api/drafts/${d.id}`, { body: ta.value }), { body: ta.value });
  return d;
}
async function rewrite(form) {
  const c = form.closest(".draftcard"), ta = c.querySelector("textarea"), rule = c.querySelector(".rule");
  if (!form.remark.value.trim()) return form.remark.focus();
  await busyBtn(form.querySelector("button"), async () => {
    const r = await api("POST", `/api/drafts/${c.dataset.did}/revise`, { remark: form.remark.value, body: ta.value });
    ta.value = r.draft.body; form.remark.value = "";
    Object.assign(R.draft_list.find((x) => x.id === c.dataset.did) || {}, r.draft);
    rule.innerHTML = r.rule ? `Always write like this? <b>${esc(r.rule)}</b> ${button("Yes, remember", "inbtn", {})}` : "";
    if (r.rule) rule.querySelector("button").onclick = async () => { await api("POST", "/api/style", { rule: r.rule }); rule.textContent = "Remembered."; };
  });
}
$("#today-body").addEventListener("change", async (e) => { if (e.target.id === "draft-text") { await saveDraft().catch(() => {}); renderToday(); } });

export { draftCard, draftOf, rewrite, saveDraft };
