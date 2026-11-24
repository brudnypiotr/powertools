// Diagnostics.
import { $, api } from "./core.js";
import { stopped } from "./state.js";
import { onTab } from "./tabs.js";

async function loadDiagnostics() {
  $("#diag").textContent = JSON.stringify(await api("GET", "/api/state"), null, 2);
  $("#log").textContent = (await api("GET", "/api/log?lines=200")).lines.join("\n");
}
$("#quit").addEventListener("click", async () => {
  if (!confirm("Quit powertools? The hotkey stops working until you start it again.")) return;
  await api("POST", "/api/quit");
  stopped();
});

onTab("diagnostics", loadDiagnostics);
