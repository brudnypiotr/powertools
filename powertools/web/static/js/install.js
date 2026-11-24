// Install the window as an app (own taskbar icon, pinning).
import { $ } from "./core.js";

let installEvent = null, appInstalled = false;
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
function isInstalled(yes) { appInstalled = yes; renderInstall(); }
function renderInstall() {
  if (appInstalled) {
    $("#install-text").textContent = "Installed as an app: pin it from the taskbar.";
    $("#install-do").classList.add("hidden");
    return;
  }
  $("#install-do").classList.toggle("hidden", !installEvent);
  $("#install-text").textContent = installEvent ? "Install the window as an app to get its own taskbar icon and pinning."
    : `To get its own taskbar icon: open ${location.origin} in Edge, then ⋯ menu > Apps > Install this site as an app.`;
}
window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); installEvent = e; renderInstall(); });
window.addEventListener("appinstalled", () => { appInstalled = true; $("#install-text").textContent = "Installed. Next time the app window opens with its own icon."; $("#install-do").classList.add("hidden"); });
$("#install-do").addEventListener("click", async () => { if (installEvent) { installEvent.prompt(); installEvent = null; renderInstall(); } });
renderInstall();

export { isInstalled };
