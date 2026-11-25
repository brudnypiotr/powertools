// Dictation into a field of this window: click the microphone, speak, click again (or press the dictation hotkey).
import { $, api, esc, say, toast } from "./core.js";
import { onState } from "./state.js";

const MIC = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3z"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>';
// next to a field, inside the same form or .microw
const micButton = (what = "Dictate") => `<button type="button" class="micbtn" data-mic="1" title="${esc(what)}" aria-label="${esc(what)}" aria-pressed="false">${MIC}</button>`;
const fieldOf = (b) => b.closest("form, .microw").querySelector("textarea, input:not([type=hidden]):not([type=checkbox])");
let field = null, home = "", seq = null;       // home: how to find the field again when the list was drawn meanwhile
const target = () => (field && field.isConnected ? field : home ? $(home) : null);

function insert(f, text) {
  const a = f.selectionStart ?? f.value.length, b = f.selectionEnd ?? a, before = f.value.slice(0, a);
  const gap = before && !/\s$/.test(before) ? " " : "";
  f.value = before + gap + text + f.value.slice(b);
  f.focus();
  f.setSelectionRange(a + gap.length + text.length, a + gap.length + text.length);
  f.dispatchEvent(new Event("input", { bubbles: true }));
}
addEventListener("pointerdown", (e) => { if (e.target.closest("[data-mic]")) e.preventDefault(); }, true);   // the field keeps the focus
addEventListener("click", async (e) => {
  const b = e.target.closest("[data-mic]");
  if (!b) return;
  if (b.getAttribute("aria-pressed") !== "true") {
    field = fieldOf(b);
    home = field.id ? "#" + field.id : field.dataset.k ? `[data-k="${field.dataset.k}"]` : "";
    field.focus();
  }
  try { await api("POST", "/api/toggle", { target: "window" }); } catch (err) { say(err.message); }
});
onState((s) => {
  const f = target(), on = s.dictation === "recording" && s.dictation_to === "window", last = s.last || {};
  for (const b of document.querySelectorAll("[data-mic]")) b.setAttribute("aria-pressed", String(on && fieldOf(b) === f));
  if (seq === null) seq = last.seq || 0;
  if (!last.seq || last.seq === seq) return;
  seq = last.seq;
  if (last.to !== "window") return;
  if (last.error) say(last.error);
  else if (!last.text) toast("No speech heard");
  else if (f) insert(f, last.text);
});

export { micButton };
