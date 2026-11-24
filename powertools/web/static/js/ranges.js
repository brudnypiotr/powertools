// The range picker of the exports.

const RANGES = [["days:2", "Last 2 days"], ["days:10", "Last 10 days"], ["days:30", "Last 30 days"],
  ["new", "Only new since last export"], ["since", "Since date..."], ["all", "Everything"]];
document.querySelectorAll(".range").forEach((el) => {
  el.innerHTML = `<select>${RANGES.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select>
    <input type="date" hidden>`;
  const sel = el.querySelector("select");
  if (el.dataset.for !== "mail-search") sel.value = "new";
  else sel.querySelector('option[value="new"]').remove();
  sel.onchange = () => { el.querySelector("input").hidden = sel.value !== "since"; };
});
function rangeOf(name) {
  const el = document.querySelector(`.range[data-for="${name}"]`);
  const v = el.querySelector("select").value;
  if (v.startsWith("days:")) return { kind: "days", days: Number(v.slice(5)) };
  if (v === "since") return { kind: "since", date: el.querySelector("input").value };
  return { kind: v };
}
const TYPES = { oneOnOne: "1:1", group: "group", meeting: "meeting" };

export { TYPES, rangeOf };
