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

- [x] **T4-1a**: ~~React を追加する~~ **Phase 4 で実装済み**。`react 19.2.5`, `react-dom 19.2.5` インストール済み。`@types/react`, `@types/react-dom`, `@vitejs/plugin-react@4.7.0`（Vite 6 互換版）追加済み。
- [x] **T4-1b**: ~~Tailwind v4 導入~~ **Phase 4 で実装済み**。`tailwindcss 4.2.2`, `@tailwindcss/vite 4.2.2` インストール済み。`vite.config.ts` に `tailwindcss()` 追加。`src/styles/tailwind.css`（`@import "tailwindcss";`）作成済み。
- [ ] **T4-1c**: ESLint / Prettier 設定（`pnpm dlx typescript-eslint` など）。**Phase 4 でスキップ。** `pnpm lint` スクリプトは未追加。Phase 5/6 対応推奨（T6-2a の CI も lint を想定しているため）。
- [x] **T4-1d**: ~~既存 `vite.config.ts` に `@vitejs/plugin-react` と Tailwind プラグインを追加~~ **Phase 4 で実装済み**。`worker.format:"es"`・COOP/COEP ヘッダ・WASM コピーは Phase 3/2 から継続。
- [x] **T4-1e**: ~~Zustand を追加~~ **Phase 4 で実装済み**。`zustand 5.0.12` インストール済み。`jszip 3.10.1` も同時追加（T4-4c 用）。
- [x] **T4-1f**: ~~`index.html` を React エントリに置き換える~~ **Phase 4 で実装済み**。`<div id="root">` + `<script type="module" src="/src/main.tsx">` に置き換え済み。

### T4-2: 画像入力

- [x] **T4-2a**: ~~`DropZone` コンポーネント~~ **Phase 4 で実装済み**（`src/components/DropZone.tsx`）。D&D・クリックファイル選択・Ctrl+V ペースト対応。キーボード操作（Enter/Space）対応。
- [ ] **T4-2b**: 受け入れる拡張子: jpg/jpeg/png/bmp/tiff/tif/jp2。**tiff/jp2 は Phase 5 スコープに延期。** 現在は JPEG・PNG・BMP・WebP のみ対応（`createImageBitmap` が処理できる形式）。
- [x] **T4-2c**: ~~入力画像を `createImageBitmap` で Bitmap 化~~ **Phase 4 で実装済み**。`App.tsx` の `handleFile()` で `createImageBitmap` を呼び、幅・高さを取得してストアに保存後、`OcrClient.ocr()` に渡す。

### T4-3: 進捗表示

- [x] **T4-3a**: ~~`LoadProgress` コンポーネント~~ **Phase 4 で実装済み**（`src/components/LoadProgress.tsx`）。5 ステージ縦並び、ステージごとにパーセンテージバー + 完了チェックマーク表示。
- [x] **T4-3b**: ~~エラーバナー~~ **Phase 4 で実装済み**。`App.tsx` の `ErrorDisplay` コンポーネントでエラーメッセージと「再試行」ボタンを表示。再試行時に Worker を再生成して `initClient()` を呼び直す。

### T4-4: 結果表示

- [x] **T4-4a**: ~~`ImageViewer` コンポーネント~~ **Phase 4 で実装済み**（`src/components/ImageViewer.tsx`）。SVG オーバーレイで BBox を描画（`deim.py` `colorlist` と同配色）。ホバーでテキストツールチップ表示。ズーム/パンは未導入（代わりに CSS scroll）。Phase 5 で `react-zoom-pan-pinch` 等の追加を検討。
- [x] **T4-4b**: ~~`ResultTabs` コンポーネント~~ **Phase 4 で実装済み**（`src/components/ResultTabs.tsx`）。Text / XML / JSON タブ + コピーボタン。Prism.js ハイライトは未導入（plain `<pre>` を使用）。Phase 5 で必要に応じて追加可。
- [x] **T4-4c**: ~~`DownloadButtons` コンポーネント~~ **Phase 4 で実装済み**（`src/components/DownloadButtons.tsx`）。`.txt`・`.xml`・`.json`・`viz.png` の個別 DL、および JSZip によるまとめて ZIP ダウンロード。

### T4-5: 縦書き / 読み順の表示補助

- [x] **T4-5a**: ~~`writing-mode: vertical-rl` 切り替え~~ **Phase 4 で実装済み**。`ResultTabs.tsx` の `isVerticalPage()` で行 BBox の `h > w` 比率から自動判定し、50% 超で `writing-mode: vertical-rl` を適用。
- [x] **T4-5b**: ~~双方向ライン ハイライト~~ **Phase 4 で実装済み**。`useOcrStore.highlightedLineId` を通じて `ImageViewer`（SVG rect の fill/stroke 変更）と `ResultTabs`（行背景色変更）が双方向に連動。

### T4-6: 状態管理

- [x] **T4-6a**: ~~`useOcrStore` (Zustand)~~ **Phase 4 で実装済み**（`src/state/useOcrStore.ts`）。実装済み state: `phase: 'idle' | 'loading' | 'ready' | 'ocr' | 'done' | 'error'`、`progress: Record<string, number>`、`currentImage`（objectUrl・width・height・fileName）、`result: OcrResult | null`、`lang: 'en' | 'ja'`、`highlightedLineId`。`history: OcrResult[]`（IndexedDB 保持）は未実装（任意項目）。

### T4-7: スクリーンキャプチャモード（任意）

- [ ] **T4-7a**: スクリーンキャプチャ機能。**Phase 4 では MVP 外としてスキップ。** Phase 5 以降で検討。

### T4-8: i18n

- [x] **T4-8a**: ~~EN / JA 2 言語対応~~ **Phase 4 で実装済み**（`src/lib/i18n.ts`）。`react-i18next` の代わりにシンプルな `t(lang, key)` 関数を実装（依存ゼロ）。`navigator.language` で初期言語を自動検出。Header にトグルボタンあり。

### T4-9: アクセシビリティ / UX

- [x] **T4-9a**: ~~キーボード操作~~ **Phase 4 で実装済み**。DropZone は `tabIndex=0` + Enter/Space でファイル選択。全ボタンはデフォルト `<button>` で Tab フォーカス可能。
- [x] **T4-9b**: ~~初回訪問モーダル~~ **Phase 4 で実装済み**（`App.tsx` `InitModal`）。`localStorage` の有無で初回のみ表示。モデル容量・オフライン動作・プライバシーを説明。
- [x] **T4-9c**: ~~`ErrorBoundary`~~ **Phase 4 で実装済み**（`src/components/ErrorBoundary.tsx`）。React の `getDerivedStateFromError` でスタックを隠し、Dismiss ボタンを表示。

### T4-10: フッター / ライセンス

- [x] **T4-10a**: ~~CC BY 4.0 表記~~ **Phase 4 で実装済み**（`src/components/Footer.tsx`）。CC BY 4.0・NDL 帰属・Pyodide / onnxruntime-web / React / Vite / Tailwind CSS のクレジット表示。プライバシー注記も含む。
- [ ] **T4-10b**: GitHub リポジトリへのリンクは Footer に追加済み。`/LICENCE_DEPENDENCEIES` 相当を HTML 化した専用ページは**未実装**。Phase 6 T6-5a/b で対応予定。

## 受け入れ基準（Definition of Done）

- ユーザは URL を開いた直後に進捗バーを見ながら初回ダウンロード（~150MB）を待ち、準備完了後に 1 枚目の画像をドロップして結果が表示される。
- **2 枚目以降は 30 秒以内**に結果が表示される（Phase 0 T0-8 の性能 DoD 継承、典型的ノート PC、A4 スキャン画像想定）。
- 得られるテキスト／XML／JSON が CLI と同等（Phase 0 T0-8 / Phase 3 DoD 継承、文字一致率 ≥ 98%）。
- 画像・結果はネットワーク外に送信されていない（DevTools Network タブで確認、Phase 0 T0-6 / T0-8 継承）。
- 100% オフラインで再アクセス時に即起動できる（Cache Storage / Service Worker）。

## Phase 4 実装結果サマリ

> **Phase 4 完了**（2026-04-19）。以下は Phase 5/6 実装者向けの引き継ぎ情報。

### 実装済みファイル一覧（`ndlocr-lite-web/src/`）

| ファイル | 役割 |
|---|---|
| `main.tsx` | React エントリ（StrictMode、`#root` にマウント） |
| `App.tsx` | フェーズ状態機械（idle→loading→ready→ocr→done→error）、OcrClient ライフサイクル管理、グローバル paste ハンドラ、InitModal |
| `state/useOcrStore.ts` | Zustand ストア（phase, progress, currentImage, result, lang, highlightedLineId） |
| `lib/i18n.ts` | EN/JA 翻訳定数 + `t(lang, key)` 関数（react-i18next 不使用） |
| `lib/fileUtils.ts` | `downloadBlob()` / `baseName()` ユーティリティ |
| `styles/tailwind.css` | Tailwind v4 エントリ（`@import "tailwindcss"`） |
| `components/Header.tsx` | タイトル・サブタイトル・言語トグル |
| `components/Footer.tsx` | プライバシー注記・CC BY 4.0・依存クレジット・GitHub リンク |
| `components/DropZone.tsx` | D&D / クリック / Ctrl+V ペースト、キーボードアクセス対応 |
| `components/LoadProgress.tsx` | 5 ステージ進捗バー（pyodide→packages→wheel→models→init） |
| `components/ImageViewer.tsx` | 画像表示 + SVG BBox オーバーレイ（deim.py colorlist 配色）+ ホバーツールチップ |
| `components/ResultTabs.tsx` | Text/XML/JSON タブ、コピーボタン、縦書き writing-mode、双方向ハイライト |
| `components/DownloadButtons.tsx` | 個別 DL（.txt/.xml/.json/viz.png）+ JSZip 一括 ZIP |
| `components/ErrorBoundary.tsx` | React クラスコンポーネント（getDerivedStateFromError） |

### 未実装・Phase 5/6 への引き継ぎ事項

| 項目 | 優先度 | 担当フェーズ |
|---|---|---|
| ESLint / Prettier（T4-1c） | 高（T6-2a CI の lint ステップに必要） | Phase 5 前に対応推奨 |
| tiff/jp2 WASM デコード（T4-2b） | 中 | Phase 5 |
| ImageViewer ズーム/パン（T4-4a） | 低 | Phase 5 |
| Prism.js XML ハイライト（T4-4b） | 低 | Phase 5 |
| IndexedDB 処理履歴（T4-6a） | 低（任意） | Phase 5 |
| スクリーンキャプチャ（T4-7a） | 低（任意） | Phase 5/6 |
| LICENCE_DEPENDENCEIES HTML（T4-10b） | 中 | Phase 6 |

## 非ゴール

- 複数ページの一括処理（ZIP ドロップ）。Phase 5 以降の拡張。
- PDF の直読み（`pdf.js` 連携）。Phase 5 以降。
- 結果の手動編集 UI。
