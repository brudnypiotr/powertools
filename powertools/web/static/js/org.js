// Org charts.
import { $, api, esc, say } from "./core.js";
import { graphCall, onToken } from "./graph.js";
import { loadJobs } from "./jobs.js";
import { on, onTab } from "./tabs.js";

let orgChain = [], orgRoot = null;
const personLine = (p) => `<b>${esc(p.name)}</b>${p.title ? ` <span class="muted">${esc(p.title)}</span>` : ""}`;
async function loadOrgChain() {
  try { orgChain = await graphCall("GET", "/api/org/chain"); }
  catch (e) { $("#org-chain").textContent = e.message; return; }
  $("#org-chain").innerHTML = orgChain.map((p, i) =>
    `<button type="button" data-i="${i}" title="${esc(p.department)}">${i ? "↑" + i + " " : ""}${esc(p.name)}</button>`).join(" ");
  $("#org-chain").querySelectorAll("button").forEach((b) => (b.onclick = () => pickOrg(orgChain[Number(b.dataset.i)])));
}
function pickOrg(p) {
  orgRoot = p;
  $("#org-root").innerHTML = `From ${personLine(p)}${p.department ? ` · ${esc(p.department)}` : ""}`;
}
$("#org-search").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = e.target.q.value.trim();
  if (q.length < 2) return;
  $("#org-found").innerHTML = `<li class="muted">Searching...</li>`;
  let found;
  try { found = await graphCall("GET", "/api/org/search?q=" + encodeURIComponent(q)); }
  catch (err) { $("#org-found").innerHTML = `<li>${esc(err.message)}</li>`; return; }
  $("#org-found").innerHTML = found.map((p, i) => `<li><button type="button" data-i="${i}">Pick</button> ${personLine(p)}
    ${p.department ? `<span class="muted">· ${esc(p.department)}</span>` : ""}</li>`).join("") || `<li class="muted">Nobody found.</li>`;
  $("#org-found").querySelectorAll("button").forEach((b) => (b.onclick = () => pickOrg(found[Number(b.dataset.i)])));
});
$("#org-export").addEventListener("click", async () => {
  if (!orgRoot) return say("Pick a person first: one of your managers or a search result.");
  try {
    const job = await graphCall("POST", "/api/org/export", { id: orgRoot.id, depth: Number($("#org-depth").value), above: $("#org-above").checked });
    loadJobs(); waitOrgJob(job.id);
  } catch (e) { say(e.message); }
});
function waitOrgJob(id) {  // open the chart as soon as the export is done
  setTimeout(async () => {
    const j = (await api("GET", "/api/jobs")).find((x) => x.id === id);
    if (!j) return;
    if (j.state === "running" || j.state === "queued" || j.state === "needs_token") return waitOrgJob(id);
    if (j.state === "done" && j.result) { await loadOrgSaved(); openOrg(j.result); }
  }, 1000);
}
async function loadOrgSaved() {
  const list = await api("GET", "/api/org/saved");
  const sel = $("#org-saved"), keep = sel.value;
  sel.innerHTML = `<option value="">Open a saved chart...</option>` + list.map((c) =>
    `<option value="${esc(c.name)}">${esc(c.root)} · ${c.people} people · ${esc(c.generated)}</option>`).join("");
  sel.value = keep;
}
async function openOrg(name) {
  if (!name) return;
  $("#org-saved").value = name;
  try { OrgChart($("#org-view"), await api("GET", "/api/org/saved/" + encodeURIComponent(name))); }
  catch (e) { $("#org-view").textContent = e.message; }
}
$("#org-saved").addEventListener("change", (e) => openOrg(e.target.value));

onTab("org", () => { loadOrgSaved(); if (!orgChain.length) loadOrgChain(); });
onToken(() => { if (on("org")) loadOrgChain(); });
