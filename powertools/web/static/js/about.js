// About: version, update, the way back, changelog.
import { $, api, esc } from "./core.js";
import { isInstalled } from "./install.js";
import { session } from "./state.js";

const dlg = $("#about");
const logItems = (items) => items.map((c) => `<li><span>${esc(c.date)}</span><span>${esc(c.subject)}</span></li>`).join("");
async function loadAbout(fetchRemote) {
  $("#about-status").textContent = fetchRemote ? "Checking for updates..." : "";
  $("#about-do").classList.add("hidden");
  let a;
  try { a = await api("GET", "/api/about" + (fetchRemote ? "?fetch=1" : "")); }
  catch (e) { $("#about-status").textContent = e.message; return; }
  $("#about-version").textContent = `v${a.version}${a.commit ? " · " + a.commit : ""}${a.branch ? " · " + a.branch : ""}`;
  const e = a.engine || {};
  const rows = [["Whisper", `${e.model || "?"}${e.device ? " on " + e.device + (e.compute ? "/" + e.compute : "") : ""} (${e.state || "?"})`],
    ["Python", a.python], ["Folder", a.root], ["Updates from", a.remote || "?"]];
  $("#about-info").innerHTML = rows.map(([k, v]) => `<span>${esc(k)}</span><span>${esc(v)}</span>`).join("");
  $("#about-log").innerHTML = logItems(a.changelog || []);
  const pending = a.pending || [];
  $("#about-pending").innerHTML = pending.length ? `<h3>New in the update</h3><ul class="changelog new">${logItems(pending)}</ul>` : "";
  if (a.error) $("#about-status").textContent = a.git ? `Updates unavailable: ${a.error}` : "Updates need git (not found).";
  else if (pending.length) {
    $("#about-status").textContent = a.busy ? `${pending.length} new change(s). Update after: ${a.busy}.` : `${pending.length} new change(s) available.`;
    $("#about-do").classList.toggle("hidden", !!a.busy);
  } else $("#about-status").textContent = a.fetched ? "You have the latest version." : "";
  const prev = a.previous || {};
  $("#about-back").classList.toggle("hidden", !prev.version || !!a.busy);
  $("#about-back").textContent = `Back to ${prev.version || ""}`;
  isInstalled(!!a.installed);
}
$("#engine-foot").addEventListener("click", () => { dlg.showModal(); loadAbout(true); });
dlg.querySelector(".close").addEventListener("click", () => dlg.close());
dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
$("#about-source").addEventListener("submit", async (e) => {
  e.preventDefault(); $("#about-source-note").textContent = "Checking the repository (it may ask you to sign in to GitHub)...";
  try { const r = await api("POST", "/api/update/source", { remote: e.target.remote.value.trim() }); $("#about-source-note").textContent = "Updates now come from " + r.remote; loadAbout(true); }
  catch (err) { $("#about-source-note").textContent = err.message; }
});
$("#about-check").addEventListener("click", () => loadAbout(true));
$("#about-do").addEventListener("click", () => otherVersion("/api/update", "Updating..."));
$("#about-back").addEventListener("click", () => otherVersion("/api/update/rollback", "Going back..."));
async function otherVersion(path, doing) {
  $("#about-do").classList.add("hidden");
  $("#about-back").classList.add("hidden");
  session.updating = true;
  $("#about-status").textContent = doing;
  let r;
  try { r = await api("POST", path); } catch (e) { session.updating = false; $("#about-status").textContent = e.message; return; }
  if (!r.restarting) { session.updating = false; loadAbout(false); return; }
  $("#about-status").textContent = `${r.before} → ${r.after}, a backup is made. Restarting (setup may run first)...`;
  const started = Date.now();
  await new Promise((ok) => setTimeout(ok, 3000));
  const wait = async () => {
    try { await api("GET", "/api/ping"); location.reload(); }
    catch { if (Date.now() - started < 300000) setTimeout(wait, 1500); else $("#about-status").textContent = "Restart takes long: start powertools from the shortcut (see setup.log)."; }
  };
  wait();
}
