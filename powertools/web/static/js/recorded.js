// Recorded meetings, and the question whether to record the call.
import { $, api, draw, esc, say } from "./core.js";
import { onState } from "./state.js";
import { onTab } from "./tabs.js";

const post = async (path, body) => { try { await api("POST", "/api/meetings/" + path, body); } catch (e) { say(e.message); } };
function renderPrompt(p) {
  const el = $("#prompt");
  if (!p || !p.title) { el.classList.add("hidden"); return; }
  if (!el.classList.contains("hidden")) return;
  el.innerHTML = `<b>Teams call</b><span class="grow">Record '${esc(p.title)}'? (${esc(p.detail)})</span>
    <button class="rec">Record</button><button class="no">Not now</button>`;
  el.classList.remove("hidden");
  el.querySelector(".rec").onclick = async () => { el.classList.add("hidden"); await post("start", { from_prompt: true }); loadMeetings(); };
  el.querySelector(".no").onclick = () => { el.classList.add("hidden"); post("dismiss"); };
}
function teamsLink(t) {
  if (!t) return "";
  if (!t.connected) return "Now: Teams is not reachable (closed, or the API is off).";
  if (!t.paired) return "Now: connected to Teams, not paired yet (happens in the next call).";
  return "Now: connected and paired" + (t.in_meeting ? (t.muted ? ", in a call, muted." : ", in a call.") : ".");
}
const clock = (s) => [Math.floor(s / 3600), Math.floor(s / 60) % 60, Math.floor(s) % 60].map((n) => String(n).padStart(2, "0")).join(":");
let meetTimer = null, liveCount = -1;
const openMeetings = new Set();  // meetings whose details are unfolded (kept across refreshes)
async function loadMeetings() {
  clearTimeout(meetTimer);
  let m;
  try { m = await api("GET", "/api/meetings"); } catch { return; }
  const cur = m.current, now = $("#meeting-now"), live = $("#meeting-live");
  if (cur) {
    if (!now.querySelector(".timer")) {
      now.innerHTML = `<div class="meetbar"><span class="pill recording">recording</span><b class="mtitle"></b>
        <span class="timer"></span><span class="pill transcribing mutedp hidden" title="Me muted: your microphone is not recorded">Me muted</span><span class="lv">Me <span class="meter"><span class="lme"></span></span></span>
        <span class="lv">Others <span class="meter"><span class="lot"></span></span></span></div>
        <div class="meetbar" style="margin-top:10px"><input class="note" placeholder="Note for this moment (optional)">
        <button class="mark">Mark moment</button><button class="mute"></button><button class="stop rec">Stop</button></div>`;
      now.querySelector(".mute").onclick = async () => { await post("mute", {}); loadMeetings(); };
      now.querySelector(".mark").onclick = async () => { await post("mark", { note: now.querySelector(".note").value }); now.querySelector(".note").value = ""; };
      now.querySelector(".stop").onclick = async () => { await post("stop"); setTimeout(loadMeetings, 500); };
    }
    now.querySelector(".mtitle").textContent = cur.title;
    now.querySelector(".timer").textContent = clock(Date.now() / 1000 - cur.started);
    now.querySelector(".mutedp").classList.toggle("hidden", !m.muted);
    now.querySelector(".mute").textContent = m.muted ? "Unmute Me" : "Mute Me";
    now.querySelector(".lme").style.width = Math.min(100, m.levels.me * 600) + "%";
    now.querySelector(".lot").style.width = Math.min(100, m.levels.others * 600) + "%";
    live.classList.remove("hidden");
    if (cur.live.length !== liveCount) {
      liveCount = cur.live.length;
      const stick = live.scrollTop + live.clientHeight >= live.scrollHeight - 30;
      live.innerHTML = cur.live.map((s) => `<p><span class="t">${clock(s.start)}</span><span class="who">${s.speaker === "me" ? "Me" : "Others"}:</span> ${esc(s.text)}</p>`).join("")
        || "<p class='t'>The live transcript appears every 30 seconds.</p>";
      if (stick) live.scrollTop = live.scrollHeight;
    }
  } else {
    liveCount = -1;
    live.classList.add("hidden");
    if (!now.querySelector(".start")) {
      now.innerHTML = `<div class="meetbar"><input class="title" placeholder="Title (empty: from the calendar)">
        <button class="start rec">Start recording</button></div>
        <small>Records your microphone (Me) and what you hear (Others). Teams calls are noticed automatically.
        <span class="tlink"></span></small>`;
      now.querySelector(".start").onclick = async () => {
        try { await api("POST", "/api/meetings/start", { title: now.querySelector(".title").value }); loadMeetings(); }
        catch (e) { say(e.message); }
      };
    }
  }
  const tl = now.querySelector(".tlink");
  if (tl) tl.textContent = m.mute_hotkey ? `${m.mute_hotkey.toUpperCase()} mutes Teams and "Me" in the recording together.` : "";
  const status = { done: "done", finalizing: "transcribing...", interrupted: "interrupted", recording: "recording" };
  const hm = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const mins = (sec) => sec >= 3600 ? `${Math.floor(sec / 3600)} h ${Math.round(sec % 3600 / 60)} min` : `${Math.max(1, Math.round(sec / 60))} min`;
  draw("#meeting-list", m.sessions.map((s) => {
    const people = s.attendees || [], st = s.stats || {}, me = st.me || {}, ot = st.others || {};
    const talk = (me.seconds || 0) + (ot.seconds || 0);
    const share = talk ? ` · you spoke ${Math.round(100 * (me.seconds || 0) / talk)}%` : "";
    const open = openMeetings.has(s.id);
    return `<li data-id="${esc(s.id)}" class="${open ? "open" : ""}">
      <div class="grow"><div class="mhead"><b>${esc(s.title)}</b> <span class="pill ${s.status === "done" ? "ready" : s.status === "finalizing" ? "transcribing" : "error"}">${status[s.status] || esc(s.status)}</span></div>
        <div class="muted">${new Date(s.started * 1000).toLocaleDateString()} ${hm(s.started)}–${hm(s.started + (s.seconds || 0))} · ${mins(s.seconds || 0)}
          · ${people.length ? `${people.length} invited` : "attendees unknown"}${share}</div>
        ${s.error ? `<div class="warn">${esc(s.error)}</div>` : ""}
        ${s.summary_status === "running" ? `<div class="muted">Summary: ${esc(s.summary_step || "writing...")}</div>` : ""}
        ${s.summary_status === "error" ? `<div class="warn">Summary failed: ${esc(s.summary_error || "")}</div>` : ""}
        <div class="mdetail">
          ${s.summary ? `<div><span class="muted">Summary</span> ${esc(s.summary.summary)}</div>
            ${s.summary.key_points.length ? `<ul class="kp">${s.summary.key_points.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>` : ""}` : ""}
          ${s.organizer ? `<div><span class="muted">Organizer</span> ${esc(s.organizer)}</div>` : ""}
          ${people.length ? `<div><span class="muted">Invited</span> ${people.map(esc).join(", ")}</div>`
            : `<div class="muted">No attendees yet. <button data-act="cal">Fill from calendar</button> (needs the Graph token from the Teams tab)</div>`}
          ${talk ? `<div><span class="muted">Speaking</span> Me ${mins(me.seconds || 0)} (${me.words || 0} words) · Others ${mins(ot.seconds || 0)} (${ot.words || 0} words)</div>` : ""}
          ${(s.marks || []).length ? `<div><span class="muted">Marked moments</span> ${s.marks.length}</div>` : ""}
          ${(s.muted || []).length ? `<div><span class="muted">Me muted</span> ${mins((s.muted || []).reduce((a, [x, y]) => a + ((y ?? s.seconds) - x), 0))} (not recorded)</div>` : ""}
          ${s.transcript_model ? `<div><span class="muted">Transcript</span> ${esc(s.transcript_model)}</div>` : ""}
          ${s.devices ? `<div><span class="muted">Devices</span> mic: ${esc(s.devices.mic || "default")} · others: ${esc(s.devices.output || "default")}</div>` : ""}
          ${s.invite ? `<details><summary class="muted">Invitation</summary><pre>${esc(s.invite)}</pre></details>` : ""}
          ${s.md ? `<div class="muted small">${esc(s.md)}</div>` : ""}
        </div></div>
      <div class="mact">
        ${s.md ? `<button data-act="open">Open</button>` : ""}
        ${(s.status === "interrupted" || s.status === "done") && s.audio ? `<button data-act="final">Transcribe again</button>` : ""}
        ${s.status === "done" && s.md && s.summary_status !== "running" ? `<button data-act="sum">${s.summary ? "Summarize again" : "Summarize"}</button>` : ""}
        <button data-act="del">Delete</button></div></li>`;
  }).join("") || "<li>No recordings yet.</li>");
  document.querySelectorAll(".open-out:not([data-kind])").forEach((b) => (b.onclick = () => api("POST", "/api/open-output")));
  if ($("#meetings").classList.contains("active")) meetTimer = setTimeout(loadMeetings, cur ? 1000 : 4000);
}
$("#meeting-list").addEventListener("click", async (e) => {
  const act = e.target.dataset.act, li = e.target.closest("li");
  if (!li || !li.dataset.id) return;
  if (!act) {  // click on the row: show / hide the details
    if (e.target.closest("details, a")) return;
    li.classList.toggle("open");
    li.classList.contains("open") ? openMeetings.add(li.dataset.id) : openMeetings.delete(li.dataset.id);
    return;
  }
  if (act === "open") { try { await api("POST", `/api/meetings/${li.dataset.id}/open`, {}); } catch (err) { say(err.message); } return; }
  if (act === "cal") {
    try { const r = await api("POST", `/api/meetings/${li.dataset.id}/calendar`, {});
      if (!r.calendar) say("No Teams meeting on your calendar at that time (an ad-hoc call has no attendee list)."); }
    catch (err) { say(err.message); }
    loadMeetings(); return;
  }
  if (act === "sum") {
    try { await api("POST", `/api/meetings/${li.dataset.id}/summarize`, {}); } catch (err) { say(err.message); }
    loadMeetings(); return;
  }
  if (act === "del" && !confirm("Delete this recording (audio and live transcript)? The exported Markdown stays.")) return;
  try {
    await api(act === "del" ? "DELETE" : "POST", `/api/meetings/${li.dataset.id}` + (act === "final" ? "/finalize" : ""));
  } catch (err) { say(err.message); }
  loadMeetings();
});

$("#update-all").onclick = async () => {
  const b = $("#update-all"); b.disabled = true;
  try { const r = await api("POST", "/api/meetings/update-all", {});
    say(`${r.updated} transcripts updated, ${r.with_attendees} with attendees from the calendar.` +
          (r.token ? "" : " Set a Graph token in the Teams tab to fill in the attendees."));
  } catch (err) { say(err.message); }
  b.disabled = false; loadMeetings();
};

onState((s) => renderPrompt(s.pending_prompt));
onTab("meetings", loadMeetings);

export { teamsLink };
