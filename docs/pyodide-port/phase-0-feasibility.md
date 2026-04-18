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
  - `src/model/deim-s-1024x1024.onnx` (~40MB) レイアウト検出
  - `src/model/parseq-ndl-16x256-30-tiny-192epoch-tegaki3.onnx` (~35MB) 短文行用
  - `src/model/parseq-ndl-16x384-50-tiny-146epoch-tegaki2.onnx` (~36MB) 中文行用
  - `src/model/parseq-ndl-16x768-100-tiny-165epoch-tegaki2.onnx` (~40MB) 長文行用
- 画像処理: `opencv-python-headless`（`cv2.resize`, `cv2.rotate`）。
- パース/ツリー: `lxml`、標準 `xml.etree.ElementTree`。
- 並列: `concurrent.futures.ThreadPoolExecutor`。
- その他: `numpy`, `Pillow`, `PyYAML`, `pypdfium2`（PDF 入力、GUI のみ）。

## TODO

- [ ] **T0-1**: Pyodide で利用可能なパッケージを確認し、本プロジェクトの `requirements.txt` と突き合わせる。
  - `numpy`, `Pillow`, `PyYAML`, `lxml` は Pyodide 公式ホイールあり。
  - `opencv-python-headless` は Pyodide 非対応 → Phase 1 で置換方針を固める。
  - `onnxruntime`（Python）は Pyodide 非対応 → **onnxruntime-web（JS）** で代替する前提を採用。
  - `pypdfium2` は Pyodide 非対応 → PDF 入力は JS 側（`pdf.js` 等）で処理、または Phase 4 スコープ外にする。
  - `flet`, `reportlab`, `dill`, `networkx`, `tqdm`, `ordered-set` は Pyodide 移植スコープ外（不要または純 Python で動く）。
- [ ] **T0-2**: 実行アーキテクチャを決定し、次の 1 枚絵を本書に追記する。

  ```
  [ Browser Main Thread ]
      │  UI (React/Vite 等), File I/O, Canvas 描画
      ▼
  [ Web Worker: Pyodide ]  ←→  [ Web Worker: onnxruntime-web (WASM/WebGPU) ]
      │  前処理/後処理, XY-Cut, XML 生成                推論のみ
  ```

  - 推論は **onnxruntime-web** を採用（Python ORT は Pyodide 非対応のため）。
  - 推論呼び出しは Pyodide から `pyodide.ffi` 経由で JS に委譲、または Main スレッドが仲介。
  - まずは「Pyodide (Worker) ⇄ Main」「ORT-web (Main or 別 Worker)」で PoC し、Phase 5 で最適化。
- [ ] **T0-3**: フロントエンド技術スタックを決定する。
  - 候補: **Vite + React + TypeScript**（推奨）/ SvelteKit / Vanilla TS。
  - 本ドキュメントでは以降 **Vite + React + TypeScript** を前提に記述する。
  - スタイリング: Tailwind CSS（軽量）または CSS Modules。
  - 状態管理: 小規模なので React Hooks + Zustand 程度で十分。
- [ ] **T0-4**: モデル配信・キャッシュ戦略の基本方針を決める。
  - 配信元: GitHub Releases / 静的ホスティング（CDN）/ Hugging Face Hub のいずれか。
  - 初回ダウンロードは進捗表示必須（計 ~150MB）。
  - ブラウザ側キャッシュ: **Cache Storage API**（`caches.open`）を第一選択、フォールバックで IndexedDB。
  - 量子化版（INT8/FP16）を Phase 5 で併提供する余地を残す。
- [ ] **T0-5**: 対象ブラウザ・ハード要件を確定。
  - 最低: Chrome/Edge 119+, Firefox 120+, Safari 17+（`cross-origin isolated` + SharedArrayBuffer + WASM SIMD 利用可）。
  - 推奨 RAM: 4GB 以上空きメモリ（モデル展開 + 画像）。
  - モバイル Safari はベストエフォート（Phase 5 で確認）。
- [ ] **T0-6**: セキュリティ / CSP / COOP-COEP 方針を確定。
  - `Cross-Origin-Opener-Policy: same-origin` + `Cross-Origin-Embedder-Policy: require-corp` を本番配信時に付与（SIMD + threads 有効化のため）。
  - モデル配信元にも `Cross-Origin-Resource-Policy: cross-origin` を付与。
  - 静的配信前提で、アップロード画像はローカル処理のみ（外部送信なし）と明文化する。
- [ ] **T0-7**: ライセンス確認。
  - 本体は CC BY 4.0（`LICENCE`）。依存ライブラリの移植・再配布ライセンスを `LICENCE_DEPENDENCEIES` で確認。
  - フロントエンドバンドル時に上記ライセンス表記を含めること。
- [ ] **T0-8**: 成功指標（DoD）の最小セットを決める。
  - サンプル画像 `resource/digidepo_3048008_0025.jpg` の OCR 結果テキストが既存 `resource/digidepo_3048008_0025.xml` 相当で一致（誤差許容）。
  - 初回ロード後、2 枚目以降の 1 ページ処理が一般的なノート PC で 30 秒以内。
  - ブラウザ単体で完結し、サーバー送信が発生しないこと。

## 成果物

- 本フェーズの成果は本ファイル自体（アーキテクチャ決定記録）。
- 併せてリポジトリ直下に `docs/pyodide-port/README.md` をロードマップ index として作成する。

## Phase 0 完了条件

- 上記 T0-1〜T0-8 の各決定事項が本書に書き込まれ、チームで合意されている。
- Phase 1 に進めるだけの前提（言語、FW、ORT バックエンド、ホスティング）が確定している。
