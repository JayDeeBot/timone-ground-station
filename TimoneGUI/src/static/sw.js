/* static/sw.js */
const CACHE_NAME = 'timonegui-static-v5'; // bump to force update

const PRECACHE = [
  // HTML shell (optional; remove if your index changes frequently)
  '/',

  // Vendor CSS/JS (mostly static)
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

  // Your app JS — will be network-first at runtime (but we can still seed)
  '/static/js/main.js',
  '/static/js/historical.js',
  '/static/js/maps.js',
  '/static/js/logs.js',
  '/static/js/settings.js',
  '/static/js/telemetry.js',

  // DO NOT precache favicon.ico to avoid manifest confusion
  // '/static/images/favicon.ico',
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

// Strategy helpers
async function networkFirst(event) {
  try {
    const fresh = await fetch(event.request);
    const cache = await caches.open(CACHE_NAME);
    cache.put(event.request, fresh.clone());
    return fresh;
  } catch (e) {
    const cached = await caches.match(event.request);
    if (cached) return cached;
    throw e;
  }
}

async function cacheFirst(event) {
  const cached = await caches.match(event.request);
  if (cached) return cached;
  const fresh = await fetch(event.request);
  const cache = await caches.open(CACHE_NAME);
  cache.put(event.request, fresh.clone());
  return fresh;
}

// Cache rules
self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // --- Bypass SSE / event-streams entirely ---
  const accept = req.headers.get('accept') || '';
  if (
    accept.includes('text/event-stream') ||
    url.pathname === '/api/logs/stream' ||
    url.pathname === '/api/telemetry/stream'
  ) {
    return; // go straight to network
  }

  // Only handle GET
  if (req.method !== 'GET') return;

  // API calls: let them hit network (no SW caching)
  if (url.pathname.startsWith('/api/')) return;

  // Always network-first for manifest and sw (prevents stale)
  if (url.pathname === '/manifest.json' || url.pathname === '/sw.js') {
    event.respondWith(networkFirst(event));
    return;
  }

  // Network-first for your changing JS/CSS during development
  if (url.pathname.startsWith('/static/js/') || url.pathname.startsWith('/static/css/')) {
    event.respondWith(networkFirst(event));
    return;
  }

  // Everything else same-origin: cache-first
  if (url.origin === self.origin) {
    event.respondWith(cacheFirst(event));
  }
});
