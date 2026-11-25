// Talking to the app, escaping, toasts, the pieces every list is made of, and drawing without disturbing the user.
// Knows no tab.

const $ = (s) => document.querySelector(s);
const api = async (method, path, body) => {
  const r = await fetch(path, {
    method,
    headers: { "X-Powertools": "1", "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json();
  if (r.status === 403 && data.error === "locked") locked();
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
};
let isLocked = false;
async function locked() {   // this window has no key (a cleared cookie, a first start after an update): the app opens one that has
  if (isLocked) return;
  isLocked = true;
  document.body.innerHTML = `<main class="main"><div class="empty"><b>powertools opens in a new window</b><p class="muted">This one has no access yet. You can close it.</p><p><button id="unlock" class="primary">Open it again</button></p></div></main>`;
  const ask = () => fetch("/api/unlock", { method: "POST", headers: { "X-Powertools": "1" }, body: "{}" }).catch(() => {});
  $("#unlock").onclick = ask;
  if (!sessionStorage.getItem("asked")) { sessionStorage.setItem("asked", "1"); ask(); }
  const back = setInterval(async () => { try { if ((await fetch("/api/state")).ok) { clearInterval(back); sessionStorage.removeItem("asked"); location.reload(); } } catch { /* the app restarts */ } }, 2000);
}
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

const toasts = Object.assign(document.createElement("div"), { className: "toasts" });
toasts.setAttribute("role", "status");
document.body.appendChild(toasts);
function toast(text, ms = 1800, action) {  // action: [label, function]
  const t = Object.assign(document.createElement("div"), { className: "toast", textContent: text });
  if (action) {
    const b = Object.assign(document.createElement("button"), { textContent: action[0] });
    b.onclick = () => { t.remove(); action[1](); };
    t.appendChild(b);
  }
  toasts.appendChild(t);
  while (toasts.children.length > 3) toasts.firstChild.remove();
  setTimeout(() => t.remove(), ms);
}
const say = (text) => toast(text, 6000);  // an error or a hint: never a dialog that blocks the window
const setText = (sel, t) => { const e = $(sel); if (e.textContent !== t) e.textContent = t; };
function setLabel(sel, text, kind) { setText(sel, text); const e = $(sel); if (e.dataset.kind !== kind) { e.dataset.kind = kind; e.className = "label " + kind; } }
const ago = (iso) => {
  if (!iso) return "";
  const d = new Date(iso), days = Math.floor((Date.now() - d) / 864e5);
  return days < 1 ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : days < 7 ? `${days} d ago` : d.toLocaleDateString();
};
const url = (u) => (/^https?:\/\//.test(u || "") ? esc(u) : "");
const SRC = { mail: "Mail", teams: "Teams", jira: "Jira", meeting: "Meeting" };
const clockOf = (iso) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const dayOf = (iso) => new Date(iso).toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" });
const isToday = (iso) => new Date(iso).toDateString() === new Date().toDateString();
const plusDays = (n) => { const d = new Date(); d.setDate(d.getDate() + n); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
const plain = (t) => String(t || "").replace(/^((re|fw|fwd|aw|odp|pd)\s*:\s*)+/i, "") || t;
const link = (u, text) => (url(u) ? `<a href="${url(u)}" target="_blank" rel="noopener">${esc(text)}</a>` : esc(text));
const cap = (t) => { t = String(t || ""); return t.charAt(0).toUpperCase() + t.slice(1); };
const list = (l) => (l.length ? `<ul>${l.map((x) => `<li>${x}</li>`).join("")}</ul>` : `<p class="muted">Nothing.</p>`);
const busyBtn = async (b, fn) => {
  const label = b.textContent;
  b.disabled = true; b.textContent = "Working...";
  try { await fn(); } catch (e) { say(e.message.slice(0, 200)); }
  b.disabled = false; b.textContent = label;
};

// ---------------------------------------------------------------- drawing without disturbing
let pressed = false;
addEventListener("pointerdown", () => (pressed = true), true);
for (const ev of ["pointerup", "pointercancel"]) addEventListener(ev, () => (pressed = false), true);
const typingIn = (box) => { const a = document.activeElement; return a && box.contains(a) && (a.tagName === "SELECT" || (/INPUT|TEXTAREA/.test(a.tagName) && a.type !== "radio")); };
function draw(sel, html, force) {   // only when something changed, never under the user's hands; scroll and focus stay
  const box = $(sel);
  if (box.html === html || (!force && (pressed || typingIn(box)))) return false;
  const top = box.scrollTop, a = document.activeElement, again = a && box.contains(a) && a.dataset.k ? a.dataset.k : "";
  box.innerHTML = html;
  box.html = html;
  box.scrollTop = top;
  if (again) { const f = [...box.querySelectorAll("[data-k]")].find((x) => x.dataset.k === again); if (f) f.focus({ preventScroll: true }); }
  return true;
}
function bar(sel, html) { $(sel).classList.toggle("hidden", !html); draw(sel, html, true); }
const segments = (sel, rows, cur, attr) => draw(sel, rows.map(([id, name, n]) => `<button type="button" data-${attr}="${id}" data-k="seg-${id}" aria-pressed="${id === cur}">${name}${n ? ` <i>${n}</i>` : ""}</button>`).join(""));

// ---------------------------------------------------------------- the pieces
const attrs = (data) => Object.entries(data).filter(([, v]) => v !== undefined).map(([k, v]) => ` data-${k}="${esc(v)}"`).join("");
// kind: primary | quietbtn | inbtn | pillbtn | ""; data.k keeps the focus when the list is drawn again
const button = (text, kind, data, more = "") => `<button type="button"${kind ? ` class="${kind}"` : ""}${attrs(data)} ${more}>${esc(text)}</button>`;
const tag = (text, kind = "") => (text ? `<span class="ptag ${kind}">${esc(text)}</span>` : "");
const GAP = `<span class="grow"></span>`;
// one row of a list: { id, title, sub, tag, kind, time, current }
const rowHtml = (r) => `<button type="button" class="prow"${attrs({ row: r.id, k: r.id || "brief" })} aria-current="${!!r.current}">
  ${r.time ? `<time>${esc(r.time)}</time>` : ""}<span class="ptext"><b>${r.unread ? `<i class="dot" title="New"></i>` : ""}${esc(r.title)}</b><span>${esc(r.sub)}</span></span>${tag(r.tag, r.kind)}</button>`;
const outLink = (href, text, kind = "") => `<a class="btnlink ${kind}" href="${url(href)}" target="_blank" rel="noopener" data-k="${esc(text)}">${esc(text)}</a>`;

export { GAP, attrs, button, outLink, rowHtml, tag };
export { $, SRC, ago, api, bar, busyBtn, cap, clockOf, dayOf, draw, esc, isLocked, isToday, link, list, plain, plusDays, say, segments, setLabel, setText, toast, typingIn, url };
