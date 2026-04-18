# Phase 2: モデル配信 / キャッシュ & onnxruntime-web 推論基盤

ONNX モデル 4 本（合計 ~150MB）をブラウザに配信し、**onnxruntime-web** で推論するための基盤を整備する。Python 側（Pyodide）との接続は Phase 3 で扱う。本フェーズは JavaScript / TypeScript のみで完結するラッパーを独立して PoC する。

## ゴール

- `onnxruntime-web` を使って、ブラウザから 4 つのモデルを読み込み推論を走らせる最小コードが動く。
- 初回ダウンロード → Cache Storage への保存 → 2 回目以降オフライン動作が成立する。
- モデル配信元（CDN/Releases）と取得フローを確定する。
- 推論 API の TypeScript 型を Phase 3（Pyodide bridge）に提供する形で固める。

## 成果物ディレクトリ

```
frontend/
  src/
    ort/
      ortSession.ts         # InferenceSession 生成とキャッシュ連携
      modelCache.ts         # Cache Storage API ラッパー
      detector.ts           # DEIM セッション用 infer() 関数
      recognizer.ts         # PARSeq セッション用 infer() 関数 (3 モデル)
    types/
      ortTypes.ts           # Python <-> JS 間の I/O 型定義
  public/
    ort/                    # onnxruntime-web の wasm/mjs 配布ファイル
  vite.config.ts
  package.json
```

## TODO

### T2-1: モデル配信の確定

- [ ] **T2-1a**: 配信元を決定し URL を固定化。
  - 第一候補: GitHub Releases の `ndlocr-lite` リポジトリ（`/releases/download/<tag>/<file>.onnx`）。
  - 代替: Hugging Face Hub (`https://huggingface.co/<org>/ndlocr-lite-web/resolve/main/<file>.onnx`)。
  - 自前 CDN（CloudFront / Cloudflare R2）も可。
- [ ] **T2-1b**: 配信元に以下のレスポンスヘッダを設定。
  - `Cross-Origin-Resource-Policy: cross-origin`
  - `Content-Type: application/octet-stream`
  - `Cache-Control: public, max-age=31536000, immutable`（ファイル名にハッシュ or タグを含める）
- [ ] **T2-1c**: モデルマニフェスト JSON を同ディレクトリに置く:
  ```json
  {
    "version": "2026.04.18",
    "models": [
      {"id": "deim",  "file": "deim-s-1024x1024.onnx", "sha256": "...", "size": 40256763},
      {"id": "rec30", "file": "parseq-ndl-16x256-30-tiny-192epoch-tegaki3.onnx", "sha256": "...", "size": 35848117},
      {"id": "rec50", "file": "parseq-ndl-16x384-50-tiny-146epoch-tegaki2.onnx", "sha256": "...", "size": 36920058},
      {"id": "rec100","file": "parseq-ndl-16x768-100-tiny-165epoch-tegaki2.onnx", "sha256": "...", "size": 40984184}
    ]
  }
  ```
- [ ] **T2-1d**: `scripts/compute_model_hashes.py` を用意して `manifest.json` を自動生成できるようにする。

### T2-2: Cache Storage API ラッパー

- [ ] **T2-2a**: `frontend/src/ort/modelCache.ts` を実装。
  - `ensureModel(id: string, url: string, sha256: string, onProgress?): Promise<ArrayBuffer>`
  - フロー: Cache にあれば取り出し → なければ `fetch` でストリームダウンロード → `ReadableStream` で進捗通知 → SHA-256 検証 → Cache に保存。
  - `caches.open('ndlocr-models-v1')` を使い、バージョン変更時は旧キャッシュを削除。
- [ ] **T2-2b**: フォールバック: Cache Storage が使えない環境（Private Mode 等）では IndexedDB にフォールバック。実装は軽量ラッパー `idb-keyval` を採用可。
- [ ] **T2-2c**: 進捗 UI の更新は `onProgress(loaded, total)` コールバックで行う前提とし、Phase 4 の UI から呼び出す。

### T2-3: onnxruntime-web 基盤

- [ ] **T2-3a**: `package.json` に `onnxruntime-web` を追加。バージョンは Phase 0 時点で安定している系統を固定（例: `^1.23.x`）。
- [ ] **T2-3b**: `vite.config.ts` で `onnxruntime-web` の WASM 配布ファイルを `public/ort/` にコピー。
  - `import.meta.env.BASE_URL + 'ort/'` を `ort.env.wasm.wasmPaths` に設定。
  - 必要ファイル: `ort-wasm-simd-threaded.wasm`, `ort-wasm-simd-threaded.mjs` ほか。
- [ ] **T2-3c**: COOP/COEP ヘッダを dev server に設定（`vite.config.ts` の `server.headers`）:
  - `Cross-Origin-Opener-Policy: same-origin`
  - `Cross-Origin-Embedder-Policy: require-corp`
- [ ] **T2-3d**: `ortSession.ts` を実装。
  - `createSession(modelBytes: ArrayBuffer, options?): Promise<InferenceSession>`
  - `ort.env.wasm.numThreads` を `navigator.hardwareConcurrency` から算出（上限 4）。
  - `executionProviders`: `['webgpu', 'wasm']` を試行。WebGPU 非対応時は WASM にフォールバック。
- [ ] **T2-3e**: 4 セッションの遅延初期化（Lazy Singleton）を書く。
  - DEIM は起動直後ロードしてよい（必須）。
  - PARSeq 30/50/100 は初回の認識呼び出し時にそれぞれ遅延ロード。

### T2-4: Detector / Recognizer JS ラッパー（Python 側と I/F を揃える）

Phase 1 で定義した「`infer(feeds) -> outputs`」に合致するものを JS 側に用意する。

- [ ] **T2-4a**: `frontend/src/ort/detector.ts`
  ```ts
  // feeds.image: Float32Array (1,3,H,W)
  // feeds.orig_target_sizes: BigInt64Array (1,2)
  // 戻り値: [class_ids, bboxes, scores, (char_counts?)]
  export async function runDeim(feeds: {
    image: Float32Array; imageShape: [number, number, number, number];
    orig_target_sizes: BigInt64Array;
  }): Promise<Float32Array[]>;
  ```
- [ ] **T2-4b**: `frontend/src/ort/recognizer.ts`
  - `runParseq(variant: '30'|'50'|'100', image: Float32Array, shape: [1,3,H,W]): Promise<Float32Array>`
- [ ] **T2-4c**: `Tensor` を作る際の `dtype` は DEIM が `float32` + `int64`, PARSeq が `float32`。`BigInt64Array` の扱いに注意する。
- [ ] **T2-4d**: エラーハンドリング: メモリ不足 (`RangeError`) 時は「WebGPU→WASM」「モデル未ロード時の再取得」などのリトライ経路を明示。

### T2-5: モデル動作検証（Pyodide なしの E2E）

- [ ] **T2-5a**: `frontend/scripts/verify-ort.ts` に CLI スクリプトを作り、Node + Playwright ではなく **ブラウザ手動確認ページ**でも良いので、サンプル画像を JS 側だけで前処理 → ORT 実行 → 生テンソル出力を JSON で確認。
- [ ] **T2-5b**: 期待される出力の形状（DEIM なら `(1,N)` / `(1,N,4)` / `(1,N)` / `(1,N)` 等）を README 化して Phase 3 に引き渡す。
- [ ] **T2-5c**: サンプル画像 `resource/digidepo_3048008_0025.jpg` での実行時間（p50/p95）を計測し Phase 5 の最適化目標のベースラインとする。

### T2-6: 型定義の共有

- [ ] **T2-6a**: `frontend/src/types/ortTypes.ts` にて次を定義:
  ```ts
  export interface DetectorOutputs {
    classIds: Int32Array | Int64Array;
    bboxes: Float32Array;     // flatten
    scores: Float32Array;
    charCounts?: Float32Array;
  }
  export interface RecognizerInput {
    image: Float32Array;
    shape: [1, 3, number, number];
  }
  ```
- [ ] **T2-6b**: Phase 3 の Pyodide bridge で JSON/TypedArray のゼロコピー受け渡しを行うため、**TypedArray の transfer** を前提に API を設計する。

## Phase 2 完了条件

- 静的配信された ONNX を Cache Storage にキャッシュしつつ `onnxruntime-web` で読み込み、推論呼び出しができる。
- DEIM / PARSeq 30/50/100 の 4 セッションが、同一サンプル画像に対して安定した出力を返す。
- TypeScript の `infer` 風インターフェイスが Phase 3 に提供できる形で確定している。
