// The tabs. A module says what to do when its tab opens (onTab); this one knows none of them.
import { $ } from "./core.js";

const openers = {};
const onTab = (names, fn) => { for (const n of [names].flat()) (openers[n] ||= []).push(fn); };
const on = (name) => $("#" + name).classList.contains("active");
function openTab(b) {
  if (b.disabled) return;
  if (b.closest("#more-nav")) openMore(true);
  document.querySelectorAll("nav button, .tab").forEach((e) => e.classList.remove("active"));
  b.classList.add("active");
  document.querySelectorAll("nav button[data-tab]").forEach((e) => e.toggleAttribute("aria-current", e === b));
  $("#" + b.dataset.tab).classList.add("active");
  $(".main").scrollTop = 0;
  $(".main").classList.toggle("full", $("#" + b.dataset.tab).classList.contains("panes"));
  (openers[b.dataset.tab] || []).forEach((fn) => fn(b.dataset.tab));
}
document.querySelectorAll("nav button[data-tab]").forEach((b) => b.addEventListener("click", () => openTab(b)));
function openMore(on) { $("#more-nav").classList.toggle("hidden", !on); $("#more").setAttribute("aria-expanded", on); }
$("#more").addEventListener("click", () => openMore($("#more-nav").classList.contains("hidden")));


function showTab(name) { const b = document.querySelector(`nav button[data-tab="${name}"]`); if (b && !b.classList.contains("active")) b.click(); }

export { on, onTab, showTab };
