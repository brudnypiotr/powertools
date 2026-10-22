"use strict";
const $ = (s) => document.querySelector(s);
const api = async (method, path, body) => {
  const r = await fetch(path, {
    method,
    headers: { "X-Powertools": "1", "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
};
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

// ---------------------------------------------------------------- tabs
document.querySelectorAll("nav button[data-tab]").forEach((b) =>
  b.addEventListener("click", () => {
    if (b.disabled) return;
    document.querySelectorAll("nav button, .tab").forEach((e) => e.classList.remove("active"));
    b.classList.add("active");
    $("#" + b.dataset.tab).classList.add("active");
    if (b.dataset.tab === "settings") loadSettings();
    if (b.dataset.tab === "diagnostics") loadDiagnostics();
  }));

// ---------------------------------------------------------------- status
let lastId = null;
const HINTS = {
  idle: (s) => `Press ${s.hotkey} anywhere to dictate.`,
  recording: (s) => `Listening... press ${s.hotkey} again to stop.`,
  transcribing: () => "Transcribing...",
  error: () => "Something went wrong, see below.",
};
async function poll() {
  let s;
  try { s = await api("GET", "/api/state"); } catch { return setTimeout(poll, 2000); }
  const e = s.engine;
  const shown = e.state === "ready" || s.dictation !== "idle" ? s.dictation : e.state === "error" ? "error" : "loading";
  const pill = $("#pill");
  pill.className = "pill " + shown;
  pill.textContent = shown;
  $("#hint").textContent = shown === "loading" ? `Loading Whisper ${e.model}...`
    : e.state === "error" && s.dictation === "idle" ? `Whisper failed: ${e.error || ""}` : (HINTS[shown] || (() => ""))(s);
  $("#toggle").textContent = s.dictation === "recording" ? "Stop" : "Start dictation";
  $("#level").style.width = Math.min(100, s.level * 600) + "%";
  const last = s.last || {};
  const box = $("#last");
  if (last.error) { box.classList.remove("hidden"); box.innerHTML = `<span class="err">${esc(last.error)}</span>`; }
  else if (last.id && last.id !== lastId) {
    lastId = last.id;
    box.classList.remove("hidden");
    box.innerHTML = `<div class="meta">${last.audio_s}s audio, ${last.took_s}s to transcribe, ${esc(last.language)}</div>
      <div class="text">${esc(last.text)}</div>`;
    loadHistory();
  }
  setTimeout(poll, s.dictation === "recording" ? 150 : 700);
}
$("#toggle").addEventListener("click", () => api("POST", "/api/toggle"));

// ---------------------------------------------------------------- history
async function loadHistory() {
  const items = await api("GET", "/api/history?limit=100&q=" + encodeURIComponent($("#search").value));
  $("#history").innerHTML = items.map((i) => `
    <li data-id="${i.id}">
      <div class="meta">${new Date(i.ts * 1000).toLocaleString()} · ${esc(i.language)} · ${i.audio_s}s
        <span class="actions"><button data-act="copy">Copy</button><button data-act="fix">Fix a word</button>
        <button data-act="del">Delete</button></span></div>
      <div class="text">${esc(i.text)}</div>
    </li>`).join("") || "<li>Nothing yet.</li>";
}
$("#search").addEventListener("input", () => loadHistory());
$("#history").addEventListener("click", async (ev) => {
  const act = ev.target.dataset.act;
  const li = ev.target.closest("li");
  if (!act || !li) return;
  const text = li.querySelector(".text").textContent;
  if (act === "copy") navigator.clipboard.writeText(text);
  if (act === "del") { await api("DELETE", "/api/history/" + li.dataset.id); loadHistory(); }
  if (act === "fix" && !li.querySelector(".fix")) {
    const sel = window.getSelection().toString().trim();
    li.insertAdjacentHTML("beforeend", `<form class="fix"><input name="wrong" placeholder="heard" value="${esc(sel)}">
      <input name="right" placeholder="should be"><button>Add rule</button></form>`);
    li.querySelector(".fix").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = e.target;
      await api("POST", "/api/correction", { wrong: f.wrong.value, right: f.right.value });
      f.replaceWith(Object.assign(document.createElement("div"), { className: "meta", textContent: "Rule added." }));
    });
  }
});

// ---------------------------------------------------------------- settings
async function loadSettings() {
  const [s, devices] = await Promise.all([api("GET", "/api/settings"), api("GET", "/api/devices")]);
  const f = $("#settings-form");
  f.input_device.innerHTML = `<option value="">Windows default</option>` +
    [...new Set([...devices, s.input_device].filter(Boolean))].map((d) => `<option>${esc(d)}</option>`).join("");
  for (const k of ["hotkey", "model", "language", "input_device", "beam_size"]) f[k].value = s[k];
  for (const k of ["paste", "restore_clipboard", "open_window_on_start"]) f[k].checked = s[k];
  f.vocabulary.value = s.vocabulary.join("\n");
  f.replacements.value = s.replacements.map((r) => `${r.from} => ${r.to}`).join("\n");
}
$("#settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const lines = (t) => t.split("\n").map((l) => l.trim()).filter(Boolean);
  const body = {
    hotkey: f.hotkey.value, model: f.model.value, language: f.language.value, input_device: f.input_device.value,
    beam_size: Number(f.beam_size.value), paste: f.paste.checked, restore_clipboard: f.restore_clipboard.checked,
    open_window_on_start: f.open_window_on_start.checked, vocabulary: lines(f.vocabulary.value),
    replacements: lines(f.replacements.value).filter((l) => l.includes("=>"))
      .map((l) => { const [a, ...b] = l.split("=>"); return { from: a.trim(), to: b.join("=>").trim() }; }),
  };
  try {
    const r = await api("POST", "/api/settings", body);
    $("#saved").textContent = r.changed.length ? "Saved: " + r.changed.join(", ") : "No changes.";
  } catch (err) { $("#saved").textContent = err.message; }
});

// ---------------------------------------------------------------- diagnostics
async function loadDiagnostics() {
  $("#diag").textContent = JSON.stringify(await api("GET", "/api/state"), null, 2);
  $("#log").textContent = (await api("GET", "/api/log?lines=200")).lines.join("\n");
}
$("#quit").addEventListener("click", async () => {
  if (!confirm("Quit powertools? The hotkey stops working until you start it again.")) return;
  await api("POST", "/api/quit");
  document.body.innerHTML = "<main><p>powertools has stopped. Start it again from the desktop shortcut.</p></main>";
});

poll();
loadHistory();
