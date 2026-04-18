# Phase 6: 配布・CI/CD・ドキュメント

Web アプリを公開して継続運用可能な状態にする。モデル同梱の特殊性（~150MB）と、CC BY 4.0 ライセンスの表示義務に注意する。

## ゴール

- 本番用ビルドが自動で生成・配信される。
- モデル配信とフロントエンド配信が独立してデプロイ・差し替え可能。
- READMEと利用者ガイドが揃い、`ndlocr-lite-web` として GitHub から辿れる。

## TODO

### T6-1: ホスティング構成の決定

- [ ] **T6-1a**: 候補を比較し選定（Cloudflare Pages / GitHub Pages / Netlify / Vercel / 自前）。
  - 要件: COOP/COEP 付与可能（GitHub Pages は直接不可、Workers/CF Pages は可能）。
  - 要件: `public/ort/*.wasm` の `Content-Type: application/wasm`。
  - 推奨: **Cloudflare Pages + Functions**（無料枠で COOP/COEP 設定可）。
- [ ] **T6-1b**: モデル配信は別ホスト（例: CF R2 / GitHub Releases）とし、フロントからは URL 注入。

### T6-2: ビルド / リリース自動化

- [ ] **T6-2a**: `.github/workflows/frontend-ci.yml`:
  - `node` セットアップ → `npm ci` → `npm run lint` → `npm run typecheck` → `npm run test` → `npm run build`。
  - Pull Request で実行。
- [ ] **T6-2b**: `.github/workflows/frontend-deploy.yml`:
  - main ブランチへの push で Cloudflare Pages へデプロイ。
  - Secrets: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`。
- [ ] **T6-2c**: `.github/workflows/models-release.yml`:
  - タグ push (`model-v*`) で ONNX ファイル + `manifest.json` を GitHub Releases に上げる。SHA-256 は前段で計算。
- [ ] **T6-2d**: `ndlocr_web` ホイールも同じリリースに添付し、フロントからの `micropip.install(url)` 用 URL を固定化。

### T6-3: Pyodide / Python 側のテスト自動化

- [ ] **T6-3a**: `.github/workflows/python-ci.yml`:
  - Phase 1 で作った `pytest` スイート（回帰テスト）を `python:3.10`, `3.11` で実行。
  - Pyodide スモークは `pyodide-test` で実行（時間がかかる場合は nightly のみ）。
- [ ] **T6-3b**: `benchmark.md` の数値更新を `workflow_dispatch` で任意実行できるようにする。

### T6-4: ドキュメント

- [ ] **T6-4a**: `README.md` 末尾に「ブラウザ版のご案内」セクションを追加し、公開 URL / モデル容量 / 対応ブラウザを記載。
- [ ] **T6-4b**: `frontend/README.md` に開発手順（`npm run dev`, `npm run build`, `npm run preview`）と COOP/COEP 要件を記載。
- [ ] **T6-4c**: `docs/pyodide-port/architecture.md` に本ポートの全体図（シーケンス / コンポーネント）を記載（Phase 0-3 の決定をまとめたもの）。
- [ ] **T6-4d**: ユーザー向けガイド（`docs/user-guide-web.md`）:
  - 画像の入れ方、結果の見方、ダウンロードの方法、ネットワーク送信しない旨の説明。
  - オフライン動作の仕組み（Cache Storage / PWA）。

### T6-5: ライセンス / 帰属表記

- [ ] **T6-5a**: フロントエンドバンドルに `LICENCE`（CC BY 4.0）と `LICENCE_DEPENDENCEIES` を含める（`/about` ページ or フッタの「ライセンス」モーダル）。
- [ ] **T6-5b**: Pyodide / onnxruntime-web / React 等、主要な再配布 OSS のライセンスも列挙。
- [ ] **T6-5c**: モデル配信時のライセンス表記も合わせて確認（学習データ由来の制約がないか再確認）。

### T6-6: 監視・運用

- [ ] **T6-6a**: 本番公開後のクラッシュレポート手段（Sentry など）を導入するか、しないか方針を決定。プライバシーの観点から **導入しない**選択肢も検討（ネットワーク送信しないことが売りのため）。
- [ ] **T6-6b**: ユーザからの不具合報告窓口（Issue テンプレート）。
- [ ] **T6-6c**: モデル更新 / バージョン差し替え手順のランブックを `docs/runbook-model-update.md` に記載。

### T6-7: 既存リポジトリとの統合

- [ ] **T6-7a**: ディレクトリ構成をモノレポにする:
  ```
  ndlocr-lite/
    src/                     # 既存 CLI（変更最小）
    src/ndlocr_web/          # Phase 1 成果
    frontend/                # Phase 2-5 成果
    docs/pyodide-port/       # 本ドキュメント一式
    ndlocr-lite-gui/         # 既存デスクトップ GUI（据え置き）
  ```
- [ ] **T6-7b**: `pyproject.toml` に `ndlocr_web` のビルド設定を追加し、`hatch build` / `python -m build` で wheel を出せるようにする。
- [ ] **T6-7c**: `CHANGELOG.md` を作り、ブラウザ版の初回リリースを記録。

### T6-8: リリース直前チェックリスト

- [ ] 公開 URL を開いて初回ダウンロード → OCR 実行 → 結果ダウンロードを一気通貫で手動テスト。
- [ ] DevTools Network タブで、外部サーバに画像が送られていないことを確認。
- [ ] Lighthouse: Performance / Accessibility / Best Practices / SEO / PWA を取得。
- [ ] ライセンス表記が UI に存在することを目視。
- [ ] サンプル画像での結果が CLI 実行結果とほぼ一致。
- [ ] COOP/COEP ヘッダが本番でも付与されている（`curl -I` で確認）。

## Phase 6 完了条件

- ブラウザ版 NDLOCR-Lite が公開 URL で動作し、Issue/PR ベースの運用に乗っている。
- 既存の CLI / デスクトップ版が壊れていない。
- 今後のモデル更新・フロント更新が独立して継続デプロイできる。
