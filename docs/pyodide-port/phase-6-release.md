# Phase 6: 配布・CI/CD・ドキュメント

Web アプリを公開して継続運用可能な状態にする。モデル同梱の特殊性（~150MB）と、CC BY 4.0 ライセンスの表示義務に注意する。

## ゴール

- 本番用ビルドが自動で生成・配信される。
- モデル配信とフロントエンド配信が独立してデプロイ・差し替え可能。
- READMEと利用者ガイドが揃い、`ndlocr-lite-web` として GitHub から辿れる。

## TODO

### T6-1: ホスティング構成の決定

- [ ] **T6-1a**: 候補を比較し選定（Cloudflare Pages / GitHub Pages / Netlify / Vercel / 自前）。
  - 要件: COOP/COEP 付与可能（GitHub Pages は直接不可、Workers/CF Pages は可能）。Phase 0 T0-6 で決めた CSP / COOP / COEP を本番でも満たせること。
  - 要件: `public/ort/*.wasm` の `Content-Type: application/wasm`。
  - 推奨: **Cloudflare Pages + Functions**（無料枠で COOP/COEP/CSP 設定可）。
  - GitHub Pages を選ぶ場合は `coi-serviceworker` で COOP/COEP を擬似付与する前提（Phase 0 T0-6 参照）。
- [ ] **T6-1b**: モデル配信は **Phase 0 T0-4 / Phase 2 T2-1a で決めた GitHub Releases** を第一選択とし、フロントからは manifest URL を環境変数 (`VITE_MODEL_MANIFEST_URL`) で注入する。

### T6-2: ビルド / リリース自動化

- [ ] **T6-2a**: `.github/workflows/web-ci.yml`:
  - `node` + `pnpm` セットアップ → `pnpm install --frozen-lockfile` → `pnpm lint` → `pnpm typecheck` → `pnpm test` → `pnpm build`。
  - Pull Request で実行。Phase 0 で採用を決めた pnpm を CI でも使う。
- [ ] **T6-2b**: `.github/workflows/web-deploy.yml`:
  - main ブランチへの push で Cloudflare Pages へデプロイ。
  - Secrets: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`。
  - デプロイ時に Phase 0 T0-6 の COOP/COEP/CSP ヘッダが正しく出ているかを `curl -I` で検証するステップを追加。
- [ ] **T6-2c**: `.github/workflows/models-release.yml`:
  - タグ push (`models-v*`) で ONNX ファイル + `manifest.json` を GitHub Releases に上げる。SHA-256 は前段で計算（Phase 0 T0-4 の検証前提）。
- [ ] **T6-2d**: `ndlocr_web` ホイールも同じリリースに添付し、フロントからの `micropip.install(url)` 用 URL を固定化。

### T6-3: Pyodide / Python 側のテスト自動化

- [ ] **T6-3a**: `.github/workflows/python-ci.yml`:
  - **Phase 1 確定**: テストは `tests/test_imgops.py`（pytest、7 テスト）と `tests/pyodide_smoke.py`（スモーク）。
  - `tests/test_imgops.py` を `python:3.10`, `3.11` マトリクスで `pytest` 実行。
  - `tests/pyodide_smoke.py` を同環境で `python tests/pyodide_smoke.py` で実行（networkx / lxml / pyyaml 要インストール）。
  - Pyodide 実機スモーク（WASM 上）は `pyodide-test` で実行（時間がかかる場合は nightly のみ）。
- [ ] **T6-3b**: `benchmark.md` の数値更新を `workflow_dispatch` で任意実行できるようにする。

### T6-4: ドキュメント

- [ ] **T6-4a**: `README.md` 末尾に「ブラウザ版のご案内」セクションを追加し、公開 URL / モデル容量 / 対応ブラウザを記載。
- [ ] **T6-4b**: `ndlocr-lite-web/README.md` に開発手順（`pnpm install`, `pnpm dev`, `pnpm build`, `pnpm preview`）と COOP/COEP/CSP 要件（Phase 0 T0-6）を記載。
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

- [ ] **T6-7a**: ディレクトリ構成をモノレポにする（Phase 0 T0-3 / 各フェーズで確定した配置）:
  ```
  ndlocr-lite/
    src/                     # 既存 CLI（変更最小）
    src/ndlocr_web/          # Phase 1 成果
    ndlocr-lite-web/         # Phase 2-5 成果（pnpm + Vite + React）
    ndlocr-lite-gui/         # 既存デスクトップ GUI（据え置き）
    docs/pyodide-port/       # 本ドキュメント一式
  ```
- [ ] **T6-7b**: `pyproject.toml` に `ndlocr_web` のビルド設定を追加し、`hatch build` / `python -m build` で wheel を出せるようにする。
  - **Phase 1 確定**: `ndl_parser.py` は `ndlocr_web` 外にある。T3-1b の対処（`ndl_parser` を `ndlocr_web` 内に移動）が先決。
  - wheel の `packages` 設定例: `[tool.hatch.build.targets.wheel] packages = ["src/ndlocr_web"]`
  - 移動後: `[tool.hatch.build.targets.wheel] packages = ["src/ndlocr_web"]` で `ndl_parser.py` が `ndlocr_web/ndl_parser.py` になることを想定。
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
