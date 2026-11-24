// Settings.
import { $, api, esc } from "./core.js";
import { teamsLink } from "./recorded.js";
import { onTab } from "./tabs.js";

async function loadSettings() {
  const [s, devices, md] = await Promise.all([api("GET", "/api/settings"), api("GET", "/api/devices"),
    api("GET", "/api/meetings/devices")]);
  const f = $("#settings-form");
  const opts = (list, cur, def) => `<option value="">${def}</option>` +
    [...new Set([...list, cur].filter(Boolean))].map((d) => `<option>${esc(d)}</option>`).join("");
  f.meeting_mic.innerHTML = opts(md.inputs, s.meeting_mic, "Windows default microphone");
  f.meeting_output.innerHTML = opts(md.outputs, s.meeting_output, "Windows default output");
  for (const k of ["meeting_mic", "meeting_output", "meeting_final_model", "meeting_keep_audio_days", "meeting_mute_hotkey"]) f[k].value = s[k];
  f.meeting_prompt.checked = s.meeting_prompt;
  f.meeting_mute_sync.checked = s.meeting_mute_sync;
  f.meeting_summary.checked = s.meeting_summary;
  api("GET", "/api/meetings").then((m) => { $("#teams-link").textContent = m.teams && m.teams.connected ? teamsLink(m.teams) : ""; }).catch(() => {});
  f.notifications.checked = s.notifications;
  f.input_device.innerHTML = `<option value="">Windows default</option>` +
    [...new Set([...devices, s.input_device].filter(Boolean))].map((d) => `<option>${esc(d)}</option>`).join("");
  for (const k of ["hotkey", "model", "language", "input_device", "beam_size", "output_dir", "jira_url", "jira_email",
    "jira_max_attachment_mb", "memory_dir", "silence_days", "matter_quiet_days", "copilot_daily_credits", "copilot_effort", "llm_gpu", "llm_keep_alive_min", "wording_hotkey", "wording_mode"]) f[k].value = s[k];
  for (const k of ["paste", "restore_clipboard", "open_window_on_start", "mail_attachments", "app_window", "jira_attachments",
    "small_on_cpu", "dictation_cleanup", "auto_drafts", "check_answers"]) f[k].checked = s[k];
  const llmModels = (installed, suggested) => {
    const names = [...new Set([...installed, ...suggested, s.llm_model].filter(Boolean))];
    f.llm_model.innerHTML = names.map((m) => `<option value="${esc(m)}">${esc(m)}${installed.includes(m) ? "" : " (not downloaded)"}</option>`).join("");
    f.llm_model.value = s.llm_model;
  };
  llmModels([s.llm_model], []);
  api("GET", "/api/llm/status").then((st) => llmModels((st.models || []).map((m) => m.name), st.suggested || [])).catch(() => {});
  f.vocabulary.value = s.vocabulary.join("\n");
  f.exclude.value = s.exclude.join("\n");
  f.replacements.value = s.replacements.map((r) => `${r.from} => ${r.to}`).join("\n");
}
const settingsForm = $("#settings-form");
$("#settings-nav").innerHTML = [...settingsForm.querySelectorAll("h3")].map((h, n) => { h.id = "set-" + n; return `<button type="button" data-to="set-${n}">${esc(h.textContent)}</button>`; }).join("");
$("#settings-nav").addEventListener("click", (e) => { const b = e.target.closest("[data-to]"); if (b) document.getElementById(b.dataset.to).scrollIntoView({ block: "start" }); });
settingsForm.addEventListener("input", (e) => { if (!e.target.closest("#look")) $("#saved").textContent = "Not saved yet"; });
settingsForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const lines = (t) => t.split("\n").map((l) => l.trim()).filter(Boolean);
  const body = {
    hotkey: f.hotkey.value, model: f.model.value, language: f.language.value, input_device: f.input_device.value,
    beam_size: Number(f.beam_size.value), paste: f.paste.checked, restore_clipboard: f.restore_clipboard.checked,
    open_window_on_start: f.open_window_on_start.checked, small_on_cpu: f.small_on_cpu.checked, app_window: f.app_window.checked, vocabulary: lines(f.vocabulary.value),
    output_dir: f.output_dir.value.trim(), mail_attachments: f.mail_attachments.checked,
    jira_url: f.jira_url.value.trim(), jira_email: f.jira_email.value.trim(), jira_attachments: f.jira_attachments.checked,
    jira_max_attachment_mb: Number(f.jira_max_attachment_mb.value),
    meeting_prompt: f.meeting_prompt.checked, notifications: f.notifications.checked, meeting_mic: f.meeting_mic.value, meeting_output: f.meeting_output.value,
    meeting_final_model: f.meeting_final_model.value, meeting_keep_audio_days: Number(f.meeting_keep_audio_days.value),
    meeting_mute_sync: f.meeting_mute_sync.checked, meeting_summary: f.meeting_summary.checked, meeting_mute_hotkey: f.meeting_mute_hotkey.value.trim(),
    exclude: lines(f.exclude.value), memory_dir: f.memory_dir.value.trim(), auto_drafts: f.auto_drafts.checked, check_answers: f.check_answers.checked, silence_days: Number(f.silence_days.value),
    matter_quiet_days: Number(f.matter_quiet_days.value), copilot_daily_credits: Number(f.copilot_daily_credits.value), copilot_effort: f.copilot_effort.value,
    llm_model: f.llm_model.value, llm_gpu: f.llm_gpu.value, llm_keep_alive_min: Number(f.llm_keep_alive_min.value),
    dictation_cleanup: f.dictation_cleanup.checked, wording_hotkey: f.wording_hotkey.value, wording_mode: f.wording_mode.value,
    replacements: lines(f.replacements.value).filter((l) => l.includes("=>"))
      .map((l) => { const [a, ...b] = l.split("=>"); return { from: a.trim(), to: b.join("=>").trim() }; }),
  };
  try {
    const r = await api("POST", "/api/settings", body);
    $("#saved").textContent = r.changed.length ? "Saved: " + r.changed.join(", ") : "No changes.";
  } catch (err) { $("#saved").textContent = err.message; }
});

onTab("settings", loadSettings);
