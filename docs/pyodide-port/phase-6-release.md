# Phase 6: 配布・CI/CD・ドキュメント

Web アプリを公開して継続運用可能な状態にする。モデル同梱の特殊性（~150MB）と、CC BY 4.0 ライセンスの表示義務に注意する。

> **Phase 5 実装後の注意**（2026-04-20 完了）:
> - **ビルド確認済み**: `pnpm typecheck` + `pnpm lint` + `pnpm build` ともにエラーなし。
> - **ESLint 導入済み**: `eslint.config.js`（flat config: `@eslint/js` + `typescript-eslint` + `eslint-plugin-react-hooks`）と `pnpm lint` スクリプトが存在する。T6-2a の CI ワークフローにそのまま組み込める。
> - **PWA 対応済み**: `vite-plugin-pwa` で Service Worker + Web App Manifest を生成。本番配信は COOP/COEP 必須（T6-1a）に加え、Workbox の `navigateFallback` が機能するよう SPA 用 fallback を設定する。
> - **モデル配信**: PWA プリキャッシュには UI バンドル + ORT WASM + wheel のみを含め、ONNX モデル（~150MB）は既存の Cache Storage ロジック（`modelCache.ts`）が管理する。PWA のオフライン検証（T6-8）では初回アクセスで一度モデルをダウンロードさせてから `context.setOffline(true)` で 2 回目を実行する。
> - **Web 側テストなし**: `pnpm test` スクリプトは未設定。E2E は T6-3 相当の Playwright でカバーする想定。CI に含める場合は T6-2a を調整すること。
> - **Footer ライセンス表記は実装済み**: CC BY 4.0 / NDL 帰属 / 主要依存のクレジットを Footer に表示済み。T6-5a/b の LICENCE_DEPENDENCEIES HTML 化は残タスク。
> - **ベースライン計測**: `docs/pyodide-port/benchmark.md` に Phase 4 時点の数値と Phase 5 目標を記録済み。T6-3b での自動更新の参照先として利用する。

## ゴール

- 本番用ビルドが自動で生成・配信される。
- モデル配信とフロントエンド配信が独立してデプロイ・差し替え可能。
- READMEと利用者ガイドが揃い、`ndlocr-lite-web` として GitHub から辿れる。

## TODO

### T6-1: ホスティング構成の決定

- [x] **T6-1a**: 候補を比較し選定 → **Cloudflare Pages** に決定（COOP/COEP/CSP 設定可、HTTPS 必須、WASM `Content-Type` 自動付与、SPA fallback 対応）。`ndlocr-lite-web/README.md` に `_headers` 設定例を記載。
- [x] **T6-1b**: モデル配信は GitHub Releases を第一選択とし、`VITE_MODEL_MANIFEST_URL` 環境変数で URL を注入できるよう `OcrClient.ts` を修正済み。

### T6-2: ビルド / リリース自動化

- [x] **T6-2a**: `.github/workflows/web-ci.yml` — PR で pnpm lint → typecheck → build を実行。
- [x] **T6-2b**: `.github/workflows/web-deploy.yml` — main push で Cloudflare Pages にデプロイ。`curl -I` による COOP/COEP ヘッダ検証ステップ付き。Secrets: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`。
- [x] **T6-2c**: `.github/workflows/models-release.yml` — `models-v*` タグで ONNX 4 本 + `manifest.json` を GitHub Releases にアップロード。
- [x] **T6-2d**: `ndlocr_web` ホイールも `models-release` ワークフローで同一 Release に添付。

### T6-3: Pyodide / Python 側のテスト自動化

- [x] **T6-3a**: `.github/workflows/python-ci.yml` — `test_imgops.py`（pytest）と `pyodide_smoke.py` を Python 3.10/3.11 マトリクスで実行。
- [ ] **T6-3b**: `benchmark.md` 数値の自動更新（`workflow_dispatch`）— Playwright + Performance API による自動計測は未実装（T5-0b 残タスク）。手動更新で対応。

### T6-4: ドキュメント

- [x] **T6-4a**: `README.md` に「ブラウザ版のご案内」セクション追加（公開 URL・モデル容量・対応ブラウザ・プライバシー説明）。
- [x] **T6-4b**: `ndlocr-lite-web/README.md` 新規作成（開発手順・環境変数・COOP/COEP 要件・PWA 挙動・モデル差し替え案内）。
- [x] **T6-4c**: `docs/pyodide-port/architecture.md` 新規作成（コンポーネント図・初期化シーケンス・OCR 実行シーケンス・技術選定根拠・ヘッダ要件）。
- [x] **T6-4d**: `docs/user-guide-web.md` 新規作成（画像入力・OCR・結果確認・ダウンロード・オフライン・PWA インストール・低メモリ端末・プライバシー）。

### T6-5: ライセンス / 帰属表記

- [x] **T6-5a**: フッタの「ライセンス」ボタンでモーダル（`LicenseModal.tsx`）を開けるように実装。CC BY 4.0 帰属も表示。
- [x] **T6-5b**: Pyodide / onnxruntime-web / React / Vite / Tailwind / Comlink / Zustand / JSZip / idb-keyval / vite-plugin-pwa / Workbox / numpy / Pillow / lxml / networkx / PyYAML のライセンスを `LicenseModal` に列挙。
- [ ] **T6-5c**: モデル配信時のライセンス表記（学習データ由来の制約）は手動確認が必要。

### T6-6: 監視・運用

- [x] **T6-6a**: クラッシュレポートは**導入しない**方針に決定。「画像をサーバに送信しない」がアプリの最大の特徴であるため、外部サービスへの通信を追加しない。不具合は GitHub Issues で収集。
- [x] **T6-6b**: GitHub Issue テンプレート 3 種を追加（バグ報告・機能要望・OCR 精度問題）。
- [x] **T6-6c**: `docs/runbook-model-update.md` 作成（SHA-256 計算→manifest 更新→タグ作成→CI 確認→ロールバック）。

### T6-7: 既存リポジトリとの統合

- [x] **T6-7a**: ディレクトリ構成をモノレポにする（Phase 0 T0-3 / 各フェーズで確定した配置）:
  ```
  ndlocr-lite/
    src/                     # 既存 CLI（変更最小）
    src/ndlocr_web/          # Phase 1 成果
    ndlocr-lite-web/         # Phase 2-5 成果（pnpm + Vite + React）
    ndlocr-lite-gui/         # 既存デスクトップ GUI（据え置き）
    docs/pyodide-port/       # 本ドキュメント一式
  ```
- [x] **T6-7b**: ~~`pyproject.toml` に `ndlocr_web` のビルド設定を追加し、wheel を出せるようにする。~~ **Phase 3 で実装済み**。
  - `src/pyproject.toml` 作成済み（`setuptools.build_meta` バックエンド）。
  - `ndl_parser.py` を `src/ndlocr_web/` 内に移動済み（T3-1b 対処完了）。
  - `python -m build --wheel --no-isolation --outdir ndlocr-lite-web/public/wheels src/` でビルド可能。
  - `scripts/build-wheel.sh` として自動化スクリプトも追加済み。
  - ビルド済みホイール `ndlocr_web-0.1.0-py3-none-any.whl` は `ndlocr-lite-web/public/wheels/` に配置済み。
  - T6-2d での GitHub Releases 添付は任意（CDN 不要のためアプリバンドルに同梱する方針）。
- [x] **T6-7c**: `CHANGELOG.md` を作り、ブラウザ版の初回リリースを記録。

### T6-8: リリース直前チェックリスト

- [ ] 公開 URL を開いて初回ダウンロード → OCR 実行 → 結果ダウンロードを一気通貫で手動テスト。
- [ ] DevTools Network タブで、外部サーバに画像が送られていないことを確認。
- [ ] Lighthouse: Performance / Accessibility / Best Practices / SEO / PWA を取得。**PWA スコアで「installable」と「offline-capable」にチェックが付くこと**（Phase 5 T5-6）。
- [ ] ライセンス表記が UI に存在することを目視。
- [ ] サンプル画像での結果が CLI 実行結果とほぼ一致。
- [ ] COOP/COEP ヘッダが本番でも付与されている（`curl -I` で確認）。
- [ ] **PWA インストール動作確認**: Chrome / Edge のアドレスバーに「インストール」アイコンが表示され、クリックでデスクトップ / ホーム画面へ追加できる。
- [ ] **2 回目オフライン動作確認**: 初回アクセスでモデルまで読み込ませた後、`DevTools → Network → Offline` にしてリロード → 正常起動 → OCR 実行ができる（Phase 5 T5-6b 手動版）。
- [ ] **低メモリ端末での挙動確認**: `chrome://inspect/#devices` で低メモリモードをエミュレート、もしくは実機（4 GB 未満）で警告バナーが表示されることを確認（Phase 5 T5-7c）。

## Phase 6 完了条件

- ブラウザ版 NDLOCR-Lite が公開 URL で動作し、Issue/PR ベースの運用に乗っている。
- 既存の CLI / デスクトップ版が壊れていない。
- 今後のモデル更新・フロント更新が独立して継続デプロイできる。
