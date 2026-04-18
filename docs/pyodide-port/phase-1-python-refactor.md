# Phase 1: Python コアのブラウザ互換化リファクタリング

Pyodide 上で動作する Python モジュール群（以降 **ndlocr_web**）を、既存 `src/` から切り出して作成する。ファイル I/O・`onnxruntime` 依存・OpenCV 依存を除去し、推論関数を差し込める形に分離する。

## ゴール

- 既存の `src/ocr.py` の処理フローを、**単一画像 × ndarray 入出力** の純関数群に分解する。
- OpenCV 依存を除去し、NumPy + Pillow で等価処理を実装する。
- ONNX 推論呼び出しを抽象化し、「検出器インターフェイス」「認識器インターフェイス」を注入可能にする。
- Pyodide ホイールで入る依存（`numpy`, `Pillow`, `PyYAML`, `lxml`）のみで動く状態にする。

## 対象ファイル / 新設構成

新規パッケージ `src/ndlocr_web/` を作成（既存 `src/` と並置。CLI 版は残したまま）:

```
src/ndlocr_web/
  __init__.py
  pipeline.py        # 既存 ocr.process(args) 相当の純関数化
  detector.py        # DEIM の前処理 / 後処理 (推論本体は外部注入)
  recognizer.py      # PARSEQ の前処理 / 後処理 (推論本体は外部注入)
  cascade.py         # 3 段 cascade の純関数実装 (ThreadPoolExecutor 除去)
  imgops.py          # cv2.resize / cv2.rotate / pad の NumPy+Pillow 実装
  xml_builder.py     # ndl_parser.convert_to_xml_string3 の薄いラッパー
  reading_order/     # 既存 reading_order をコピー (純 Python で動くことを確認)
  config/
    ndl.yaml
    NDLmoji.yaml
```

## TODO

### T1-1: OpenCV 除去（最優先）

既存コードの `cv2` 使用箇所と置換先:

| 参照元 | 処理 | 置換 |
|---|---|---|
| `src/deim.py:61` | `cv2.resize(paddedimg, (w,h), INTER_CUBIC)` | `PIL.Image.resize(..., Image.BICUBIC)` もしくは NumPy 実装 |
| `src/parseq.py:53` | `cv2.rotate(img, ROTATE_90_COUNTERCLOCKWISE)` | `np.rot90(img, k=1)` |
| `src/parseq.py:54` | `cv2.resize(img, (w,h), INTER_LINEAR)` | `PIL.Image.resize(..., Image.BILINEAR)` |
| `ndl_parser.py` の `cv2.findContours` 等 | 本 OCR 経路では `textblock_to_rect` しか呼ばれず `cv2` 非依存。該当関数をラップして cv2 import を遅延させる |

- [ ] **T1-1a**: `src/ndlocr_web/imgops.py` に以下を実装。
  - `resize_bicubic(img: np.ndarray, size: tuple[int,int]) -> np.ndarray`
  - `resize_bilinear(img: np.ndarray, size: tuple[int,int]) -> np.ndarray`
  - `rotate_ccw_90(img: np.ndarray) -> np.ndarray`（`np.rot90`）
  - `pad_to_square(img: np.ndarray) -> tuple[np.ndarray, int]`（返り値: 正方パディング画像と元辺最大値）
- [ ] **T1-1b**: ピクセル値一致を検証。原実装との MSE < 0.5 以内になることを既存サンプル画像で確認する単体テストを作る (`tests/test_imgops.py`)。BICUBIC は OpenCV と PIL で端で微妙に差が出る点に注意。
- [ ] **T1-1c**: `ndl_parser.textblock_to_polygon` は現状の OCR フローでは呼ばれないため、`import cv2` を関数内遅延 import に移動 or 分離し、Pyodide で import エラーにならないようにする。

### T1-2: Detector / Recognizer の推論差し込み可能化

- [ ] **T1-2a**: `src/ndlocr_web/detector.py` に `class DEIMDetector` を定義。
  - コンストラクタ: `infer: Callable[[dict[str, np.ndarray]], list[np.ndarray]]`, `classes: list[str]`, `score_threshold`, `conf_threshold`, `iou_threshold`。
  - `preprocess(img) -> dict[str, np.ndarray]`（既存 `DEIM.preprocess` 相当。`orig_target_sizes` も含める）。
  - `postprocess(outputs, img_w, img_h) -> list[Detection]`（既存 `DEIM.postprocess` 相当。`print` 削除）。
  - `detect(img)` は `postprocess(infer(preprocess(img)))` の合成。
- [ ] **T1-2b**: `src/ndlocr_web/recognizer.py` に `class PARSeqRecognizer` を定義。
  - コンストラクタ: `infer`, `charlist`, `input_size=(W, H)`。
  - `preprocess(img)`, `postprocess(outputs) -> str`, `read(img)`。
- [ ] **T1-2c**: `infer` の入出力型を **NumPy ndarray の辞書** に統一し、JS 側（onnxruntime-web）で受け渡しやすくする（Phase 3 で Pyodide → JS bridge を実装）。

### T1-3: パイプラインの純関数化

既存 `src/ocr.py:process(args)` は `glob` によるファイル列挙、`Image.open` によるディスク読み込み、`os.makedirs` による出力書き出しを内包している。これをブラウザ向けに分解する。

- [ ] **T1-3a**: `src/ndlocr_web/pipeline.py` に `run_ocr_on_image(rgb: np.ndarray, detector, recognizer30, recognizer50, recognizer100, *, viz: bool = False) -> OcrResult` を実装。
  - 入力: RGB の `np.ndarray`（H×W×3, uint8）。
  - 出力: `OcrResult` データクラス。
    ```python
    @dataclass
    class OcrResult:
        xml: str                # 既存 <OCRDATASET>...</OCRDATASET>
        text: str               # \n 連結本文
        json: dict              # {"contents": [...], "imginfo": {...}}
        viz_png: bytes | None   # viz=True のとき、枠描画済み PNG bytes
    ```
  - ファイル I/O は一切行わない。
- [ ] **T1-3b**: 既存 `src/ocr.py` の `process_cascade` を `src/ndlocr_web/cascade.py` に移植。`ThreadPoolExecutor` を取り除き、**直列ループ**で実装（Pyodide のメインスレッドはシングルスレッド、Web Worker 内でも `asyncio` ベースの並列が主流のため）。
  - 並列実行はブラウザ側で別 Worker を使うのが最適だが、Phase 1 では単純に直列化。Phase 5 で並列化を再検討。
- [ ] **T1-3c**: `reading_order/xy_cut/eval.py` の `eval_xml` をそのまま再利用できるか確認（`lxml` と `xml.etree.ElementTree` の互換）。必要なら Python 版に合わせて薄く調整。
- [ ] **T1-3d**: 既存 `ocr.process` の冒頭にある `sys.setrecursionlimit(5000)` は `pipeline.py` 読み込み時に設定する（XY-Cut が再帰）。

### T1-4: 設定ファイル（YAML）の扱い

- [ ] **T1-4a**: `src/config/ndl.yaml`, `src/config/NDLmoji.yaml` を `src/ndlocr_web/config/` に複製（あるいはシンボリックリンク）。
- [ ] **T1-4b**: Pyodide 環境では同梱ファイルを `importlib.resources` 経由で読み込めるようにし、パス引数は受け付けない API にする (`load_class_mapping()`, `load_charset()`)。

### T1-5: CLI 版との共存

- [ ] **T1-5a**: 既存 `src/ocr.py` を薄く書き換え、`ndlocr_web.pipeline.run_ocr_on_image` を呼ぶだけにする。CLI 引数解釈／ファイル I/O は `ocr.py` に残す。
  - 既存の `get_detector` / `get_recognizer` は内部で `onnxruntime.InferenceSession` を作り、そのラムダを `ndlocr_web.DEIMDetector(infer=lambda feeds: session.run(...))` に差し込む。
  - これで「CLI は従来通り `onnxruntime` で動く」「ブラウザは `onnxruntime-web` から `infer` を注入」という二重構成が成立する。
- [ ] **T1-5b**: `pytest` で CLI 回帰テスト。`resource/` のサンプル画像 1 枚で XML/TXT が Phase 0 で決めた許容誤差内であることを確認。

### T1-6: Pyodide でのローカル動作確認（モデルなし）

- [ ] **T1-6a**: 最小スクリプトを作成:
  ```python
  # tests/pyodide_smoke.py
  from ndlocr_web.pipeline import run_ocr_on_image
  # infer はダミー (固定出力) にしてパイプライン経路が走ることだけ検証
  ```
- [ ] **T1-6b**: ローカルで `pyodide-build` で WebAssembly ビルドを実行し、スモークが通ることを確認（実モデルの注入は Phase 3）。

## 非ゴール

- 本フェーズでは **実推論**は行わない。`infer` はダミー関数で良い。
- PDF 入力、スクリーンキャプチャは対象外（Phase 4 以降で検討）。
- GUI (`ndlocr-lite-gui/`) は変更しない。

## Phase 1 完了条件

- `src/ndlocr_web/` パッケージが存在し、`opencv-python-headless` / `onnxruntime` に **import 時点で依存しない**。
- 既存 CLI が `ndlocr_web` 経由で動き、サンプル画像の OCR 結果が従来と一致。
- Pyodide ビルドがスモークテストを通る。
