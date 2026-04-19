"""Pure-function OCR pipeline for browser (Pyodide) use."""
from __future__ import annotations
import sys
import os
import json
import io
from dataclasses import dataclass, field
import xml.etree.ElementTree as ET
import numpy as np
from PIL import Image, ImageDraw

sys.setrecursionlimit(5000)

_SRC_DIR = os.path.dirname(os.path.dirname(__file__))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from reading_order.xy_cut.eval import eval_xml  # noqa: E402
from .xml_builder import build_xml             # noqa: E402
from .cascade import process_cascade, RecogLine  # noqa: E402


@dataclass
class OcrResult:
    xml: str
    text: str
    json: dict = field(default_factory=dict)
    viz_png: bytes | None = None


def run_ocr_on_image(
    rgb: np.ndarray,
    detector,
    recognizer30,
    recognizer50,
    recognizer100,
    *,
    img_name: str = "image.jpg",
    viz: bool = False,
) -> OcrResult:
    """Run the full OCR pipeline on a single RGB ndarray.

    Parameters
    ----------
    rgb:
        H×W×3 uint8 numpy array in RGB order.
    detector:
        DEIMDetector instance (or duck-typed equivalent with .detect() and .classes).
    recognizer30, recognizer50, recognizer100:
        PARSeqRecognizer instances for 3-stage cascade.
    img_name:
        Filename used inside generated XML; no I/O is performed.
    viz:
        When True, return a PNG-encoded image with bounding boxes overlaid.
    """
    img_h, img_w = rgb.shape[:2]

    # --- Detection ---
    detections = detector.detect(rgb)
    classeslist = list(detector.classes.values())

    # --- Build result_obj for ndl_parser ---
    result_obj: list[dict] = [dict(), dict()]
    result_obj[0][0] = []
    for i in range(17):
        result_obj[1][i] = []

    for det in detections:
        xmin, ymin, xmax, ymax = det.box
        conf = det.confidence
        char_count = det.pred_char_count
        if det.class_index == 0:
            result_obj[0][0].append([xmin, ymin, xmax, ymax])
        result_obj[1][det.class_index].append([xmin, ymin, xmax, ymax, conf, char_count])

    # --- XML generation + reading-order sort ---
    xmlfrag = build_xml(img_w, img_h, img_name, classeslist, result_obj)
    xmlstr = "<OCRDATASET>" + xmlfrag + "</OCRDATASET>"
    root = ET.fromstring(xmlstr)
    eval_xml(root, logger=None)

    # --- Collect LINE crops ---
    alllineobj: list[RecogLine] = []
    tatelinecnt = 0
    alllinecnt = 0

    for idx, lineobj in enumerate(root.findall(".//LINE")):
        xmin = int(lineobj.get("X"))
        ymin = int(lineobj.get("Y"))
        line_w = int(lineobj.get("WIDTH"))
        line_h = int(lineobj.get("HEIGHT"))
        try:
            pred_char_cnt = float(lineobj.get("PRED_CHAR_CNT"))
        except (TypeError, ValueError):
            pred_char_cnt = 100.0
        if line_h > line_w:
            tatelinecnt += 1
        alllinecnt += 1
        lineimg = rgb[ymin:ymin + line_h, xmin:xmin + line_w, :]
        alllineobj.append(RecogLine(lineimg, idx, pred_char_cnt))

    # Fallback: no LINE elements but detections exist
    if len(alllineobj) == 0 and len(detections) > 0:
        page = root.find("PAGE")
        for idx, det in enumerate(detections):
            xmin, ymin, xmax, ymax = det.box
            line_w = int(xmax - xmin)
            line_h = int(ymax - ymin)
            if line_w > 0 and line_h > 0:
                line_elem = ET.SubElement(page, "LINE")
                line_elem.set("TYPE", "本文")
                line_elem.set("X", str(int(xmin)))
                line_elem.set("Y", str(int(ymin)))
                line_elem.set("WIDTH", str(line_w))
                line_elem.set("HEIGHT", str(line_h))
                line_elem.set("CONF", f"{det.confidence:0.3f}")
                line_elem.set("PRED_CHAR_CNT", f"{det.pred_char_count:0.3f}")
                if line_h > line_w:
                    tatelinecnt += 1
                alllinecnt += 1
                lineimg = rgb[int(ymin):int(ymax), int(xmin):int(xmax), :]
                alllineobj.append(RecogLine(lineimg, idx, det.pred_char_count))

    # --- Recognition ---
    resultlinesall = process_cascade(alllineobj, recognizer30, recognizer50, recognizer100)

    # --- Write strings back into XML tree ---
    resjsonarray = []
    for idx, lineobj in enumerate(root.findall(".//LINE")):
        if idx >= len(resultlinesall):
            break
        lineobj.set("STRING", resultlinesall[idx])
        xmin = int(lineobj.get("X"))
        ymin = int(lineobj.get("Y"))
        line_w = int(lineobj.get("WIDTH"))
        line_h = int(lineobj.get("HEIGHT"))
        try:
            conf = float(lineobj.get("CONF"))
        except (TypeError, ValueError):
            conf = 0.0
        resjsonarray.append({
            "boundingBox": [
                [xmin, ymin], [xmin, ymin + line_h],
                [xmin + line_w, ymin], [xmin + line_w, ymin + line_h],
            ],
            "id": idx,
            "isVertical": "true",
            "text": resultlinesall[idx],
            "isTextline": "true",
            "confidence": conf,
        })

    # --- Assemble output XML ---
    page_xml = ET.tostring(root.find("PAGE"), encoding="unicode")
    allxmlstr = "<OCRDATASET>\n" + page_xml + "\n</OCRDATASET>"

    # --- Text output ---
    alltextlist = ["\n".join(resultlinesall)]
    if alllinecnt > 0 and tatelinecnt / alllinecnt > 0.5:
        alltextlist = alltextlist[::-1]
    text_out = "\n".join(alltextlist)

    # --- JSON output ---
    json_out: dict = {
        "contents": [resjsonarray],
        "imginfo": {
            "img_width": img_w,
            "img_height": img_h,
            "img_name": img_name,
        },
    }

    # --- Optional viz ---
    viz_png: bytes | None = None
    if viz:
        pil = Image.fromarray(rgb)
        draw = ImageDraw.Draw(pil)
        for det in detections:
            x1, y1, x2, y2 = det.box
            draw.rectangle([x1, y1, x2, y2], outline=(0, 0, 255), width=2)
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        viz_png = buf.getvalue()

    return OcrResult(xml=allxmlstr, text=text_out, json=json_out, viz_png=viz_png)
