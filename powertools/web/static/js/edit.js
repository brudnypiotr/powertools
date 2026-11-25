// Editing and deleting lines of the memory, wherever they are shown; every change can be undone.
import { api, attrs, esc, say, toast } from "./core.js";
import { micButton } from "./mic.js";
import { loadRecap } from "./recap/store.js";

// a line of a memory file as a list item with Edit and Delete; `html` is how it is shown
const editable = (file, line, html) => `<li class="eline"${attrs({ file, line })}><span class="etext">${html}</span>${TOOLS}</li>`;
const TOOLS = `<span class="tools"><button type="button" data-tool="edit" title="Edit" aria-label="Edit">✎</button><button type="button" data-tool="del" title="Delete" aria-label="Delete">✕</button></span>`;
const editableList = (file, lines, show = esc) => (lines.length ? `<ul class="elines">${lines.map((x) => editable(file, x, show(x))).join("")}</ul>` : `<p class="muted">Nothing.</p>`);

async function undoMemory(id) {
  try { toast("Undone in " + (await api("POST", "/api/memory/undo", { id })).file); await loadRecap(); } catch (e) { say(e.message); }
}
const done = (text, id) => toast(text, 6000, id ? ["Undo", () => undoMemory(id)] : undefined);
async function change(li, text, said) {
  try {
    const e = await api("POST", "/api/memory/line", { file: li.dataset.file, line: li.dataset.line, new: text });
    if (document.activeElement) document.activeElement.blur();
    await loadRecap();
    done(said, e.id);
  } catch (err) { say(err.message); }
}
addEventListener("click", (e) => {
  const b = e.target.closest("[data-tool]"), li = b && b.closest(".eline");
  if (!li) return;
  if (b.dataset.tool === "del") return change(li, "", "Deleted");
  const old = li.innerHTML;
  li.innerHTML = `<form class="fixline grow"><input name="line" data-k="eline" aria-label="Line" value="${esc(li.dataset.line.replace(/\s*\(you\)\s*$/, ""))}">${micButton()}<button>Save</button><button type="button" class="quietbtn" data-cancel="1">Cancel</button></form>`;
  const f = li.querySelector("form");
  f.line.focus();
  f.querySelector("[data-cancel]").onclick = () => { li.innerHTML = old; };
  f.onsubmit = (ev) => { ev.preventDefault(); ev.stopPropagation(); if (f.line.value.trim()) change(li, f.line.value, "Saved"); };
});

export { done, editableList, undoMemory };
