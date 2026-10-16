const CACHE = 'trapguard-shell-v1';
const SHELL = ['/', '/static/index.html', '/static/style.css', '/static/app.js', '/static/manifest.json'];
self.addEventListener('install', event => event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL))));
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/verify/') || url.pathname.startsWith('/snapshots/')) return;
  event.respondWith(caches.match(event.request).then(cached => cached || fetch(event.request)));
});
