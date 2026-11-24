// I have an idea: a file the user sends.
import { $, api, esc, say } from "./core.js";

const ideaBox = $("#idea-box");
const ideaForm = ideaBox.querySelector("form");
let pictures = [];
const drawPictures = () => { $("#idea-pics").innerHTML = pictures.map((p, n) => `<button type="button" data-n="${n}" title="Remove"><img src="${p}" alt="Picture ${n + 1}"></button>`).join(""); };
$("#idea").addEventListener("click", async () => {
  $("#idea-done").innerHTML = ""; ideaForm.querySelector(".err").textContent = "";
  ideaBox.showModal();
  try {
    const i = await api("GET", "/api/feedback");
    $("#idea-report").textContent = i.report;
    $("#idea-github").classList.toggle("hidden", !i.issues);
  } catch (err) { ideaForm.querySelector(".err").textContent = err.message; }
});
ideaBox.querySelector(".close").addEventListener("click", () => ideaBox.close());
ideaBox.addEventListener("paste", (e) => {
  for (const it of e.clipboardData.items) if (/^image\/(png|jpeg|webp)$/.test(it.type) && pictures.length < 6) {
    const r = new FileReader();
    r.onload = () => { pictures.push(r.result); drawPictures(); };
    r.readAsDataURL(it.getAsFile());
  }
});
$("#idea-pics").addEventListener("click", (e) => { const b = e.target.closest("[data-n]"); if (b) { pictures.splice(Number(b.dataset.n), 1); drawPictures(); } });
ideaForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target, text = f.text.value;
  try {
    const r = await api("POST", "/api/feedback/package", { text, diagnostics: f.diagnostics.checked, pictures });
    const note = encodeURIComponent("powertools feedback: " + text.split("\n")[0].slice(0, 100));
    $("#idea-done").innerHTML = `<b>${esc(r.name)}</b> is on your Desktop. Send it to whoever looks after powertools.
      <span class="row"><button type="button" id="idea-show">Show the file</button>${r.contact ? `<a class="btnlink" target="_blank" rel="noopener" href="https://teams.microsoft.com/l/chat/0/0?users=${encodeURIComponent(r.contact)}&message=${note}">Message in Teams</a> <a class="btnlink" href="mailto:${encodeURIComponent(r.contact)}?subject=${note}">E-mail</a>` : ""}</span>`;
    $("#idea-show").onclick = () => api("POST", "/api/feedback/show", { name: r.name }).catch((err) => say(err.message));
    f.text.value = ""; pictures = []; drawPictures();
  } catch (err) { f.querySelector(".err").textContent = err.message; }
});
$("#idea-github").addEventListener("click", async () => {
  try { const r = await api("POST", "/api/idea", { text: ideaForm.text.value, diagnostics: ideaForm.diagnostics.checked }); window.open(r.url, "_blank", "noopener"); ideaForm.text.value = ""; ideaBox.close(); }
  catch (err) { ideaForm.querySelector(".err").textContent = err.message; }
});
