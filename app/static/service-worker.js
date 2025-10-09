// app/static/service-worker.js

// === OBSŁUGA POWIADOMIEŃ PUSH ===
self.addEventListener('push', function(event) {
    const data = event.data.json();
    const options = {
        body: data.body,
        icon: '/static/mobile_assets/icon_szwalnia_192.png', // Domyślna ikona
        badge: '/static/mobile_assets/icon_szwalnia_192.png'
    };
    event.waitUntil(
        self.registration.showNotification(data.title, options)
    );
});

self.addEventListener('notificationclick', function(event) {
    event.notification.close();
    // Po kliknięciu powiadomienia, otwórz główną stronę aplikacji
    event.waitUntil(
        clients.openWindow('/')
    );
});

// === OBSŁUGA GŁĘBOKICH LINKÓW (DEEP LINKING) PO SKANIE QR ===
self.addEventListener('fetch', event => {
    // Sprawdzamy, czy żądanie jest nawigacją do naszej specjalnej strony
    if (event.request.mode === 'navigate') {
        const url = new URL(event.request.url);

        // Jeśli link pasuje do wzorca /show_order/...
        if (url.origin === self.origin && url.pathname.startsWith('/show_order/')) {
            // Przechwytujemy to zdarzenie
            event.respondWith(
                (async () => {
                    // 1. Wyciągamy ID zlecenia z linku
                    const orderId = url.pathname.split('/').pop();
                    
                    // 2. Otwieramy główny widok aplikacji PWA (np. krojowni)
                    // Można by tu dodać logikę, która decyduje, czy otworzyć krojownię czy szwalnię
                    const appUrl = '/mobile/krojownia';
                    const client = await self.clients.openWindow(appUrl);

                    // 3. Jeśli udało się otworzyć okno, wysyłamy do niego wiadomość
                    if (client) {
                        // Czekamy chwilę, aby aplikacja się załadowała
                        setTimeout(() => {
                            client.postMessage({ type: 'SHOW_ORDER', orderId: orderId });
                        }, 1500); // 1.5 sekundy opóźnienia
                    }
                    
                    // Zwracamy puste przekierowanie, bo faktyczne działanie wykonało się powyżej
                    return new Response(null, { status: 302, headers: { Location: appUrl } });
                })()
            );
        }
    }
});