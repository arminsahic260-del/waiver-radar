// Waiver Radar service worker.
// - page (navigation): network-first, cached copy when offline
// - data/*.json: cache-first; refreshed in the background, and open pages get a "data-updated" message when
//   the snapshot actually changed, so they re-render without a reload
// - other same-origin files and Google Fonts: served from cache, refreshed in the background
// Cache writes are best-effort: a full or unavailable cache must never turn a good network response into an error.
const VERSION = "v1";
const SHELL = `wr-shell-${VERSION}`;
const DATA = "wr-data";
const FONTS = "wr-fonts";
const PRECACHE = ["./", "manifest.json", "icons/icon.svg", "icons/icon-192.png", "icons/icon-512.png",
  "icons/apple-touch-icon.png"];

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL);
    for (const url of PRECACHE) {
      try {
        await cache.add(new Request(url, { cache: "reload" }));
      } catch (e) {
        console.warn(`precache ${url}: ${e.name} ${e.message}`);
      }
    }
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) {
      if (key.startsWith("wr-shell-") && key !== SHELL) await caches.delete(key);
    }
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin === location.origin && /\/data\/[^/]+\.json$/.test(url.pathname)) {
    event.respondWith(dataFirst(event, url));
  } else if (req.mode === "navigate") {
    event.respondWith(pageNetworkFirst(req));
  } else if (url.origin === location.origin) {
    event.respondWith(staleWhileRevalidate(event, SHELL));
  } else if (url.hostname === "fonts.googleapis.com" || url.hostname === "fonts.gstatic.com") {
    event.respondWith(staleWhileRevalidate(event, FONTS));
  }
});

async function put(name, key, res) {
  try {
    await (await caches.open(name)).put(key, res);
  } catch (e) {
    console.warn(`cache put ${key}: ${e.name}`);
  }
}

async function match(name, key, opts) {
  try {
    return await (await caches.open(name)).match(key, opts);
  } catch (e) {
    return undefined;
  }
}

async function dataFirst(event, url) {
  const key = url.origin + url.pathname;
  const cached = await match(DATA, key);
  const oldText = cached ? await cached.clone().text() : null;
  const refresh = fetch(event.request.url, { cache: "no-cache" }).then(async (res) => {
    if (!res.ok) return res;
    const text = await res.clone().text();
    await put(DATA, key, res.clone());
    if (oldText !== null && text !== oldText) await broadcast({ type: "data-updated", url: url.pathname });
    return res;
  }).catch(() => null);
  if (cached) {
    event.waitUntil(refresh);
    return cached;
  }
  return (await refresh) || new Response("{}", { status: 503, headers: { "Content-Type": "application/json" } });
}

async function pageNetworkFirst(req) {
  let res;
  try {
    res = await fetch(req);
  } catch (e) {
    return (await match(SHELL, "./")) || Response.error();
  }
  if (res.ok) await put(SHELL, "./", res.clone());
  return res;
}

async function staleWhileRevalidate(event, name) {
  const cached = await match(name, event.request, { ignoreSearch: name === SHELL });
  const refresh = fetch(event.request).then(async (res) => {
    if (res.ok || res.type === "opaque") await put(name, event.request, res.clone());
    return res;
  }).catch(() => null);
  if (cached) {
    event.waitUntil(refresh);
    return cached;
  }
  return (await refresh) || Response.error();
}

async function broadcast(msg) {
  for (const client of await self.clients.matchAll({ type: "window" })) client.postMessage(msg);
}
