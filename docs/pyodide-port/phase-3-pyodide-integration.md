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
                     Phase 2 実装済み:
                       src/ort/ortSession.ts  (セッション管理)
                       src/ort/detector.ts    (runDeim)
                       src/ort/recognizer.ts  (runParseq)
```

- 「Pyodide Worker」が推論要求を **同期で**待つために `SharedArrayBuffer` + `Atomics.wait/notify` を使う。
- Alternative（フォールバック）: すべて Promise ベースで、`run_ocr_on_image` を `async def` にして Python 側から `await` する。Pyodide は `pyodide.ffi.to_promise` をサポートしているためこちらの方が実装コストは低い。**まずは async 版で実装し、遅ければ Phase 5 で SAB 化。**

## TODO

### T3-1: Pyodide ビルド戦略

- [ ] **T3-1a**: Pyodide ランタイムの配信方法を決める。
  - 公式 CDN (`jsdelivr.net/npm/pyodide@<ver>/`) を使うのが最も簡単。
  - オフライン配信要件があるなら `pyodide` npm パッケージから静的ビルドして `public/pyodide/` に配置。
- [ ] **T3-1b**: `ndlocr_web` の配布方法を決める。

  > **Phase 1 実装上の注意**: `xml_builder.py` は `sys.path` に `src/` を追加して `ndl_parser` を import する設計になっている。また `reading_order/` は `ndlocr_web` 内にコピー済みだが `ndl_parser.py` は `src/` 直下のまま。Pyodide ではこの相対パス解決が機能しないため、ホイール化時は以下の対処が必要:
  > 1. `ndl_parser.py` を `ndlocr_web/` 内に移動（または `xml_builder.py` の import を `ndlocr_web` パッケージ相対 import に変更）。
  > 2. `xml_builder.py` の `sys.path` 操作を削除し、`from .ndl_parser import convert_to_xml_string3` に変更。

  配布方式:
  1. **ホイール化（推奨）**: `src/ndlocr_web/` + `src/ndl_parser.py` を一まとめにして `ndlocr_web-0.1.0-py3-none-any.whl` を作成し `micropip.install('/wheels/ndlocr_web-...whl')`。
  2. **ソース展開**: ビルド時に必要ファイルを `public/py/` に配置し Worker で `pyodide.unpackArchive` する。

- [ ] **T3-1c**: 依存パッケージ導入:

  > **Phase 1 実装で判明**: `reading_order/order/smooth_order.py` が `networkx` を import するため、`networkx` も必要。

  ```js
  await pyodide.loadPackage(['numpy', 'Pillow', 'lxml', 'networkx']);
  await pyodide.runPythonAsync(`
    import micropip
    await micropip.install('PyYAML')  # Pyodide に無い/バージョン差異があれば
  `);
  ```

### T3-2: Worker のスケルトン

- [ ] **T3-2a**: `ndlocr-lite-web/src/workers/pyodide.worker.ts` を実装。
  - 起動メッセージ: `init` → Pyodide + `ndlocr_web` ロード、`progress` イベント送出。
  - OCR メッセージ: `ocr` → ImageBitmap → Uint8ClampedArray → ndarray → `run_ocr_on_image` → 結果 JSON を返す。
  - **Phase 1 確定**: `ndlocr_web.__init__` が `sys.setrecursionlimit(5000)` を呼ぶため Worker 側での明示的な設定は不要。
- [ ] **T3-2b**: `ndlocr-lite-web/src/lib/OcrClient.ts` で Worker を起動・メッセージ送受信する高レベル API。
  ```ts
  const client = new OcrClient();
  await client.init((p) => setProgress(p));
  const result = await client.ocr(imageBitmap, { viz: true });
  // result.xml / result.text / result.json / result.vizBlob
  ```
  **Phase 1 確定の OcrResult フィールド**:
  - `xml: str` — `<OCRDATASET>...</OCRDATASET>` 文字列
  - `text: str` — 改行連結本文（縦書き時は行逆順）
  - `json: dict` — `{"contents": [...], "imginfo": {...}}`
  - `viz_png: bytes | None` — viz=True 時のみ PNG bytes

### T3-3: 画像転送

- [ ] **T3-3a**: 画像は **ImageBitmap**（UI で `createImageBitmap(file)` 経由）で Worker へ転送（`postMessage([bitmap], [bitmap])` で Transferable）。
- [ ] **T3-3b**: Worker 内で `OffscreenCanvas` + `getImageData` で `Uint8ClampedArray` を取得し、Pyodide へ `pyodide.toPy`（もしくは `pyodide.ffi.to_js`）で NumPy 配列化。
  - **Phase 1 確定**: `run_ocr_on_image` の `rgb` 引数は `np.ndarray` (H×W×3, uint8, RGB 順)。`getImageData` の RGBA から `[:, :, :3]` でスライスする。
- [ ] **T3-3c**: 大画像時のメモリを考え、Pyodide 側に渡すときは 1 度だけコピーして、以降は Python 内で参照する。

### T3-4: ONNX 推論呼び出し橋渡し

- [ ] **T3-4a**: Python 側の `infer` 抽象を JS 側へバインドする。
  - Pyodide Worker 内から Main Thread へ「推論要求」を送り、Main 側の `OrtClient` が別 Worker（ORT Worker）に投げる。
  - 戻りを Pyodide Worker が **await** する（async パス）。
  - **Phase 2 実装済み**: `OrtClient` は `src/ort/detector.ts` の `runDeim()` と `src/ort/recognizer.ts` の `runParseq()` をラップする形で実装する。型定義は `src/types/ortTypes.ts` の `DetectorFeeds` / `DetectorOutputs` / `RecognizerFeeds` / `RecognizerOutputs` を使う。
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
- [ ] **T3-4d**: `run_ocr_on_image` を `async def` に変更し、`process_cascade` / `recognizer.read()` も `await` 対応にする。
  - **Phase 1 状態**: `run_ocr_on_image`・`process_cascade`・`PARSeqRecognizer.read` は現在同期関数。本フェーズで `async def run_ocr_on_image(...)` に変更し、cascade の各 `recognizer.read(img)` を `await` 呼び出しに変える。
  - `DEIMDetector.detect` も同様に `async def detect` に変換する。
  - `cascade.py` の `process_cascade` を `async def process_cascade(...)` として `await recognizerXX.read(img)` で直列 await する。

### T3-5: Worker とメインの RPC 共通レイヤ

- [ ] **T3-5a**: **Phase 0 で Comlink 採用を決定済み**。`ndlocr-lite-web/src/lib/rpc.ts` は Comlink (`comlink` npm) の薄いラッパーとして作り、OCR Worker / ORT Worker 双方に型付きプロキシを提供する。
  - Comlink の `Comlink.expose()` / `Comlink.wrap()` でリクエスト ID 管理を肩代わりさせる。
  - Transferable は Comlink の `Comlink.transfer()` で明示。Pyodide 側は `to_js({ dict_converter: Object.fromEntries })` で整える。
- [ ] **T3-5b**: OCR Worker と ORT Worker の双方で同じ Comlink 経由 RPC を使う。Comlink が足枷になるケース（`SharedArrayBuffer` + `Atomics` を使いたい Phase 5 最適化等）では `rpc.ts` に低レベル API を追加する。

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

- [ ] **T3-8a**: `ndlocr-lite-web/tests/e2e/ocr.spec.ts`（Playwright）:
  1. トップページ訪問
  2. サンプル画像をドロップ
  3. 進捗が 100% になるまで待機
  4. 結果テキストに既知の部分文字列が含まれることを検証
- [ ] **T3-8b**: 上記テストを CI に載せるのは Phase 6。本フェーズではローカルで通ればよい。

## Phase 3 完了条件

- Worker 内の Pyodide が `ndlocr_web.pipeline.run_ocr_on_image` を走らせ、ORT Worker を呼び出して結果を返す。
- サンプル画像の OCR 結果が CLI 実行結果と **ほぼ一致**（誤差許容: テキスト一致率 95% 以上）。
- UI（Phase 4 のプレースホルダで可）からの 1 リクエストが成功し、進捗がリアルタイムに表示される。
