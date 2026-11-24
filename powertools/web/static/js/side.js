// Bottom left: the model, what is left of its credits, and when the access ends.
import { $, api, say, setLabel, setText, toast } from "./core.js";
import { openConnect } from "./recap/connect.js";
import { onState } from "./state.js";
import { showTab } from "./tabs.js";

const modelName = (m) => String(m || "").split("-").map((w) => (/^gpt$/i.test(w) ? "GPT" : /^[a-z]/.test(w) ? w[0].toUpperCase() + w.slice(1) : w)).join(" ").replace(/^GPT /, "GPT-");
const thousands = (n) => (n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1000 ? Math.round(n / 1000) + "k" : String(n || 0));
function renderSide(llm, g) {
  const cloud = llm.provider === "copilot";
  setText("#side-model", cloud ? modelName(llm.model) : llm.model || "");
  setText("#side-tokens", cloud ? `${thousands(llm.tokens_today)} tokens today` : "on this laptop");
  const q = cloud && llm.quota, left = q && q.left !== null && q.left !== undefined, few = (n) => Number(n).toLocaleString([], { maximumFractionDigits: n < 100 ? 1 : 0 });
  setText("#side-credits", !cloud ? "nothing leaves it" : `${(llm.credits_today || 0).toFixed(1)}${llm.limit ? ` of ${llm.limit}` : ""} credits today`);
  $("#side-quota").classList.toggle("hidden", !q);
  if (q) {
    setText("#side-unit", q.unit);
    setText("#side-pct", q.unlimited ? "unlimited" : left ? Math.round(q.used_pct) + "% used" : "?");
    $("#side-fill").style.width = left ? Math.max(2, Math.min(100, q.used_pct)) + "%" : "0";
    $("#side-fill").className = !left ? "" : q.used_pct >= 90 ? "bad" : q.used_pct >= 70 ? "warn" : "";
    setText("#side-left", left ? `${few(q.left)} of ${few(q.entitlement)} left` : "");
    setText("#side-reset", `new in ${q.days_left} day${q.days_left === 1 ? "" : "s"} (${q.work_days_left} working)`);
    $("#side-quota").title = `${q.plan ? `Plan ${q.plan}. ` : ""}Read from GitHub at ${new Date(q.checked * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}, less what powertools used since; powertools this month: ${few(llm.credits_month || 0)}. Click to read it now.`;
  }
  $("#side-bar").classList.toggle("hidden", !cloud || !llm.limit || !!left);
  $("#side-bar span").style.width = Math.min(100, 100 * (llm.credits_today || 0) / (llm.limit || 1)) + "%";
  const m = g.expires_min || 0;
  setLabel("#side-graph", g.has_token ? `${m} min` : "expired", !g.has_token ? "bad" : m < 10 ? "warn" : "good");
  const d = llm.github_days, known = cloud && d !== null && d !== undefined;
  $("#side-github-row").classList.toggle("hidden", !known);
  $("#side-github-renew").classList.toggle("hidden", !known || d > 7);
  if (known) setLabel("#side-github", d > 0 ? `${d} day${d === 1 ? "" : "s"}` : "expired", d < 1 ? "bad" : d < 8 ? "warn" : "good");
}
const askQuota = () => api("POST", "/api/ai/quota").then(() => toast("Asking GitHub...")).catch((e) => say(e.message));
$("#side-quota").addEventListener("click", askQuota);
$("#side-quota").addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); askQuota(); } });
$("#side-renew").addEventListener("click", () => { showTab("recap"); openConnect(); });
$("#side-github-renew").addEventListener("click", () => { showTab("ai"); $("#copilot-card details").open = true; });

onState((s) => renderSide(s.llm || {}, s.graph || {}));
