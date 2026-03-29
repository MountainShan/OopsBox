const CACHE_NAME = 'oopsbox-v1';
const SHELL_URLS = [
  '/',
  '/static/icon-192.png',
  '/static/icon-512.png',
  '/static/manifest.json',
];

// Install: cache shell
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

// Fetch: network first, fallback to cache
self.addEventListener('fetch', e => {
  // Skip non-GET and API requests
  if (e.request.method !== 'GET' || e.request.url.includes('/api/')) return;

  e.respondWith(
    fetch(e.request)
      .then(resp => {
        // Cache successful responses for shell URLs
        if (resp.ok && SHELL_URLS.some(u => e.request.url.endsWith(u))) {
          const clone = resp.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(e.request, clone));
        }
        return resp;
      })
      .catch(() =>
        caches.match(e.request).then(cached => cached || new Response(
          '<html><body style="background:#0e1015;color:#c8ccd5;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;flex-direction:column;gap:12px">' +
          '<div style="font-size:24px">OopsBox</div>' +
          '<div style="color:#6b7080">Offline — waiting for connection</div>' +
          '<button onclick="location.reload()" style="padding:8px 20px;border-radius:8px;border:none;background:#e07840;color:#fff;cursor:pointer;font-size:14px">Retry</button>' +
          '</body></html>',
          { headers: { 'Content-Type': 'text/html' } }
        ))
      )
  );
});
