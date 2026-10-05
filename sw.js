// Memoria offline: dati e pagina "prima dalla rete" (se manca la rete, l'ultima copia salvata);
// foto, librerie e pezzi di mappa già visti "prima dalla memoria".
const V = 'funghi-v1', TILES = 'funghi-mappe-v1', MAXTILES = 1500;
const BASE = ['./', 'index.html', 'data/data.json', 'data/profili_specie.json', 'data/boschi.json', 'manifest.webmanifest', 'icon-192.png',
  'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css', 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'];
self.addEventListener('install', e => { e.waitUntil(caches.open(V).then(c => c.addAll(BASE)).then(() => self.skipWaiting())) });
self.addEventListener('activate', e => { e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== V && k !== TILES).map(k => caches.delete(k)))).then(() => self.clients.claim())) });
const tile = u => /tile\.opentopomap\.org|tile\.openstreetmap\.org|arcgisonline\.com/.test(u);
async function rete(req) {
  try { const r = await fetch(req); if (r.ok) (await caches.open(V)).put(req.url.split('?')[0], r.clone()); return r }
  catch (e) { const m = await caches.match(req.url.split('?')[0]); if (m) return m; throw e }
}
async function memoria(req, nome) {
  const m = await caches.match(req, { ignoreSearch: false }); if (m) return m;
  const r = await fetch(req);
  if (r.ok || r.type === 'opaque') {
    const c = await caches.open(nome); c.put(req, r.clone());
    if (nome === TILES) c.keys().then(k => { if (k.length > MAXTILES) k.slice(0, k.length - MAXTILES).forEach(x => c.delete(x)) });
  }
  return r;
}
self.addEventListener('fetch', e => {
  const u = e.request.url; if (e.request.method !== 'GET') return;
  if (tile(u)) return e.respondWith(memoria(e.request, TILES));
  if (/\/foto\/|unpkg\.com|\.png$/.test(u)) return e.respondWith(memoria(e.request, V));
  if (u.startsWith(self.location.origin)) return e.respondWith(rete(e.request));
});
