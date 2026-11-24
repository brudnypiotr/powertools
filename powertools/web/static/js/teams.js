// Teams exports.
import { $, ago, esc, say } from "./core.js";
import { graphCall, onToken } from "./graph.js";
import { loadJobs } from "./jobs.js";
import { TYPES, rangeOf } from "./ranges.js";
import { on, onTab } from "./tabs.js";

let chats = [];
async function loadChats(refresh) {
  $("#chats").innerHTML = `<tr><td colspan="5">Loading chats...</td></tr>`;
  try { chats = await graphCall("GET", "/api/teams/chats" + (refresh ? "?refresh=1" : "")); }
  catch (e) { $("#chats").innerHTML = `<tr><td colspan="5">${esc(e.message)}</td></tr>`; return; }
  renderChats();
}
function renderChats() {
  const q = $("#chat-filter").value.toLowerCase(), type = $("#chat-type").value;
  const rows = chats.filter((c) => (!type || c.type === type) &&
    (!q || c.title.toLowerCase().includes(q) || c.members.join(" ").toLowerCase().includes(q)));
  $("#chats").innerHTML = rows.map((c) => `<tr>
      <td><input type="checkbox" value="${esc(c.id)}"></td><td>${esc(c.title)}</td>
      <td class="muted">${TYPES[c.type] || esc(c.type)}</td><td class="muted">${ago(c.last)}</td>
      <td class="muted">${c.exported ? ago(c.exported) : ""}</td></tr>`).join("")
    || `<tr><td colspan="5">No chats match.</td></tr>`;
}
$("#chat-filter").addEventListener("input", renderChats);
$("#chat-type").addEventListener("change", renderChats);
$("#chat-refresh").addEventListener("click", () => loadChats(true));
$("#chat-all").addEventListener("change", (e) => document.querySelectorAll("#chats input").forEach((c) => (c.checked = e.target.checked)));
$("#chat-export").addEventListener("click", async () => {
  const ids = [...document.querySelectorAll("#chats input:checked")].map((c) => c.value);
  if (!ids.length) return say("Select chats first.");
  try { await graphCall("POST", "/api/teams/export", { ids, range: rangeOf("teams") }); loadJobs(); }
  catch (e) { say(e.message); }
});

onTab("teams", () => { if (!chats.length) loadChats(false); });
onToken(() => { if (on("teams")) loadChats(true); });
