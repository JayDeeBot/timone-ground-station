/* static/sw.js */
const CACHE_NAME = 'timonegui-static-v3'; // bump to force update

const PRECACHE = [
  // HTML shell
  '/',

  // Vendor CSS/JS
  '/static/vendor/bootstrap-5.3.0/css/bootstrap.min.css',
  '/static/vendor/bootstrap-5.3.0/js/bootstrap.bundle.min.js',
  '/static/vendor/bootstrap-icons-1.7.2/bootstrap-icons.css',
  '/static/vendor/bootstrap-icons-1.7.2/fonts/bootstrap-icons.woff2',
  '/static/vendor/bootstrap-icons-1.7.2/fonts/bootstrap-icons.woff',
  '/static/vendor/leaflet-1.7.1/leaflet.css',
  '/static/vendor/leaflet-1.7.1/leaflet.js',
  '/static/vendor/leaflet-1.7.1/images/marker-icon.png',
  '/static/vendor/leaflet-1.7.1/images/marker-icon-2x.png',
  '/static/vendor/leaflet-1.7.1/images/marker-shadow.png',
  '/static/vendor/chart.js-4/chart.umd.min.js',

  // Your CSS
  '/static/css/main.css',

  // Your JS
  '/static/js/graph-interactions.js',
  '/static/js/main.js',
  '/static/js/historical.js',
  '/static/js/maps.js',
  '/static/js/logs.js',
  '/static/js/settings.js',
  '/static/js/telemetry.js',  // <-- added

  // Images frequently used
  '/static/images/favicon.ico',
  // '/static/images/ground-station.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then(async keys => {
      await Promise.all(
        keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
      );
      await self.clients.claim();
    })
  );
});

// Optional: allow manual skipWaiting from the page
self.addEventListener('message', (event) => {
  if (event.data === 'SKIP_WAITING') self.skipWaiting();
});

// Cache-first for same-origin static assets; network for APIs
self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // --- IMPORTANT: completely bypass SSE / event-streams ---
  const accept = req.headers.get('accept') || '';
  if (
    accept.includes('text/event-stream') ||
    url.pathname === '/api/logs/stream' ||
    url.pathname === '/api/telemetry/stream'   // <-- added explicit bypass
  ) {
    return; // let the browser hit network directly
  }

  // Only handle GET
  if (req.method !== 'GET') return;

  // API calls: let them hit network (no SW caching)
  if (url.pathname.startsWith('/api/')) {
    return; // network default
  }

  // Same-origin static: cache-first
  if (url.origin === self.origin) {
    event.respondWith(
      caches.match(req).then(cached => {
        if (cached) return cached;
        return fetch(req).then(resp => {
          const copy = resp.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(req, copy));
          return resp;
        }).catch(() => cached);
      })
    );
  }
});
