// anthill service worker - offline app shell + web push.
// Caches static assets only; never caches authenticated HTML/API responses
// (those must always hit the server so data stays fresh and access-controlled).

const CACHE = "anthill-shell-v5";   // bump to evict stale cached assets (e.g. old CSS)
const SHELL = [
  "/static/style.css",
  "/static/lucide.min.js",
  "/static/dictation.js",
  "/static/marked.min.js",
  "/static/purify.min.js",
  "/static/tour.js",
  "/static/icon-192.png",
  "/static/icon-512.png",
  "/static/anthill-mark-light.png",
  "/static/favicon-32.png",
  "/static/manifest.webmanifest",
  "/static/offline.html",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;

  // Static assets.
  if (url.pathname.startsWith("/static/")) {
    const isCode = url.pathname.endsWith(".css") || url.pathname.endsWith(".js");
    if (isCode) {
      // Network-first for CSS/JS: always pick up styling/script changes when online,
      // fall back to cache offline. (Cache-first here is what stranded old styles.)
      e.respondWith(
        fetch(e.request).then((resp) => {
          const copy = resp.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
          return resp;
        }).catch(() => caches.match(e.request))
      );
    } else {
      // Images / manifest: stale-while-revalidate (fast, refreshes in the background).
      e.respondWith(
        caches.open(CACHE).then((cache) =>
          cache.match(e.request).then((hit) => {
            const fetchPromise = fetch(e.request).then((resp) => {
              if (resp && resp.status === 200) cache.put(e.request, resp.clone());
              return resp;
            }).catch(() => hit);
            return hit || fetchPromise;
          })
        )
      );
    }
    return;
  }

  // Navigations: network-first, fall back to the offline page when truly offline.
  if (e.request.mode === "navigate") {
    e.respondWith(
      fetch(e.request).catch(() => caches.match("/static/offline.html"))
    );
    return;
  }
  // Everything else (API, SSE): straight to network - never cached.
});

// ── web push ──────────────────────────────────────────────────────────────
self.addEventListener("push", (e) => {
  let data = { title: "anthill", body: "You have an update." };
  try { if (e.data) data = e.data.json(); } catch (_) {}
  e.waitUntil(
    self.registration.showNotification(data.title || "anthill", {
      body: data.body || "",
      icon: "/static/icon-192.png",
      badge: "/static/icon-192.png",
      data: { url: data.url || "/" },
    })
  );
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const target = (e.notification.data && e.notification.data.url) || "/";
  e.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
      for (const w of wins) { if (w.url.includes(target) && "focus" in w) return w.focus(); }
      return clients.openWindow(target);
    })
  );
});
