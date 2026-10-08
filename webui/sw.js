/* SD Agent service worker — app-shell caching for offline launch.
   Network-first for API/dynamic content; cache-first for static assets. */

const CACHE = "sdagent-v50";
const V = "?v=53";                   // keep in sync with index.html
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

/** Is this a cached shell entry rendered against OUR asset version?
    index.html is cached unversioned, so a stale copy survives a version bump
    and asks the server for assets that have already moved on — the page then
    runs new JS against an old DOM. Only hand out a shell that matches. */
async function shellMatches(resp) {
  try {
    const text = await resp.clone().text();
    return text.includes("/static/app.js" + V);
  } catch {
    return false;
  }
}

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

  const isShell = url.pathname === "/" || url.pathname === "/index.html";

  // app shell: network-first so updates apply immediately; cache fallback
  // keeps the app openable offline
  e.respondWith(
    fetch(e.request).then((resp) => {
      if (resp.ok) {
        const copy = resp.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy));
      }
      return resp;
    }).catch(async () => {
      const hit = await caches.match(e.request);
      if (hit && !isShell) return hit;
      if (hit && await shellMatches(hit)) return hit;
      // Last resort: the URL we hold does not line up with this shell (a
      // version bump left the page asking for an asset we no longer cache).
      // Serve the same path at whatever version we do have — mismatched
      // assets degrade gracefully because every listener in app.js is bound
      // through on(), and the skew guard in app.js reloads once when it can.
      const any = await caches.match(e.request, { ignoreSearch: true });
      if (any) return any;
      throw new Error("offline and not cached");
    })
  );
});
