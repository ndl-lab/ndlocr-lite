import sys
sys.setrecursionlimit(5000)
import os
import glob
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image
from yaml import safe_load
import onnxruntime

from ndlocr_web.pipeline import run_ocr_on_image
from ndlocr_web.detector import DEIMDetector
from ndlocr_web.recognizer import PARSeqRecognizer


def _make_ort_infer(session):
    input_names = [inp.name for inp in session.get_inputs()]
    output_names = [out.name for out in session.get_outputs()]
    def infer(feeds: dict):
        ort_feeds = {k: feeds[k] for k in input_names if k in feeds}
        return session.run(output_names, ort_feeds)
    return infer


def _create_ort_session(model_path: str, device: str = "cpu"):
    opt = onnxruntime.SessionOptions()
    opt.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
    providers = ["CPUExecutionProvider"]
    if device.casefold() == "cuda":
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    if device.casefold() == "cpu":
        opt.intra_op_num_threads = 1
        opt.inter_op_num_threads = 1
    return onnxruntime.InferenceSession(model_path, opt, providers=providers)


def get_detector(args) -> DEIMDetector:
    weights_path = args.det_weights
    classes_path = args.det_classes
    assert os.path.isfile(weights_path), f"Weight file not found: {weights_path}"
    assert os.path.isfile(classes_path), f"Classes file not found: {classes_path}"

    with open(classes_path) as f:
        yaml_obj = safe_load(f)
    classes: dict[int, str] = yaml_obj["names"]

    session = _create_ort_session(weights_path, args.device)
    input_names = [inp.name for inp in session.get_inputs()]
    output_names = [out.name for out in session.get_outputs()]
    input_shape = session.get_inputs()[0].shape
    input_h, input_w = input_shape[2], input_shape[3]

    def infer(feeds: dict):
        ort_feeds = {}
        for name in input_names:
            if name in feeds:
                ort_feeds[name] = feeds[name]
        return session.run(output_names, ort_feeds)

    return DEIMDetector(
        infer=infer,
        classes=classes,
        score_threshold=args.det_score_threshold,
        conf_threshold=args.det_conf_threshold,
        iou_threshold=args.det_iou_threshold,
        input_size=(input_h, input_w),
    )


def get_recognizer(args, weights_path=None) -> PARSeqRecognizer:
    if weights_path is None:
        weights_path = args.rec_weights
    classes_path = args.rec_classes
    assert os.path.isfile(weights_path), f"Weight file not found: {weights_path}"
    assert os.path.isfile(classes_path), f"Classes file not found: {classes_path}"

    with open(classes_path, encoding="utf-8") as f:
        charobj = safe_load(f)
    charlist = list(charobj["model"]["charset_train"])

    session = _create_ort_session(weights_path, args.device)
    output_names = [out.name for out in session.get_outputs()]
    input_name = session.get_inputs()[0].name
    input_shape = session.get_inputs()[0].shape
    input_h, input_w = input_shape[2], input_shape[3]

    def infer(feeds: dict):
        return session.run(output_names, {input_name: feeds["input"]})

    return PARSeqRecognizer(
        infer=infer,
        charlist=charlist,
        input_size=(input_w, input_h),
    )


def process(args):
    rawinputpathlist = []
    if args.sourcedir is not None:
        rawinputpathlist.extend(glob.glob(os.path.join(args.sourcedir, "*")))
    if args.sourceimg is not None:
        rawinputpathlist.append(args.sourceimg)

    inputpathlist = [
        p for p in rawinputpathlist
        if p.rsplit(".", 1)[-1].lower() in {"jpg", "jpeg", "png", "tiff", "tif", "jp2", "bmp"}
    ]
    if not inputpathlist:
        print("Images are not found.")
        return
    if not os.path.exists(args.output):
        print("Output Directory is not found.")
        return

    detector = get_detector(args)
    recognizer100 = get_recognizer(args)
    recognizer30 = get_recognizer(args, weights_path=args.rec_weights30)
    recognizer50 = get_recognizer(args, weights_path=args.rec_weights50)

    for inputpath in inputpathlist:
        pil_image = Image.open(inputpath).convert("RGB")
        img = np.array(pil_image)
        img_name = os.path.basename(inputpath)
        start = time.time()

        result = run_ocr_on_image(
            img,
            detector=detector,
            recognizer30=recognizer30,
            recognizer50=recognizer50,
            recognizer100=recognizer100,
            img_name=img_name,
            viz=args.viz,
        )

        output_stem = os.path.splitext(img_name)[0]
        with open(os.path.join(args.output, output_stem + ".xml"), "w", encoding="utf-8") as f:
            f.write(result.xml)
        with open(os.path.join(args.output, output_stem + ".json"), "w", encoding="utf-8") as f:
            json.dump(result.json, f, ensure_ascii=False, indent=2)
        with open(os.path.join(args.output, output_stem + ".txt"), "w", encoding="utf-8") as f:
            f.write(result.text)
        if result.viz_png is not None:
            ext = "png"
            with open(os.path.join(args.output, f"viz_{output_stem}.{ext}"), "wb") as f:
                f.write(result.viz_png)

        print("Total calculation time:", time.time() - start)


def main():
    import argparse
    base_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Arguments for NDLOCR-Lite")

    parser.add_argument("--sourcedir", type=str, required=False)
    parser.add_argument("--sourceimg", type=str, required=False)
    parser.add_argument("--output", type=str, required=True)
    parser.add_argument("--viz", type=bool, required=False, default=False)
    parser.add_argument("--det-weights", type=str, default=str(base_dir / "model" / "deim-s-1024x1024.onnx"))
    parser.add_argument("--det-classes", type=str, default=str(base_dir / "config" / "ndl.yaml"))
    parser.add_argument("--det-score-threshold", type=float, default=0.2)
    parser.add_argument("--det-conf-threshold", type=float, default=0.25)
    parser.add_argument("--det-iou-threshold", type=float, default=0.2)
    parser.add_argument("--simple-mode", type=bool, default=False)
    parser.add_argument("--rec-weights30", type=str, default=str(base_dir / "model" / "parseq-ndl-16x256-30-tiny-192epoch-tegaki3.onnx"))
    parser.add_argument("--rec-weights50", type=str, default=str(base_dir / "model" / "parseq-ndl-16x384-50-tiny-146epoch-tegaki2.onnx"))
    parser.add_argument("--rec-weights", type=str, default=str(base_dir / "model" / "parseq-ndl-16x768-100-tiny-165epoch-tegaki2.onnx"))
    parser.add_argument("--rec-classes", type=str, default=str(base_dir / "config" / "NDLmoji.yaml"))
    parser.add_argument("--device", type=str, default="cpu", choices=["cpu", "cuda"])
    args = parser.parse_args()
    process(args)


if __name__ == "__main__":
    main()
