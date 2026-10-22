// PyWeb service worker: offline support and push notifications for installable apps.
//
// What it keeps:
//  - static files (/static/...): from the cache first when they're fingerprinted (immutable),
//    otherwise from the network, with the cached copy as the fallback offline;
//  - the offline page (and anything listed in PWA(precache=...)), stored on install;
//  - pages, only with PWA(cache_pages=True), and only those the server marked as safe to keep
//    (rendered for a visitor who isn't signed in).
// What it never touches: anything but GET, other sites, /__pyweb/ (server functions, forms,
// live updates, uploads) except its own offline page and icons, and requests that carry credentials.
const CONFIG = __PYWEB_SW_CONFIG__;
const STATIC = "pyweb-static-" + CONFIG.version;
const PAGES = "pyweb-pages-" + CONFIG.version;

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(STATIC)
      .then((cache) => cache.addAll(CONFIG.precache))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys
        .filter((k) => k.startsWith("pyweb-") && k !== STATIC && k !== PAGES)
        .map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("message", (event) => {
  // The page asks for this after signing out: forget every kept page.
  if (event.data === "pyweb:forget-pages") event.waitUntil(caches.delete(PAGES));
});

function isOwn(path) {
  return path === CONFIG.offline || CONFIG.precache.includes(path) || path.startsWith("/__pyweb/pwa/");
}

function bypass(request, url) {
  if (request.method !== "GET") return true;
  if (url.origin !== self.location.origin) return true;
  if (request.headers.has("authorization") || request.headers.has("range")) return true;
  if ((request.headers.get("accept") || "").includes("text/event-stream")) return true;
  if (url.pathname === "/sw.js") return true;
  if (url.pathname.startsWith("/__pyweb/") && !isOwn(url.pathname)) return true;
  return false;
}

async function staticFile(request) {
  const cache = await caches.open(STATIC);
  const hit = await cache.match(request);
  if (hit && /immutable/.test(hit.headers.get("cache-control") || "")) return hit;
  try {
    const response = await fetch(request);
    if (response.ok) cache.put(request, response.clone());
    return response;
  } catch (err) {
    if (hit) return hit;
    throw err;
  }
}

async function page(request) {
  try {
    const response = await fetch(request);
    if (CONFIG.cachePages && response.ok && response.headers.get("x-pyweb-offline") === "1") {
      const cache = await caches.open(PAGES);
      cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    if (CONFIG.cachePages) {
      const kept = await caches.match(request, { cacheName: PAGES });
      if (kept) return kept;
    }
    const offline = await caches.match(CONFIG.offline, { cacheName: STATIC });
    if (offline) return offline;
    throw err;
  }
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (bypass(request, url)) return;
  if (request.mode === "navigate") {
    event.respondWith(page(request));
  } else if (url.pathname.startsWith("/static/") || isOwn(url.pathname)) {
    event.respondWith(staticFile(request));
  }
});

// ------------------------------------------------------------------ push notifications

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (_) {
    data = { title: event.data ? event.data.text() : "" };
  }
  const options = {
    body: data.body || "",
    icon: data.icon || CONFIG.icon,
    badge: data.badge || CONFIG.icon,
    data: { url: data.url || "/" },
  };
  if (data.tag) options.tag = data.tag;
  if (data.renotify) options.renotify = true;
  event.waitUntil(self.registration.showNotification(data.title || CONFIG.name, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL((event.notification.data && event.notification.data.url) || "/", self.location.origin);
  if (target.origin !== self.location.origin) return;        // notifications only open this app
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const client of list) {
        if (client.url === target.href && "focus" in client) return client.focus();
      }
      return self.clients.openWindow(target.href);
    }),
  );
});

self.addEventListener("pushsubscriptionchange", (event) => {
  // The browser replaced the subscription: tell the server about the new one.
  const old = event.oldSubscription;
  event.waitUntil(
    fetch("/__pyweb/push/key").then((r) => r.json()).then((info) =>
      self.registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: decode(info.key) }))
      .then((sub) => fetch("/__pyweb/push/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ ...sub.toJSON(), replaces: old ? old.endpoint : null }),
      }))
      .catch(() => {}),
  );
});

function decode(b64) {
  const s = atob(b64.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((b64.length + 3) % 4));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
}
