# NDLOCR-Lite Web — アーキテクチャ

Phase 0〜5 の実装決定をまとめた全体構成ドキュメントです。

## コンポーネント図

```
┌──────────────────────────── Browser ─────────────────────────────────────┐
│                                                                           │
│  ┌─────────────────── Main Thread ──────────────────────────────────┐    │
│  │  React 19 SPA (Vite 6 + Tailwind CSS v4 + Zustand)               │    │
│  │                                                                   │    │
│  │  App.tsx ─ state machine: init → loading → ready → ocr → done    │    │
│  │                                                                   │    │
│  │  DropZone          ImageViewer         ResultTabs                 │    │
│  │  LoadProgress      DownloadButtons     Header/Footer              │    │
│  │                                                                   │    │
│  │  OcrClient.ts ──Comlink──────────────────────────────────┐        │    │
│  └───────────────────────────────────────────────────────────┼────────┘    │
│                                                              │             │
│  ┌─────────── Web Worker (pyodide.worker.ts) ────────────────┘             │
│  │                                                                         │
│  │  Pyodide 0.27.x (WASM)                                                  │
│  │    └─ ndlocr_web (Python wheel, micropip)                               │
│  │         ├─ pipeline.run_ocr_on_image()                                  │
│  │         ├─ detector.DEIMDetector        ◄── callback ── ORT session     │
│  │         ├─ recognizer.PARSeqRecognizer  ◄── callback ── ORT session     │
│  │         ├─ cascade / imgops / xml_builder / reading_order               │
│  │         └─ bridge.py (JS ↔ Python 非同期コールバック)                   │
│  │                                                                         │
│  │  onnxruntime-web 1.22 (WASM SIMD + WebGPU)                              │
│  │    ├─ ortSession.ts   — InferenceSession 管理・キャッシュ               │
│  │    ├─ detector.ts     — DEIM v2 前後処理                                │
│  │    └─ recognizer.ts   — PARSeq ×3 (rec30/rec50/rec100) 前後処理         │
│  │                                                                         │
│  └─────────────────────────────────────────────────────────────────────────┘
│                                                                            │
│  ┌────── Service Worker (Workbox) ─────────────────────────────────────┐   │
│  │  UI bundle precache (JS/CSS/HTML)                                    │   │
│  │  ORT WASM precache (*.wasm, *.mjs)                                   │   │
│  │  ndlocr_web wheel precache (*.whl)                                   │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                            │
│  Cache Storage API ── ONNX モデル (~150 MB, modelCache.ts が管理)          │
│  IndexedDB (idb-keyval) ── モデル fallback (Safari Private Mode 等)         │
└────────────────────────────────────────────────────────────────────────────┘

  GitHub Releases ── ONNX モデル・manifest.json・ndlocr_web wheel
  jsDelivr CDN ──── Pyodide runtime (pyodide@0.27.2)
```

## 初期化シーケンス

```
Main Thread                    Worker                       Network
     │                            │                            │
     │  new OcrClient()           │                            │
     │  ─── fetch manifest.json ──────────────────────────────►│
     │  ◄── ModelManifest ────────────────────────────────────│
     │                            │                            │
     │  api.init(progress, mf) ──►│                            │
     │                            │── load Pyodide WASM ──────►│
     │  ◄── progress(pyodide,%) ─◄│◄── pyodide.js ────────────│
     │                            │                            │
     │                            │── micropip.install(pkgs) ─►│
     │  ◄── progress(packages,%) ◄│◄── numpy/Pillow/lxml/…────│
     │                            │                            │
     │                            │── micropip.install(wheel) ►│
     │  ◄── progress(wheel,%) ───◄│◄── ndlocr_web-*.whl ──────│
     │                            │                            │
     │                            │── ensureModel(deim,…) ────►│
     │                            │── ensureModel(rec30,…) ───►│
     │                            │── ensureModel(rec50,…) ───►│
     │  ◄── progress(models,%) ──◄│── ensureModel(rec100,…) ──►│
     │                            │◄── *.onnx (Cache Storage) ─│
     │                            │                            │
     │                            │── InferenceSession.create()│
     │  ◄── progress(init,100%) ─◄│                            │
     │                            │                            │
     │  isReady = true            │                            │
```

## OCR 実行シーケンス

```
Main Thread                    Worker
     │                            │
     │  client.ocr(file, {viz})   │
     │  createImageBitmap(file)   │
     │  api.ocr(bitmap, …) ──────►│
     │  (transfer bitmap)         │
     │                            │── ImageBitmap → numpy array (bridge.py)
     │                            │── run_ocr_on_image(img, detector, rec×3)
     │                            │     ├─ DEIMDetector.detect()
     │                            │     │     └─ jsDeimInfer() → ORT session
     │                            │     ├─ PARSeqRecognizer.recognize()
     │                            │     │     └─ jsParseqInfer() → ORT session
     │                            │     ├─ cascade / reading_order
     │                            │     └─ xml_builder → XML string
     │  ◄── OcrResult ───────────◄│
     │  { xml, text, json, vizPng}│
```

## 技術選定の根拠（Phase 0 まとめ）

| 決定 | 選択 | 根拠 |
|------|------|------|
| フロントエンド FW | React 19 + Vite 6 | エコシステム成熟度・TypeScript サポート |
| CSS | Tailwind CSS v4 | ゼロランタイム・バンドルサイズ |
| 状態管理 | Zustand 5 | 軽量・Hooks フレンドリー |
| Worker RPC | Comlink 4 | Proxy ベースで型安全・async/await |
| WASM Python | Pyodide 0.27 | numpy/Pillow/lxml 対応・ブラウザ実績 |
| ONNX 推論 | onnxruntime-web 1.22 | WebGPU + WASM SIMD・公式サポート |
| モデル配信 | GitHub Releases | 無料・SHA-256 検証・CDN 不要 |
| ストレージ | Cache Storage API | 大容量・オフライン対応 |
| PWA | vite-plugin-pwa (Workbox) | プリキャッシュ + installable |
| パッケージマネージャ | pnpm 9 | 高速・lockfile 厳密 |
| ホスティング | Cloudflare Pages | COOP/COEP 設定可・無料枠 |

## セキュリティヘッダ

SharedArrayBuffer（WASM スレッディング）に必須：

```
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
Content-Security-Policy: (省略 — Phase 0 T0-6 参照)
```

ONNX モデル・Pyodide パッケージが外部オリジンから読み込まれるため、CSP の `connect-src` に以下のオリジンを許可すること：

- `https://github.com` / `https://objects.githubusercontent.com`（GitHub Releases）
- `https://cdn.jsdelivr.net`（Pyodide CDN）
- `https://files.pythonhosted.org`（micropip fallback）
