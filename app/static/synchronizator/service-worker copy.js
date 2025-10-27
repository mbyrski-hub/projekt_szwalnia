// Aplikacja: wersja 1.2.4 (z poprawioną obsługą powiadomień)
const SW_VERSION = '1.2.4';

self.addEventListener('message', (event) => {
    if (event.data && event.data.type === 'SKIP_WAITING') {
        self.skipWaiting();
    }
    // ### NOWY KOD - ODPOWIADANIE NA PYTANIE O WERSJĘ ###
    if (event.data && event.data.type === 'GET_VERSION') {
        // Odpowiedz klientowi, wysyłając swoją wersję
        event.source.postMessage({ type: 'VERSION_RESPONSE', version: SW_VERSION });
    }
});

// === OBSŁUGA POWIADOMIEŃ PUSH (POPRAWIONA) ===
self.addEventListener('push', function(event) {
    let data = { title: 'Nowe powiadomienie', body: '', target_url: '/', app_context: 'szwalnia' };
    
    try {
        const receivedData = event.data.json();
        data.title = receivedData.title || 'Nowe powiadomienie';
        data.body = receivedData.body || 'Otrzymano nowe powiadomienie.';
        data.target_url = receivedData.target_url || '/'; 
        data.app_context = receivedData.app_context || 'szwalnia'; // Zapisujemy kontekst aplikacji
    } catch (e) {
        console.error('Błąd parsowania JSON w powiadomieniu push, treść:', event.data.text());
        data.body = event.data.text();
    }

    const options = {
        body: data.body,
        icon: '/static/mobile_assets/icon_szwalnia_192.png',
        badge: '/static/mobile_assets/icon_szwalnia_192.png',
        data: { // Przekazujemy wszystkie potrzebne dane do obsługi kliknięcia
            url: data.target_url,
            app_context: data.app_context
        }
    };

    event.waitUntil(
        self.registration.showNotification(data.title, options)
    );
});


// === OBSŁUGA KLIKNIĘCIA W POWIADOMIENIE (NOWA, NIEZAWODNA WERSJA) ===
self.addEventListener('notificationclick', function(event) {
    event.notification.close();
    
    const payload = event.notification.data;
    const targetUrl = payload.url; // np. '/show_order/123'
    const appContext = payload.app_context || 'szwalnia'; // 'krojownia' lub 'szwalnia'

    let orderId = null;
    if (targetUrl && targetUrl.startsWith('/show_order/')) {
        orderId = targetUrl.split('/').pop();
    }

    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function(clientList) {
            // Szukamy jakiegokolwiek otwartego okna naszej aplikacji
            const appClient = clientList.find(c => 
                c.url.includes('/mobile/krojownia') || c.url.includes('/mobile/szwalnia')
            );

            if (appClient) {
                // PRZYPADEK 1: Aplikacja jest już otwarta (w tle)
                console.log('Aplikacja jest otwarta, aktywuję i wysyłam zlecenie...');
                appClient.focus();
                if (orderId) {
                    appClient.postMessage({ type: 'SHOW_ORDER', orderId: orderId });
                }
                return;
            }

            if (clients.openWindow) {
                // PRZYPADEK 2: Aplikacja jest zamknięta
                console.log(`Aplikacja jest zamknięta, otwieram kontekst: ${appContext}`);
                const appUrl = `/mobile/${appContext}`;
                // Otwieramy właściwą aplikację, dodając ID zlecenia w hashu, aby mogła je odczytać
                const finalUrlToOpen = orderId ? `${appUrl}#showOrder=${orderId}` : appUrl;
                return clients.openWindow(finalUrlToOpen);
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
            
            // Jeśli żadna aplikacja nie jest otwarta, przekieruj na stronę publiczną
            return fetch(event.request);
        })());
    }
});