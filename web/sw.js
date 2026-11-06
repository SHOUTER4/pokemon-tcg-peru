// Service worker de la web instalable (PWA): avisos push de subastas y la página sin conexión.
// Los avisos los manda la PC (fb_subastas.py / notificar.py) aunque la app esté cerrada.
const CACHE = 'tcg-peru-v1';

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(['./', 'icono-192.png'])).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// Página: primero la red (datos del día); sin conexión, la última copia guardada.
self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET' || new URL(req.url).origin !== location.origin) return;
  e.respondWith(fetch(req).then(r => {
    if (r.ok && req.mode === 'navigate') {
      const copia = r.clone();
      caches.open(CACHE).then(c => c.put('./', copia));
    }
    return r;
  }).catch(() => caches.match(req.mode === 'navigate' ? './' : req)));
});

self.addEventListener('push', e => {
  let d = {};
  try { d = e.data.json(); } catch { d = { titulo: 'Subastas', cuerpo: e.data ? e.data.text() : '' }; }
  e.waitUntil(self.registration.showNotification(d.titulo || 'Subastas', {
    body: d.cuerpo || '', icon: 'icono-192.png', badge: 'icono-192.png',
    tag: d.etiqueta || undefined, renotify: !!d.etiqueta, data: { url: d.url || './#subastas' },
    vibrate: [200, 100, 200],
  }));
});

// Tocar el aviso abre la subasta (link de Facebook) o la web.
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = e.notification.data && e.notification.data.url || './#subastas';
  e.waitUntil(self.clients.openWindow(url));
});
