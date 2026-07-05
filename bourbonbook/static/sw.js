const CACHE = 'bourbon-book-v4';
const SHELL = ['/static/app.css', '/static/app.js', '/static/icon.svg', '/manifest.webmanifest'];
self.addEventListener('install', event => event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL))));
self.addEventListener('activate', event => event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key))))));
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (!url.pathname.startsWith('/static/') && url.pathname !== '/manifest.webmanifest') return;
  event.respondWith(caches.match(event.request).then(cached => cached || fetch(event.request)));
});
