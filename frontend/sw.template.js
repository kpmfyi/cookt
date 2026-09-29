/* cookt service worker. Built from frontend/sw.template.js by vite.config.ts
 * (the build injects the precache list and a version).
 *
 * - App shell + hashed assets: precached at install, cache-first.
 * - /api/catalog: stale-while-revalidate; tells pages when a newer catalog arrived.
 * - /images: cache-first (image URLs are immutable); the More page can fetch them all ahead.
 * - Navigation: network-first (3 s), falls back to the cached shell, so the app opens offline.
 * - Update-available flow: a new worker waits until the page asks it to skip waiting.
 */
const VERSION = "__VERSION__";
const PRECACHE = __PRECACHE__;
const SHELL = `cookt-private-shell-${VERSION}`;
const DATA = "cookt-private-data-v1";
const IMAGES = "cookt-private-images-v2";

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((cache) => cache.addAll(["/", ...PRECACHE])));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys
          .filter((key) => (key.startsWith("cookt-private-shell-") && key !== SHELL) || key === "cookt-private-images-v1")
          .map((key) => caches.delete(key)),
      );
      await self.clients.claim();
    })(),
  );
});

self.addEventListener("message", (event) => {
  if (event.data?.type === "skip-waiting") self.skipWaiting();
});

async function broadcast(message) {
  const clients = await self.clients.matchAll({ type: "window" });
  clients.forEach((client) => client.postMessage(message));
}

async function catalog(request) {
  const cache = await caches.open(DATA);
  const cached = await cache.match("/api/catalog");
  const refresh = fetch(request, { cache: "no-store" })
    .then(async (response) => {
      if (response.ok) {
        const previous = cached?.headers.get("ETag");
        await cache.put("/api/catalog", response.clone());
        if (cached && previous !== response.headers.get("ETag")) await broadcast({ type: "catalog-updated" });
      }
      return response;
    })
    .catch(() => null);
  if (cached) return cached;
  return (await refresh) || new Response(JSON.stringify({ detail: "offline" }), { status: 503, headers: { "Content-Type": "application/json" } });
}

async function networkFirst(request, cacheName, timeoutMs) {
  const cache = await caches.open(cacheName);
  try {
    const response = await Promise.race([
      fetch(request),
      new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), timeoutMs)),
    ]);
    if (response.ok) await cache.put(request, response.clone());
    return response;
  } catch {
    const cached = await cache.match(request);
    if (cached) return cached;
    throw new Error("offline");
  }
}

async function cacheFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) await cache.put(request, response.clone());
  return response;
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/mcp")) return;

  if (request.mode === "navigate") {
    event.respondWith(
      (async () => {
        try {
          return await networkFirst(request, SHELL, 3000);
        } catch {
          return (await caches.match("/", { cacheName: SHELL })) || Response.error();
        }
      })(),
    );
    return;
  }
  if (url.pathname === "/api/catalog") {
    event.respondWith(catalog(request));
    return;
  }
  if (/^\/api\/recipes\/[^/]+$/.test(url.pathname)) {
    event.respondWith(networkFirst(request, DATA, 4000).catch(() => Response.error()));
    return;
  }
  if (url.pathname.startsWith("/images/")) {
    event.respondWith(cacheFirst(request, IMAGES));
    return;
  }
  if (url.pathname.startsWith("/assets/") || PRECACHE.includes(url.pathname)) {
    event.respondWith(caches.match(request).then((cached) => cached || fetch(request)));
  }
});

// Timer alerts pushed by the server (cookt.push) reach the device even while it is locked.
self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { body: event.data?.text() };
  }
  event.waitUntil(
    Promise.all([
      self.registration.showNotification(data.title || "cookt", {
        body: data.body || "",
        tag: data.tag,
        renotify: true,
        requireInteraction: true,
        icon: "/icons/icon-192.png",
        badge: "/icons/icon-192.png",
        data: { url: data.url || "/" },
      }),
      self.navigator.setAppBadge?.().catch(() => undefined),
    ]),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = event.notification.data?.url || "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      const client = clients[0];
      return client ? client.focus() : self.clients.openWindow(url);
    }),
  );
});
