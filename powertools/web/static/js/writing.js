// Writing: corrections of a text (the wording hotkey, or pasted).
import { $, api, esc, toast } from "./core.js";
import { onState } from "./state.js";
import { onTab, showTab } from "./tabs.js";

let focusSeq = null, wordingSeen = null;
function gateLocalAI(llm) {  // Writing needs a model: greyed out until there is one
  const ok = !llm || llm.installed || llm.running;
  const b = document.querySelector('nav button[data-tab="writing"]');
  b.disabled = !ok;
  b.title = ok ? "" : "Needs Ollama or GitHub Copilot: see the AI tab";
  if (!ok && b.classList.contains("active")) showTab("ai");
}
function followFocus(s) {
  const f = s.focus || {}, w = s.wording || {};
  if (focusSeq === null ? w.state === "working" : f.seq !== focusSeq) { if (f.tab) showTab(f.tab); }
  focusSeq = f.seq;
  const seen = `${w.id}:${w.state}`;
  if (w.id && seen !== wordingSeen) { wordingSeen = seen; if ($("#writing").classList.contains("active")) loadWording(); }
}
async function loadWording() {
  const st = await api("GET", "/api/state");
  $("#w-hint").innerHTML = `Select an English text anywhere and press <b>${esc(st.wording_hotkey)}</b>, or paste it here.`;
  renderWording(await api("GET", "/api/wording"));
}
const STYLE_LABELS = { professional: "More professional", casual: "More casual", shorter: "Shorter" };
function renderWording(w) {
  const box = $("#w-result");
  if (!w || !w.state) { box.innerHTML = ""; return; }
  if (w.original && w.original !== $("#w-text").value && w.mode !== "window") $("#w-text").value = w.original;
  if (w.state === "working") { $("#w-status").textContent = "Checking with the model..."; box.innerHTML = ""; return; }
  $("#w-status").textContent = w.stats ? `${w.stats.model} · ${w.stats.total_s} s` : "";
  if (w.state === "error") { box.innerHTML = `<div class="card"><span class="err">${esc(w.error)}</span></div>`; return; }
  const issues = w.style ? "" : `<h3>Mistakes</h3>` + (w.issues.length ? `<ul class="issues">${w.issues.map((i) => `<li><span class="wrong">${esc(i.wrong)}</span> →
      <span class="better">${esc(i.better)}</span><span class="muted"> · ${esc(i.why)}</span></li>`).join("")}</ul>`
    : `<div class="ok">${w.language && !/^en/i.test(w.language) ? `Not English (${esc(w.language)}): nothing to correct.` : "No mistakes found."}</div>`);
  box.innerHTML = `<div class="card">${issues}
    ${w.changed ? `<h3>${w.style ? esc(STYLE_LABELS[w.style] || w.style) : "Corrected"}</h3><textarea id="w-better" rows="6">${esc(w.improved)}</textarea>
    <div class="row"><span class="muted small">${w.replaced ? "Already pasted over the selection." : "Edit if needed, then copy."}</span>
      <button id="w-copy" class="primary">Copy</button></div>` : ""}</div>`;
  const copy = $("#w-copy");
  if (copy) copy.onclick = () => { navigator.clipboard.writeText($("#w-better").value); toast("Copied"); };
}
async function runWording(style) {
  const text = $("#w-text").value.trim();
  if (!text) return $("#w-text").focus();
  const buttons = document.querySelectorAll("#w-check, .w-style");
  buttons.forEach((b) => (b.disabled = true));
  $("#w-status").textContent = style ? "Rewriting with the model..." : "Checking with the model...";
  $("#w-result").innerHTML = "";
  try { renderWording(await api("POST", "/api/wording", { text, style })); }
  catch (e) { $("#w-status").textContent = ""; $("#w-result").innerHTML = `<div class="card"><span class="err">${esc(e.message)}</span></div>`; }
  buttons.forEach((b) => (b.disabled = false));
}
$("#w-check").addEventListener("click", () => runWording(""));
document.querySelectorAll(".w-style").forEach((b) => b.addEventListener("click", () => runWording(b.dataset.style)));

onState((s) => { gateLocalAI(s.llm); followFocus(s); });
onTab("writing", loadWording);
