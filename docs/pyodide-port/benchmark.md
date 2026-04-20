# Phase 4 Baseline Benchmarks

Recorded on 2026-04-19 at Phase 4 completion. These are the numbers Phase 5 targets to improve.

## Methodology

- Browser: Chrome 124 on Apple M2 MacBook Air 16 GB RAM
- Image: NDL sample scan, 2500×3600 px (A4 300 dpi)
- Measurement: Chrome DevTools Network + Performance panels
- Memory: `performance.memory.usedJSHeapSize` peak (Chrome only)
- Repeated 3 times; median reported

## Phase 4 Baseline Numbers

| Metric | Value | Phase 5 Target |
|--------|-------|----------------|
| **Initial load — model bytes** | ~154 MB (4 ONNX files, uncompressed) | ≤75 MB (50%↓ via quantization + Brotli) |
| **Initial load — total time (cold, desktop)** | ~45 s (model download on 50 Mbps) | ≤20 s (Brotli + CDN) |
| **2nd visit cold-start (models cached)** | ~8 s (Pyodide boot + pkg install) | ≤3 s (SW precache) |
| **DEIM inference / page** | ~2.1 s (WASM, M2) | ≤1.0 s (WebGPU or INT8) |
| **PARSeq total / page** | ~18 s (3× serial, ~80 lines) | ≤8 s (parallel workers) |
| **XY-Cut (reading order)** | ~0.4 s | ≤0.4 s (pure Python, already fast) |
| **Total OCR wall time / page** | ~21 s | ≤10 s |
| **JS heap peak** | ~1.1 GB | ≤700 MB |
| **JS bundle size (gzip)** | 100 kB JS + 4 kB CSS | ≤80 kB (manualChunks) |
| **Lighthouse Performance score** | 52 (TTI penalised by 45 s load) | ≥70 |
| **PWA: installable** | No | Yes |
| **PWA: offline (2nd visit)** | No | Yes |

## Model Sizes (Phase 4 FP32)

| Model | Size |
|-------|------|
| deim-s-1024x1024.onnx | 38.4 MB |
| parseq rec30 | 34.2 MB |
| parseq rec50 | 35.2 MB |
| parseq rec100 | 39.1 MB |
| **Total** | **146.9 MB** |

## Phase 5 Progress

Updated as tasks complete.

| Task | Status | Impact |
|------|--------|--------|
| ESLint setup | ✅ done | CI readiness |
| T5-2a WASM SIMD explicit | ✅ done | Baseline confirmed |
| T5-4c Pre-scale >4 k images | ✅ done | Memory -20% for large scans |
| T5-5c Vite manualChunks | ✅ done | Bundle -10 kB gzip |
| T5-6a/b/c PWA + SW | ✅ done | 2nd visit ≤3 s, offline OK |
| T5-7a Python gc.collect | ✅ done | Heap -150 MB |
| T5-7b ORT release API | ✅ done | Post-OCR heap -400 MB option |
| T5-7c Low-memory warning | ✅ done | UX on 4 GB devices |
| T5-1a INT8 quantization | ⏳ pending | Model 50%↓ (requires Python env) |
| T5-1b FP16 for WebGPU | ⏳ pending | Inference 2×↑ on GPU |
| T5-2b WebGPU EP | ✅ done (Phase 2/4) | Already in ortSession.ts |
| T5-2c Parallel PARSeq Workers | ⏳ pending | PARSeq 3×↑ (complex refactor) |
| T5-0b Playwright regression | ⏳ pending | CI performance gating |
