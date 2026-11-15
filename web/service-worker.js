'use strict';
const CACHE = 'shunyan-shell-v2-audio';
const SHELL = ['./', './index.html', './settings.html', './pairing.html', './styles.css', './device-layout.js', './app.js', './audio-stream.js', './audio-capture-worklet.js', './settings.js', './settings-layout.js', './pairing.js', './manifest.webmanifest'];
self.addEventListener('install', (event) => event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL))));
self.addEventListener('activate', (event) => event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))));
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin || url.pathname.includes('/api/')) return;
  if (event.request.method !== 'GET') return;
  event.respondWith(fetch(event.request).then((response) => {
    const copy = response.clone(); caches.open(CACHE).then((cache) => cache.put(event.request, copy)); return response;
  }).catch(() => caches.match(event.request).then((response) => response || caches.match('./index.html'))));
});
