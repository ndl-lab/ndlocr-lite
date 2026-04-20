import { defineConfig } from "vite";
import { viteStaticCopy } from "vite-plugin-static-copy";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { VitePWA } from "vite-plugin-pwa";

const COOP_COEP_HEADERS = {
  "Cross-Origin-Opener-Policy": "same-origin",
  "Cross-Origin-Embedder-Policy": "require-corp",
};

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    viteStaticCopy({
      targets: [
        {
          // Copy onnxruntime-web WASM/ESM files into public/ort/ so the
          // runtime can locate them via ort.env.wasm.wasmPaths at runtime.
          src: "node_modules/onnxruntime-web/dist/*.{wasm,mjs,js}",
          dest: "ort",
        },
      ],
    }),
    // T5-6a: PWA Service Worker — precaches UI assets + ORT WASM files.
    // ONNX model files (~150 MB) and Pyodide packages are NOT precached here;
    // they are handled by the existing Cache Storage logic in modelCache.ts.
    VitePWA({
      registerType: "autoUpdate",
      // Inject the SW registration snippet into index.html automatically.
      injectRegister: "auto",
      // T5-6c: App manifest for installable PWA (home screen / desktop).
      manifest: {
        name: "ndlocr-lite web",
        short_name: "ndlocr-lite",
        description: "National Diet Library OCR — runs entirely in your browser, no server needed.",
        theme_color: "#1d4ed8",
        background_color: "#f8fafc",
        display: "standalone",
        start_url: "/",
        icons: [
          {
            src: "icons/icon.svg",
            sizes: "any",
            type: "image/svg+xml",
            purpose: "any",
          },
        ],
      },
      workbox: {
        // Only precache UI bundle + ORT WASM (small files, fast load).
        // Glob patterns are relative to the build output directory.
        globPatterns: [
          "**/*.{js,css,html}",
          "ort/*.{wasm,mjs,js}",
          "wheels/*.whl",
          "manifest.json",
        ],
        // ORT WASM files can exceed Workbox's 2 MB default limit.
        maximumFileSizeToCacheInBytes: 30 * 1024 * 1024,
        // The SW itself must be served with COOP/COEP headers so it can
        // create SharedArrayBuffer-backed WASM threads.  Vite preview/dev
        // already set these headers; for production nginx/CDN config see
        // docs/pyodide-port/phase-6-release.md.
        navigateFallback: "/index.html",
      },
    }),
  ],

  server: {
    headers: COOP_COEP_HEADERS,
  },

  preview: {
    headers: COOP_COEP_HEADERS,
  },

  build: {
    target: "es2022",
    rollupOptions: {
      // Treat onnxruntime-web as external so the WASM files loaded at runtime
      // are picked up from public/ort/ rather than bundled inline.
      external: [],
      output: {
        // T5-5c: Split heavy vendor libraries into separate cached chunks so
        // the ~100 kB UI bundle stays cacheable across minor app updates.
        manualChunks: {
          "vendor-react":   ["react", "react-dom"],
          "vendor-comlink": ["comlink"],
          "vendor-zustand": ["zustand"],
          "vendor-jszip":   ["jszip"],
          "vendor-idb":     ["idb-keyval"],
        },
      },
    },
  },

  worker: {
    // Bundle workers as ES modules (required for Comlink expose/wrap and
    // for dynamic imports inside the Pyodide worker).
    format: "es",
  },
});
