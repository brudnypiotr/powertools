// Asks the app for its state all the time; modules say what they show of it (onState).
import { api, isLocked } from "./core.js";

let failures = 0;
const session = { updating: false };   // a new version is being started: the app is away for a moment
const watchers = [];
const onState = (fn) => watchers.push(fn);
function stopped() {
  document.body.innerHTML = `<main class="main"><p class="muted">powertools has stopped. Start it again from the
    shortcut or the Start menu.</p></main>`;
  window.close();  // allowed for an app window opened with one page
}
async function poll() {
  if (isLocked) return;
  let s;
  try { s = await api("GET", "/api/state"); failures = 0; }
  catch {
    if (isLocked) return;
    if (++failures >= 3 && !session.updating) return stopped();  // the app quit (tray > Quit): close this window too
    return setTimeout(poll, 2000);
  }
  watchers.forEach((fn) => fn(s));
  setTimeout(poll, s.dictation === "recording" ? 150 : document.hidden ? 2000 : 700);   // nobody looks: less often
}

export { poll, onState, session, stopped };
