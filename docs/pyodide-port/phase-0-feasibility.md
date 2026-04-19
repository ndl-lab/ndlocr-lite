# Phase 0: 調査・前提整理（Feasibility & Architecture）

本フェーズでは、NDLOCR-Lite をブラウザ上のスタンドアロン Web アプリとして動かす前提条件を確定する。実装に着手する前の「設計契約」を文書化し、以降のフェーズで参照する。

## ゴール

- ブラウザだけで完結する OCR パイプライン（レイアウト認識 → 文字認識 → 読み順整序）の実行アーキテクチャを決める。
- Pyodide で動かす範囲／JavaScript 側で動かす範囲の責務分担を決める。
- ONNX モデル配信・キャッシュ戦略の基本方針を決める。
- フロントエンド技術スタックを決める。

## 前提（現状コードの調査結果）

- エントリポイント: `src/ocr.py`（CLI）、`ndlocr-lite-gui/main.py`（Flet デスクトップ GUI）。
- 推論バックエンド: `onnxruntime` (Python)。ONNX モデル 4 本（計約 150MB）。
  - `src/model/deim-s-1024x1024.onnx` (~39MB) レイアウト検出
  - `src/model/parseq-ndl-16x256-30-tiny-192epoch-tegaki3.onnx` (~35MB) 短文行用
  - `src/model/parseq-ndl-16x384-50-tiny-146epoch-tegaki2.onnx` (~36MB) 中文行用
  - `src/model/parseq-ndl-16x768-100-tiny-165epoch-tegaki2.onnx` (~40MB) 長文行用
- 画像処理: `opencv-python-headless`（`cv2.resize`, `cv2.rotate`）。
- パース/ツリー: `lxml`、標準 `xml.etree.ElementTree`。
- 並列: `concurrent.futures.ThreadPoolExecutor`。
- その他: `numpy`, `Pillow`, `PyYAML`, `pypdfium2`（PDF 入力、GUI のみ）。

## 決定事項サマリ

| # | 決定 |
|---|---|
| T0-1 | Pyodide で動かすのは `numpy` / `Pillow` / `PyYAML` / `lxml` のみ。OpenCV は Phase 1 で NumPy+Pillow に置換、`onnxruntime` は **onnxruntime-web (JS)** で代替。 |
| T0-2 | Main Thread (React UI) ⇄ Pyodide Worker ⇄ ORT-web Worker の 3 レイヤ構成を採用。Pyodide から ORT-web へは Main 経由で RPC。 |
| T0-3 | **Vite + React + TypeScript + Tailwind CSS + Zustand** を採用。 |
| T0-4 | モデルは **GitHub Releases** で配信し、**Cache Storage API** でブラウザ側に永続化。初回は進捗表示付きダウンロード。 |
| T0-5 | **Chrome/Edge 119+, Firefox 120+, Safari 17+**。cross-origin isolated + WASM SIMD 必須。推奨 RAM 4GB。 |
| T0-6 | 本番配信は **COOP: same-origin + COEP: require-corp**。アップロード画像の外部送信は禁止（ローカル処理のみ）。 |
| T0-7 | 本体は **CC BY 4.0**。Pyodide 本体は MPL2.0、onnxruntime-web は MIT。フロントバンドルにライセンス表記を同梱。 |
| T0-8 | サンプル `resource/digidepo_3048008_0025.jpg` の OCR 結果が既存 xml とほぼ一致、初回ロード後 2 枚目以降で 30 秒以内、サーバー送信ゼロ。 |

## TODO

- [x] **T0-1**: Pyodide で利用可能なパッケージを確認し、本プロジェクトの `requirements.txt` と突き合わせる。

  | 既存依存 | Pyodide 対応 | 方針 |
  |---|---|---|
  | `numpy==2.2.2` | ○ (公式 wheel あり) | そのまま利用 |
  | `pillow==12.1.1` | ○ (公式 wheel あり) | そのまま利用 |
  | `PyYAML==6.0.1` | ○ | そのまま利用 |
  | `lxml==5.4.0` | ○ | そのまま利用 |
  | `opencv-python-headless==4.11.0.86` | × | Phase 1 で `PIL.Image.resize` / `np.rot90` に置換 |
  | `onnxruntime==1.23.2` | × | **onnxruntime-web (JS)** を採用、Pyodide から FFI で呼び出し |
  | `pypdfium2==4.30.0` | × | PDF 入力は Phase 4 スコープ外、必要なら JS 側 `pdf.js` で前処理 |
  | `flet==0.27.6` | 対象外 | デスクトップ GUI 専用、Web 版では利用しない |
  | `reportlab`, `dill`, `networkx`, `tqdm`, `ordered-set`, `pyparsing`, `protobuf` | 対象外 | 現行 OCR 経路では未使用、または純 Python 代替で対応可能 |

  - 方針: `requirements.txt` はそのまま CLI/デスクトップ用に残し、ブラウザ版の Python 依存は `src/ndlocr_web/` パッケージ側で最小集合（numpy / Pillow / PyYAML / lxml）に限定する。

- [x] **T0-2**: 実行アーキテクチャを決定する。

  ```
  ┌────────────────────── Browser ──────────────────────────────┐
  │                                                             │
  │  [ Main Thread ]                                            │
  │   React + Vite UI, File I/O, Canvas 可視化, RPC 仲介         │
  │        │                                                    │
  │        ├──▶ [ Worker A: Pyodide ]                           │
  │        │     ndlocr_web (前処理/後処理/XY-Cut/XML 生成)       │
  │        │     Python 側は純関数群。推論は callback で外部注入 │
  │        │                                                    │
  │        └──▶ [ Worker B: onnxruntime-web ]                   │
  │              DEIM + PARSeq ×3 セッションを保持               │
  │              WebGPU 優先 / WASM SIMD+threads フォールバック   │
  │                                                             │
  │  モデルキャッシュ : Cache Storage API (~150MB)               │
  └─────────────────────────────────────────────────────────────┘
  ```

  - **役割分担**:
    - *Main*: UI/DnD/Canvas/プログレス、Worker 生成・仲介。ORT Worker の `infer(name, inputs)` を直接叩く。
    - *Pyodide Worker*: `ndlocr_web.run_ocr_on_image(rgb, infer_fn)` を実行。`infer_fn` は Main Thread 経由で ORT Worker を呼ぶ非同期 JS プロキシ（Pyodide から `await` で呼べるよう `pyodide.ffi.to_js` を介す）。
    - *ORT Worker*: DEIM + PARSeq×3 の `InferenceSession` を保持。`postMessage` ベースの JSON プロトコル（`{type: "infer", session: "deim", inputs: {...}}`）で呼び出し。
  - **通信方式**: Phase 3 では Comlink を利用して Main ⇄ Worker RPC を型付けする。Pyodide Worker から ORT Worker を直接叩くのではなく、Main 仲介にすることで WebGPU コンテキストを片方向に寄せ、初期化を単純化する。
  - **PoC 手順**: Phase 3 で先にサンプル画像 1 枚を通し、ORT-web のスループットを測定した上で、必要なら Phase 5 で「Pyodide → ORT 直結（SharedArrayBuffer + TransferableBuffer）」への最適化を検討する。

- [x] **T0-3**: フロントエンド技術スタックを決定する。

  | 層 | 採用 | 理由 |
  |---|---|---|
  | ビルド | **Vite 7.x** | ES モジュール & 開発サーバが軽量、`coi-serviceworker` 連携も簡単 |
  | フレームワーク | **React 18 + TypeScript 5** | ライブラリエコシステム・型情報が厚く、状態管理も小規模で済む |
  | スタイリング | **Tailwind CSS v4** | デザイン負荷を下げ、ダークモード対応もプリセットで可能 |
  | 状態管理 | **Zustand** | OCR 進捗・結果・キャッシュ状況など数個のストアで完結し、Redux ほどの重厚さは不要 |
  | Worker RPC | **Comlink 4.x** | Pyodide Worker / ORT Worker の型付き呼び出しを簡潔化 |
  | パッケージマネージャ | **pnpm** | モノレポ化してもキャッシュ効率がよく、既存デスクトップ GUI と切り離して運用可能 |

  - ディレクトリ: 既存 `ndlocr-lite-gui/` と並置して **`ndlocr-lite-web/`** を Phase 4 で新設する（Phase 1〜3 は `src/ndlocr_web/` の Python パッケージ整備が中心で、フロント実装は Phase 4 まで棚上げ）。

- [x] **T0-4**: モデル配信・キャッシュ戦略を決定する。

  - **配信元**: GitHub Releases に `ndlocr-lite-models-vX.Y.Z` タグでアップロードし、**jsDelivr / unpkg / GitHub Raw** のいずれかの CDN 経由で配信する。モデルは `sha256` ハッシュ付きのマニフェスト (`manifest.json`) と一緒に配信し、検証後にキャッシュする。
  - **キャッシュ**: 第一選択は **Cache Storage API** (`caches.open("ndlocr-lite-models-v1")`)、フォールバックで IndexedDB。量子化モデル切替時もキャッシュ名のバージョンでバスティング可能にする。
  - **ダウンロード UX**: 初回 150MB のダウンロードは Fetch + `ReadableStream` で進捗表示。途中中断・再開（Range リクエスト）は Phase 5 で検討。
  - **量子化版の余地**: Phase 5 で INT8/FP16 量子化モデル（目標合計 ~40MB）を併提供できるよう、`manifest.json` に `variant` フィールドを持たせる設計にする（`{name, url, sha256, variant: "fp32"|"int8"|"fp16"}`）。

- [x] **T0-5**: 対象ブラウザ・ハード要件を確定する。

  | 区分 | 要件 |
  |---|---|
  | 対応ブラウザ（最低） | Chrome / Edge 119+、Firefox 120+、Safari 17+ |
  | 必須機能 | WebAssembly SIMD、WebAssembly threads（= `cross-origin isolated` + SharedArrayBuffer）、Cache Storage、Fetch Streams |
  | 推奨 | WebGPU 対応ブラウザ（Chrome/Edge 121+、Safari 18+、Firefox Nightly）で推論を加速 |
  | RAM | 空き 4GB 以上（モデル 150MB + WASM ヒープ + 入力画像を考慮） |
  | モバイル | iOS Safari 17+ / Android Chrome 119+ はベストエフォート。モデルダウンロードサイズ・メモリ上限から、Phase 5 で量子化モデル配信をもって正式対応を検討 |
  | ネットワーク | 初回のみモデルダウンロードでオンライン必須。2 回目以降は完全オフライン動作を目標 |

- [x] **T0-6**: セキュリティ / CSP / COOP-COEP 方針を確定する。

  - **本番配信 HTTP ヘッダ**:
    - `Cross-Origin-Opener-Policy: same-origin`
    - `Cross-Origin-Embedder-Policy: require-corp`
    - `Cross-Origin-Resource-Policy: same-origin`（同一オリジン資産）
    - `Content-Security-Policy: default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self' blob:; connect-src 'self' https://github.com https://*.githubusercontent.com; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline';`
  - **モデル配信元 CDN**: 配信側にも `Cross-Origin-Resource-Policy: cross-origin` と `Access-Control-Allow-Origin: *` を付与する。
  - **ローカル処理の明文化**: アップロード画像・PDF は一切外部送信しないことを README・UI フッタに明記する（分析用ビーコン等も搭載しない）。
  - **開発環境**: Vite dev server は `@vitejs/plugin-basic-ssl` と `server.headers` で COOP/COEP を付与。GitHub Pages へデプロイする場合は `coi-serviceworker` を利用して同等ヘッダを擬似付与する。

- [x] **T0-7**: ライセンス確認。

  - 本体コード: **CC BY 4.0**（`LICENCE`）。フロントエンド UI・ビルド成果物にも同ライセンスを継承し、画面フッタ／`about` に表記する。
  - 依存ライブラリ:
    - Pyodide 本体: **MPL 2.0**
    - onnxruntime-web: **MIT**
    - React / Vite / Zustand / Comlink / Tailwind CSS: **MIT**
    - NumPy / Pillow / PyYAML / lxml: **BSD 3-Clause** または互換
  - 成果物バンドルに `LICENCE_DEPENDENCEIES` 相当のサードパーティライセンス表を同梱する（Phase 6 で `vite-plugin-license` などを使い自動生成）。
  - モデル（ONNX）は NDLOCR-Lite 本体と同じ配布条件に従う（`train/README.md` 参照）。

- [x] **T0-8**: 成功指標（DoD）。

  1. **機能**: サンプル画像 `resource/digidepo_3048008_0025.jpg` をブラウザ版で処理し、CLI 版 (`resource/digidepo_3048008_0025.xml`) とテキストの **文字一致率 ≥ 98%**（軽微な誤字は許容）で一致すること。
  2. **性能**: 初回ロード（モデルダウンロード込み）完了後、同一セッション内での 2 枚目以降の 1 ページ処理が、M1/M2 MacBook または同等 CPU のノート PC（RAM 16GB, ブラウザ Chrome 最新安定版）で **30 秒以内**で終わること。
  3. **プライバシー**: 画像 / テキスト / OCR 結果ともに外部ネットワークへの送信が発生しないこと（DevTools Network タブと CSP で確認）。
  4. **対応環境**: T0-5 の対応ブラウザでクラッシュせずに OCR を完走できること。
  5. **ライセンス表記**: UI 上から本体・依存ライブラリのライセンス表記に到達できること。

## 成果物

- 本ファイル（アーキテクチャ決定記録 / ADR を兼ねる）。
- ロードマップ index: [`docs/pyodide-port/README.md`](./README.md)。

## Phase 0 完了条件

- 上記 T0-1〜T0-8 の決定事項が本書に書き込まれ、チームで合意されている（本 PR がその合意点）。
- Phase 1 に進めるだけの前提（言語、FW、ORT バックエンド、ホスティング）が確定している。
