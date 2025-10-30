// app/static/service-worker.js

// --- WERSJONOWANIE I AKTUALIZACJE ---
const CACHE_VERSION = 'v1.6.5'; // NOWA WERSJA
const CACHE_NAME = `szwalnia-cache-${CACHE_VERSION}`;
const PRECACHE_ASSETS = [];

// 1. INSTALACJA
self.addEventListener('install', (event) => {
    console.log('[SW] Zdarzenie: Instalacja', CACHE_VERSION);
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => {
                console.log("[SW] Cache'owanie wstępne zasobów...");
                return cache.addAll(PRECACHE_ASSETS).catch(err => console.warn("[SW] Błąd cache'owania wstępnego:", err));
            })
            .then(() => {
                console.log('[SW] Wymuszanie aktywacji (skipWaiting)');
                return self.skipWaiting();
            })
    );
});

// 2. AKTYWACJA
self.addEventListener('activate', (event) => {
    console.log('[SW] Zdarzenie: Aktywacja', CACHE_VERSION);
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames.map((cacheName) => {
                    if (cacheName.startsWith('szwalnia-cache-') && cacheName !== CACHE_NAME) {
                        console.log(`[SW] Usuwanie starego cache'a: ${cacheName}`);
                        return caches.delete(cacheName);
                    }
                })
            );
        }).then(() => {
            console.log('[SW] Przejęcie kontroli (clients.claim)');
            return self.clients.claim();
        })
    );
});

// 3. POBIERANIE (Fetch)
self.addEventListener('fetch', (event) => {
    // Ignoruj żądania inne niż GET
    if (event.request.method !== 'GET') {
        return;
    }

    const url = new URL(event.request.url);

    // *** POPRAWKA: Ignoruj żądania API ORAZ rozszerzeń Chrome ***
    if (url.pathname.startsWith('/api/') || url.protocol === 'chrome-extension:') {
        event.respondWith(fetch(event.request));
        return;
    }
    
    // Strategia "Network First"
    event.respondWith(
        fetch(event.request)
            .then((networkResponse) => {
                // Klonujemy OD RAZU
                const responseClone = networkResponse.clone();
                event.waitUntil(
                    caches.open(CACHE_NAME).then((cache) => {
                        // Zapisujemy KLONA do cache
                        cache.put(event.request, responseClone);
                    })
                );
                // Zwracamy ORYGINAŁ do przeglądarki
                return networkResponse;
            })
            .catch(() => {
                // Jeśli sieć zawiodła (offline), spróbuj pobrać z cache'a
                console.log(`[SW] Sieć niedostępna dla ${event.request.url}. Próba pobrania z cache'a.`);
                return caches.match(event.request);
            })
    );
});

// 4. OBSŁUGA WIADOMOŚCI (ODPOWIEDŹ Z WERSJĄ)
self.addEventListener('message', (event) => {
    if (event.data && event.data.type === 'GET_VERSION') {
        console.log(`[SW] Otrzymano zapytanie o wersję. Wysyłanie: ${CACHE_VERSION}`);
        if (event.source) {
             event.source.postMessage({ type: 'VERSION_RESPONSE', version: CACHE_VERSION });
        } else {
             self.clients.matchAll().then(clients => {
                clients.forEach(client => client.postMessage({ type: 'VERSION_RESPONSE', version: CACHE_VERSION }));
             });
        }
    }
});

// 5. POWIADOMIENIA PUSH (Odebranie)
self.addEventListener('push', (event) => {
    console.log('[SW] Otrzymano powiadomienie push!');
    let data = {};
    try { data = event.data.json(); } catch (e) { console.error('[SW] Błąd parsowania danych push:', e); data = { title: 'Błąd', body: event.data.text() }; }
    const title = data.title || 'Szwalnia HOXA';
    const options = {
        body: data.body || 'Otrzymano nowe powiadomienie.',
        icon: '/static/mobile_assets/icon_szwalnia_192.png',
        badge: '/static/mobile_assets/icon_szwalnia_192.png',
        data: { target_url: data.target_url || '/', app_context: data.app_context || 'szwalnia' }
    };
    event.waitUntil(self.registration.showNotification(title, options));
});

// 6. KLIKNIĘCIE W POWIADOMIENIE
self.addEventListener('notificationclick', (event) => {
    console.log('[SW] Kliknięto powiadomienie.');
    event.notification.close();
    const targetUrl = event.notification.data.target_url || '/';
    const appContext = event.notification.data.app_context || 'szwalnia';
    let appUrl = '/';
    if (appContext === 'krojownia') appUrl = '/mobile/krojownia';
    else if (appContext === 'szwalnia') appUrl = '/mobile/szwalnia';
    else if (appContext === 'admin') appUrl = '/mobile/admin';
    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
            for (let client of clientList) {
                if (client.url.startsWith(self.location.origin + appUrl) && 'focus' in client) {
                    console.log(`[SW] Znaleziono pasującego klienta (${appUrl}), fokusowanie.`);
                    client.navigate(targetUrl);
                    return client.focus();
                }
            }
            console.log(`[SW] Nie znaleziono klienta, otwieranie nowej karty: ${targetUrl}`);
            if (clients.openWindow) return clients.openWindow(targetUrl);
        })
    );
});