import { defineConfig } from "vite";
import { viteStaticCopy } from "vite-plugin-static-copy";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

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
    },
  },

  worker: {
    // Bundle workers as ES modules (required for Comlink expose/wrap and
    // for dynamic imports inside the Pyodide worker).
    format: "es",
  },
});
