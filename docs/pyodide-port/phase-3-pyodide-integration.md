# Phase 3: Pyodide 統合 & Web Worker 化

Phase 1 で作った `ndlocr_web` パッケージを Pyodide にロードし、Phase 2 の onnxruntime-web 推論関数を差し込む。UI フリーズを防ぐため Pyodide は **Web Worker** 内で実行する。

## ゴール

- Pyodide を Worker 起動 → `ndlocr_web` をロード → 画像を受け取り → `run_ocr_on_image` を実行 → XML/Text/JSON/viz を返す。
- Python ↔ JS 間で ONNX 推論呼び出しを**同期的**に橋渡しする（Pyodide → Worker Main → ORT Worker のメッセージング）。
- RPC 層 `WorkerRPC` を実装し、UI 層（Phase 4）はシンプルに `ocr(imageBitmap)` を呼べる。

## アーキテクチャ詳細

```
Main Thread (UI)
  ├── OcrClient  ---[postMessage]---  Pyodide Worker
  │                                        │
  │                                        ├─ Pyodide runtime
  │                                        ├─ ndlocr_web (Python)
  │                                        └─ infer() 呼び出し時は
  │                                           Main へ postMessage で転送
  │
  └── OrtClient  <---[postMessage]-------┘
                 └── onnxruntime-web (WASM)
                     ※ Phase 2 の ortSession を wrap
```

- 「Pyodide Worker」が推論要求を **同期で**待つために `SharedArrayBuffer` + `Atomics.wait/notify` を使う。
- Alternative（フォールバック）: すべて Promise ベースで、`run_ocr_on_image` を `async def` にして Python 側から `await` する。Pyodide は `pyodide.ffi.to_promise` をサポートしているためこちらの方が実装コストは低い。**まずは async 版で実装し、遅ければ Phase 5 で SAB 化。**

## TODO

### T3-1: Pyodide ビルド戦略

- [ ] **T3-1a**: Pyodide ランタイムの配信方法を決める。
  - 公式 CDN (`jsdelivr.net/npm/pyodide@<ver>/`) を使うのが最も簡単。
  - オフライン配信要件があるなら `pyodide` npm パッケージから静的ビルドして `public/pyodide/` に配置。
- [ ] **T3-1b**: `ndlocr_web` の配布方法を決める。次のいずれか:
  1. **ホイール化**: `src/ndlocr_web/` から `ndlocr_web-0.1.0-py3-none-any.whl` を作成し、`micropip.install('/wheels/ndlocr_web-...whl')`。純 Python なので any-wheel で可。
  2. **ソース展開**: ビルド時に `ndlocr_web/**` を `public/py/` に配置し、Worker で `pyodide.unpackArchive` する。
  - 推奨: **1 のホイール化**（依存解決が簡潔）。
- [ ] **T3-1c**: 依存パッケージ導入:
  ```js
  await pyodide.loadPackage(['numpy', 'Pillow', 'lxml']);
  await pyodide.runPythonAsync(`
    import micropip
    await micropip.install('PyYAML')  # Pyodide に無い/バージョン差異があれば
  `);
  ```

### T3-2: Worker のスケルトン

- [ ] **T3-2a**: `frontend/src/workers/pyodide.worker.ts` を実装。
  - 起動メッセージ: `init` → Pyodide + `ndlocr_web` ロード、`progress` イベント送出。
  - OCR メッセージ: `ocr` → ImageBitmap → Uint8ClampedArray → ndarray → `run_ocr_on_image` → 結果 JSON を返す。
- [ ] **T3-2b**: `frontend/src/lib/OcrClient.ts` で Worker を起動・メッセージ送受信する高レベル API。
  ```ts
  const client = new OcrClient();
  await client.init((p) => setProgress(p));
  const result = await client.ocr(imageBitmap, { viz: true });
  // result.xml / result.text / result.json / result.vizBlob
  ```

### T3-3: 画像転送

- [ ] **T3-3a**: 画像は **ImageBitmap**（UI で `createImageBitmap(file)` 経由）で Worker へ転送（`postMessage([bitmap], [bitmap])` で Transferable）。
- [ ] **T3-3b**: Worker 内で `OffscreenCanvas` + `getImageData` で `Uint8ClampedArray` を取得し、Pyodide へ `pyodide.toPy`（もしくは `pyodide.ffi.to_js`）で NumPy 配列化。
- [ ] **T3-3c**: 大画像時のメモリを考え、Pyodide 側に渡すときは 1 度だけコピーして、以降は Python 内で参照する。

### T3-4: ONNX 推論呼び出し橋渡し

- [ ] **T3-4a**: Python 側の `infer` 抽象を JS 側へバインドする。
  - Pyodide Worker 内から Main Thread へ「推論要求」を送り、Main 側の `OrtClient` が別 Worker（ORT Worker）に投げる。
  - 戻りを Pyodide Worker が **await** する（async パス）。
- [ ] **T3-4b**: Python 側ラッパー:
  ```python
  # ndlocr_web/bridge.py
  class JSInfer:
      def __init__(self, js_infer_coro):
          self._c = js_infer_coro  # awaitable JS func

      async def __call__(self, feeds: dict[str, np.ndarray]) -> list[np.ndarray]:
          # feeds を to_js (転送), 戻りを numpy に戻す
          ...
  ```
- [ ] **T3-4c**: 数値ゼロコピー:
  - NumPy → JS: `np_array.tobytes()` ではなく、Pyodide の buffer プロトコル経由で `Float32Array` を作成。
  - JS → NumPy: `Float32Array` → `np.frombuffer`。
- [ ] **T3-4d**: `run_ocr_on_image` を `async def` に変更（Phase 1 で既に分岐可能な設計にしておくこと）。`recognizer.read(img)` も `await` 対応。

### T3-5: Worker とメインの RPC 共通レイヤ

- [ ] **T3-5a**: `frontend/src/lib/rpc.ts` に軽量 RPC を実装。
  - リクエストごとに `id` を付与し、`postMessage({id, type, payload})` → レスポンス `postMessage({id, ok, result|error})`。
  - Transferable は送信側が第 2 引数で指定。
- [ ] **T3-5b**: OCR Worker と ORT Worker の双方で同じ RPC を使う。

### T3-6: エラーハンドリング・キャンセル

- [ ] **T3-6a**: Pyodide 例外（`PyProxy` 経由）をキャッチし `{name, message, stack}` で Main に返す。
- [ ] **T3-6b**: キャンセル: `AbortController` シグナルを RPC で伝搬。Python 側は長い処理の途中で `await asyncio.sleep(0)` を入れてキャンセルを検知可能にする。
- [ ] **T3-6c**: OOM: 推論 Worker が落ちた場合は `Worker.onerror` を検知して再起動し、状態を初期化する。

### T3-7: ロード進捗の可視化

- [ ] **T3-7a**: 以下のフェーズごとに 0-100% の進捗を UI に流す:
  1. Pyodide runtime ダウンロード（~8MB）
  2. 標準ライブラリ（numpy/Pillow/lxml）ロード
  3. `ndlocr_web` ホイール取得
  4. ONNX モデル 4 本のダウンロード（Phase 2 の `modelCache`）
  5. ORT セッション初期化

- [ ] **T3-7b**: 各ステップで `status` 文字列と `percent` を送出し、UI 側で簡易タイムライン表示。

### T3-8: E2E スモークテスト

- [ ] **T3-8a**: `frontend/tests/e2e/ocr.spec.ts`（Playwright）:
  1. トップページ訪問
  2. サンプル画像をドロップ
  3. 進捗が 100% になるまで待機
  4. 結果テキストに既知の部分文字列が含まれることを検証
- [ ] **T3-8b**: 上記テストを CI に載せるのは Phase 6。本フェーズではローカルで通ればよい。

## Phase 3 完了条件

- Worker 内の Pyodide が `ndlocr_web.pipeline.run_ocr_on_image` を走らせ、ORT Worker を呼び出して結果を返す。
- サンプル画像の OCR 結果が CLI 実行結果と **ほぼ一致**（誤差許容: テキスト一致率 95% 以上）。
- UI（Phase 4 のプレースホルダで可）からの 1 リクエストが成功し、進捗がリアルタイムに表示される。
