// Mail exports.
import { $, ago, esc, say } from "./core.js";
import { graphCall, onToken } from "./graph.js";
import { loadJobs } from "./jobs.js";
import { rangeOf } from "./ranges.js";
import { on, onTab } from "./tabs.js";

let foldersLoaded = false, threads = [];
async function loadFolders() {
  try {
    const folders = await graphCall("GET", "/api/mail/folders");
    $("#mail-search").folder.innerHTML = `<option value="">All mail</option>` +
      folders.map((f) => `<option value="${esc(f.id)}">${esc(f.name)} (${f.total})</option>`).join("");
    foldersLoaded = true;
  } catch (e) { /* no token yet */ }
}
$("#mail-search").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  $("#threads").innerHTML = `<tr><td colspan="6">Searching...</td></tr>`;
  try {
    threads = await graphCall("POST", "/api/mail/threads",
      { folder: f.folder.value, person: f.person.value, subject: f.subject.value, range: rangeOf("mail-search") });
  } catch (err) { $("#threads").innerHTML = `<tr><td colspan="6">${esc(err.message)}</td></tr>`; return; }
  $("#threads").innerHTML = threads.map((t, i) => `<tr>
      <td><input type="checkbox" value="${i}"></td><td>${esc(t.subject)}${t.attachments ? " 📎" : ""}</td>
      <td class="muted">${esc(t.people.join(", "))}</td><td class="muted">${t.count}</td>
      <td class="muted">${ago(t.last)}</td><td class="muted">${t.exported ? ago(t.exported) : ""}</td></tr>`).join("")
    || `<tr><td colspan="6">Nothing found.</td></tr>`;
});
$("#mail-all").addEventListener("change", (e) => document.querySelectorAll("#threads input").forEach((c) => (c.checked = e.target.checked)));
$("#mail-export").addEventListener("click", async () => {
  const items = [...document.querySelectorAll("#threads input:checked")].map((c) => threads[Number(c.value)])
    .map((t) => ({ id: t.id, subject: t.subject }));
  if (!items.length) return say("Select threads first.");
  try { await graphCall("POST", "/api/mail/export", { items, range: rangeOf("mail") }); loadJobs(); }
  catch (e) { say(e.message); }
});

onTab("mail", () => { if (!foldersLoaded) loadFolders(); });
onToken(() => { if (on("mail") && !foldersLoaded) loadFolders(); });
