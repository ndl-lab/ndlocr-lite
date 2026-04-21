# Runbook: モデル更新手順

NDLOCR-Lite Web で使用する ONNX モデルを新バージョンに差し替える手順です。

## 概要

モデルファイル（4 つの `.onnx`）と `manifest.json` を GitHub Releases に新規タグとしてアップロードし、フロントエンドが次回起動時に新バージョンを取得するようにします。

---

## 前提

- `src/model/` に 4 つの新 ONNX ファイルが用意されている。
- `python -m build` が実行できる環境（Python 3.10+、`pip install build`）。
- GitHub の書き込み権限があるアカウントで `gh` CLI が認証されている。

---

## 手順

### 1. 新しい ONNX ファイルを配置する

```bash
# src/model/ に 4 ファイルを配置
ls src/model/
# deim-s-1024x1024.onnx
# parseq-ndl-16x256-30-*.onnx
# parseq-ndl-16x384-50-*.onnx
# parseq-ndl-16x768-100-*.onnx
```

### 2. SHA-256 を計算する

```bash
sha256sum src/model/*.onnx
```

### 3. manifest.json を更新する

`ndlocr-lite-web/public/manifest.json` を開き、以下を更新します。

```json
{
  "version": "YYYY.MM.DD",
  "baseUrl": "https://github.com/ndl-lab/ndlocr-lite/releases/download/models-vYYYY.MM.DD/",
  "models": [
    {
      "id": "deim",
      "file": "<新しいファイル名>.onnx",
      "sha256": "<計算した SHA-256>",
      "size": <バイト数>
    },
    ...
  ]
}
```

**注意**: `baseUrl` の末尾スラッシュを忘れずに。

### 4. Cache Storage のバージョンを上げる（必要な場合）

モデルの互換性が変わった場合は `src/ort/modelCache.ts` の `CACHE_NAME` を更新します。

```typescript
// 例: v1 → v2
const CACHE_NAME = "ndlocr-models-v2";
```

これにより古いキャッシュが自動で無効化され、ユーザが次回起動時に新モデルをダウンロードします。

### 5. ndlocr_web のバージョンが変わった場合はホイールを再ビルド

Python パッケージ側に変更があった場合：

```bash
bash scripts/build-wheel.sh
```

`ndlocr-lite-web/public/wheels/ndlocr_web-*.whl` が更新されます。

### 6. 変更をコミットしてタグを打つ

```bash
git add ndlocr-lite-web/public/manifest.json
git add ndlocr-lite-web/public/wheels/  # ホイール再ビルドした場合
git commit -m "chore: update ONNX models to YYYY.MM.DD"
git tag models-vYYYY.MM.DD
git push origin main
git push origin models-vYYYY.MM.DD
```

### 7. CI が GitHub Releases を自動作成する

`models-release` ワークフローが起動し、以下をアップロードします。

- `*.onnx` × 4
- `manifest.json`
- `ndlocr_web-*.whl`

GitHub Actions のログで完了を確認してください。

### 8. デプロイを確認する

ブラウザのキャッシュをクリア（または別のプロファイル）してから Web 版を開き、新しいモデルがダウンロードされることを確認します。

DevTools → Application → Cache Storage → `ndlocr-models-v*` のエントリに新しいバージョンの URL が表示されれば OK。

---

## ロールバック手順

問題が発生した場合は、`manifest.json` を旧バージョンに戻してコミット・プッシュします。ユーザのブラウザキャッシュには古いモデルが残っているため、多くの場合は再ダウンロードなしで旧バージョンで動作します。

```bash
git revert HEAD  # manifest.json を戻す
git push origin main
```

---

## 関連ファイル

| ファイル | 役割 |
|---------|------|
| `ndlocr-lite-web/public/manifest.json` | モデルメタデータ（URL・SHA-256・サイズ） |
| `ndlocr-lite-web/src/ort/modelCache.ts` | Cache Storage 管理・ハッシュ検証 |
| `ndlocr-lite-web/src/ort/ortSession.ts` | ORT InferenceSession 管理 |
| `scripts/build-wheel.sh` | ndlocr_web ホイールビルドスクリプト |
| `.github/workflows/models-release.yml` | GitHub Releases 自動作成ワークフロー |
