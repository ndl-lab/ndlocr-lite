/// <reference lib="webworker" />

type ManifestEntry = { url: string; revision: string | null };

declare let self: ServiceWorkerGlobalScope & {
  __WB_MANIFEST: ReadonlyArray<ManifestEntry | string>;
};

// ── Cache names ───────────────────────────────────────────────────────────────
const PRECACHE = "ndlocr-lite-precache-v1";
const RUNTIME = "ndlocr-lite-runtime-v1";

// ── Precache URL list (injected by VitePWA at build time) ─────────────────────
const PRECACHE_URLS = self.__WB_MANIFEST.map((e) =>
  typeof e === "string" ? e : e.url,
);

// ── Lifecycle ─────────────────────────────────────────────────────────────────
self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(PRECACHE).then((cache) => cache.addAll(PRECACHE_URLS)),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((k) => k !== PRECACHE && k !== RUNTIME)
            .map((k) => caches.delete(k)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

// ── COOP/COEP header injection ────────────────────────────────────────────────
// GitHub Pages cannot serve custom HTTP headers, so the service worker
// re-wraps every response with the headers required for SharedArrayBuffer.
function withCOI(response: Response): Response {
  if (response.status === 0) return response;
  const headers = new Headers(response.headers);
  headers.set("Cross-Origin-Embedder-Policy", "require-corp");
  headers.set("Cross-Origin-Resource-Policy", "cross-origin");
  headers.set("Cross-Origin-Opener-Policy", "same-origin");
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

// ── Fetch handler ─────────────────────────────────────────────────────────────
self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  if (request.cache === "only-if-cached" && request.mode !== "same-origin")
    return;

  event.respondWith(
    (async () => {
      // 1. Serve from precache (versioned assets)
      const precached = await caches.match(request, { cacheName: PRECACHE });
      if (precached) return withCOI(precached);

      // 2. Navigation fallback → index.html from precache (SPA routing)
      if (request.mode === "navigate") {
        const indexUrl = new URL(
          import.meta.env.BASE_URL + "index.html",
          self.location.href,
        ).href;
        const index = await caches.match(new Request(indexUrl), {
          cacheName: PRECACHE,
        });
        if (index) return withCOI(index);
      }

      // 3. Runtime cache (same-origin resources not in precache)
      const runtimeCached = await caches.match(request, {
        cacheName: RUNTIME,
      });
      if (runtimeCached) return withCOI(runtimeCached);

      // 4. Network with optional runtime caching
      const response = await fetch(request);
      if (response.ok && new URL(request.url).origin === self.location.origin) {
        const cache = await caches.open(RUNTIME);
        cache.put(request, response.clone());
      }
      return withCOI(response);
    })(),
  );
});
