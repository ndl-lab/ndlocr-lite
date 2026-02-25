
# 学習について

## レイアウト認識(DEIMv2)

Shihua Huang and Yongjie Hou and Longfei Liu and Xuanlong Yu and Xi Shen. Real-Time Object Detection Meets DINOv3. arXiv preprint arXiv:2509.20787, 2025.(<https://arxiv.org/abs/2509.20787>)

を利用してレイアウト認識モデルを作成します。

ここではdeimv2-sのみを対象としてカスタマイズを行います。

他のサイズのモデル等については公式リポジトリを参考にしてください。

この項で紹介する当館が作成したサンプルコードは[deimv2code](./deimv2code)ディレクトリ以下にあります。

### 環境構築

```sh
python3 -m venv deimenv
source ./deimenv/bin/activate
git clone https://github.com/Intellindust-AI-Lab/DEIMv2
cd DEIMv2
python3 -m pip install --upgrade pip
pip install torch==2.5.1 torchvision==0.20.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cu121
pip install faster-coco-eval>=1.6.7 PyYAML tensorboard scipy calflops transformers
```

他、[公式リポジトリ](https://github.com/Intellindust-AI-Lab/DEIMv2)

を参考に、vitt_distill.ptをダウンロードして、ckptsディレクトリ以下に展開しておく必要があります。

### レイアウト認識(DEIMv2)学習データの変換

準備中

### 学習(1)

```sh
cp -r deimv2code/part1/* . 
CUDA_VISIBLE_DEVICES=0,1,2 torchrun --master_port=7777 --nproc_per_node=3 train.py -c configs/ndl_deimv2/deimv2_dinov3_s_coco_r4_800.yml --seed=0
```

### 学習(2)

```sh
cp -r deimv2code/part2/* . 
CUDA_VISIBLE_DEVICES=0,1,2 torchrun --master_port=7777 --nproc_per_node=3 train.py -c configs/ndl_deimv2/deimv2_dinov3_s_coco_r4_800.yml --seed=0 -t /data1/DEIMv2/outputs/deimv2_dinov3_s_coco_r4_800/last.pth
```

### レイアウト認識(DEIMv2)学習済モデルのONNXへの変換

```sh
pip install numpy==1.21.6 onnx==1.16.2 onnxruntime-gpu==1.18.1
python tools/deployment/export_onnx.py --check -c ./configs/ndl_deimv2/deimv2_dinov3_s_coco_r4_800.yml -r outputs/deimv2_dinov3_s_coco_r4_800/last.pth
```

上記の例の場合、outputs/deimv2_dinov3_s_coco_r4_800 以下にlatest.onnxが出力されます。

NDLOCR-Liteで利用する場合は、--det-weightsオプションでonnxファイルのパスを指定してください。

## 文字列認識(PARSeq)

Darwin Bautista, Rowel Atienza. Scene text recognition with permuted autoregressive sequence models. arXiv:2212.06966, 2022. (<https://arxiv.org/abs/2207.06966>)

を利用して文字列認識モデルを作成します。

ここではNDLOCR-Liteと同様に3種類の大きさの異なるモデルを作成する手順を説明しています。

この項で紹介する当館が作成したサンプルコードは[parseqcode](./parseqcode)ディレクトリ以下にあります。

### 文字列認識(PARSeq)の環境構築

```sh
python3 -m venv parseqenv
source ./parseqenv/bin/activate
git clone https://github.com/baudm/parseq
cp -r parseqcode/* ./parseq
cd parseq
python3 -m pip install --upgrade pip
platform=cu121
make torch-${platform}
pip install -r requirements/core.${platform}.txt -e .[train,test]
pip install tqdm
```

そのままではONNX変換時にエラーが発生することがあるので、parseq/strhub/models/parseq/model.py
の117行目([元リポジトリの次の箇所](https://github.com/baudm/parseq/blob/1902db043c029a7e03a3818c616c06600af574be/strhub/models/parseq/model.py#L117))

```python
tgt_mask = query_mask = torch.triu(torch.ones((num_steps, num_steps), dtype=torch.bool, device=self._device), 1)
```

を

```python
tgt_mask = query_mask = torch.triu(torch.ones((num_steps, num_steps), dtype=torch.float, device=self._device), 1)
```

に変更してください。

### 文字列認識(PARSeq)学習データの変換

[OCR学習用データセット（みんなで翻刻）](https://github.com/ndl-lab/ndl-minhon-ocrdataset)の「利用方法」を参考に画像とテキストデータを対応付けた1行データセットを作成してください。

honkoku_rawdataディレクトリ内に行ごとの切り出し画像とテキストデータが次のように配置されているとします。

```sh
001E3C19A3E626EC382F86D201FEFB8C-001_0.jpg
001E3C19A3E626EC382F86D201FEFB8C-001_0.txt
001E3C19A3E626EC382F86D201FEFB8C-003_0.jpg
001E3C19A3E626EC382F86D201FEFB8C-003_0.txt
……
```

[convertkotensekidata2lmdb.py](./parseqcode/convertkotensekidata2lmdb.py)を実行するとtraindataとvaliddataディレクトリにparseqの学習に利用するlmdb形式のデータセット(data.mdb、lock.mdb)が出力されます。

```sh
python3 convertkotensekidata2lmdb.py
```

出力されたデータセットは次のコマンドで所定の位置に配置します。

```sh
mkdir data
mkdir data/train
mkdir data/train/real
mkdir data/val
cp traindata/* data/train/real/
cp validdata/* data/val/
```

### 学習

```sh
#1行当たり100文字まで読むモデル
python3 train.py --config-name=main_tiny768.yaml
#1行当たり50文字まで読むモデル
python3 train.py --config-name=main_tiny384.yaml
#1行当たり30文字まで読むモデル
python3 train.py --config-name=main_tiny256.yaml
```

### 文字列認識(PARSeq)学習済モデルのONNXへの変換

[convert2onnx.py](./parseqcode/convert2onnx.py)の「チェックポイントのパス」を書き換えて実行します。

```sh
python3 convert2onnx.py
```

モデルに対応するonnxファイルが生成されます。

NDLOCR-Liteで利用する場合は、--rec-weightsオプションでonnxファイルのパスを指定してください。
