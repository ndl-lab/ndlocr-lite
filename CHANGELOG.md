# Changelog

All notable changes to NDLOCR-Lite are documented here.

## [Unreleased] — Web 版初回リリース準備中

### Added

#### ブラウザ版（ndlocr-lite-web）— Phase 0〜6 成果

- **Phase 0**: 技術選定・アーキテクチャ設計（Vite 6 + React 19 + Pyodide + onnxruntime-web）
- **Phase 1**: Python コアの `ndlocr_web` パッケージ新設（OpenCV 依存除去、純関数化）
- **Phase 2**: onnxruntime-web 統合・ONNX モデルの Cache Storage キャッシュ
- **Phase 3**: Pyodide Web Worker 化・Comlink RPC・進捗配信
- **Phase 4**: React SPA UI（ドラッグ＆ドロップ、バウンディングボックス可視化、テキスト/XML/JSON タブ、ZIP ダウンロード、EN/JA i18n）
- **Phase 5**: パフォーマンス最適化（WASM SIMD、PWA、Service Worker、manualChunks、GC、低メモリ警告）
- **Phase 6**: CI/CD・ドキュメント・ライセンス表記
  - GitHub Actions: `web-ci`（PR lint/typecheck/build）
  - GitHub Actions: `web-deploy`（main → Cloudflare Pages）
  - GitHub Actions: `models-release`（ONNX + wheel → GitHub Releases）
  - GitHub Actions: `python-ci`（pytest + smoke テスト、Python 3.10/3.11）
  - `VITE_MODEL_MANIFEST_URL` 環境変数でモデル配信先を切り替え可能に
  - `ndlocr-lite-web/README.md` 開発者ガイド追加
  - `docs/pyodide-port/architecture.md` 全体アーキテクチャ図追加
  - `docs/user-guide-web.md` 利用者向けガイド追加
  - フッタにライセンスモーダル（LicenseModal コンポーネント）追加
  - `docs/runbook-model-update.md` モデル更新手順追加
  - GitHub Issue テンプレート追加
  - `CHANGELOG.md` 追加

---

## 既存リリース（デスクトップ版・CLI 版）

既存の CLI および Flet デスクトップ版のリリース履歴については、
[GitHub Releases](https://github.com/ndl-lab/ndlocr-lite/releases) をご参照ください。
