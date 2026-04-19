# Phase 4: フロントエンド UI 実装

Phase 3 で用意した `OcrClient` を使って、ブラウザだけで完結する SPA を構築する。Phase 0 で決定したスタック（**Vite 6 + React 18 + TypeScript 5 + Tailwind v4 + Zustand + Comlink + pnpm**）を前提とする。

> **Phase 2 実装後の注意**: `ndlocr-lite-web/` は Phase 2 で作成済み。Vite 6.4 + TypeScript 5.9 + pnpm のプロジェクトが存在し、`onnxruntime-web` と `idb-keyval` はインストール済み。`vite.config.ts`・`tsconfig.json`・`index.html` も既存。**T4-1 は「新規作成」ではなく「既存プロジェクトへの追加」として読むこと。**

> **Phase 3 実装後の注意**: 以下のファイル・設定は Phase 3 で実装済みのため、T4-1 で再作成・再インストール不要:
> - `package.json`: `comlink ^4.4.1` インストール済み（`pnpm add comlink` 不要）
> - `vite.config.ts`: `worker: { format: "es" }` 追加済み（再設定不要）
> - `src/lib/OcrClient.ts`: Phase 3 で実装済み（高レベル API）
> - `src/lib/rpc.ts`: Comlink re-export 済み
> - `src/workers/pyodide.worker.ts`: Pyodide Worker 実装済み
> - `ndlocr_web` ホイール: `public/wheels/ndlocr_web-0.1.0-py3-none-any.whl` としてビルド済み
>
> Pyodide は CDN 動的 import で読み込む設計のため **`pyodide` npm パッケージはインストール不要**。

## ゴール

- 画像のドラッグ&ドロップ／ファイル選択／ペーストで OCR を実行できる。
- 結果としてプレーンテキスト・XML・JSON を表示し、ダウンロードできる。
- レイアウト検出結果（バウンディングボックス）をオーバーレイ表示する。
- 起動時のモデルロード進捗を明示する。
- 完全静的なバンドルで、Cloudflare Pages / Vercel / GitHub Pages に置ける。

## ディレクトリ

Phase 2 で作成済みのファイルに React / Tailwind / Zustand / Comlink 層を追加する。パッケージマネージャは **pnpm**。

```
ndlocr-lite-web/              ← Phase 2 で作成済み
  index.html                  ← Phase 2 で作成済み（Phase 4 で React エントリに置き換える）
  vite.config.ts              ← Phase 2 で作成済み（React・Tailwind プラグインを追加）
  package.json                ← Phase 2 で作成済み（React・Zustand・Comlink を追加）
  pnpm-lock.yaml              ← Phase 2 で作成済み
  public/
    ort/                      ← Phase 2 でビルド時コピー済み (ORT WASM)
    manifest.json             ← Phase 2 で作成済み (モデルハッシュ)
    verify.html               ← Phase 2 で作成済み (ORT スモークテスト)
    wheels/                   ← Phase 3 で作成済み (ndlocr_web-0.1.0-py3-none-any.whl)
    sample/                   # 任意: サンプル画像
  src/
    ort/                      ← Phase 2 で作成済み
      ortSession.ts
      modelCache.ts
      detector.ts
      recognizer.ts
    types/                    ← Phase 2 で作成済み
      ortTypes.ts
    workers/                  ← Phase 3 で作成済み
      pyodide.worker.ts
    lib/                      ← Phase 3 で作成済み (OcrClient.ts / rpc.ts)
      OcrClient.ts
      rpc.ts
      fileUtils.ts            # Phase 4 で新規作成 (File -> ImageBitmap, blob DL)
    main.tsx                  # Phase 4 で新規作成
    App.tsx                   # Phase 4 で新規作成
    components/
      DropZone.tsx
      LoadProgress.tsx
      ImageViewer.tsx         # オリジナル + 検出枠オーバーレイ
      ResultTabs.tsx          # Text / XML / JSON タブ
      DownloadButtons.tsx
      Header.tsx
      Footer.tsx              # ライセンス表記
    state/
      useOcrStore.ts          # Phase 4 で新規作成 (Zustand: 画像, 結果, 進捗)
    styles/
      tailwind.css
```

## TODO

### T4-1: プロジェクト初期化（既存プロジェクトへの追加）

- [ ] **T4-1a**: React を追加する（新規作成ではない）。
  ```sh
  cd ndlocr-lite-web
  pnpm add react react-dom
  pnpm add -D @types/react @types/react-dom @vitejs/plugin-react
  ```
- [ ] **T4-1b**: Tailwind v4 導入（既存 `vite.config.ts` に追記）。
  ```sh
  pnpm add -D tailwindcss @tailwindcss/vite
  ```
  `vite.config.ts` の `plugins` 配列に `tailwindcss()` を追加。`src/styles/tailwind.css` に `@import "tailwindcss";`。
- [ ] **T4-1c**: ESLint / Prettier 設定（`pnpm dlx typescript-eslint` など）。
- [ ] **T4-1d**: 既存 `vite.config.ts` に `@vitejs/plugin-react` と Tailwind プラグインを追加。`worker.format:"es"`・COOP/COEP ヘッダ・WASM コピーは Phase 3/2 で設定済みのため変更不要。
- [ ] **T4-1e**: Zustand を追加（`comlink` は Phase 3 で、`onnxruntime-web`・`idb-keyval` は Phase 2 でインストール済み。`pyodide` npm パッケージは CDN 動的 import を使うためインストール不要）。
  ```sh
  pnpm add zustand
  ```
- [ ] **T4-1f**: `index.html` を React エントリに置き換える（`<div id="root">` + `<script type="module" src="/src/main.tsx">`）。Phase 2 のプレースホルダ `index.html` は削除。

### T4-2: 画像入力

- [ ] **T4-2a**: `DropZone` コンポーネント。
  - ドラッグ&ドロップ、クリックで `<input type="file" accept="image/*">`。
  - クリップボード貼り付け（`document.addEventListener('paste', ...)` で `ClipboardEvent.clipboardData.items` を見る）。
- [ ] **T4-2b**: 受け入れる拡張子: jpg/jpeg/png/bmp/tiff/tif/jp2。
  - tiff/jp2 はブラウザ直読み不可のため、`utif` / `OpenJPH` など WASM で変換する（Phase 5 スコープでも可。まずは jpg/png のみ対応と割り切って良い）。
- [ ] **T4-2c**: 入力画像を `createImageBitmap` で Bitmap 化し、プレビュー表示 + OCR ワーカーへ送信。

### T4-3: 進捗表示

- [ ] **T4-3a**: `LoadProgress` コンポーネント。
  - 起動時の 5 ステップ（Pyodide → stdlib → ndlocr_web → ONNX x4 → セッション初期化）を縦並びで表示。
  - 各行にパーセンテージバー + 完了チェック。
- [ ] **T4-3b**: 途中で再試行が必要な場合のエラーバナー。ネットワークエラー時の再ダウンロードボタン。

### T4-4: 結果表示

- [ ] **T4-4a**: `ImageViewer` コンポーネント。
  - 左ペイン: 入力画像 + SVG で Bounding Box オーバーレイ。
  - クラス別に色分け（`src/deim.py` の `colorlist` と同じ配色）。
  - ホバーでテキストをツールチップ表示。
  - ズーム / パン対応（`react-zoom-pan-pinch` などのライブラリでも可）。
- [ ] **T4-4b**: `ResultTabs` コンポーネント。
  - **Text** タブ: 認識テキストを `pre` で表示、行番号付き。
  - **XML** タブ: `<OCRDATASET>...</OCRDATASET>` を syntax highlight（Prism.js）付きで表示。
  - **JSON** タブ: `contents[]` を折り畳み表示（`react-json-view-lite` 等）。
- [ ] **T4-4c**: `DownloadButtons` コンポーネント。
  - `.txt`, `.xml`, `.json`, `viz_xxx.png` のダウンロードボタン。
  - 複数まとめて DL したいケースは `jszip` で zip 化。

### T4-5: 縦書き / 読み順の表示補助

- [ ] **T4-5a**: OCR 結果の `isVertical` に応じて Text タブの文字方向を切り替え（CSS `writing-mode: vertical-rl`）。
- [ ] **T4-5b**: 行をクリックすると画像側の対応 BBox をハイライト（双方向リンク）。

### T4-6: 状態管理

- [ ] **T4-6a**: `useOcrStore` (Zustand) に次の state:
  - `phase: 'idle' | 'loading' | 'ready' | 'ocr' | 'error'`
  - `progress: { step: string; percent: number }[]`
  - `currentImage: { bitmap, width, height, fileName } | null`
  - `result: OcrResult | null`
  - `history: OcrResult[]`（直近の処理履歴を IndexedDB に保持、任意）

### T4-7: スクリーンキャプチャモード（任意）

- [ ] **T4-7a**: `navigator.mediaDevices.getDisplayMedia` + 「キャプチャ」ボタンで画面領域を取得 → `ImageBitmap` 化 → OCR 実行。既存 GUI (`ndlocr-lite-gui`) 相当の機能。Phase 0 で MVP 外にするのでも良い。

### T4-8: i18n

- [ ] **T4-8a**: `ndlocr-lite-gui/uicomponent/localelabel.py` の翻訳辞書を参考に `en` / `ja` の 2 言語を用意（`react-i18next`）。最低でも UI ラベルと「ブラウザで完結する」旨の注意書き。

### T4-9: アクセシビリティ / UX

- [ ] **T4-9a**: キーボード操作で全ボタンにアクセスできる。
- [ ] **T4-9b**: 初回訪問時の説明モーダル（モデル DL 容量、初回のみ時間がかかる旨、ローカル処理）。
- [ ] **T4-9c**: エラー時のフォールバック (`ErrorBoundary`) でスタックを隠し、サポート情報を出す。

### T4-10: フッター / ライセンス

- [ ] **T4-10a**: CC BY 4.0 表記、NDL への帰属、依存ライブラリ（Pyodide, onnxruntime-web, React, Vite, Tailwind）を列挙。
- [ ] **T4-10b**: GitHub リポジトリへのリンク、`/LICENCE_DEPENDENCEIES` 相当を HTML 化して配置。

## 受け入れ基準（Definition of Done）

- ユーザは URL を開いた直後に進捗バーを見ながら初回ダウンロード（~150MB）を待ち、準備完了後に 1 枚目の画像をドロップして結果が表示される。
- **2 枚目以降は 30 秒以内**に結果が表示される（Phase 0 T0-8 の性能 DoD 継承、典型的ノート PC、A4 スキャン画像想定）。
- 得られるテキスト／XML／JSON が CLI と同等（Phase 0 T0-8 / Phase 3 DoD 継承、文字一致率 ≥ 98%）。
- 画像・結果はネットワーク外に送信されていない（DevTools Network タブで確認、Phase 0 T0-6 / T0-8 継承）。
- 100% オフラインで再アクセス時に即起動できる（Cache Storage / Service Worker）。

## 非ゴール

- 複数ページの一括処理（ZIP ドロップ）。Phase 5 以降の拡張。
- PDF の直読み（`pdf.js` 連携）。Phase 5 以降。
- 結果の手動編集 UI。
