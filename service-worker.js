/* Forged Titanium — service worker
   Cache-first for the app shell (so it installs and opens offline),
   network-first-with-cache-fallback for data.json (so an online visit
   always tries to get the newest data, but an offline visit still shows
   whatever was cached last). Bump CACHE_VERSION whenever index.html,
   manifest.json or the icons change so returning visitors pick up the
   update instead of a stale cached shell. */
const CACHE_VERSION = 'ft-shell-v2';
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

  const url = new URL(req.url);
  if (url.pathname.endsWith('/data.json')) {
    // Network-first: always try to get the freshest data, fall back to
    // whatever was cached from the last successful fetch when offline.
    event.respondWith(
      fetch(req).then((res) => {
        const copy = res.clone();
        caches.open(CACHE_VERSION).then((cache) => cache.put(req, copy));
        return res;
      }).catch(() => caches.match(req))
    );
    return;
  }

  // Cache-first for everything else (the app shell).
  event.respondWith(
    caches.match(req).then((cached) => cached || fetch(req).then((res) => {
      const copy = res.clone();
      caches.open(CACHE_VERSION).then((cache) => cache.put(req, copy));
      return res;
    }).catch(() => cached))
  );
});
