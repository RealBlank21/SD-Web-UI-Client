/* SD Agent service worker — app-shell caching for offline launch.
   Network-first for API/dynamic content; cache-first for static assets. */

const CACHE = "sdagent-v42";
const V = "?v=46";                   // keep in sync with index.html
const SHELL = [
  "/", "/index.html", "/static/style.css" + V, "/static/app.js" + V,
  "/manifest.webmanifest", "/static/icon-192.png", "/static/icon-512.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)));
    await self.clients.claim();
    // tell open pages we took over so they can reload onto fresh assets
    const msgs = await self.clients.matchAll({ type: "window" });
    for (const c of msgs) c.postMessage({ type: "sw-takeover", cache: CACHE });
  })());
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;

  // never cache dynamic/auth-gated content
  if (url.pathname.startsWith("/api/") ||
      url.pathname.startsWith("/outputs/") ||
      url.pathname.startsWith("/thumb/")) {
    e.respondWith(fetch(e.request));
    return;
  }

  // app shell: network-first so updates apply immediately; cache fallback
  // keeps the app openable offline
  e.respondWith(
    fetch(e.request).then((resp) => {
      if (resp.ok) {
        const copy = resp.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy));
      }
      return resp;
    }).catch(() => caches.match(e.request))
  );
});
