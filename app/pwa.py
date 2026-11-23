"""AKSynxa V3.2 — PWA (Progressive Web App) support.

Owner apne phone par dashboard ko "Install" karke home screen se
app ki tarah khol sakta hai:

  GET /manifest.webmanifest   Web app manifest
  GET /sw.js                  Minimal service worker (dashboard shell
                              cache; baaki sab network — data hamesha
                              fresh, koi stale stats nahi)
  GET /pwa/icon-192.png       Generated placeholder icons (repo me
  GET /pwa/icon-512.png       logo image asset nahi tha, isliye brand
                              colors me simple generated PNG)

Dashboard aur business-login pages ke <head> me manifest link +
service worker registration app/templates me laga hai.
"""
import struct
import zlib

from fastapi.responses import JSONResponse, Response

_THEME = "#0b1f4a"
_BG = "#f4f6fb"

_MANIFEST = {
    "name": "AKSynxa — Business Dashboard",
    "short_name": "AKSynxa",
    "description": "WhatsApp bookings, leads aur reminders — ek jagah.",
    "start_url": "/dashboard",
    "scope": "/",
    "display": "standalone",
    "background_color": _BG,
    "theme_color": _THEME,
    "icons": [
        {"src": "/pwa/icon-192.png", "sizes": "192x192",
         "type": "image/png", "purpose": "any"},
        {"src": "/pwa/icon-512.png", "sizes": "512x512",
         "type": "image/png", "purpose": "any"},
    ],
}

_SW_JS = """// AKSynxa service worker — minimal aur safe.
// Sirf app shell (dashboard page + manifest + icon) cache hota hai;
// API/data calls hamesha network se — stats kabhi stale nahi honge.
const CACHE = "aksynxa-pwa-v1";
const CORE = ["/dashboard", "/manifest.webmanifest", "/pwa/icon-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(CORE))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))
    )).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  if (event.request.mode === "navigate") {
    event.respondWith(
      fetch(event.request)
        .then((resp) => {
          const copy = resp.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, copy));
          return resp;
        })
        .catch(() => caches.match(event.request))
    );
  }
});
"""


def _make_png(size: int) -> bytes:
    """Simple brand icon: navy background, violet center square,
    white inner block (AK sync-arrow ki jagah placeholder geometry).
    Pure stdlib — koi PIL dependency nahi."""
    navy = (11, 31, 74)
    violet = (124, 58, 237)
    white = (255, 255, 255)
    q = size // 4          # center square ka margin
    inner = size // 8      # white block ka extra margin
    rows = []
    for y in range(size):
        row = bytearray()
        in_mid_y = q <= y < size - q
        in_in_y = q + inner <= y < size - q - inner
        for x in range(size):
            if in_in_y and q + inner <= x < size - q - inner:
                row += bytes(white)
            elif in_mid_y and q <= x < size - q:
                row += bytes(violet)
            else:
                row += bytes(navy)
        rows.append(b"\x00" + bytes(row))
    raw = b"".join(rows)

    def chunk(typ: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + typ + data +
                struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


_ICON_CACHE: dict = {}


def _icon(size: int) -> bytes:
    if size not in _ICON_CACHE:
        _ICON_CACHE[size] = _make_png(size)
    return _ICON_CACHE[size]


def register_pwa(app) -> None:
    @app.get("/manifest.webmanifest", tags=["PWA"],
             summary="Web app manifest (PWA install ke liye)")
    def manifest():
        return JSONResponse(_MANIFEST,
                            media_type="application/manifest+json")

    @app.get("/sw.js", tags=["PWA"], summary="Minimal service worker")
    def service_worker():
        return Response(_SW_JS, media_type="application/javascript")

    @app.get("/pwa/icon-192.png", tags=["PWA"], include_in_schema=False)
    def icon_192():
        return Response(_icon(192), media_type="image/png")

    @app.get("/pwa/icon-512.png", tags=["PWA"], include_in_schema=False)
    def icon_512():
        return Response(_icon(512), media_type="image/png")
