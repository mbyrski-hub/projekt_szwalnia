// Aplikacja: wersja 1.1

// === NOWY KOD DO OBSŁUGI AKTUALIZACJI ===
self.addEventListener('message', (event) => {
    if (event.data && event.data.type === 'SKIP_WAITING') {
        self.skipWaiting();
    }
});
// === KONIEC NOWEGO KODU ===

// === OBSŁUGA POWIADOMIEŃ PUSH ===
self.addEventListener('push', function(event) {
    let data = { title: 'Domyślny tytuł', body: 'Brak treści', target_url: '/' };
    
    try {
        const receivedData = event.data.json();
        data.title = receivedData.title || 'Nowe powiadomienie';
        data.body = receivedData.body || 'Otrzymano nowe powiadomienie.';
        data.target_url = receivedData.target_url || '/'; 
    } catch (e) {
        console.error('Błąd parsowania JSON w powiadomieniu push, treść:', event.data.text());
        data.title = 'Nowe powiadomienie';
        data.body = event.data.text();
    }

    const options = {
        body: data.body,
        icon: '/static/mobile_assets/icon_szwalnia_192.png',
        badge: '/static/mobile_assets/icon_szwalnia_192.png',
        data: {
            url: data.target_url
        }
    };

    event.waitUntil(
        self.registration.showNotification(data.title, options)
    );
});


// === OBSŁUGA KLIKNIĘCIA W POWIADOMIENIE ===
self.addEventListener('notificationclick', function(event) {
    event.notification.close();
    const targetUrl = event.notification.data.url;

    // Ten kod szuka otwartej aplikacji i ją aktywuje, a jeśli jest zamknięta - otwiera nową.
    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function(clientList) {
            for (let i = 0; i < clientList.length; i++) {
                let client = clientList[i];
                if (client.url === targetUrl && 'focus' in client) {
                    return client.focus();
                }
            }
            if (clients.openWindow) {
                return clients.openWindow(targetUrl);
            }
        })
    );
});


// === INTELIGENTNA OBSŁUGA GŁĘBOKICH LINKÓW (DEEP LINKING) PO SKANIE QR ===
self.addEventListener('fetch', event => {
    const url = new URL(event.request.url);

    if (event.request.mode === 'navigate' && url.origin === self.origin && url.pathname.startsWith('/show_order/')) {
        event.respondWith((async () => {
            const orderId = url.pathname.split('/').pop();
            const allClients = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });

            const szwalniaClient = allClients.find(c => c.url.includes('/mobile/szwalnia'));
            if (szwalniaClient) {
                await szwalniaClient.focus();
                szwalniaClient.postMessage({ type: 'SHOW_ORDER', orderId: orderId });
                return Response.redirect(szwalniaClient.url, 302);
            }

            const krojowniaClient = allClients.find(c => c.url.includes('/mobile/krojownia'));
            if (krojowniaClient) {
                await krojowniaClient.focus();
                krojowniaClient.postMessage({ type: 'SHOW_ORDER', orderId: orderId });
                return Response.redirect(krojowniaClient.url, 302);
            }

            return fetch(event.request);
        })());
    }
});