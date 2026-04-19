# Phase 2: モデル配信 / キャッシュ & onnxruntime-web 推論基盤

ONNX モデル 4 本（合計 ~150MB）をブラウザに配信し、**onnxruntime-web** で推論するための基盤を整備する。Python 側（Pyodide）との接続は Phase 3 で扱う。本フェーズは JavaScript / TypeScript のみで完結するラッパーを独立して PoC する。

## ゴール

- `onnxruntime-web` を使って、ブラウザから 4 つのモデルを読み込み推論を走らせる最小コードが動く。
- 初回ダウンロード → Cache Storage への保存 → 2 回目以降オフライン動作が成立する。
- モデル配信元（CDN/Releases）と取得フローを確定する。
- 推論 API の TypeScript 型を Phase 3（Pyodide bridge）に提供する形で固める。

## 成果物ディレクトリ

Phase 0 で決めた通り、フロントエンドプロジェクトは既存 `ndlocr-lite-gui/` と並置して **`ndlocr-lite-web/`** に作る。

```
ndlocr-lite-web/
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
    manifest.json           # モデルマニフェスト（SHA-256 + baseUrl 入り）
    verify.html             # ORT スモークテスト用ブラウザ手動確認ページ
  vite.config.ts
  package.json              # pnpm 管理
```

## 実装結果サマリ（2026-04-19 完了）

| 項目 | 実測値 |
|------|--------|
| onnxruntime-web | 1.24.3 |
| Vite | 6.4.2 |
| TypeScript | 5.9.3 |
| `tsc --noEmit` | ✅ エラーなし |
| `vite build` | ✅ 成功 |

**manifest.json の実際のフォーマット**（`baseUrl` フィールドを追加。Phase 0 仕様から拡張）:
```json
{
  "version": "2026.04.19",
  "baseUrl": "https://github.com/michitomo/ndlocr-lite/releases/download/models-v2026.04.19/",
  "models": [...]
}
```
`modelCache.ts` の `ensureModel()` は `baseUrl + entry.file` で URL を組み立てる。Phase 6 で `VITE_MODEL_MANIFEST_URL` を注入するとき、`manifest.json` 全体の URL だけを差し替えれば `baseUrl` ごと切り替わる設計。

## TODO

### T2-1: モデル配信の確定

- [x] **T2-1a**: 配信元を決定し URL を固定化。
  - **Phase 0 で決定**: **GitHub Releases**（`ndlocr-lite` リポジトリ）を第一選択、必要に応じて jsDelivr / GitHub Raw CDN でラップする。
  - URL 規約: `/releases/download/models-v<semver>/<file>.onnx`。ファイル名にバージョンを含めずタグで bust する。
  - 代替: Hugging Face Hub / Cloudflare R2 は Phase 5 以降で転送量が問題になったときに再検討。
- [ ] **T2-1b**: 配信元に以下のレスポンスヘッダを設定（GitHub Releases 側の設定は Phase 6 で確認）。
  - `Cross-Origin-Resource-Policy: cross-origin`
  - `Content-Type: application/octet-stream`
  - `Cache-Control: public, max-age=31536000, immutable`（ファイル名にハッシュ or タグを含める）
- [x] **T2-1c**: モデルマニフェスト JSON を `ndlocr-lite-web/public/manifest.json` に配置（実測 SHA-256 入り）。
- [x] **T2-1d**: `scripts/compute_model_hashes.py` を用意して `manifest.json` を自動生成できるようにする。

### T2-2: Cache Storage API ラッパー

- [x] **T2-2a**: `ndlocr-lite-web/src/ort/modelCache.ts` を実装。
  - `ensureModel(id: string, url: string, sha256: string, onProgress?): Promise<ArrayBuffer>`
  - フロー: Cache にあれば取り出し → なければ `fetch` でストリームダウンロード → `ReadableStream` で進捗通知 → SHA-256 検証 → Cache に保存。
  - `caches.open('ndlocr-models-v1')` を使い、バージョン変更時は `evictModelCache()` で旧キャッシュを削除。
- [x] **T2-2b**: フォールバック: Cache Storage が使えない環境（Private Mode 等）では `idb-keyval` 経由の IndexedDB にフォールバック。
- [x] **T2-2c**: 進捗 UI の更新は `onProgress(loaded, total)` コールバックで行う前提とし、Phase 4 の UI から呼び出す。

### T2-3: onnxruntime-web 基盤

- [x] **T2-3a**: `package.json` に `onnxruntime-web 1.24.3` を追加（`^1.22.0` 指定、実インストールは 1.24.3）。
- [x] **T2-3b**: `vite.config.ts` で `vite-plugin-static-copy` を使い `node_modules/onnxruntime-web/dist/` の WASM/mjs ファイルを `public/ort/` にコピー。`ort.env.wasm.wasmPaths` を `import.meta.env.BASE_URL + 'ort/'` に設定。
- [x] **T2-3c**: COOP/COEP ヘッダを `server.headers` と `preview.headers` 両方に設定。
- [x] **T2-3d**: `ortSession.ts` を実装。`createSession()` + `getSession()` による遅延シングルトン。`numThreads = min(hardwareConcurrency, 4)`。
- [x] **T2-3e**: DEIM は `loadAllSessions()` で即時ロード。PARSeq 30/50/100 は `getSession()` 初回呼び出し時に遅延ロード。

### T2-4: Detector / Recognizer JS ラッパー（Python 側と I/F を揃える）

Phase 1 で定義した「`infer(feeds) -> outputs`」に合致するものを JS 側に用意する。

**Phase 1 で確定した feed キー:**
- `DEIMDetector.preprocess()` → `{"images": tensor(1,3,H,W), "orig_target_sizes": int64(1,2)}`
- `PARSeqRecognizer.preprocess()` → `{"input": tensor(1,3,H,W)}`

- [x] **T2-4a**: `ndlocr-lite-web/src/ort/detector.ts` — `runDeim(feeds: DetectorFeeds): Promise<DetectorOutputs>` を実装。`orig_target_sizes` は `BigInt64Array` で `int64` テンソルを生成。
- [x] **T2-4b**: `ndlocr-lite-web/src/ort/recognizer.ts` — `runParseq(variant: '30'|'50'|'100', feeds: RecognizerFeeds): Promise<RecognizerOutputs>` を実装。入力 shape をバリアント別に検証。
- [x] **T2-4c**: `dtype`: DEIM は `float32` + `int64`（`BigInt64Array`）、PARSeq は `float32` のみ。
- [x] **T2-4d**: `RangeError`（OOM）を明示的に再送出し、呼び出し元でリトライ可能にした。

### T2-5: モデル動作検証（Pyodide なしの E2E）

- [x] **T2-5a**: `ndlocr-lite-web/public/verify.html` — ブラウザ手動確認ページ。ORT ロード → manifest 取得 → DEIM session 作成 → 合成テンソルで推論 → 出力 shape/dtype/実行時間を表示。PARSeq rec30 も同様。
- [ ] **T2-5b**: 出力形状（DEIM なら `(1,N)` / `(1,N,4)` / `(1,N)` / `(1,N)` 等）を README 化して Phase 3 に引き渡す。（`verify.html` の実行結果を記録すること）
- [ ] **T2-5c**: サンプル画像 `resource/digidepo_3048008_0025.jpg` での実行時間（p50/p95）を計測し Phase 5 の最適化目標のベースラインとする。（`verify.html` で実機計測後に `docs/pyodide-port/benchmark.md` へ記録）

### T2-6: 型定義の共有

- [x] **T2-6a**: `ndlocr-lite-web/src/types/ortTypes.ts` にて `DetectorFeeds` / `DetectorOutputs` / `RecognizerFeeds` / `RecognizerOutputs` / `ModelManifest` / `ProgressCallback` を定義。`PARSEQ_INPUT_SIZES` 定数も同ファイルに置く。
- [x] **T2-6b**: Phase 3 の Pyodide bridge で JSON/TypedArray のゼロコピー受け渡しを行うため、**TypedArray の transfer** を前提に API を設計した。

## Phase 2 完了条件

- ✅ 静的配信された ONNX を Cache Storage にキャッシュしつつ `onnxruntime-web` で読み込み、推論呼び出しができる。
- ✅ TypeScript の `infer` 風インターフェイスが Phase 3 に提供できる形で確定している。
- ⏳ DEIM / PARSeq 30/50/100 の 4 セッションが、同一サンプル画像に対して安定した出力を返す（`verify.html` での実機確認待ち — モデルが GitHub Releases に公開されてから）。
