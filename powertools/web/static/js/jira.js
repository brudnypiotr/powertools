// Jira downloads.
import { $, ago, api, esc, say, toast } from "./core.js";
import { loadJobs } from "./jobs.js";
import { onTab } from "./tabs.js";

async function renderJiraBar(check) {
  const bar = $(".jirabar");
  if (check) bar.querySelectorAll("button").forEach((b) => (b.disabled = true));
  const busy = bar.querySelector(".token-check");
  if (check && busy) busy.textContent = "Checking...";
  let s;
  try { s = await api("GET", "/api/jira/status" + (check ? "?check=1" : "")); }
  catch (e) { bar.textContent = e.message; return; }
  const status = !s.site ? `<span class="warn">No Jira site: set it in Settings > Jira.</span>`
    : !s.has_token ? `<span class="warn">No Jira token: paste one here.</span>`
    : s.error ? `<span class="warn">${esc(s.error)}</span>`
    : `<span class="ok">Connected${s.user ? " as " + esc(s.user) : ""}</span>`;
  const when = s.checked_at ? ` · checked ${new Date(s.checked_at * 1000).toLocaleTimeString()}` : "";
  const mode = s.site ? (s.cloud ? "Jira Cloud: e-mail + API token" : "Jira Data Center: personal access token") : "";
  const rows = (s.checks || []).map((c) => `<tr><td class="${c.ok ? "ok" : c.ok === false ? "warn" : "muted"}">${
    c.ok ? "✓" : c.ok === false ? "✗" : "·"}</td><td>${esc(c.source)}</td><td class="muted">${esc(c.token)}</td>
    <td class="muted">${esc(c.email)}</td><td>${esc(c.detail)}</td></tr>`).join("");
  bar.innerHTML = `<div class="jira-line">${status} <span class="muted">${esc(s.site)} · ${esc(mode)}${when}</span></div>
    ${rows ? `<table class="list checks"><thead><tr><th></th><th>Token from</th><th>Token</th><th>E-mail</th><th>Result</th></tr></thead><tbody>${rows}</tbody></table>` : ""}
    <div class="jira-line"><input type="password" placeholder="Paste a Jira API token (Cloud) or personal access token (Data Center)" class="token-input">
    <button class="token-save">${s.token_source === "saved in powertools" ? "Replace token" : "Save token"}</button><button class="token-check">Check</button>
    ${s.token_source === "saved in powertools" ? `<button class="token-clear">Forget token</button>` : ""}</div>`;
  bar.querySelector(".token-check").onclick = () => renderJiraBar(true);
  bar.querySelector(".token-save").onclick = async () => {
    const input = bar.querySelector(".token-input"), token = input.value.trim();
    if (!token) { input.focus(); return toast("Paste a token into the box first"); }
    const btn = bar.querySelector(".token-save");
    btn.disabled = true; btn.textContent = "Checking...";
    try { await api("POST", "/api/jira/token", { token }); toast("Token saved"); }
    catch (e) { say(e.message); }
    renderJiraBar(false);
  };
  const clr = bar.querySelector(".token-clear");
  if (clr) clr.onclick = async () => { await api("DELETE", "/api/jira/token"); renderJiraBar(false); };
  if (check) toast(s.error ? "Check failed, see the table" : "Jira accepted the token");
}
let jiraFieldsLoaded = false, jiraLast = { jql: "", issues: [] }, jiraDone = [];
async function loadJiraFields() {
  if (jiraFieldsLoaded) return;
  try {
    const fields = await api("GET", "/api/jira/fields");
    $("#jira-field-names").innerHTML = fields.map((f) => `<option value="${esc(f.name)}">${esc(f.id)}</option>`).join("");
    jiraFieldsLoaded = true;
  } catch { /* not connected yet */ }
}
const OPS = ["=", "!=", "~", "!~", ">", ">=", "<", "<=", "in", "not in", "is", "is not"];
$("#jira-add-filter").addEventListener("click", () => {
  const row = document.createElement("div");
  row.className = "filter";
  row.innerHTML = `<input class="f-field" list="jira-field-names" placeholder="field, e.g. Fix versions">
    <select class="f-op">${OPS.map((o) => `<option>${o}</option>`).join("")}</select>
    <input class="f-value" placeholder="value (in: a, b; is: EMPTY)"><button type="button" class="icon" title="Remove">✕</button>`;
  row.querySelector("button").onclick = () => row.remove();
  $("#jira-filters").appendChild(row);
});
function jiraOptions() {
  const o = $(".jira-opts");
  return { depth: Number(o.querySelector("[name=depth]").value),
    follow: [...o.querySelectorAll("[name=follow]:checked")].map((c) => c.value),
    attachments: o.querySelector("[name=attachments]").checked,
    max_issues: Number(o.querySelector("[name=max_issues]").value) };
}
async function jiraDownload(seed) {
  try { await api("POST", "/api/jira/download", { seed, options: jiraOptions() }); loadJobs(); watchJira(); }
  catch (e) { say(e.message); }
}
$("#jira-quick").addEventListener("submit", (e) => {
  e.preventDefault();
  const f = e.target;
  if (!f.value.value.trim()) return;
  jiraDownload({ mode: f.mode.value, value: f.value.value });
});
$("#jira-search").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const form = { project: f.project.value, epic: f.epic.value, labels: f.labels.value, text: f.text.value,
    updated_days: f.updated_days.value ? Number(f.updated_days.value) : 0, jql: f.jql.value,
    filters: [...document.querySelectorAll("#jira-filters .filter")].map((r) => ({
      field: r.querySelector(".f-field").value, op: r.querySelector(".f-op").value, value: r.querySelector(".f-value").value })) };
  $("#jira-results").innerHTML = `<tr><td colspan="7">Searching...</td></tr>`;
  try { jiraLast = await api("POST", "/api/jira/search", { form }); }
  catch (err) { $("#jira-results").innerHTML = `<tr><td colspan="7">${esc(err.message)}</td></tr>`; $("#jira-jql").textContent = ""; return; }
  $("#jira-jql").textContent = `JQL: ${jiraLast.jql} · ${jiraLast.issues.length} issue(s)`;
  $("#jira-results").innerHTML = jiraLast.issues.map((i) => `<tr>
      <td><input type="checkbox" value="${esc(i.key)}"></td><td>${esc(i.key)}</td><td>${esc(i.summary)}</td>
      <td class="muted">${esc(i.type)}</td><td class="muted">${esc(i.status)}</td><td class="muted">${ago(i.updated)}</td>
      <td class="muted">${i.downloaded ? ago(i.downloaded) + (i.changed ? ' <span class="warn">changed</span>' : "") : ""}</td></tr>`).join("")
    || `<tr><td colspan="7">Nothing found.</td></tr>`;
});
$("#jira-all").addEventListener("change", (e) => document.querySelectorAll("#jira-results input").forEach((c) => (c.checked = e.target.checked)));
$("#jira-dl-sel").addEventListener("click", () => {
  const keys = [...document.querySelectorAll("#jira-results input:checked")].map((c) => c.value);
  if (!keys.length) return say("Select issues first.");
  jiraDownload({ mode: "keys", value: keys.join(",") });
});
$("#jira-dl-all").addEventListener("click", () => {
  if (!jiraLast.jql) return say("Search first.");
  jiraDownload({ mode: "jql", value: jiraLast.jql });
});
async function loadJiraDownloaded() {
  try { jiraDone = await api("GET", "/api/jira/downloaded"); } catch { jiraDone = []; }
  renderJiraDownloaded();
}
function renderJiraDownloaded() {
  const q = $("#jira-dl-filter").value.toLowerCase();
  const rows = jiraDone.filter((r) => !q || (r.key + " " + r.summary + " " + r.status).toLowerCase().includes(q));
  $("#jira-downloaded").innerHTML = rows.map((r) => `<tr>
      <td><input type="checkbox" value="${esc(r.key)}"></td><td>${esc(r.key)}</td><td>${esc(r.summary)}</td>
      <td class="muted">${esc(r.status)}</td><td class="muted">${ago(r.updated)}</td><td class="muted">${ago(r.downloaded)}</td>
      <td class="${r.last_result === "changed" ? "warn" : "muted"}">${esc(r.last_result)}</td></tr>`).join("")
    || `<tr><td colspan="7">Nothing downloaded yet.</td></tr>`;
}
$("#jira-dl-filter").addEventListener("input", renderJiraDownloaded);
$("#jira-dl-check").addEventListener("change", (e) => document.querySelectorAll("#jira-downloaded input").forEach((c) => (c.checked = e.target.checked)));
async function jiraRefresh(keys) {
  try {
    await api("POST", "/api/jira/refresh", { keys, options: { attachments: $(".jira-opts [name=attachments]").checked } });
    loadJobs(); watchJira();
  } catch (e) { say(e.message); }
}
$("#jira-refresh-sel").addEventListener("click", () => {
  const keys = [...document.querySelectorAll("#jira-downloaded input:checked")].map((c) => c.value);
  if (!keys.length) return say("Select downloaded issues first.");
  jiraRefresh(keys);
});
$("#jira-refresh-all").addEventListener("click", () => jiraRefresh([]));
let jiraWatch = null;
function watchJira() {  // refresh the Downloaded list while a Jira job runs
  clearTimeout(jiraWatch);
  jiraWatch = setTimeout(async () => {
    const jobs = await api("GET", "/api/jobs");
    loadJiraDownloaded();
    if (jobs.some((j) => j.title.startsWith("Jira") && (j.state === "running" || j.state === "queued"))) watchJira();
  }, 2000);
}

onTab("jira", () => { renderJiraBar(false); loadJiraDownloaded(); loadJobs(); loadJiraFields(); });
