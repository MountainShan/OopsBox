const CACHE = 'oopsbox-v31';
const SHELL = [
  '/',
  '/static/css/app.css',
  '/static/js/api.js',
  '/static/js/files.js',
  '/static/js/chat-ui.js',
  '/static/js/chat-panels.js',
  '/static/js/chat.js',
  '/static/js/terminal.js',
  '/static/js/viewer.js',
  '/static/js/statusbar.js',
  '/static/favicon.svg',
  '/static/icon.svg',
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

  // Network-first for everything: the cache only serves the app shell when
  // the server is unreachable. Cache-first kept old CSS/JS after a deploy.
  e.respondWith(
    fetch(e.request)
      .then(res => {
        if (res.ok && e.request.method === 'GET') {
          const copy = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, copy));
        }
        return res;
      })
      .catch(async () => (await caches.match(e.request))
        || (e.request.destination === 'document' && await caches.match('/'))
        || Response.error())
  );
});

// ── Push: an agent waits for you, or usage passed the alert threshold ──
self.addEventListener('push', e => {
  let msg = {};
  try { msg = e.data ? e.data.json() : {}; } catch (_) { msg = { body: e.data && e.data.text() }; }
  e.waitUntil(self.registration.showNotification(msg.title || 'OopsBox', {
    body: msg.body || '',
    tag: msg.tag,
    renotify: !!msg.tag,
    icon: '/static/icon-192.png?v=3',
    badge: '/static/favicon-32.png?v=3',
    data: { url: msg.url || '/' },
  }));
});

// Tapping it opens OopsBox on that project's chat, in a window already open if any.
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = new URL(e.notification.data?.url || '/', self.location.origin).href;
  e.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    const win = wins.find(w => new URL(w.url).origin === self.location.origin);
    if (win) {
      win.postMessage({ type: 'open', url });
      return win.focus();
    }
    return self.clients.openWindow(url);
  })());
});
