/* Installable app (DECISIONS D101). Keeps a copy of the site's own files so pages still open offline.
   Live data (GitHub raw files, exchange prices) is never cached here: it always comes from the network,
   so the app can't show old numbers as if they were current. */
const VERSION = "omega-v1";
const SCOPE = new URL("./", self.location).href;

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll([SCOPE, `${SCOPE}style.css`, `${SCOPE}shell.js`, `${SCOPE}theme.js`])).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET" || !req.url.startsWith(SCOPE)) return;   // other sites' data: straight to the network
  const keep = (res) => { if (res.ok) { const copy = res.clone(); caches.open(VERSION).then((c) => c.put(req, copy)); } return res; };
  // Newest copy from the network every time, so an update never leaves old code running; the saved copy only when offline.
  e.respondWith(fetch(req).then(keep).catch(() => caches.match(req).then((r) => r || (req.mode === "navigate" ? caches.match(SCOPE) : Response.error()))));
});
