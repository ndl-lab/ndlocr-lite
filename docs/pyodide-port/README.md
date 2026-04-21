# NDLOCR-Lite ブラウザ移植（Pyodide 版） ロードマップ

本ディレクトリは、NDLOCR-Lite を **ブラウザ単体で動作するスタンドアロン Web アプリ** に移植するための段階的実装 TODO です。各フェーズは独立した Markdown として分割されています。

## 前提アーキテクチャ

Phase 3/4/5 までの実装構成:

```
┌────────────────────── Browser ──────────────────────────────┐
│                                                             │
│  [ Main Thread ]                                            │
│   React + Vite UI, File I/O, Canvas 可視化                  │
│   Service Worker 登録 (Phase 5 T5-6)                        │
│        │                                                    │
│        └──▶ [ Worker: Pyodide + onnxruntime-web ]           │
│              ndlocr_web (Python, 前処理/後処理/整序/XML生成) │
│              DEIM + PARSeq ×3 推論 (WebGPU/WASM SIMD)        │
│                                                             │
│  モデルキャッシュ : Cache Storage API (~150MB)               │
│  UI + ORT WASM + wheel : Service Worker precache             │
└─────────────────────────────────────────────────────────────┘
```

※ Phase 0 の計画では Pyodide と ORT を別 Worker にする想定だったが、Phase 3 実装時に単一 Worker に統合した（`src/workers/pyodide.worker.ts` に ORT セッションを同居）。Phase 5 T5-2c で PARSeq セッションのみ別 Worker へ分離する案を追加。

## フェーズ一覧

| # | タイトル | ファイル | 主なゴール | 状態 |
|---|---|---|---|---|
| 0 | 調査・前提整理 | [phase-0-feasibility.md](./phase-0-feasibility.md) | 技術選定 / 依存調査 / DoD 定義 | ✅ 完了 |
| 1 | Python コアのブラウザ互換化 | [phase-1-python-refactor.md](./phase-1-python-refactor.md) | `opencv` / `onnxruntime` 依存除去、純関数化、`ndlocr_web` パッケージ新設 | ✅ 完了 |
| 2 | モデル配信 & onnxruntime-web 統合 | [phase-2-models-and-ort-web.md](./phase-2-models-and-ort-web.md) | Cache Storage、ORT-web セッション、`infer()` I/F 確定 | ✅ 完了 |
| 3 | Pyodide 統合 & Web Worker 化 | [phase-3-pyodide-integration.md](./phase-3-pyodide-integration.md) | Worker 化、Python ⇄ JS RPC、進捗配信 | ✅ 完了 |
| 4 | フロントエンド UI 実装 | [phase-4-frontend-ui.md](./phase-4-frontend-ui.md) | D&D / 結果表示 / DL / 可視化 | ✅ 完了（2026-04-19） |
| 5 | パフォーマンス / サイズ最適化 | [phase-5-performance.md](./phase-5-performance.md) | 量子化 / WebGPU / 並列 / PWA | 🟡 主要最適化実装済み（2026-04-20）、量子化 / 並列 Worker / Playwright 自動計測は残タスク |
| 6 | 配布・CI/CD・ドキュメント | [phase-6-release.md](./phase-6-release.md) | 本番配信、GitHub Actions、ライセンス表記 | ✅ 完了（2026-04-21）|
| ベンチマーク | [benchmark.md](./benchmark.md) | Phase 4 baseline + Phase 5 目標値 / 進捗表 | — |

## 進め方のガイドライン

- **原則直列**: Phase 0 → 1 → 2 → 3 → 4 → 5 → 6 の順で進める。ただし Phase 2 は Phase 1 と並行可（ORT 側は Python 非依存のため）。
- **各フェーズ末にサンプル画像 `resource/digidepo_3048008_0025.jpg` で動作確認**し、CLI 結果との差分を記録する。
- 既存 CLI (`src/ocr.py`) とデスクトップ GUI (`ndlocr-lite-gui/`) は**破壊しない**。ブラウザ版はサイドバイサイドで新設する。
- ライセンス表記（CC BY 4.0）をフロントにも必ず反映する。

## 用語

- **ndlocr_web**: Phase 1 で新設する純 Python パッケージ。ファイル I/O 非依存で `run_ocr_on_image(rgb, detector, recognizers...)` を提供。
- **OcrClient**: Phase 3 で作る Main Thread 側の高レベル API（Worker を隠蔽）。
- **ORT Worker**: onnxruntime-web を抱える Web Worker。Pyodide Worker から RPC で呼ばれる。
