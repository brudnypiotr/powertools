// The export jobs under the export tabs.
import { api, esc } from "./core.js";
import { onToken, renderGraphBar } from "./graph.js";
import { onTab } from "./tabs.js";

let jobsTimer = null;
async function loadJobs() {
  const jobs = await api("GET", "/api/jobs");
  const card = (j) => `<div class="card job ${j.state}">
      <div class="title"><b>${esc(j.title)}</b> · ${j.state.replace("_", " ")} ${j.total ? `· ${j.done}/${j.total}` : ""}</div>
      ${j.state === "running" || j.state === "queued" ? `<div class="bar"><span style="width:${j.total ? (100 * j.done / j.total) : 5}%"></span></div>` : ""}
      ${j.error ? `<div>${esc(j.error)}</div>` : ""}
      ${j.files.length || j.notes.length ? `<ul>${j.files.map((f) => `<li class="file"><span title="${esc(f)}">${esc(f.split(/[\\/]/).pop())}</span>
          <button class="mini" data-open="${esc(f)}">Open</button><button class="mini" data-reveal="${esc(f)}">Open folder</button></li>`).join("")}
        ${j.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}</div>`;
  document.querySelectorAll(".jobs").forEach((el) => {  // each tab lists only its own exports
    const mine = jobs.filter((j) => !el.dataset.kind || j.kind === el.dataset.kind);
    el.innerHTML = mine.length ? `<div class="row"><h2>Exports</h2><button class="open-out" data-kind="${el.dataset.kind || ""}">Open export folder</button></div>` +
      mine.map(card).join("") : "";
  });
  document.querySelectorAll(".jobs .open-out").forEach((b) => (b.onclick = () => api("POST", "/api/open-output", { kind: b.dataset.kind })));
  document.querySelectorAll(".jobs [data-open]").forEach((b) => (b.onclick = () => api("POST", "/api/open-path", { path: b.dataset.open })));
  document.querySelectorAll(".jobs [data-reveal]").forEach((b) => (b.onclick = () => api("POST", "/api/open-path", { path: b.dataset.reveal, folder: true })));
  if (jobs.some((j) => j.state === "needs_token")) renderGraphBar();
  clearTimeout(jobsTimer);
  if (jobs.some((j) => j.state === "running" || j.state === "queued")) jobsTimer = setTimeout(loadJobs, 1500);
}

onTab(["teams", "mail", "org"], loadJobs);
onToken(loadJobs);

export { loadJobs };
