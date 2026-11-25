// Dictation: what it does now, and the history.
import { $, api, esc, say, sure, toast } from "./core.js";
import { onState } from "./state.js";

let lastId = null;
const HINTS = {
  idle: (s) => `Press ${s.hotkey} anywhere to dictate.`,
  recording: (s) => `Listening... press ${s.hotkey} again to stop.`,
  transcribing: () => "Transcribing...",
  error: () => "Something went wrong, see below.",
};
function showDictation(s) {
  const e = s.engine;
  const shown = e.state === "ready" || s.dictation !== "idle" ? s.dictation : e.state === "error" ? "error" : "loading";
  const pill = $("#pill");
  pill.className = "pill " + shown;
  pill.textContent = shown;
  $("#hint").textContent = shown === "loading" ? `Loading ${e.model}...`
    : e.state === "error" && s.dictation === "idle" ? `Model failed: ${e.error || ""}`
    : (HINTS[shown] || (() => ""))(s) + (shown === "idle" && e.note ? ` (${e.note})` : "");
  $("#toggle").classList.toggle("on", s.dictation === "recording");
  $("#engine-foot").innerHTML = `${esc(e.model)}${e.device ? " · " + esc(e.device === "cpu" ? "CPU" : e.compute === "int8_float16" ? "cuda (saving)" : e.device) : ""}<br>${esc(s.hotkey)} · v${esc(s.version)}`;
  $("#engine-foot").title = e.note ? e.note : "About, changelog and updates";
  $("#level").style.width = Math.min(100, s.level * 600) + "%";
  const last = s.last || {};
  const box = $("#last");
  if (last.error) {
    box.innerHTML = `<span class="err">${esc(last.error)}</span>`;
    if (last.id && last.id !== lastId) { lastId = last.id; loadHistory(); }
  }
  else if (last.id && last.id !== lastId) {
    lastId = last.id;
    box.innerHTML = `${esc(last.text)}<span class="meta">${last.audio_s}s → ${last.took_s}s · ${esc(last.language)}</span>`;
    loadHistory();
  }
}
onState(showDictation);
$("#toggle").addEventListener("click", () => api("POST", "/api/toggle"));

async function loadHistory() {
  const items = await api("GET", "/api/history?limit=100&q=" + encodeURIComponent($("#search").value));
  const when = (ts) => {
    const d = new Date(ts * 1000), today = new Date().toDateString() === d.toDateString();
    return today ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : d.toLocaleDateString([], { day: "2-digit", month: "short" }) + " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  };
  $("#history").innerHTML = items.map((i) => `
    <li data-id="${i.id}" title="${esc(i.language)} · ${i.audio_s}s audio · ${i.took_s}s">
      <time>${when(i.ts)}</time>
      <div class="body"><div class="text">${esc(i.text)}</div>${i.raw && i.raw !== i.text
        ? `<div class="raw muted small">Whisper: ${esc(i.raw)}</div>` : ""}</div>
      <span class="actions">
        <button class="icon" data-act="copy" title="Copy">${ICONS.copy}</button>
        <button class="icon" data-act="fix" title="Fix a word (adds a replacement rule)">${ICONS.fix}</button>
        <button class="icon" data-act="edit" title="Edit the text">✎</button>
        <button class="icon" data-act="del" title="Delete">${ICONS.del}</button>
      </span>
    </li>`).join("") || `<li class="empty">Nothing yet. Press the hotkey anywhere and speak.</li>`;
}
$("#history-clear").addEventListener("click", (e) => sure(e.target, "Delete the whole history?", [["Delete", async () => { await api("DELETE", "/api/history"); loadHistory(); }]]));
$("#search").addEventListener("input", () => loadHistory());
const ICONS = {
  copy: '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/></svg>',
  fix: '<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13 7 4 4"/></svg>',
  del: '<svg viewBox="0 0 24 24"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>',
};
$("#history").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("[data-act]");
  const li = ev.target.closest("li");
  if (!li) return;
  if (!btn) { if (!ev.target.closest("form")) li.classList.toggle("open"); return; }
  const act = btn.dataset.act;
  const text = li.querySelector(".text").textContent;
  if (act === "copy") { navigator.clipboard.writeText(text); toast("Copied"); }
  if (act === "del") { await api("DELETE", "/api/history/" + li.dataset.id); loadHistory(); }
  if (act === "edit" && !li.querySelector(".edit")) {
    li.querySelector(".body").insertAdjacentHTML("beforeend", `<form class="edit fixline"><textarea name="text" rows="3">${esc(text)}</textarea><button class="primary">Save</button></form>`);
    li.querySelector(".edit").addEventListener("submit", async (e) => {
      e.preventDefault();
      try { await api("POST", "/api/history/" + li.dataset.id, { text: e.target.text.value }); loadHistory(); } catch (err) { say(err.message); }
    });
  }
  if (act === "fix" && !li.querySelector(".fix")) {
    const sel = window.getSelection().toString().trim();
    li.querySelector(".body").insertAdjacentHTML("beforeend", `<form class="fix"><input name="wrong" placeholder="heard" value="${esc(sel)}">
      <input name="right" placeholder="should be"><button class="primary">Add rule</button></form>`);
    li.querySelector(".fix").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = e.target;
      await api("POST", "/api/correction", { wrong: f.wrong.value, right: f.right.value });
      f.remove();
      toast("Rule added");
    });
  }
});

loadHistory();
