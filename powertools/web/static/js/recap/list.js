// Today, the list on the left.
import { $, SRC, ago, button, cap, clockOf, draw, esc, isToday, plain, rowHtml, segments, setText } from "../core.js";
import { renderBody } from "./body.js";
import { renderSetup } from "./setup.js";
import { R, V, meetingsToday, onRecap, openOfMine, opened, running } from "./store.js";

// ---------------------------------------------------------------- rows: { id, kind, title, sub, tag, hot } and i, p or d (item, promise, draft)
const TABS = [["me", "For me"], ["wait", "Waiting"], ["chg", "Changed"]];
const item = (kind) => (i) => {
  const who = i.person || i.people || SRC[i.source] || "";
  return { id: i.key, i, kind, title: i.error ? "Not sorted: " + plain(i.title) : cap(i.action || i.summary || plain(i.title)),
    sub: [who, plain(i.title)].filter(Boolean).join(", "), tag: itemTag(kind, i), hot: kind !== "closed" && (!!i.due || i.quiet) };
};
function itemTag(kind, i) {
  if (kind === "closed" && i.done_by === "answer") return "you answered";
  return kind === "me" && i.wrote ? "you wrote" : i.due || ago(i.last);
}
const promise = (p) => ({ id: "p:" + p.id, p, kind: "promise", title: cap(p.what), tag: p.due || ago(p.last), hot: !!p.due,
  sub: `${p.mine ? "You promised" + (p.to ? " " + p.to : "") : p.who + " promised"}, ${plain(p.title)}` });
const text = (d) => ({ id: "d:" + d.id, d, kind: "text", title: d.title, sub: "Draft", tag: d.created.slice(5, 10) });
const group = (name, rows, folded = false) => ({ name, rows, folded });
const GROUPS = {
  me: () => [
    group("Decide", R.decisions.map(item("me"))),
    group("Do", [...R.mine.filter((i) => i.category === "todo").map(item("me")), ...R.promised.map(promise)]),
    group("Answer", R.mine.filter((i) => i.category === "reply").map(item("me"))),
    group("Not sorted yet", R.unsorted.map(item("chg"))),
    group("You answered", R.answered.map(item("closed")), true),
    group("My texts", R.draft_list.filter((d) => !d.key).map(text))],
  wait: () => [
    group("Quiet for long", R.waiting.filter((i) => i.quiet).map(item("wait"))),
    group("Still in time", R.waiting.filter((i) => !i.quiet).map(item("wait"))),
    group("Promised to me", R.owed.map(promise))],
  chg: () => [
    group("Changed", R.changed.map(item("chg"))),
    group("Ignored", R.ignored.map(item("chg")), true),
    group("Done and later", R.closed.map(item("closed")), true)],
};
const shownRows = (tab) => GROUPS[tab]().filter((g) => !g.folded).flatMap((g) => g.rows);
const find = (id) => Object.values(GROUPS).flatMap((f) => f()).flatMap((g) => g.rows).find((r) => r.id === id);
function next(id) {   // the row after this one, so the work goes on
  const rows = shownRows(V.tab), at = rows.findIndex((r) => r.id === id);
  return at < 0 ? "" : (rows[at + 1] || rows[at - 1] || { id: "" }).id;
}
const mineFirst = () => openOfMine().sort((a, b) => !!b.due - !!a.due).slice(0, 3);

// ---------------------------------------------------------------- the head: when, how far, Update or Stop
function renderHead() {
  const job = R.job || {}, w = running(), l = R.last || {};
  setText("#today-status", w ? doing(job) : asOf(l));
  $("#today-status").title = [l.found !== undefined && `${l.found} conversations read in ${l.seconds} s`, l.credits && `${l.credits} AI credits`,
    R.reviewed && `you checked ${R.reviewed}, right ${R.agreed}`].filter(Boolean).join(", ");
  $("#recap-progress").classList.toggle("hidden", !w && job.state !== "needs_token");
  $("#recap-progress span").style.width = (job.total ? Math.round(100 * job.done / job.total) : 3) + "%";
  setText("#recap-update", w ? "Stop" : "Update");
  $("#recap-update").classList.toggle("primary", !R.updated && R.setup_done);
  $("#recap").classList.toggle("solo", !R.setup_done);
}
const doing = (job) => (R.ahead.length && job.state === "queued" ? "After: " + R.ahead.join(", ") : R.current || "Starting");
function asOf(last) {
  if (!R.updated) return "Nothing read yet";
  const when = new Date(R.updated * 1000), day = isToday(when) ? "" : when.toLocaleDateString([], { day: "numeric", month: "short" }) + " ";
  return `As of ${day}${clockOf(when)}, ${last.new || 0} new`;
}

// ---------------------------------------------------------------- the rows
function renderToday() {
  const job = R.job || {};
  renderHead();
  if (!R.setup_done) return renderSetup();
  if (job.state === "needs_token" && V.mode !== "connect" && V.asked !== job.id) { V.asked = job.id; V.mode = "connect"; }
  segments("#today-tabs", TABS.map(([id, name]) => [id, name, shownRows(id).length]), V.tab, "seg");
  const groups = GROUPS[V.tab]().filter((g) => g.rows.length), me = V.tab === "me";
  draw("#today-rows", [problems(job), me ? briefRow() + checkLine() : "", ...groups.map(groupHtml), me ? suggestions() : "",
    groups.length || !R.updated ? "" : `<div class="empty"><b>Nothing here.</b></div>`, V.tab === "chg" && R.ask_feedback ? MISSED : ""].join(""));
  renderBody();
}
const current = (r) => ({ ...r, kind: r.hot ? "hot" : "", current: V.mode === "item" && V.sel === r.id });
function groupHtml(g) {
  const rows = () => g.rows.map(current).map(rowHtml).join("");
  if (!g.folded) return `<h2>${esc(g.name)}</h2>${rows()}`;
  return `<button type="button" class="foldbtn" data-fold="${esc(g.name)}" data-k="fold-${esc(g.name)}" aria-expanded="${!!opened[g.name]}">${esc(g.name)} <i>${g.rows.length}</i></button>${opened[g.name] ? rows() : ""}`;
}
function problems(job) {
  const said = job.state === "error" ? [`The last Update stopped: ${job.error}. Nothing is lost, press Update again.`] : (R.last || {}).problems || [];
  return !running() && said.length ? `<div class="note">${said.map(esc).join("<br>")}</div>` : "";
}
function briefRow() {
  const n = meetingsToday().length, sub = mineFirst().length ? `${shownRows("me").length} for you, ${n === 1 ? "1 meeting" : n + " meetings"} today` : "The day at a glance";
  return rowHtml({ id: "", title: "Morning brief", sub, current: V.mode === "brief" });
}
function checkLine() {   // did I answer in the meantime?
  if (!R.updated || running() || !openOfMine().some((i) => ["mail", "teams"].includes(i.source))) return "";
  const again = (label) => button(label, "inbtn", { do: "check", k: "check" });
  const said = R.checking ? "Looking for your answers..." : R.check_error ? `Looking for your answers did not work: ${esc(R.check_error)} ${again("Try again")}`
    : `Your answers seen until ${clockOf((R.checked || R.updated) * 1000)}. ${again("Check now")}`;
  return `<p class="checkline muted small">${said}</p>`;
}
function suggestions() {
  const one = (s) => `<div class="suggest" data-key="${esc(s.items[0].key)}"><input aria-label="Name of the matter" value="${esc(s.name)}">
    <span class="muted small">${esc(s.items.map((x) => plain(x.title)).join(", "))}</span>
    <div>${button("Create it", "", { do: "new_matter" })}${button("No", "quietbtn", { do: "no_matter" })}</div></div>`;
  return R.suggested.length ? `<h2>A new matter?</h2>${R.suggested.map(one).join("")}` : "";
}
const MISSED = `<form class="feedback"><label>What did the Recap miss today?<input name="t" placeholder="One sentence; the next Update learns from it"></label><button>Save</button></form>`;

onRecap("recap", renderToday);

export { find, mineFirst, next, renderToday };
