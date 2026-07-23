const CACHE = "glyph-v20"; // bumped — causes browser to install fresh SW and drop old cache
const SHELL = ["/manifest.json", "/icons/192.png", "/icons/512.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", e => {
  // delete every old cache version so stale files don't linger — this is also what
  // heals devices that cached a redirect response under the old cache (see below)
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => clients.claim())
  );
});

// only complete, non-redirect, same-origin 200s are safe to cache. Caching a redirect
// once caused an infinite / <-> /login loop: a logged-in visit to /login cached its 302,
// and after sessions reset the server redirected / -> /login while the cache redirected
// /login -> / forever. Never again.
function cacheable(res) {
  return res && res.ok && !res.redirected && res.type === "basic";
}

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);

  // API calls always go to the network — never cache notes
  if (url.pathname.startsWith("/api/")) return;

  // auth pages redirect based on session state — the browser must always see the live
  // server's answer, so the SW stays completely out of the way
  if (url.pathname === "/login" || url.pathname === "/signup") return;

  // index.html: network first so code changes are always picked up immediately
  // falls back to cache only when fully offline
  if (url.pathname === "/" || url.pathname === "/index.html") {
    e.respondWith(
      fetch(e.request)
        .then(res => {
          if (cacheable(res)) {
            const copy = res.clone();
            caches.open(CACHE).then(c => c.put(e.request, copy));
          }
          return res;
        })
        .catch(() => caches.match(e.request))
    );
    return;
  }

  // icons, manifest etc: cache first (they rarely change)
  e.respondWith(
    caches.match(e.request).then(cached => {
      if (cached) return cached;
      return fetch(e.request).then(res => {
        if (cacheable(res)) {
          const copy = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, copy));
        }
        return res;
      });
    })
  );
});
