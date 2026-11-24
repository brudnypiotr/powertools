// What Today, Matters and Meetings show (R, from the app) and what is chosen in them (V).
import { api, isToday, setText } from "../core.js";
import { on, onTab } from "../tabs.js";

let R = null, recapTimer = null;
const V = {
  tab: "me", sel: "", mode: "brief", wrong: false, brief: null,     // Today: mode brief | item | ask | connect
  mTab: "on", mSel: "", mFile: "",                                  // Matters
  meetTab: "ahead", meet: "", card: null,                           // Meetings
  pending: null,                                                    // goes on by itself when the Graph token is there
};
const opened = {};                                                  // folded groups the user opened
const running = () => R && R.job && ["queued", "running"].includes(R.job.state);
const panes = [];
const onRecap = (tab, fn) => panes.push([tab, fn]);
async function loadRecap() {
  clearTimeout(recapTimer);
  try { R = await api("GET", "/api/assistant"); }
  finally {       // also after an error (a file that is locked for a moment): it is asked again
    recapTimer = setTimeout(() => { if (["recap", "matters", "meetings"].some(on)) loadRecap().catch(() => {}); }, running() || (R && R.checking) ? 2000 : R ? 60000 : 5000);
  }
  const today = meetingsToday().length, mine = openOfMine().length;
  setText("#nav-today", mine ? String(mine) : "");
  setText("#nav-meetings", today ? String(today) : "");
  for (const [tab, fn] of panes) if (on(tab)) fn();
}
const openOfMine = () => [...R.decisions, ...R.mine];
const meetingsToday = () => R.events.filter((e) => isToday(e.start));
const select = (id) => { V.sel = id; V.mode = id ? "item" : "brief"; V.wrong = false; };
const backToItem = () => { V.mode = V.sel ? "item" : "brief"; };

onTab(["recap", "matters", "meetings"], loadRecap);

export { R, V, backToItem, loadRecap, meetingsToday, onRecap, openOfMine, opened, running, select };
