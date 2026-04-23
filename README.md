# NDLOCR-Liteアプリケーションのリポジトリ

NDLOCR-Liteを利用してテキスト化を実行するためのアプリケーションを提供するリポジトリです。

NDLOCR-Liteは、[NDLOCR](https://github.com/ndl-lab/ndlocr_cli)の軽量版を目指して開発したOCRであり、ノートパソコン等の一般的な家庭用コンピュータやOS環境で、図書や雑誌といった資料のデジタル化画像からテキストデータが作成できるOCRです。

GPUを必要としないOCR処理に特徴があり、ノートパソコン等の一般的な家庭用コンピュータやOS環境において高速に実行可能です。

Windows(Windows 11)、Mac(Apple M4, macOS Sequoia)及びLinux(Ubuntu 22.04)環境において動作確認しています。

本プログラムは[NDLラボ](https://lab.ndl.go.jp)におけるこれまでの調査研究活動によって得られた知見、特に[NDL古典籍OCR-Lite](https://github.com/ndl-lab/ndlkotenocr-lite)の開発経験を踏まえて職員が内製で開発しました。

本プログラムは、国立国会図書館がCC BY 4.0ライセンスで公開するものです。詳細については[LICENCE](./LICENCE)をご覧ください。なお、本アプリケーションの実行時に利用するライブラリ等のライセンスについては[LICENCE_DEPENDENCIES](./LICENCE_DEPENDENCEIES)をご覧ください。

## デスクトップアプリケーションによる利用

**デスクトップアプリケーションを利用する際には、日本語（全角文字）を含まないパスにアプリケーションを配置してください。全角文字を含む場合に起動しないことがあります。**

[releases](https://github.com/ndl-lab/ndlocr-lite/releases)からお使いのOS環境（Windows/Mac/Linux）に合ったファイルをダウンロードしてください。

デスクトップアプリケーションの操作方法については[NDLOCR-Liteの使い方](https://lab.ndl.go.jp/data_set/ndlocrlite-usage/)、ビルド方法については[デスクトップアプリケーションの利用方法](./ndlocr-lite-gui/README.md)を参照してください。

次のgifアニメーションは、

[国立国会図書館総務部総務課 編『国立国会図書館年報』昭和27年度,国立国会図書館,1954. 国立国会図書館デジタルコレクション https://dl.ndl.go.jp/pid/3048008"](https://dl.ndl.go.jp/pid/3048008/1/24)

をNDLOCR-Liteの画面キャプチャ機能によって、画像ファイルを介さずにテキスト化するデモを示しています。

<img src="resource/demo_lite.gif" width="600" alt="キャプチャモードのデモ動画">

## コマンドラインからの利用

※コマンドラインから操作を行うにはPython 3.10以上が必要です。

事前準備

```bash
git clone https://github.com/ndl-lab/ndlocr-lite
cd ndlocr-lite
pip install -r requirements.txt
cd src
```

実行例1.（同階層にある「9892834_0001」という名称のディレクトリ内の画像を一括処理し、tmpdirという名称のディレクトリに結果を出力する。）

```bash
python3 ocr.py --sourcedir 9892834_0001 --output tmpdir 
```

実行例2.（同階層にある「digidepo_1287221_00000002.jpg」という名称の画像を処理し、tmpdirという名称のディレクトリに結果を出力する。）

```bash
python3 ocr.py --sourceimg digidepo_1287221_00000002.jpg --output tmpdir
```

uv(<https://github.com/astral-sh/uv>)をお使いの環境であれば、以下のようにしても導入・実行可能です。この場合、「ndlocr-lite」というコマンドから実行できます。

導入方法

```bash
git clone https://github.com/ndl-lab/ndlocr-lite
cd ndlocr-lite
uv tool install .
```

実行例

```bash
ndlocr-lite --sourceimg digidepo_1287221_00000002.jpg --output tmpdir 
```

### パラメータの説明

#### `--sourcedir`オプション

処理したい画像の含まれるディレクトリを絶対パスまたは相対パスで指定する。ディレクトリ内の"jpg（jpegも可）"、"png"、"tiff（tifも可）"、"jp2"及び"bmp"の拡張子のファイルを順次処理する。

#### `--sourceimg`オプション

処理したい画像を絶対パスまたは相対パスで直接指定する。"jpg（jpegも可）"、"png"、"tiff（tifも可）"、"jp2"及び"bmp"の拡張子のファイルを処理することが可能。

#### `--output`オプション

OCR結果を保存する出力先ディレクトリを相対パスまたは絶対パスで指定する。

#### `--viz`オプション

`--viz True`を指定することで、文字認識箇所を青枠で表示した画像を出力先ディレクトリに出力する。

#### `--device`オプション（ベータ）

対応GPUを搭載したサーバかつonnxruntime-gpuがインストールされている環境に限り、`--device cuda`を指定することでGPUを利用した処理に切り替える。

#### `--json-only`オプション
`--json-only`を指定することで、出力形式のうちjsonのみを出力する。

#### `--enable-tcy`オプション
`--enable-tcy`を指定することで、縦中横の読み取りを改善する。



## OCR結果の例

|資料画像|OCR結果の冒頭（誤認識を含む）|OCR結果のxml|
|---|---|---|
|<img src="./resource/viz_digidepo_2531162_0024.jpg" width="400" alt="レイアウト認識結果"><br>国立国会図書館総務部 編『国立国会図書館スタッフ・マニュアル』E-2,国立国会図書館,1963.8. 国立国会図書館デジタルコレクション <https://dl.ndl.go.jp/pid/2531162/1/23>|(ヱ)気送子送付管気送子送付には、上記気送響にて送付するものと、空気の圧縮を使用せず,直接落下させる装置の二通りがある。後者の送付管は山納台左側に設置されており.5|[OCR結果(xmlファイル)](./resource/digidepo_2531162_0024.xml)|
|<img src="./resource/viz_digidepo_11048278_po_geppo1803_00021.jpg" width="400" alt="レイアウト認識結果"><br> 館内スコープ　次世代室の謎に迫れ！. 国立国会図書館月報. 2018, (683),　 p.20. <http://dl.ndl.go.jp/info:ndljp/pid/11048278>|はじめまして!私は2017年4月に就職後、次世代システム開発研究室(次世代室)という場所で仕事をしています。でも、「次世代室」って何をするところか想像しにくいですよね。次世代室は、図書館の役割がインターネット等の情報技術で変化する中、より先進的なサービスを検討していくために作られた比較的新しい部署です。|[OCR結果(xmlファイル)](./resource/digidepo_11048278_po_geppo1803_00021.xml)|
|<img src="./resource/viz_digidepo_3048008_0025.jpg" width="400" alt="レイアウト認識結果"><br>国立国会図書館総務部総務課 編『国立国会図書館年報』昭和27年度,国立国会図書館,1954. 国立国会図書館デジタルコレクション <https://dl.ndl.go.jp/pid/3048008/1/25>|第8章職員、庁舍、財政、記念行事等1.職員A.司書職員の研修昭和26年度(第4回)研修に引続き、昭和27年度(第5回)司書職員研修を27年6月2日から28年4月10日まで320時間、研修生50名に実施した。本年度は、東京学芸大学の協力を得て、講師は、本館職員が専門分野の科目を担当した外、東京学芸大学の教授が担当した。本年度も單位科目ごとに試験を実施し、必修科目(11單位)選択科目(4單位)合わせて15單位以上の試験に合格した43名が修了した。|[OCR結果(xmlファイル)](./resource/digidepo_3048008_0025.xml)|


## モデルの認識性能について

* [F値による評価結果](/evaluation_jptype.csv) 
日本語活字の図書及び雑誌に対するF値による評価結果を公表しています。

[NDLOCR ver.2.1の評価結果](https://lab.ndl.go.jp/data_set/r4ocr/r4_software#5-3-文字認識性能の評価結果)と同様の評価用データセット・同様の評価手法による値です。

* 手書き文字に対する評価(NDLOCR-Lite ver.1.2.1)

Maeda, K., & Okazaki, N. JaWildText: A Benchmark for Vision-Language Models on Japanese Scene Text Understanding. arXiv preprint arXiv:2603.27942, 2026.(<https://arxiv.org/abs/2603.27942>)

によって作成された[JaWildText（外部サイト）](https://huggingface.co/datasets/llm-jp/jawildtext)のうち、手書き文字データセット1,065画像を用いて評価しました。

全体平均CER : 0.268 (対象: 1065 ファイル)

縦書き平均CER : 0.279 (対象: 295 ファイル)

横書き平均CER : 0.264 (対象: 768 ファイル)


## モデルの再学習及びカスタマイズについて（開発者向け情報）

[学習及びモデル変換手順](/train/README.md)をご覧ください。

## 技術情報について（開発者向け情報）

NDLOCR-Liteは「レイアウト認識」、「文字列認識」、「読み順整序」の3つの機能（モジュール）を組み合わせて実現しています。

レイアウト認識にはDEIMv2[1]、文字列認識にはPARSeq[2]をそれぞれ用いており、読み順整序については当館が公開している[NDLOCR](https://github.com/ndl-lab/ndlocr_cli)と同様のモジュールを用いています。

[1]Shihua Huang and Yongjie Hou and Longfei Liu and Xuanlong Yu and Xi Shen. Real-Time Object Detection Meets DINOv3. arXiv preprint arXiv:2509.20787, 2025.(<https://arxiv.org/abs/2509.20787>)

[2]Darwin Bautista, Rowel Atienza. Scene text recognition with permuted autoregressive sequence models. arXiv:2212.06966, 2022. (<https://arxiv.org/abs/2207.06966>)

レイアウト認識及び文字列認識の機械学習モデルは、いずれもpytorchをフレームワークとした学習を行った後にONNX形式に変換して利用しています。詳しくは[学習及びモデル変換手順](/train/README.md)をご覧ください。
