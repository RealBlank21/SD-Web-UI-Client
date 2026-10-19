/* Anima service worker — app-shell caching for offline launch.
   Network-first for API/dynamic content; cache-first for static assets and
   for the display images (see `displayImage`). */

const CACHE = "sdagent-v118";
const V = "?v=118";                   // keep in sync with index.html

/* Display images get their own cache, and it is deliberately NOT versioned
   with the shell. The shell cache is emptied by every version bump, and
   throwing away a few hundred MB of pictures because app.js changed would be
   absurd. The bytes are addressed by names the server generates, so a stale
   entry is not possible except for an overwrite in place — which is what
   IMG_MAX_AGE is for. */
const IMG_CACHE = "sdagent-img";
const SHELL = [
  "/", "/index.html", "/static/style.css" + V, "/static/app.js" + V,
  "/manifest.webmanifest", "/static/icon-192.png", "/static/icon-512.png",
  // the login wordmark's face — the gate is the first thing an offline PWA
  // launch draws, so it should not fall back to a system font
  "/static/anima-display.woff2" + V,
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    const keys = await caches.keys();
    // IMG_CACHE is kept across versions on purpose — see its declaration. It
    // is the one cache here that is not disposable, and deleting it on every
    // version bump would make the whole feature pointless.
    await Promise.all(keys
      .filter((k) => k !== CACHE && k !== IMG_CACHE)
      .map((k) => caches.delete(k)));
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

/* ------------------------------------------------ display images (/image/, /thumb/)

   Cache-first, because the point of the layer is twofold and the HTTP cache
   only delivers one of them. It saves the bytes on a repeat view — the server
   already does that with a year of `max-age` — but it also *holds onto them*:
   iOS gives a tab a few tens of MB of HTTP cache and evicts it whenever it
   likes, so scrolling a large gallery re-downloads pictures the reader has
   already seen. Cache Storage is not given that treatment.

   The budget is bytes rather than a file count, because a 4 MB uploaded scan
   and a 129 KB generation should not cost the same. 384 MB holds this box's
   entire library several times over (310 MB of webp derivatives plus ~50 MB
   of thumbnails), which is the point: a reader who has browsed everything
   should stop paying for any of it. */
const IMG_BUDGET = 384 * 1024 * 1024;
//: Trim down to this once over budget, not to the budget exactly: the next
//: ~10% of writes then cost nothing, where trimming to the line would re-measure
//: the whole cache on every single image.
const IMG_LOW = Math.floor(IMG_BUDGET * 0.9);

/* How long a cached image is trusted before it is refreshed behind the
   reader's back. Images are named for what they are and are never rewritten
   in place, with one exception — a regeneration whose seed and timestamp
   repeat, and an upload onto a name that exists — so the entry is served
   immediately either way and quietly replaced a week later. Same policy as
   the server's own `stale-while-revalidate`, so the two cannot disagree. */
const IMG_MAX_AGE = 7 * 24 * 60 * 60 * 1000;

//: Running total of cached bytes. `null` means "not measured yet".
let imgBytes = null;

/** Sum the cache. Not free — it is one `match` per entry — so it runs once
    per worker lifetime and again only when the budget is actually exceeded,
    where `imgTrim` then frees a tenth of the cache to stay off this path. */
async function imgMeasure(cache) {
  let total = 0;
  for (const req of await cache.keys()) {
    const res = await cache.match(req);
    total += Number((res && res.headers.get("Content-Length")) || 0);
  }
  imgBytes = total;
  return total;
}

/** Evict oldest-first until the cache is back under the low-water mark.
    `cache.keys()` is specified to return insertion order, and a `put` of an
    existing request moves it to the end, so that order is an LRU — for free. */
async function imgTrim(cache) {
  await imgMeasure(cache);
  if (imgBytes <= IMG_BUDGET) return;
  for (const req of await cache.keys()) {
    if (imgBytes <= IMG_LOW) break;
    const res = await cache.match(req);
    imgBytes -= Number((res && res.headers.get("Content-Length")) || 0);
    await cache.delete(req);
  }
  if (imgBytes < 0) imgBytes = 0;
}

/** Store a display image, stamped with the time so it can age out later. */
async function imgStore(cache, req, resp) {
  const size = Number(resp.headers.get("Content-Length") || 0);
  const headers = new Headers(resp.headers);
  headers.set("x-img-at", String(Date.now()));
  // Built from the clone's *stream* rather than its blob: nothing here should
  // hold an image in memory just to add a header to it.
  await cache.put(req, new Response(resp.clone().body, {
    status: resp.status, statusText: resp.statusText, headers,
  }));
  if (imgBytes === null) await imgMeasure(cache);   // first store: settles this put
  else imgBytes += size;
  if (imgBytes > IMG_BUDGET) await imgTrim(cache);
}

async function displayImage(event, req) {
  const cache = await caches.open(IMG_CACHE);
  const hit = await cache.match(req);
  if (hit) {
    const at = Number(hit.headers.get("x-img-at") || 0);
    if (Date.now() - at > IMG_MAX_AGE) event.waitUntil(imgRefresh(cache, req));
    return hit;
  }
  const resp = await fetch(req);          // throws when offline and never seen
  if (resp.status === 200) await imgStore(cache, req, resp);
  return resp;
}

async function imgRefresh(cache, req) {
  try {
    const resp = await fetch(req);
    if (resp.status === 200) await imgStore(cache, req, resp);
  } catch {
    /* offline — the copy already on screen stands, and the next load retries */
  }
}

/* The display cache is keyed by URL, and a URL is not scoped to an account:
   `/image/~u002/x.png` means the same thing to every session that asks for it.
   So the cache goes when the identity does — see `forgetUser` in app.js, which
   is what sends this. Without it, the next person at this device could read
   the previous user's gallery out of the address bar, offline. */
self.addEventListener("message", (e) => {
  if (e.data && e.data.type === "image-clear") {
    e.waitUntil(caches.delete(IMG_CACHE).then(() => { imgBytes = null; }));
  }
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;

  // never cache dynamic/auth-gated content. `/outputs/` sits here rather than
  // with the display images below on purpose: it is the 1.1 MB master, fetched
  // by the Download button and the avatar/cover picker, and it has no business
  // taking a third of the reader's budget for a file they chose to save.
  if (url.pathname.startsWith("/api/") ||
      url.pathname.startsWith("/outputs/")) {
    e.respondWith(fetch(e.request));
    return;
  }

  // the display images: the `/image/` derivative and the `/thumb/` grid copy
  if (url.pathname.startsWith("/image/") ||
      url.pathname.startsWith("/thumb/")) {
    e.respondWith(displayImage(e, e.request));
    return;
  }

  const isShell = url.pathname === "/" || url.pathname === "/index.html";

  // app shell: network-first so updates apply immediately; cache fallback
  // keeps the app openable offline.
  //
  // `cache: "no-cache"` is load-bearing, not decoration. A reverse proxy in
  // front of this server rewrites the origin's `Cache-Control: no-cache` into
  // a multi-hour `max-age`, and a plain `fetch()` inside a service worker
  // still consults the HTTP cache — so index.html, app.js and style.css would
  // all sit in the browser's cache together, CONSISTENTLY on an old version.
  // The skew guard in app.js cannot see that (every file agrees), so the page
  // would simply run yesterday's build for hours. `no-cache` revalidates every
  // request with the origin without disabling the cache for good responses.
  e.respondWith(
    fetch(e.request, { cache: "no-cache" }).then((resp) => {
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
