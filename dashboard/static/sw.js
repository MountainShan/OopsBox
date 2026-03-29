const CACHE_NAME = 'oopsbox-v2';
const SHELL_URLS = [
  '/static/icon-192.png',
  '/static/icon-512.png',
  '/static/manifest.json',
];

// Install: cache static assets only
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(SHELL_URLS))
  );
  self.skipWaiting();
});

// Activate: clean old caches
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Fetch: only cache static assets, never intercept navigation or API
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // Never intercept: non-GET, API, navigation (HTML pages), auth
  if (e.request.method !== 'GET') return;
  if (url.pathname.startsWith('/api/')) return;
  if (e.request.mode === 'navigate') return;
  if (url.pathname === '/' || url.pathname === '/login') return;

  // Only handle static assets
  if (!url.pathname.startsWith('/static/')) return;

  e.respondWith(
    caches.match(e.request).then(cached => {
      // Return cached and update in background
      const fetchPromise = fetch(e.request).then(resp => {
        if (resp.ok) {
          caches.open(CACHE_NAME).then(cache => cache.put(e.request, resp.clone()));
        }
        return resp;
      }).catch(() => cached);

      return cached || fetchPromise;
    })
  );
});
