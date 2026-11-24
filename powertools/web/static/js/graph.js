// The Microsoft token: the bar of the export tabs, catching a copied token, who wants to know.
import { api, esc, say, toast } from "./core.js";
import { onTab } from "./tabs.js";

async function renderGraphBar() {
  const s = await api("GET", "/api/graph/status");
  const waiting = s.watch && s.watch.active ? `<div class="token-wait">Waiting for the token: in the Graph Explorer window sign in if asked,
    open the <b>Access token</b> tab and click its copy button. It is saved here automatically.</div>` : "";
  const fields = `<button class="token-get ${s.has_token ? "" : "primary"}">Get ${s.has_token ? "a new" : "a"} token</button>
    <input type="password" placeholder="...or paste the access token here" aria-label="Graph access token" class="token-input">
    <button class="token-save">${s.has_token ? "Replace" : "Connect"}</button>`;
  document.querySelectorAll(".graphbar").forEach((bar) => {
    if (bar.contains(document.activeElement) && document.activeElement.value) return;  // a token is being pasted
    bar.innerHTML = s.has_token
      ? `<span class="ok">Connected${s.user ? " as " + esc(s.user) : ""}</span> <span class="muted">${s.expires_min} min left</span>
         <details class="token-more" ${waiting ? "open" : ""}><summary>New token</summary><div class="row">${fields}</div></details>${waiting}`
      : `<div class="token-why"><b>Connect to your mail, Teams and calendar.</b> <span class="muted">A token from
         Microsoft Graph Explorer, valid for about an hour: press Get a token, sign in, copy it there.</span></div>${fields}${waiting}`;
    bar.querySelector(".token-get").onclick = async () => {
      try { await api("POST", "/api/graph/token/fetch"); watchGraphToken(s.has_token ? s.expires_min : -1); renderGraphBar(); }
      catch (e) { say(e.message); }
    };
    bar.querySelector(".token-save").onclick = async () => {
      const token = bar.querySelector(".token-input").value.trim();
      if (!token) return;
      try {
        const r = await api("POST", "/api/graph/token", { token });
        bar.querySelector(".token-input").value = "";
        renderGraphBar();
        tokenSaved();
      } catch (e) { say(e.message); }
    };
  });
}
const tokenWatchers = [];
const onToken = (fn) => tokenWatchers.push(fn);       // called when a token was saved, wherever
function tokenSaved() { tokenWatchers.forEach((fn) => fn()); }
let graphWatch = null;
function watchGraphToken(before) {  // poll until the copied token is caught (or the 5 minutes run out)
  clearTimeout(graphWatch);
  graphWatch = setTimeout(async () => {
    const s = await api("GET", "/api/graph/status");
    if (s.watch.active) return watchGraphToken(before);
    renderGraphBar();
    if (s.watch.result === "saved") {
      toast("Connected");
      tokenSaved();
    } else if (s.watch.result && s.watch.result !== "timeout") say(s.watch.result);
  }, 1500);
}
async function graphCall(method, path, body) {
  try { return await api(method, path, body); }
  catch (e) { if (e.message === "token") { renderGraphBar(); throw new Error("Paste a fresh token above."); } throw e; }
}

onTab(["teams", "mail", "org"], renderGraphBar);

export { graphCall, onToken, renderGraphBar, tokenSaved, watchGraphToken };
