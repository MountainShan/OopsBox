const CACHE = 'oopsbox-v1';
const SHELL = [
  '/',
  '/static/css/app.css',
  '/static/js/api.js',
  '/static/js/files.js',
  '/static/js/terminal.js',
  '/static/js/viewer.js',
  '/static/favicon.svg',
  '/static/icon-192.png',
  '/static/icon-512.png',
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // Never intercept API calls, terminal iframes, or cross-origin
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/')) return;
  if (url.pathname.startsWith('/terminal/')) return;

  // Network-first for HTML (always fresh), cache-first for static assets
  if (e.request.destination === 'document' || url.pathname === '/') {
    e.respondWith(
      fetch(e.request).catch(() => caches.match('/'))
    );
  } else {
    e.respondWith(
      caches.match(e.request).then(cached => cached || fetch(e.request))
    );
  }
});
