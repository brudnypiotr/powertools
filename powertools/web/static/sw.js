// Minimal service worker: makes the window installable as an app (own taskbar icon). No caching: the app is local.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
