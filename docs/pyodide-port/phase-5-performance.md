# Phase 5: パフォーマンス / サイズ最適化

MVP（Phase 4 までで動く状態）をベースに、初回ロード時間・推論スループット・メモリ使用量を改善する。Phase 2 で取得したベースライン値と比較しながら進める。

## ゴール

- 初回ロード容量を 50% 以上削減（モデル量子化 + gzip/br 配信）。
- 推論時間を 2〜4 倍高速化（WebGPU / SIMD / threads / モデル並列）。
- 2 ページ目以降のコールドスタートを 3 秒以下。

## 計測

- [ ] **T5-0a**: まず Phase 4 完了時点の数値を `docs/pyodide-port/benchmark.md` に記録。
  - 初回ロード総容量 / 総秒数
  - 1 枚あたり DEIM 推論時間、PARSeq 推論時間、XY-Cut 時間、その他
  - Chrome DevTools Performance でのメインスレッド占有率
  - メモリピーク（`performance.memory.usedJSHeapSize` / Task Manager）
- [ ] **T5-0b**: Playwright + `pw-test-runner` または `lhci` で自動回帰測定できる仕組みを敷く。

## TODO

### T5-1: ONNX モデル量子化

- [ ] **T5-1a**: `onnxruntime` の `quantize_dynamic` で INT8 動的量子化を試す。
  - 対象: DEIM (detector), PARSeq tiny × 3。
  - 精度回帰を `resource/*.xml` との比較で確認（文字一致率 -2pt 以内を許容）。
- [ ] **T5-1b**: FP16 変換版も作成（WebGPU で特に効く）。
- [ ] **T5-1c**: モデルマニフェストに `variant: "fp32" | "fp16" | "int8"` を追加し、UI で切り替え可能に。

### T5-2: onnxruntime-web の高速化オプション

- [ ] **T5-2a**: `ort.env.wasm.simd = true`, `ort.env.wasm.numThreads = <n>` を COOP/COEP 下で検証。
- [ ] **T5-2b**: `executionProviders: ['webgpu']` を優先（対応ブラウザのみ）。WebGPU 非対応時は `wasm`。
- [ ] **T5-2c**: PARSeq 30/50/100 の 3 セッションは **並列 Worker** に分散。
  - Phase 3 ではシングル ORT Worker 前提だったが、ここで 3 Worker 並列化。
  - `navigator.hardwareConcurrency` が 4 未満の時は PARSeq 100 のみ別 Worker にする等の階段的戦略。

### T5-3: 推論パイプラインの並列化

- [ ] **T5-3a**: 現行の cascade は「30 → 50 → 100 の段階的再判定」だが、短文 / 中文 / 長文の **バッチ分け** が可能。並列 Worker への発注を非同期でパイプ化する。
- [ ] **T5-3b**: DEIM 実行中に PARSeq セッションのウォームアップ（ダミー推論）を並行実行し、最初の実推論を高速化。

### T5-4: 画像前処理の高速化

- [ ] **T5-4a**: Pyodide 上の `Pillow` リサイズはそれほど速くない。`OffscreenCanvas` で `drawImage` リサイズを行い、ピクセル配列だけ Pyodide に戻す方式を検証。
- [ ] **T5-4b**: 前処理のテンソル化（`(H,W,3) uint8 -> (1,3,H,W) float32`）を JS 側で `Float32Array` に直接書き込み、Pyodide を介さず Main/Worker 間で Transfer。
- [ ] **T5-4c**: 画像が極端に大きい（>4k）場合の事前スケーリング。DEIM の入力は 1024 固定なので、それ以上は事前に 2048 長辺まで落としても精度に影響しにくいと期待。

### T5-5: コード / アセット配信の圧縮

- [ ] **T5-5a**: ONNX ファイルを `brotli -q 11` でプリ圧縮し `Content-Encoding: br` で配信。ONNX は float 重みがメインで Brotli で 30-40% 縮むことが多い。
- [ ] **T5-5b**: Pyodide ランタイムもローカル hosting 時は `.br` / `.gz` 版を同梱。
- [ ] **T5-5c**: UI バンドルは Vite の `build.target: 'es2022'` + `manualChunks` でベンダー分離。

### T5-6: Service Worker / PWA 化

- [ ] **T5-6a**: `vite-plugin-pwa` で Service Worker を生成し、モデル・Pyodide・UI 全部を precache。
- [ ] **T5-6b**: 「完全オフライン動作」を自動テスト（Playwright の `context.setOffline(true)` で 2 回目訪問を検証）。
- [ ] **T5-6c**: インストール可能な PWA（マニフェスト・アイコン）にし、デスクトップ / モバイル ホーム画面へ追加できるようにする。

### T5-7: メモリ最適化

- [ ] **T5-7a**: 大きな画像から切り出した行画像（`npimg`）を都度 `del` + `gc.collect()`。
- [ ] **T5-7b**: ORT セッションは OCR 完了後に `release()`するオプション（低メモリモード）を追加。
- [ ] **T5-7c**: `navigator.deviceMemory < 4` の端末では警告 + 縮小画像に fallback。

### T5-8: PARSeq 同文モデルのスリム化（任意）

- [ ] **T5-8a**: 3 モデルを 1 モデル + 動的 H × W 入力に統合できるか検討（学習済み重みの結合、または `simple-mode` 相当の単一モデル運用）。
- [ ] **T5-8b**: 精度と速度のトレードオフ評価。

### T5-9: クロスブラウザ検証

- [ ] **T5-9a**: Chrome/Edge/Firefox/Safari の最新 2 メジャーで動作確認。
- [ ] **T5-9b**: iOS Safari のメモリ制約（~1GB）での挙動確認。必要なら量子化モデルのみ配信するモードを iOS 限定で有効化。
- [ ] **T5-9c**: Android Chrome ミッドレンジ端末での処理時間計測。

## Phase 5 完了条件

- `benchmark.md` のメトリクスが目標値を満たす。
- Lighthouse で Performance >=70, PWA: インストール可能、オフライン OK。
- すべての最適化が OFF/ON で切替可能（設定モーダル）で、将来の精度比較を阻害しない。
