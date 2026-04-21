import { defineConfig } from "vite";
import { viteStaticCopy } from "vite-plugin-static-copy";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { VitePWA } from "vite-plugin-pwa";

const COOP_COEP_HEADERS = {
  "Cross-Origin-Opener-Policy": "same-origin",
  "Cross-Origin-Embedder-Policy": "require-corp",
};

// Base path: '/' for Cloudflare Pages/local, '/repo-name/' for GitHub Pages.
const base = process.env.VITE_BASE ?? "/";

export default defineConfig({
  base,
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
    VitePWA({
      // Use injectManifest so our custom src/sw.ts handles COOP/COEP headers
      // required for SharedArrayBuffer on GitHub Pages (which cannot serve
      // custom HTTP headers).
      strategies: "injectManifest",
      srcDir: "src",
      filename: "sw.ts",
      registerType: "autoUpdate",
      injectRegister: "auto",
      manifest: {
        name: "ndlocr-lite web",
        short_name: "ndlocr-lite",
        description: "National Diet Library OCR — runs entirely in your browser, no server needed.",
        theme_color: "#1d4ed8",
        background_color: "#f8fafc",
        display: "standalone",
        start_url: base,
        icons: [
          {
            src: "icons/icon.svg",
            sizes: "any",
            type: "image/svg+xml",
            purpose: "any",
          },
        ],
      },
      injectManifest: {
        globPatterns: [
          "**/*.{js,css,html}",
          "ort/*.{wasm,mjs,js}",
          "wheels/*.whl",
          "manifest.json",
        ],
        maximumFileSizeToCacheInBytes: 30 * 1024 * 1024,
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
