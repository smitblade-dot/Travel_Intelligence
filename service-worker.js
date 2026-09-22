/* Travel Intelligence — service worker
   Network-first for everything (index.html, data.json, the app shell),
   with a cache fallback so a previously-visited page still opens offline.
   A network-first strategy means a visitor with an existing service
   worker always sees the latest deployed content without needing a
   manual cache-version bump on every content change. */
const CACHE_VERSION = 'ti-shell-v4';
const SHELL_FILES = [
  './',
  './index.html',
  './manifest.json',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/icon-maskable-512.png',
  './assets/img/cyprus-aphrodite-sunset.jpg'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) => cache.addAll(SHELL_FILES)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((names) => Promise.all(
      names.filter((n) => n !== CACHE_VERSION).map((n) => caches.delete(n))
    )).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  // Network-first for everything: always try to get the latest deployed
  // version, and only fall back to the cache (ignoring the query string,
  // so a plain request can still match a cache-busted entry and vice
  // versa) when the network is unavailable, e.g. offline.
  event.respondWith(
    fetch(req).then((res) => {
      const copy = res.clone();
      caches.open(CACHE_VERSION).then((cache) => cache.put(req, copy));
      return res;
    }).catch(() => caches.match(req, { ignoreSearch: true }))
  );
});
