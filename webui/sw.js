/* SD Agent service worker — app-shell caching for offline launch.
   Network-first for API/dynamic content; cache-first for static assets. */

const CACHE = "sdagent-v1";
const SHELL = [
  "/", "/index.html", "/static/style.css", "/static/app.js",
  "/manifest.webmanifest", "/static/icon-192.png", "/static/icon-512.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) =>
    Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
  ));
  self.clients.claim();
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

  // app shell: cache-first, refresh in background
  e.respondWith(
    caches.match(e.request).then((hit) => {
      const net = fetch(e.request).then((resp) => {
        if (resp.ok) {
          const copy = resp.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return resp;
      }).catch(() => hit);
      return hit || net;
    })
  );
});
