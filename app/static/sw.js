// Service Worker for Forge Communicator
// Handles push notifications and offline caching for PWA
// iOS 16.4+ and Android Chrome support Web Push when installed as PWA

// Cache name is checked against server version on each page load
// When a new version is deployed, the old cache is automatically cleared
let CACHE_NAME = 'forge-communicator-v12';
const OFFLINE_URL = '/offline';

// Static assets to cache for offline use
const STATIC_ASSETS = [
    '/',
    '/offline',
    '/static/app.js',
    '/static/chirp.mp3',
    '/static/favicon.svg',
    '/static/forge-logo.png',
    '/manifest.json',
    '/auth/login'
];

// Check server version and update cache name if needed
async function checkVersion() {
    try {
        // Add timeout to prevent hanging
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 5000);
        
        const response = await fetch('/version', { 
            cache: 'no-store',
            signal: controller.signal 
        });
        clearTimeout(timeoutId);
        
        if (response.ok) {
            const data = await response.json();
            if (data.cache_key) {
                CACHE_NAME = data.cache_key;
                return data;
            }
        }
    } catch (e) {
        console.log('[SW] Could not check version:', e.message || e);
    }
    return null;
}

// Install event - activate immediately, cache in background
self.addEventListener('install', (event) => {
    console.log('[SW] Service worker installing...');
    // Skip waiting immediately - don't block on caching
    self.skipWaiting();
    
    // Cache assets in background (don't block activation)
    event.waitUntil(
        checkVersion()
            .then(() => caches.open(CACHE_NAME))
            .then((cache) => {
                console.log('[SW] Caching static assets with cache:', CACHE_NAME);
                // Cache what we can, don't fail if some assets aren't available
                return Promise.allSettled(
                    STATIC_ASSETS.map(url => 
                        cache.add(url).catch(err => console.log(`[SW] Failed to cache ${url}:`, err))
                    )
                );
            })
            .catch(err => console.log('[SW] Install caching error (non-blocking):', err))
    );
});

// Activate event - claim clients immediately, clean up old caches in background
self.addEventListener('activate', (event) => {
    console.log('[SW] Service worker activating...');
    // Claim clients immediately
    event.waitUntil(clients.claim());
    
    // Clean up old caches in background
    caches.keys()
        .then((cacheNames) => {
            return Promise.all(
                cacheNames
                    .filter((name) => name !== CACHE_NAME)
                    .map((name) => {
                        console.log('[SW] Deleting old cache:', name);
                        return caches.delete(name);
                    })
            );
        })
        .catch(err => console.log('[SW] Cache cleanup error (non-blocking):', err));
});

// Fetch event - network first, fallback to cache
self.addEventListener('fetch', (event) => {
    // Skip non-GET requests
    if (event.request.method !== 'GET') return;
    
    // Skip cross-origin requests
    if (!event.request.url.startsWith(self.location.origin)) return;
    
    // Skip API requests (don't cache dynamic data)
    if (event.request.url.includes('/api/') || 
        event.request.url.includes('/push/') ||
        event.request.url.includes('/auth/') && !event.request.url.includes('/login')) {
        return;
    }
    
    event.respondWith(
        fetch(event.request)
            .then((response) => {
                // Clone the response before caching
                if (response.status === 200) {
                    const responseClone = response.clone();
                    caches.open(CACHE_NAME)
                        .then((cache) => cache.put(event.request, responseClone));
                }
                return response;
            })
            .catch(() => {
                // Offline - try to return cached version
                return caches.match(event.request)
                    .then((cachedResponse) => {
                        if (cachedResponse) {
                            return cachedResponse;
                        }
                        // For navigation requests, return offline page
                        if (event.request.mode === 'navigate') {
                            return caches.match(OFFLINE_URL);
                        }
                        // Return a simple offline response for other requests
                        return new Response('Offline', { status: 503, statusText: 'Service Unavailable' });
                    });
            })
    );
});

// Push notification received
self.addEventListener('push', (event) => {
    console.log('[SW] Push notification received');
    
    let data = {
        title: 'Forge Communicator',
        body: 'You have a new message',
        icon: '/static/icons/icon-192x192.png',
        badge: '/static/icons/icon-96x96.png',
        data: { url: '/' }
    };
    
    if (event.data) {
        try {
            // Read the data as text first (can only read once)
            const rawText = event.data.text();
            console.log('[SW] Raw push data:', rawText);
            
            // Parse the JSON from the text
            const parsed = JSON.parse(rawText);
            console.log('[SW] Parsed push data:', JSON.stringify(parsed));
            data = { ...data, ...parsed };
        } catch (e) {
            console.error('[SW] Error parsing push data:', e);
        }
    }
    
    console.log('[SW] Final notification - title:', data.title, 'body:', data.body);
    
    // iOS Safari has limited notification options - use compatible subset
    const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) || 
                  (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    
    const options = {
        body: data.body,
        icon: data.icon,
        badge: data.badge,
        tag: data.tag || 'forge-notification',
        data: data.data,
        // Let system notification play sound (works even when app is closed)
        silent: false,
        // iOS doesn't support these - only include on non-iOS
        ...(isIOS ? {} : {
            renotify: true,
            requireInteraction: true,  // Keep notification visible until user interacts
            vibrate: [100, 50, 100, 50, 100],
            actions: [
                { action: 'open', title: 'Open' },
                { action: 'dismiss', title: 'Dismiss' }
            ]
        })
    };
    
    console.log('[SW] Notification options:', JSON.stringify(options));
    
    // Show notification and play sound
    event.waitUntil(
        self.registration.showNotification(data.title, options)
            .then(() => {
                // Notify all clients to play in-app sound as backup
                return self.clients.matchAll({ type: 'window', includeUncontrolled: true });
            })
            .then((clients) => {
                clients.forEach((client) => {
                    client.postMessage({
                        type: 'PUSH_RECEIVED',
                        title: data.title,
                        body: data.body,
                        url: data.data?.url
                    });
                });
            })
    );
});

// Notification click handler
self.addEventListener('notificationclick', (event) => {
    console.log('[SW] Notification clicked');
    event.notification.close();
    
    if (event.action === 'dismiss') {
        return;
    }
    
    const urlToOpen = event.notification.data?.url || '/';
    
    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true })
            .then((clientList) => {
                // Check if there's already a window open
                for (const client of clientList) {
                    if (client.url.includes(self.location.origin) && 'focus' in client) {
                        client.navigate(urlToOpen);
                        return client.focus();
                    }
                }
                // Open a new window if none exists
                if (clients.openWindow) {
                    return clients.openWindow(urlToOpen);
                }
            })
    );
});

// Handle notification close
self.addEventListener('notificationclose', (event) => {
    console.log('[SW] Notification closed');
});
// Message handler for cache management from the client
self.addEventListener('message', (event) => {
    console.log('[SW] Received message:', event.data);
    
    if (event.data && event.data.type === 'CLEAR_CACHE') {
        // Clear all caches and reload
        event.waitUntil(
            caches.keys()
                .then((cacheNames) => {
                    console.log('[SW] Clearing all caches:', cacheNames);
                    return Promise.all(
                        cacheNames.map((name) => caches.delete(name))
                    );
                })
                .then(() => {
                    console.log('[SW] All caches cleared');
                    // Notify all clients that cache was cleared
                    return self.clients.matchAll();
                })
                .then((clients) => {
                    clients.forEach((client) => {
                        client.postMessage({ type: 'CACHE_CLEARED' });
                    });
                })
        );
    }
    
    if (event.data && event.data.type === 'CHECK_UPDATE') {
        // Check for updates and notify client
        event.waitUntil(
            checkVersion()
                .then((versionInfo) => {
                    return self.clients.matchAll();
                })
                .then((clients) => {
                    clients.forEach((client) => {
                        client.postMessage({ type: 'VERSION_INFO', cache_name: CACHE_NAME });
                    });
                    // Trigger SW update check
                    return self.registration.update();
                })
        );
    }
    
    if (event.data && event.data.type === 'SKIP_WAITING') {
        self.skipWaiting();
    }
});