'use strict';
const CACHE = 'shunyan-shell-v26-manual-restart';
const SHELL = ['./service-control.js','./service-control.css','./setup-flow.js?v=2','./setup-flow.css?v=2','./', './index.html', './settings.html', './pairing.html', './styles.css', './glyph-layer.css', './assets/approved-glyph-clean.png', './assets/glyph-patch-mask.svg', './codex-component.css', './codex-component.js', './preview-customizer.css', './preview-customizer.js', './paired-devices.css', './paired-devices.js', './device-layout.js', './physical-feedback.js', './app.js', './audio-stream.js', './audio-capture-worklet.js', './settings.js', './shortcut-recorder.js', './shortcut-recorder.css', './connection-ui.js', './vendor/qrcode.js', './vendor/jsQR.js', './settings-layout.js', './pairing.js', './manifest.webmanifest', './keycaps-3d.js', './vendor/three.module.js', './vendor/three.core.js', './assets/keywell-mask.svg', './assets/key-glass-body-mask.svg', './assets/deep-pressed-blank.png', './assets/deep-clean-panel.png', './assets/approved-deep-493.png', './assets/reference-skin.png', './assets/reference-shell-mask.svg', './assets/frosted-grain.svg', './assets/screw-cross.svg', './assets/fonts/noto-sans-sc-status-0.ttf', './assets/fonts/noto-sans-sc-status-1.ttf'];
self.addEventListener('install', (event) => event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting())));
self.addEventListener('activate', (event) => event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)))).then(() => self.clients.claim())));
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin || url.pathname.includes('/api/')) return;
  if (event.request.method !== 'GET') return;
  event.respondWith(fetch(event.request).then((response) => {
    const copy = response.clone(); caches.open(CACHE).then((cache) => cache.put(event.request, copy)); return response;
  }).catch(() => caches.match(event.request).then((response) => response || caches.match('./index.html'))));
});
