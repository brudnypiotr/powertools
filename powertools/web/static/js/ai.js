// The AI tab: where the model runs, Copilot, the local model, comparing them.
import { $, api, draw, esc, say, toast } from "./core.js";
import { onTab } from "./tabs.js";

let aiTimer = null;
const mb = (v) => (v / 1024).toFixed(1) + " GB";
async function loadAI() {
  const s = await api("GET", "/api/llm/status");
  const g = s.gpu || {}, e = s.engine || {};
  const gpu = g.name ? `<b>${esc(g.name)}</b> · ${mb(g.total_mb)} VRAM, ${mb(g.free_mb)} free now (${mb(g.used_mb)} in use)`
    : `<span class="warn">No NVIDIA GPU found${g.error ? ": " + esc(g.error) : ""}.</span> The model can still run on the CPU, slowly.`;
  const whisper = `Whisper: ${esc(e.model || "?")}${e.device ? " on " + esc(e.device) : ""} (${esc(e.state || "?")})` +
    (e.device === "cuda" ? ". Choosing Parakeet V3 for dictation leaves the whole GPU to the model." : "");
  const inst = s.install || {};
  const instProg = inst.total
    ? ` ${Math.round(100 * inst.completed / inst.total)}% (${(inst.completed / 1e9).toFixed(2)} / ${(inst.total / 1e9).toFixed(2)} GB)`
    : inst.completed ? ` ${(inst.completed / 1e9).toFixed(2)} GB` : "";
  const ollama = inst.state === "running"
    ? `<span class="warn">Setting up Ollama locally (${esc(inst.status)}${instProg})...</span>`
    : !s.installed && !s.running
    ? `<span class="warn">Ollama is not installed.</span> <button id="ai-install" class="primary">Download &amp; set up Ollama</button>
       <span class="muted small">(GitHub ZIP → %LOCALAPPDATA%\\powertools\\ollama, no installer, no admin rights)</span>`
    : !s.running ? `<span class="warn">Ollama is installed${s.exe ? ` (${esc(s.exe)})` : ""} but not running.</span> <button id="ai-start" class="primary">Start it</button>`
    : `<span class="ok">Ollama ${esc(s.version)} is running</span>${s.exe ? ` <span class="muted small">(${esc(s.exe)})</span>` : ""}.`;
  const instErr = inst.state === "error" ? `<div class="err">Ollama setup failed: ${esc(inst.error)}</div>` : "";
  const models = (s.models || []).map((m) => `<li><b>${esc(m.name)}</b> <span class="muted">${m.size_gb} GB · ${esc(m.params)} · ${esc(m.quant)}</span>
    <button class="ai-delete" data-model="${esc(m.name)}">Delete</button></li>`).join("");
  const loaded = (s.loaded || []).map((m) => `${esc(m.name)}: ${m.gpu_share}% on the GPU (${m.vram_gb} of ${m.size_gb} GB)`).join("; ");
  const missing = s.running ? s.suggested.filter((m) => !(s.models || []).some((x) => x.name === m)) : [];
  const p = s.pull || {};
  const pullProg = p.total
    ? ` ${Math.round(100 * p.completed / p.total)}% (${(p.completed / 1e9).toFixed(2)} / ${(p.total / 1e9).toFixed(2)} GB)`
    : p.completed ? ` ${(p.completed / 1e9).toFixed(2)} GB` : "";
  const pull = p.state === "running" ? `<div>Downloading ${esc(p.model)}: ${esc(p.status)}${pullProg}</div>`
    : p.state === "error" ? `<div class="err">Download of ${esc(p.model)} failed: ${esc(p.error)}</div>`
    : p.state === "done" ? `<div class="ok">${esc(p.model)} downloaded.</div>` : "";
  $("#ai-status").innerHTML = `<h3>GPU</h3><div>${gpu}</div><div class="muted small">${whisper}</div>
    <h3>Ollama</h3><div>${ollama} <button id="ai-refresh">Check again</button></div>${instErr}
    ${s.running ? `<h3>Models</h3>${models ? `<ul class="plain">${models}</ul>` : `<div class="muted">No models yet.</div>`}
      ${loaded ? `<div class="muted small">Loaded now: ${loaded}</div>` : ""}
      <div class="row">${missing.map((m) => `<button class="ai-pull" data-model="${esc(m)}">Download ${esc(m)}</button>`).join("")}</div>${pull}` : ""}`;
  $("#ai-refresh").onclick = loadAI;
  const install = $("#ai-install");
  if (install) install.onclick = async () => { install.disabled = true; try { await api("POST", "/api/llm/install"); } catch (err) { say(err.message); } loadAI(); };
  const start = $("#ai-start");
  if (start) start.onclick = async () => { start.disabled = true; try { await api("POST", "/api/llm/start"); } catch (err) { say(err.message); } loadAI(); };
  document.querySelectorAll(".ai-pull").forEach((b) => (b.onclick = async () => {
    try { await api("POST", "/api/llm/pull", { model: b.dataset.model }); } catch (err) { say(err.message); } loadAI();
  }));
  document.querySelectorAll(".ai-delete").forEach((b) => (b.onclick = async () => {
    if (!confirm(`Delete ${b.dataset.model}? You can download it again here.`)) return;
    b.disabled = true;
    try { await api("POST", "/api/llm/delete", { model: b.dataset.model }); } catch (err) { say(err.message); } loadAI();
  }));
  const sel = $("#ai-model"), cur = sel.value || s.model;
  sel.innerHTML = [...new Set([s.model, ...(s.models || []).map((m) => m.name)])].map((m) => `<option>${esc(m)}</option>`).join("");
  sel.value = cur;
  renderProvider();
  clearTimeout(aiTimer);
  if ((p.state === "running" || inst.state === "running") && $("#ai").classList.contains("active")) aiTimer = setTimeout(loadAI, 1500);
}
$("#ai-test").addEventListener("click", async () => {
  const btn = $("#ai-test");
  btn.disabled = true;
  $("#ai-result").innerHTML = `<div class="muted">Loading ${esc($("#ai-model").value)} and running the test (up to a minute on the first run)...</div>`;
  try {
    const t = await api("POST", "/api/llm/test", { model: $("#ai-model").value });
    const vram = (x) => x && x.total_mb ? `${mb(x.free_mb)} free` : "?";
    $("#ai-result").innerHTML = `<div class="verdict"><b>${esc(t.verdict)}</b></div>
      <div class="about-grid"><span>Speed</span><span>${t.tok_s} tokens/s writing, ${t.prompt_tok_s} tokens/s reading</span>
      <span>Took</span><span>${t.took_s} s (loading ${t.load_s} s)</span>
      <span>On the GPU</span><span>${t.gpu_share}% of ${t.size_gb} GB (${t.vram_gb} GB in VRAM)</span>
      <span>VRAM</span><span>before ${vram(t.gpu_before)}, after ${vram(t.gpu_after)}</span>
      <span>Answer</span><span>${t.json_ok ? `valid, ${t.issues} mistake(s) found` : '<span class="warn">not in the expected format</span>'}</span></div>
      ${t.improved ? `<div class="muted small">Better version: ${esc(t.improved)}</div>` : ""}`;
  } catch (e) { $("#ai-result").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
  btn.disabled = false;
  loadAI();
});

async function renderProvider() {
  const u = await api("GET", "/api/ai/usage");
  document.querySelectorAll('input[name="ai-provider"]').forEach((r) => {
    r.checked = r.value === (u.provider || "local");
    r.onchange = async () => { await api("POST", "/api/settings", { ai_provider: r.value }); renderProvider(); };
  });
  const by = Object.entries(u.today.by || {}).map(([k, v]) => `${esc(k)} ${v.credits.toFixed(1)} (${v.calls})`).join(", ");
  $("#ai-usage").innerHTML = `Copilot today: ${u.today.credits.toFixed(1)} of ${u.limit || "∞"} credits in ${u.today.calls} calls${by ? ": " + by : ""}. This month: ${u.month}.`;
}
function renderCopilot(c) {
  $("#copilot-status").innerHTML = c.ok ? `<span class="ok">Signed in${c.login ? " as " + esc(c.login) : ""}</span>${c.token ? " with a token" : ""}${c.detail ? ` · <span class="warn">${esc(c.detail)}</span>` : ""}`
    : `<span class="warn">${esc(c.detail || "not signed in")}</span>`;
  const sel = $("#copilot-model");
  sel.innerHTML = [...new Set([c.model, ...c.models])].map((m) => `<option ${m === c.model ? "selected" : ""}>${esc(m)}</option>`).join("");
  sel.onchange = () => api("POST", "/api/settings", { copilot_model: sel.value });
}
$("#copilot-check").addEventListener("click", async (e) => {
  e.target.disabled = true; $("#copilot-status").textContent = "Checking (the first time downloads the Copilot runtime)...";
  try { renderCopilot(await api("GET", "/api/ai/copilot")); } catch (err) { $("#copilot-status").textContent = err.message; }
  e.target.disabled = false;
});
$("#copilot-token").addEventListener("submit", async (e) => {
  e.preventDefault(); $("#copilot-status").textContent = "Checking the token...";
  try { renderCopilot(await api("POST", "/api/ai/copilot/token", { token: e.target.token.value })); e.target.token.value = ""; } catch (err) { $("#copilot-status").innerHTML = `<span class="err">${esc(err.message)}</span>`; }
});
$("#copilot-forget").addEventListener("click", async () => { await api("DELETE", "/api/ai/copilot/token"); $("#copilot-status").textContent = "Token removed."; });
$("#copilot-test").addEventListener("click", async (e) => {
  e.target.disabled = true; $("#copilot-result").innerHTML = `<div class="muted">Asking...</div>`;
  try { const t = await api("POST", "/api/ai/copilot/test"); $("#copilot-result").innerHTML = `<div class="ok">${esc(t.model)} answered in ${t.total_s} s, ${t.credits == null ? "credits unknown" : t.credits.toFixed(2) + " credits"}</div><div class="muted small">${esc(t.improved)}</div>`; }
  catch (err) { $("#copilot-result").innerHTML = `<div class="err">${esc(err.message)}</div>`; }
  e.target.disabled = false; renderProvider();
});
async function pollCompare() {
  const c = await api("GET", "/api/ai/compare"), r = c.result || {};
  $("#cmp-run").disabled = c.state === "running";
  if (c.state === "running") { $("#cmp-result").innerHTML = `<div class="muted">Sorting again (after the running job)...</div>`; return setTimeout(pollCompare, 2000); }
  $("#cmp-result").innerHTML = c.state === "error" ? `<div class="err">${esc(c.error)}</div>` : r.items === undefined ? "" : `<div class="verdict"><b>${r.items} sorted in ${r.seconds} s${r.credits ? `, ${r.credits} credits` : ""}</b>;
    right in ${r.right} of ${r.reviewed} you checked; the same as before in ${r.same}${r.errors ? `; <span class="err">${r.errors} failed: ${esc(r.error)}</span>` : ""}</div>
    <table class="list"><tr><th>Conversation</th><th>Now</th><th>Before</th><th>You</th></tr>${r.rows.filter((x) => x.now !== x.before || x.you).map((x) => `<tr><td>${esc(x.title)}</td><td>${x.now}</td><td>${x.before}</td><td>${x.you}</td></tr>`).join("")}</table>`;
}
$("#cmp-run").addEventListener("click", async () => { try { await api("POST", "/api/ai/compare", { provider: $("#cmp-provider").value, limit: Number($("#cmp-limit").value) }); pollCompare(); } catch (err) { toast(err.message, 4000); } });

async function loadLearned() {
  const rows = await api("GET", "/api/assistant/corrections");
  draw("#ai-learned", rows.length ? `<ul class="elines">${rows.slice(0, 30).map((c) => `<li class="learned"><span class="etext">${esc(c.title)}: not <b>${esc(c.was)}</b> but <b>${esc(c.now)}</b></span><span class="tools"><button type="button" data-forget="${esc(c.at)}" title="Delete" aria-label="Delete">✕</button></span></li>`).join("")}</ul>` : `<p class="muted">Nothing yet. What you correct in Today shows here; the newest eight go into every sorting.</p>`, true);
}
$("#ai-learned").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-forget]");
  if (b) { await api("DELETE", "/api/assistant/corrections?at=" + encodeURIComponent(b.dataset.forget)); loadLearned(); }
});
onTab("ai", () => { loadAI(); loadLearned(); });
