// The Microsoft token as a step of what the user wanted, not a state to watch.
import { bar, button, draw, esc, say, toast } from "../core.js";
import { onToken } from "../graph.js";
import { renderToday } from "./list.js";
import { R, V, backToItem, loadRecap } from "./store.js";

function openConnect(then) { if (then) V.pending = then; V.mode = "connect"; if (R && R.setup_done) renderToday(); else loadRecap(); }
const HEADS = { half: "One step to finish the Update", pending: "One step to go on", paused: "One step to read your mail", renew: "Renew the access" };
function bodyConnect(force) {
  const paused = R.job && R.job.state === "needs_token", waiting = R.graph.watch && R.graph.watch.active, goesOn = paused || V.pending;
  const half = paused && /^reading (?!your profile)/.test(R.paused_at);
  const head = HEADS[half ? "half" : V.pending ? "pending" : paused || !R.graph.has_token ? "paused" : "renew"];
  const steps = ["Graph Explorer opens; sign in if it asks", "Open the Access token tab there and press copy", goesOn ? "Come back: it goes on by itself" : "Come back: you are connected"];
  const now = waiting ? 1 : 0, state = (n) => (n === now ? "now" : n < now ? "past" : "");
  draw("#today-body", `<h2 class="big">${head}</h2>
    <p class="lead">${half ? `Paused while ${esc(R.paused_at.split(":")[0])}. What was read before it is kept.` : "Microsoft gives access for about an hour at a time. It takes about ten seconds."}</p>
    <ol class="steps3">${steps.map((t, n) => `<li class="${state(n)}"><i>${n + 1}</i>${t}</li>`).join("")}</ol>
    <p>${button(waiting ? "Waiting for the copy..." : "Open Graph Explorer", "primary", { do: "token-get", k: "t1" }, waiting ? "disabled" : "")} ${button("Not now", "quietbtn", { do: "token-no", k: "t2" })}</p>
    <details id="token-paste"><summary class="muted">Paste it myself</summary><form class="fixline" id="token-form"><input type="password" name="token" data-k="t3" aria-label="Access token" placeholder="Access token"><button>Connect</button></form></details>
    <p class="muted">Everything already read stays here: drafts, Ask, matters and meeting cards work without it.</p>`, force);
  bar("#today-bar", "");
}
async function recapToken() {   // called when a token was saved, here or in another tab
  const then = V.pending;
  V.pending = null; V.asked = null;
  if (V.mode === "connect") backToItem();
  toast("Connected");
  if (then) try { await then(); } catch (e) { say(e.message); }
  loadRecap();
}
async function withToken(fn) {   // runs it; without a valid token it waits for one and then runs
  try { await fn(); } catch (e) { if (e.message !== "token") throw e; openConnect(fn); }
}

onToken(recapToken);

export { bodyConnect, openConnect, withToken };
