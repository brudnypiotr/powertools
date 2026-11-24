// The first start: three short steps.
import { $, api, bar, draw, toast } from "../core.js";
import { loadRecap } from "./store.js";
import { showTab } from "../tabs.js";

let setupStep = 0;
function renderSetup() {
  if ($("#setup-form")) return;
  bar("#today-bar", "");
  draw("#today-body", `<form id="setup-form" class="form setup"><div class="steps" aria-hidden="true"><i></i><i></i><i></i></div>
    <fieldset><h2 class="big">Where should the model run?</h2>
      <label class="pick"><input type="radio" name="provider" value="copilot"><span><b>GitHub Copilot</b><br>Good on every laptop, needs a Copilot licence. The text of your mail, chats and meetings, and what you check in Writing, is sent to GitHub Copilot.</span></label>
      <label class="pick"><input type="radio" name="provider" value="local"><span><b>This laptop (Ollama)</b><br>Nothing leaves it. Needs a good GPU.</span></label></fieldset>
    <fieldset><h2 class="big">About your work</h2><p class="muted">You can dictate the answers; all of it can be changed later.</p>
      <label>What is your role, and what do people come to you for?<textarea name="role" rows="2"></textarea></label>
      <label>The 3 to 7 things you are running right now, one per line<textarea name="running" rows="4"></textarea></label>
      <label>Whose messages must you never miss?<textarea name="people" rows="1"></textarea></label></fieldset>
    <fieldset><h2 class="big">Last two</h2>
      <label>What did you find out too late recently?<textarea name="late" rows="2"></textarea></label>
      <label>Anything this tool must never read?<textarea name="never" rows="3" placeholder="HR&#10;folder: Private"></textarea><small>People, words in a title, or folder: Name; one per line.</small></label></fieldset>
    <div class="row0"><button type="button" id="setup-back">Back</button><span class="err" id="setup-err" role="alert"></span><span class="grow"></span><button class="primary" id="setup-next">Next</button></div></form>`, true);
  const f = $("#setup-form"), sets = [...f.querySelectorAll("fieldset")];
  const show = (n) => {
    setupStep = n;
    sets.forEach((x, k) => x.classList.toggle("hidden", k !== n));
    f.querySelectorAll(".steps i").forEach((x, k) => x.classList.toggle("on", k <= n));
    $("#setup-back").classList.toggle("hidden", n === 0);
    $("#setup-next").textContent = n === 2 ? "Start" : "Next";
    $("#setup-err").textContent = "";
    const first = sets[n].querySelector("textarea");
    if (first) first.focus();
  };
  show(0);
  $("#setup-back").onclick = () => show(setupStep - 1);
  f.onsubmit = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (setupStep === 0 && !f.provider.value) return ($("#setup-err").textContent = "Choose one of the two.");
    if (setupStep < 2) return show(setupStep + 1);
    const body = Object.fromEntries(["role", "running", "people", "late", "never", "provider"].map((k) => [k, f[k].value]));
    try {
      await api("POST", "/api/assistant/setup", body);
      document.activeElement.blur();
      $("#today-body").html = "";
      await loadRecap();
      if (body.provider === "copilot") { const c = await api("GET", "/api/ai/copilot"); if (!c.ok) toast("One more step: sign in to GitHub Copilot", 15000, ["Sign in", () => showTab("ai")]); }
    } catch (err) { $("#setup-err").textContent = err.message; }
  };
}

export { renderSetup };
