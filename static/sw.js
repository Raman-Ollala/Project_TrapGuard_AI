const CACHE = 'trapguard-shell-v2'; // bump this number every time you change UI files
const SHELL = ['/', '/static/index.html', '/static/style.css', '/static/app.js', '/static/manifest.json'];
self.addEventListener('install', event => { self.skipWaiting(); event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL))); });
self.addEventListener('activate', event => event.waitUntil(
  caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())
));
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/verify/') || url.pathname.startsWith('/snapshots/')) return;
  event.respondWith(caches.match(event.request).then(cached => cached || fetch(event.request)));
});
