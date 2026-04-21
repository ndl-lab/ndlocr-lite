# ndlocr-lite-web — 開発者ガイド

NDLOCR-Lite のブラウザ版フロントエンド（Vite 6 + React 19 + TypeScript 5 + Tailwind CSS v4）です。Pyodide と onnxruntime-web を使い、OCR 処理をすべてブラウザ内で完結させます。

## 必要環境

- Node.js ≥ 20
- pnpm ≥ 9（`npm i -g pnpm` でインストール）

## セットアップ

```bash
pnpm install
```

## 開発サーバ起動

```bash
pnpm dev
```

`http://localhost:5173` が開きます。COOP/COEP ヘッダは Vite dev サーバが自動的に付与します。

## 型チェック

```bash
pnpm typecheck
```

## Lint

```bash
pnpm lint
```

ESLint flat config（`eslint.config.js`）を使用しています。`@eslint/js` + `typescript-eslint` + `eslint-plugin-react-hooks` を含みます。

## プロダクションビルド

```bash
pnpm build
```

`dist/` に出力されます。Service Worker（`dist/sw.js`）も生成されます。

## プレビュー（ビルド後確認）

```bash
pnpm preview
```

`http://localhost:4173` で `dist/` の内容を確認できます。COOP/COEP ヘッダは preview サーバも自動的に付与します。

## 環境変数

| 変数 | 説明 | デフォルト |
|------|------|-----------|
| `VITE_MODEL_MANIFEST_URL` | モデルマニフェスト JSON の URL。GitHub Releases など外部ホスティングを使う場合に設定。 | `<BASE_URL>manifest.json`（バンドル同梱） |

`.env.local` に記述するか、CI の環境変数として渡します。

## ディレクトリ構成

```
ndlocr-lite-web/
├── public/
│   ├── manifest.json        # モデルメタデータ（SHA-256・URL）
│   ├── wheels/              # ndlocr_web Python wheel（Pyodide が micropip でロード）
│   ├── ort/                 # onnxruntime-web WASM（ビルド時に自動コピー）
│   ├── icons/               # PWA アイコン
│   └── sample/              # サンプル画像
├── src/
│   ├── App.tsx              # メインステートマシン（init → loading → ready → ocr）
│   ├── components/          # UI コンポーネント
│   ├── lib/
│   │   ├── OcrClient.ts     # Worker を隠蔽する高レベル API
│   │   ├── i18n.ts          # EN/JA 翻訳
│   │   └── fileUtils.ts     # ZIP ダウンロードなど
│   ├── ort/                 # onnxruntime-web セッション管理
│   ├── state/               # Zustand ストア
│   ├── types/               # 共有 TypeScript 型
│   └── workers/
│       └── pyodide.worker.ts  # Pyodide + ORT を動かす Web Worker
├── vite.config.ts
├── tsconfig.json
├── eslint.config.js
└── package.json
```

## COOP / COEP / CSP 要件

WebAssembly の `SharedArrayBuffer` を使うため、**すべてのレスポンス**に以下のヘッダが必要です。

```
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
```

- **開発・プレビュー**: Vite が自動付与します。
- **本番（Cloudflare Pages）**: `_headers` ファイルまたは Functions で設定します（後述）。
- **GitHub Pages**: `coi-serviceworker` で擬似付与できますが、PWA の Service Worker との競合に注意してください。

### Cloudflare Pages の `_headers` 設定例

`dist/` に `_headers` を追加するか、Vite ビルドの `public/_headers` として配置します。

```
/*
  Cross-Origin-Opener-Policy: same-origin
  Cross-Origin-Embedder-Policy: require-corp
  Cross-Origin-Resource-Policy: same-origin
```

## PWA と Service Worker

- `vite-plugin-pwa`（Workbox）が `dist/sw.js` を生成します。
- **本番 HTTPS 必須**: `localhost` 以外で Service Worker を有効化するには HTTPS が必要です。
- **プリキャッシュ対象**: UI バンドル（JS/CSS/HTML）、ORT WASM ファイル、ndlocr_web wheel。
- **ONNX モデル（〜150 MB）**: プリキャッシュ対象外。`modelCache.ts` の Cache Storage ロジックが初回ダウンロード後に管理します。
- **オフライン動作確認**: Chrome DevTools → Network → Offline にした状態で 2 回目のリロード → OCR 実行が完了すれば OK。

## モデルの差し替え

1. `ndlocr-lite-web/public/manifest.json` の `baseUrl`・`sha256`・`size` を更新する。
2. `git tag models-vYYYY.MM.DD && git push origin models-vYYYY.MM.DD`
3. `models-release` CI ワークフローが GitHub Releases に ONNX + manifest + wheel をアップロードします。

詳細は [docs/runbook-model-update.md](../docs/runbook-model-update.md) を参照。
