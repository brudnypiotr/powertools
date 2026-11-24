// Settings > Look.
import { $, api, toast } from "./core.js";

const LOOK = ["accent", "sidebar", "sidebar_active"], lookPicked = new Set();
async function loadLook(reload) {
  const b = await api("GET", "/api/branding");
  $("#brand-name").textContent = b.name;
  const logo = $("#brand-logo");
  logo.classList.toggle("hidden", !b.files.logo);
  if (b.files.logo) logo.src = "/branding/logo?" + b.files.logo;
  if (reload) $("#branding-css").href = "/branding.css?" + Date.now();
  $("#look-name").value = b.name === "powertools" ? "" : b.name;
  lookPicked.clear();
  const shown = { accent: "--accent", sidebar: "--side", sidebar_active: "--side2" }, css = getComputedStyle(document.documentElement);
  for (const k of LOOK) $("#look-" + k).value = b.colors[k] || css.getPropertyValue(shown[k]).trim();
}
for (const k of LOOK) $("#look-" + k).addEventListener("input", () => lookPicked.add(k));
const look = async (fn, done) => { try { const r = await fn(); await loadLook(true); toast(done || r.path, 2500); } catch (e) { toast(e.message, 5000); } };
$("#look-save").addEventListener("click", () => look(() => api("POST", "/api/branding", { powertools_name: $("#look-name").value.trim(),
  colors: Object.fromEntries([...lookPicked].map((k) => [k, $("#look-" + k).value])) }), "Applied"));
$("#look-reset").addEventListener("click", () => look(() => api("DELETE", "/api/branding"), "Back to the default look"));
$("#look-pack").addEventListener("click", () => look(() => api("POST", "/api/branding/pack")));
$("#look-zip").addEventListener("change", (e) => {
  const f = e.target.files[0];
  e.target.value = "";
  if (f) look(async () => {
    const r = await fetch("/api/branding/zip", { method: "POST", headers: { "X-Powertools": "1" }, body: f }), d = await r.json();
    if (!r.ok) throw new Error(d.error);
    return d;
  }, "Imported");
});
loadLook();
