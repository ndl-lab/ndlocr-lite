import sys
sys.setrecursionlimit(5000)
import os
import numpy as np
from PIL import Image
import xml.etree.ElementTree as ET
from pathlib import Path
from deim import DEIM
from parseq import PARSEQ

from yaml import safe_load
from concurrent.futures import ThreadPoolExecutor
import time
import shutil
import json
import glob
from functools import lru_cache
from reading_order.xy_cut.eval import eval_xml
from reading_order.xy_cut.block_xy_cut import solve as solve_reading_order
from ndl_parser import convert_to_xml_string3
from pdf_output_utils import should_output_pdf_pages_separately, write_pdf_page_outputs
from version import __version__


def _is_cjk_char(ch: str) -> bool:
    """Return True for common Japanese/CJK characters.

    This is only used to decide whether two OCR lines in the same layout
    TEXTBLOCK need an inserted ASCII space when they are joined into one
    paragraph for Markdown display.
    """
    if not ch:
        return False
    cp = ord(ch)
    return (
        0x3040 <= cp <= 0x30FF  # Hiragana / Katakana
        or 0x3400 <= cp <= 0x4DBF  # CJK Ext. A
        or 0x4E00 <= cp <= 0x9FFF  # CJK Unified Ideographs
        or 0xF900 <= cp <= 0xFAFF  # CJK Compatibility Ideographs
        or 0xFF00 <= cp <= 0xFFEF  # Full-width forms
    )


def _join_textblock_lines(lines):
    """Join physical OCR lines as one logical paragraph.

    Layout TEXTBLOCK boundaries represent paragraph/block boundaries in the
    result viewer. Physical line wraps inside a block are removed. A single
    ASCII space is inserted only when it is likely needed between Latin words.
    """
    cleaned = [str(line).replace("\r", "").replace("\n", "").strip() for line in lines]
    cleaned = [line for line in cleaned if line]
    if not cleaned:
        return ""

    out = cleaned[0]
    closing_punct = set(",.;:!?%)]}、。，．！？：；）］｝〉》」』】％")
    opening_punct = set("([{（［｛〈《「『【")
    for nxt in cleaned[1:]:
        prev = out[-1] if out else ""
        first = nxt[0] if nxt else ""
        need_space = (
            bool(prev and first)
            and not prev.isspace()
            and not first.isspace()
            and not _is_cjk_char(prev)
            and not _is_cjk_char(first)
            and prev not in opening_punct
            and first not in closing_punct
            and (prev.isalnum() or prev in "'\"")
            and (first.isalnum() or first in "'\"")
        )
        out += (" " if need_space else "") + nxt
    return out


def _escape_markdown_paragraph(text: str) -> str:
    """Escape OCR body text so it is rendered as text, not Markdown syntax."""
    escaped = str(text).replace("\\", "\\\\")
    for ch in ("`", "*", "_", "{", "}", "[", "]", "<", ">", "#", "+", "-", "!", "|"):
        escaped = escaped.replace(ch, "\\" + ch)
    return escaped


def _is_title_line_element(line) -> bool:
    """Return True when an OCR LINE is the layout category 'タイトル本文'."""
    line_type = str(line.get("TYPE") or "").strip()
    normalized = line_type.lower().replace("-", "_")
    return (
        line_type == "タイトル本文"
        or normalized in {"line_title", "title", "title_text"}
        or "タイトル本文" in line_type
    )


def _render_markdown_line_groups(lines) -> list[str]:
    """Render ordered LINE elements as headings and body paragraphs.

    Consecutive title lines are joined into one Markdown heading. Consecutive
    ordinary lines are joined into one logical paragraph. A blank line between
    returned fragments is added by the page-level builder, so TEXTBLOCK and
    title/body boundaries are visually explicit in ft.Markdown.
    """
    fragments: list[str] = []
    group_kind = None
    group_texts: list[str] = []

    def flush():
        nonlocal group_kind, group_texts
        text = _join_textblock_lines(group_texts)
        if text:
            escaped = _escape_markdown_paragraph(text)
            if group_kind == "title":
                fragments.append(f"## {escaped}")
            else:
                fragments.append(escaped)
        group_kind = None
        group_texts = []

    for line in lines:
        value = (line.get("STRING") or "").strip()
        if not value:
            continue
        kind = "title" if _is_title_line_element(line) else "body"
        if group_kind is not None and group_kind != kind:
            flush()
        group_kind = kind
        group_texts.append(value)
    flush()
    return fragments


def _line_reading_order(line):
    """Return numeric LINE@ORDER when xy-cut assigned one."""
    try:
        value = float(line.get("ORDER"))
    except (TypeError, ValueError):
        return None
    return value if np.isfinite(value) else None


def _xml_element_bbox(elem):
    """Return an axis-aligned bbox for a layout XML element when possible."""
    try:
        x = float(elem.get("X"))
        y = float(elem.get("Y"))
        w = float(elem.get("WIDTH"))
        h = float(elem.get("HEIGHT"))
        if w > 0 and h > 0:
            return (x, y, x + w, y + h)
    except (TypeError, ValueError):
        pass

    boxes = []
    lines = [elem] if str(elem.tag).upper() == "LINE" else list(elem.findall(".//LINE"))
    for line in lines:
        try:
            x = float(line.get("X"))
            y = float(line.get("Y"))
            w = float(line.get("WIDTH"))
            h = float(line.get("HEIGHT"))
        except (TypeError, ValueError):
            continue
        if w > 0 and h > 0:
            boxes.append((x, y, x + w, y + h))
    if boxes:
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )

    # TEXTBLOCK normally has a SHAPE/POLYGON rather than X/Y/WIDTH/HEIGHT.
    polygon = elem.find("./SHAPE/POLYGON")
    if polygon is not None:
        raw = str(polygon.get("POINTS") or "").strip()
        try:
            coords = [float(v) for v in raw.split(",") if v.strip()]
            points = list(zip(coords[0::2], coords[1::2]))
        except ValueError:
            points = []
        if points:
            xs = [pt[0] for pt in points]
            ys = [pt[1] for pt in points]
            return (min(xs), min(ys), max(xs), max(ys))
    return None


def _bbox_iou_for_layout(a, b):
    if a is None or b is None:
        return 0.0
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    union = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1) + max(0.0, bx2 - bx1) * max(0.0, by2 - by1) - inter
    return inter / union if union > 0 else 0.0


def _normalize_table_markdown_entries(table_markdowns):
    entries = []
    for index, item in enumerate(list(table_markdowns or [])):
        if isinstance(item, dict):
            markdown = str(item.get("markdown") or item.get("text") or "").strip()
            bbox = item.get("layout_bbox")
            if bbox is None:
                bbox = item.get("bbox")
            try:
                bbox = tuple(float(v) for v in bbox) if bbox is not None and len(bbox) == 4 else None
            except (TypeError, ValueError):
                bbox = None
            raw_table_index = item.get("table_index")
            try:
                table_index = int(raw_table_index) if raw_table_index is not None else None
            except (TypeError, ValueError):
                table_index = None
            entries.append({
                "markdown": markdown,
                "bbox": bbox,
                "index": index,
                "table_index": table_index,
                "used": False,
            })
        else:
            # Legacy v1.0.10 callers pass plain strings in the same order as
            # extract_table_regions(); preserve that stable association.
            entries.append({
                "markdown": str(item).strip(),
                "bbox": None,
                "index": index,
                "table_index": index + 1,
                "used": False,
            })
    return entries


def _match_table_markdown(block, entries, table_index=None):
    """Match a structured table result to its original XML table BLOCK.

    ``table_index`` is preferred because it is generated from the exact same
    XML table enumeration used by inference.  This avoids the v1.0.11
    regression where an empty/failed result could be paired with a different
    table after bbox-based re-matching.  Bbox IoU remains a compatibility
    fallback when no explicit index is available.
    """
    if table_index is not None:
        for entry in entries:
            if entry["used"]:
                continue
            if entry.get("table_index") == table_index:
                entry["used"] = True
                return entry["markdown"]

    block_bbox = _xml_element_bbox(block)
    best = None
    best_score = 0.0
    if block_bbox is not None:
        for entry in entries:
            if entry["used"] or entry["bbox"] is None:
                continue
            score = _bbox_iou_for_layout(block_bbox, entry["bbox"])
            if score > best_score:
                best = entry
                best_score = score
    if best is not None and best_score >= 0.5:
        best["used"] = True
        return best["markdown"]

    # Final compatibility fallback. This is intentionally last so sorting PAGE
    # children for display can never change which inference result belongs to
    # which original table block.
    for entry in entries:
        if not entry["used"]:
            entry["used"] = True
            return entry["markdown"]
    return None


def _layout_child_order(child):
    """Use descendant LINE@ORDER as the reading-order anchor of a layout item."""
    lines = [child] if str(child.tag).upper() == "LINE" else list(child.findall(".//LINE"))
    orders = [value for value in (_line_reading_order(line) for line in lines) if value is not None]
    return min(orders) if orders else None


def _sort_page_layout_children(page_elem):
    """Sort PAGE children in reading order, including BLOCK TYPE='表組'.

    ``reading_order.order.reorder.sort_lines`` intentionally leaves generic
    BLOCK elements in an unsorted bucket after TEXTBLOCK/LINE elements. That is
    correct for its line-oriented XML task but made table Markdown drift to the
    end of the GUI document. Here a table BLOCK is anchored by the ORDER of its
    descendant OCR lines. If an empty table has no LINE@ORDER, xy-cut is run on
    the top-level layout bboxes so its detected position can still be inserted
    among surrounding text.
    """
    children = list(page_elem)
    if len(children) <= 1:
        return children

    records = []
    any_missing = False
    for original_index, child in enumerate(children):
        order = _layout_child_order(child)
        bbox = _xml_element_bbox(child)
        records.append({
            "child": child,
            "order": order,
            "bbox": bbox,
            "original_index": original_index,
            "geo_order": None,
        })
        any_missing = any_missing or order is None

    # Normal case: every rendered item contains OCR lines and already has the
    # authoritative xy-cut LINE@ORDER. This directly fixes table BLOCKs that
    # eval_xml() moved after all TEXTBLOCKs.
    if not any_missing:
        return [r["child"] for r in sorted(records, key=lambda r: (r["order"], r["original_index"]))]

    # Empty table blocks have no descendant line rank. Re-run the same xy-cut
    # solver on layout-item rectangles so those blocks still obtain a spatial
    # reading-order position. Items without any usable geometry remain last.
    boxed = [r for r in records if r["bbox"] is not None]
    if boxed:
        try:
            boxes = np.asarray([[int(round(v)) for v in r["bbox"]] for r in boxed], dtype=np.int32)
            ranks = solve_reading_order(boxes, logger=None)
            for record, rank in zip(boxed, ranks):
                record["geo_order"] = float(rank)
        except Exception:
            pass

    # Where LINE@ORDER exists, keep it authoritative. For missing items, use
    # geometry rank and interpolate against known neighbors in that rank order.
    geo_sorted = sorted(
        records,
        key=lambda r: (
            r["geo_order"] is None,
            float("inf") if r["geo_order"] is None else r["geo_order"],
            r["original_index"],
        ),
    )
    for pos, record in enumerate(geo_sorted):
        if record["order"] is not None:
            continue
        prev_known = next((geo_sorted[i] for i in range(pos - 1, -1, -1) if geo_sorted[i]["order"] is not None), None)
        next_known = next((geo_sorted[i] for i in range(pos + 1, len(geo_sorted)) if geo_sorted[i]["order"] is not None), None)
        if prev_known is not None and next_known is not None:
            left = float(prev_known["order"])
            right = float(next_known["order"])
            if right > left:
                span = sum(1 for item in geo_sorted[geo_sorted.index(prev_known) + 1:geo_sorted.index(next_known)] if item["order"] is None) + 1
                offset = sum(1 for item in geo_sorted[geo_sorted.index(prev_known) + 1:pos + 1] if item["order"] is None)
                record["order"] = left + (right - left) * offset / span
            else:
                record["order"] = left + 0.001 * (pos + 1)
        elif prev_known is not None:
            record["order"] = float(prev_known["order"]) + 0.001 * (pos + 1)
        elif next_known is not None:
            record["order"] = float(next_known["order"]) - 0.001 * (len(geo_sorted) - pos)
        elif record["geo_order"] is not None:
            record["order"] = float(record["geo_order"])
        else:
            record["order"] = float("inf")

    return [r["child"] for r in sorted(records, key=lambda r: (r["order"], r["original_index"]))]


def build_markdown_text_from_page(page_elem, table_markdowns=None) -> str:
    """Build GUI Markdown in true layout reading order.
    Display rules:
      * ``LINE TYPE="タイトル本文"`` is rendered as a Markdown heading.
      * Each ordinary TEXTBLOCK is rendered as one paragraph, with a blank line
        between blocks.
      * A ``BLOCK TYPE="表組"`` is inserted at its LINE@ORDER / detected-bbox
        position and replaced by the matching Markdown table.

    ``eval_xml()`` sorts LINE/TEXTBLOCK objects but deliberately leaves generic
    BLOCK objects after them. Therefore PAGE child order must not be used as the
    reading order for tables.
    """
    if page_elem is None:
        return ""

    table_entries = _normalize_table_markdown_entries(table_markdowns)
    # Preserve the exact table enumeration used by extract_table_regions().
    # Sorting children below changes only display order, never table identity.
    table_block_indexes = {}
    table_counter = 0
    for block in page_elem.findall(".//BLOCK"):
        if str(block.get("TYPE") or "").strip() == "表組":
            table_counter += 1
            table_block_indexes[id(block)] = table_counter

    fragments: list[str] = []
    consumed = set()

    def collect_lines(elem):
        lines = list(elem.findall(".//LINE"))
        if str(elem.tag).upper() == "LINE":
            lines = [elem]
        for line in lines:
            consumed.add(id(line))
        return lines

    def append_lines(lines):
        fragments.extend(_render_markdown_line_groups(lines))

    for child in _sort_page_layout_children(page_elem):
        tag = str(child.tag).upper()
        if tag == "TEXTBLOCK":
            append_lines(collect_lines(child))
        elif tag == "BLOCK":
            block_type = str(child.get("TYPE") or "").strip()
            if block_type == "表組":
                table_lines = collect_lines(child)
                table_md = _match_table_markdown(
                    child, table_entries, table_index=table_block_indexes.get(id(child))
                )
                if table_md:
                    fragments.append(str(table_md).strip())
                else:
                    append_lines(table_lines)
            else:
                nested_blocks = child.findall(".//TEXTBLOCK")
                if nested_blocks:
                    for block in nested_blocks:
                        append_lines(collect_lines(block))
                else:
                    append_lines(collect_lines(child))
        elif tag == "WARICHUBLOCK":
            append_lines(collect_lines(child))
        elif tag == "LINE":
            append_lines(collect_lines(child))

    # Be conservative: never lose OCR text just because an unfamiliar XML
    # nesting pattern is encountered. These residual lines are sorted by their
    # own reading-order rank before rendering.
    residual = [line for line in page_elem.findall(".//LINE") if id(line) not in consumed]
    residual.sort(key=lambda line: (_line_reading_order(line) is None, _line_reading_order(line) or float("inf")))
    for line in residual:
        append_lines([line])

    return "\n\n".join(fragment for fragment in fragments if fragment.strip())

def build_markdown_text_from_xml(page_xml: str, table_markdowns=None) -> str:
    """Parse a PAGE XML fragment and build viewer Markdown."""
    if not page_xml:
        return ""
    try:
        root = ET.fromstring(page_xml)
    except ET.ParseError:
        return ""
    page_elem = root if str(root.tag).upper() == "PAGE" else root.find("PAGE")
    return build_markdown_text_from_page(page_elem, table_markdowns=table_markdowns)

class RecogLine:
    def __init__(self,npimg:np.ndarray,idx:int,pred_char_cnt:int,pred_str:str=""):
        self.npimg = npimg
        self.idx   = idx
        self.pred_char_cnt = pred_char_cnt
        self.pred_str = pred_str
    def __lt__(self, other):
        return self.idx < other.idx

def process_cascade(alllineobj:RecogLine,recognizer30,recognizer50,recognizer100,is_cascade=True):
    targetdflist30,targetdflist50,targetdflist100=[],[],[]
    for lineobj in alllineobj:
        if lineobj.pred_char_cnt==3 and is_cascade:
            targetdflist30.append(lineobj)
        elif lineobj.pred_char_cnt==2 and is_cascade:
            targetdflist50.append(lineobj)
        else:
            targetdflist100.append(lineobj)

    targetdflistall=[]
    with ThreadPoolExecutor(thread_name_prefix="recognizer") as executor:
        # The three recognizer models are independent. Start their initial jobs
        # together, then submit only the lines that overflow into the next tier.
        jobs30=[(lineobj,executor.submit(recognizer30.read,lineobj.npimg)) for lineobj in targetdflist30]
        jobs50=[(lineobj,executor.submit(recognizer50.read,lineobj.npimg)) for lineobj in targetdflist50]
        jobs100=[(lineobj,executor.submit(recognizer100.read,lineobj.npimg)) for lineobj in targetdflist100]

        for lineobj,future in jobs30:
            pred_str=future.result()
            if len(pred_str)>=25:
                jobs50.append((lineobj,executor.submit(recognizer50.read,lineobj.npimg)))
            else:
                lineobj.pred_str=pred_str
                targetdflistall.append(lineobj)

        for lineobj,future in jobs50:
            pred_str=future.result()
            if len(pred_str)>=45:
                jobs100.append((lineobj,executor.submit(recognizer100.read,lineobj.npimg)))
            else:
                lineobj.pred_str=pred_str
                targetdflistall.append(lineobj)

        targetdflist200=[]
        for lineobj,future in jobs100:
            pred_str=future.result()
            lineobj.pred_str=pred_str
            if len(pred_str)>=98 and lineobj.npimg.shape[0]<lineobj.npimg.shape[1]:
                baseimg=lineobj.npimg
                targetdflist200.append(RecogLine(
                    npimg=baseimg[:,:baseimg.shape[1]//2,:],
                    idx=lineobj.idx,
                    pred_char_cnt=100,
                ))
                targetdflist200.append(RecogLine(
                    npimg=baseimg[:,baseimg.shape[1]//2:,:],
                    idx=lineobj.idx,
                    pred_char_cnt=100,
                ))
            else:
                targetdflistall.append(lineobj)

        jobs200=[(lineobj,executor.submit(recognizer100.read,lineobj.npimg)) for lineobj in targetdflist200]
        resultlines200=[future.result() for _,future in jobs200]
        for i in range(0,len(targetdflist200)-1,2):
            ia=targetdflist200[i]
            targetdflistall.append(RecogLine(
                npimg=None,
                idx=ia.idx,
                pred_char_cnt=100,
                pred_str=resultlines200[i]+resultlines200[i+1],
            ))

    targetdflistall=sorted(targetdflistall)
    return [t.pred_str for t in targetdflistall]

def get_detector(args):
    weights_path = args.det_weights
    classes_path = args.det_classes
    assert os.path.isfile(weights_path), f"There's no weight file with name {weights_path}"
    assert os.path.isfile(classes_path), f"There's no classes file with name {weights_path}"
    detector = DEIM(model_path=weights_path,
                      class_mapping_path=classes_path,
                      score_threshold=args.det_score_threshold,
                      conf_threshold=args.det_conf_threshold,
                      iou_threshold=args.det_iou_threshold,
                      device=args.device)
    return detector

@lru_cache(maxsize=8)
def _load_charlist(classes_path):
    with open(classes_path,encoding="utf-8") as f:
        charobj=safe_load(f)
    return tuple(charobj["model"]["charset_train"])

def get_recognizer(args,weights_path=None):
    if weights_path is None:
        weights_path = args.rec_weights
    classes_path = args.rec_classes

    assert os.path.isfile(weights_path), f"There's no weight file with name {weights_path}"
    assert os.path.isfile(classes_path), f"There's no classes file with name {weights_path}"

    charlist=list(_load_charlist(os.path.abspath(classes_path)))
    
    recognizer = PARSEQ(model_path=weights_path,charlist=charlist,device=args.device)
    if getattr(args, 'enable_tcy', False):
        from tcy_wrapper import TateChuYokoWrapper
        tcy_kwargs = {k: v for k, v in vars(args).items() if k.startswith('tcy_') and k != 'enable_tcy' and v is not None}
        recognizer = TateChuYokoWrapper(recognizer, **tcy_kwargs)
    return recognizer

def inference_on_detector(args,inputname:str,npimage:np.ndarray,outputpath:str,issaveimg:bool=True):
    print("[INFO] Intialize Model")
    detector = get_detector(args)
    print("[INFO] Inference Image")
    detections = detector.detect(npimage)
    classeslist=list(detector.classes.values())
    if issaveimg:
        drawimage = npimage.copy()
        pil_image =detector.draw_detections(drawimage, detections=detections)
        os.makedirs(outputpath,exist_ok=True)
        output_filepath = os.path.join(outputpath,f"viz_{Path(inputname).name}")
        if output_filepath.split(".")[-1]=="jp2":
            output_filepath=output_filepath[:-4]+".jpg"
        print(f"[INFO] Saving result on {output_filepath}")
        pil_image.save(output_filepath)
    return detections,classeslist

def process_detector(detector,inputname:str,npimage:np.ndarray,outputpath:str,issaveimg:bool=True):
    detections = detector.detect(npimage)
    classeslist=list(detector.classes.values())
    if issaveimg:
        drawimage = npimage.copy()
        pil_image =detector.draw_detections(drawimage, detections=detections)
        os.makedirs(outputpath,exist_ok=True)
        output_filepath = os.path.join(outputpath,f"viz_{Path(inputname).name}")
        if output_filepath.split(".")[-1]=="jp2":
            output_filepath=output_filepath[:-4]+".jpg"
        print(f"[INFO] Saving result on {output_filepath}")
        pil_image.save(output_filepath)
    return detections,classeslist

def _run_ocr_on_image_array(
    detector,
    recognizer30,
    recognizer50,
    recognizer100,
    inputname: str,
    img: np.ndarray,
    outputpath: str,
    save_viz: bool = False,
):
    img_h, img_w = img.shape[:2]
    detections, classeslist = process_detector(
        detector=detector,
        inputname=inputname,
        npimage=img,
        outputpath=outputpath,
        issaveimg=save_viz,
    )
    resultobj = [dict(), dict()]
    resultobj[0][0] = list()
    for i in range(17):
        resultobj[1][i] = []
    for det in detections:
        xmin, ymin, xmax, ymax = det["box"]
        conf = det["confidence"]
        if det["class_index"] == 0:
            resultobj[0][0].append([xmin, ymin, xmax, ymax])
        resultobj[1][det["class_index"]].append([xmin, ymin, xmax, ymax, conf, det["pred_char_count"]])

    xmlstr = convert_to_xml_string3(img_w, img_h, inputname, classeslist, resultobj)
    xmlstr = xmlstr.replace("&", "&amp;")
    xmlstr = "<OCRDATASET>" + xmlstr + "</OCRDATASET>"
    root = ET.fromstring(xmlstr)
    eval_xml(root, logger=None)

    alllineobj = []
    tatelinecnt = 0
    alllinecnt = 0

    for idx, lineobj in enumerate(root.findall(".//LINE")):
        xmin = int(lineobj.get("X"))
        ymin = int(lineobj.get("Y"))
        line_w = int(lineobj.get("WIDTH"))
        line_h = int(lineobj.get("HEIGHT"))
        try:
            pred_char_cnt = float(lineobj.get("PRED_CHAR_CNT"))
        except Exception:
            pred_char_cnt = 100.0
        if line_h > line_w:
            tatelinecnt += 1
        alllinecnt += 1
        lineimg = img[ymin:ymin + line_h, xmin:xmin + line_w, :]
        alllineobj.append(RecogLine(lineimg, idx, pred_char_cnt))

    if len(alllineobj) == 0 and len(detections) > 0:
        page = root.find("PAGE")
        for idx, det in enumerate(detections):
            xmin, ymin, xmax, ymax = det["box"]
            line_w = int(xmax - xmin)
            line_h = int(ymax - ymin)
            if line_w <= 0 or line_h <= 0:
                continue
            line_elem = ET.SubElement(page, "LINE")
            c_idx = int(det["class_index"])
            type_name = "本文"
            line_elem.set("TYPE", type_name)
            line_elem.set("X", str(int(xmin)))
            line_elem.set("Y", str(int(ymin)))
            line_elem.set("WIDTH", str(line_w))
            line_elem.set("HEIGHT", str(line_h))
            line_elem.set("CONF", f"{det['confidence']:0.3f}")
            pred_char_cnt = det.get("pred_char_count", 100.0)
            line_elem.set("PRED_CHAR_CNT", f"{pred_char_cnt:0.3f}")
            if line_h > line_w:
                tatelinecnt += 1
            alllinecnt += 1
            lineimg = img[int(ymin):int(ymax), int(xmin):int(xmax), :]
            alllineobj.append(RecogLine(lineimg, idx, pred_char_cnt))

    resultlinesall = process_cascade(
        alllineobj,
        recognizer30,
        recognizer50,
        recognizer100,
        is_cascade=True,
    )

    resjsonarray = []
    text_layer_lines = []
    for idx, lineobj in enumerate(root.findall(".//LINE")):
        text = resultlinesall[idx] if idx < len(resultlinesall) else ""
        lineobj.set("STRING", text)
        xmin = int(lineobj.get("X"))
        ymin = int(lineobj.get("Y"))
        line_w = int(lineobj.get("WIDTH"))
        line_h = int(lineobj.get("HEIGHT"))
        is_vertical = line_h > line_w
        try:
            conf = float(lineobj.get("CONF"))
        except Exception:
            conf = 0.0

        type_str = lineobj.get("TYPE", "")
        c_idx = classeslist.index(type_str) if type_str in classeslist else 1
        resjsonarray.append({
            "boundingBox": [
                [xmin, ymin],
                [xmin, ymin + line_h],
                [xmin + line_w, ymin],
                [xmin + line_w, ymin + line_h],
            ],
            "id": idx,
            "isVertical": "true" if is_vertical else "false",
            "text": text,
            "isTextline": "true",
            "confidence": conf,
            "class_index": c_idx,
        })
        text_layer_lines.append({
            "x": xmin,
            "y": ymin,
            "width": line_w,
            "height": line_h,
            "text": text,
            "is_vertical": is_vertical,
        })

    page_elem = root.find("PAGE")
    page_xml = ET.tostring(page_elem, encoding="unicode")
    page_text = "\n".join(resultlinesall)
    markdown_text = build_markdown_text_from_page(page_elem)
    return {
        "page_xml": page_xml,
        "text": page_text,
        "markdown_text": markdown_text,
        "json_lines": resjsonarray,
        "text_layer_lines": text_layer_lines,
        "img_width": img_w,
        "img_height": img_h,
        "img_name": inputname,
        "line_count": alllinecnt,
        "vertical_line_count": tatelinecnt,
    }

def _text_layer_font_size(
    width: float,
    height: float,
    text: str,
    is_vertical: bool,
):
    """
    フォントサイズは「行の進行方向」ではなく、
    文字の大きさを表す方向から決める。

    横書き: bbox の高さ
    縦書き: bbox の幅

    行全体の長さは _draw_text_layer_line() 側で別途調整する。
    """
    cross_size = width if is_vertical else height

    # 元実装の 72pt 上限は、大きい見出し等で
    # bbox と文字サイズが合わなくなるので撤廃する。
    return max(1.0, cross_size * 0.95)


def _draw_text_layer_line(
    canvas_obj,
    line: dict,
    img_width: int,
    img_height: int,
    page_width: float,
    page_height: float,
    visible: bool,
):
    """
    OCR bbox に合わせて透明テキストを配置する。

    横書き:
        - fontsize は bbox 高さから決める
        - 実際の文字列幅を pdfmetrics.stringWidth() で取得
        - setHorizScale() で bbox 幅に合わせる

    縦書き:
        - fontsize は bbox 幅から決める
        - bbox 高さ / 文字数 から目標の文字送りを求める
        - setCharSpace() で縦方向の文字送りを合わせる

    これにより、行頭だけでなく行末まで OCR bbox と
    埋め込みテキストの位置が合うようにする。
    """
    from reportlab.pdfbase import pdfmetrics

    text = line["text"]
    if not text:
        return

    scale_x = page_width / max(img_width, 1)
    scale_y = page_height / max(img_height, 1)

    # OCR画像座標 -> PDF座標
    x = line["x"] * scale_x
    y_top = page_height - line["y"] * scale_y

    width = line["width"] * scale_x
    height = line["height"] * scale_y

    if width <= 0 or height <= 0:
        return

    is_vertical = bool(line["is_vertical"])

    if is_vertical:
        font_name = "HeiseiMin-W3"
    else:
        font_name = "HeiseiKakuGo-W5"

    fontsize = _text_layer_font_size(
        width=width,
        height=height,
        text=text,
        is_vertical=is_vertical,
    )

    canvas_obj.saveState()

    try:
        # 可視化モードでは青色表示。
        if visible:
            canvas_obj.setFillColorRGB(0, 0, 1)

        text_obj = canvas_obj.beginText()
        text_obj.setFont(font_name, fontsize)

        # PDFの透明テキストとしては alpha=0 だけに頼らず、
        # Text Rendering Mode 3 (Invisible) を使う。
        #
        # visible=True のデバッグ表示時は通常描画。
        text_obj.setTextRenderMode(0 if visible else 3)

        if is_vertical:
            #
            # 縦書き
            #
            # 縦書きCIDフォントは text origin の x が
            # おおむね文字セル中央になるので bbox 中央に配置。
            #
            draw_x = x + width * 0.5
            draw_y = y_top

            text_obj.setTextOrigin(draw_x, draw_y)

            text_len = len(text)

            if text_len > 1:
                # OCR bbox が表す縦方向の1文字分のピッチ
                target_pitch = height / text_len

                # ReportLab の縦書きCIDフォントでは通常、
                # fontsize が縦方向の基本 advance になる。
                #
                # vertical writing mode では charSpace の正方向が
                # 下方向の advance を小さくする側に作用するため、
                #
                #   actual_pitch = fontsize - char_space
                #
                # となるよう設定する。
                vertical_char_space = fontsize - target_pitch

                text_obj.setCharSpace(vertical_char_space)

            text_obj.textOut(text)

        else:
            #
            # 横書き
            #
            draw_x = x

            # 横方向の累積ズレとは独立なので、
            # baseline は従来の配置を基本的に維持する。
            draw_y = (
                y_top
                - height
                + max((height - fontsize) * 0.5, 0)
            )

            text_obj.setTextOrigin(draw_x, draw_y)

            # この fontsize / font で本来必要になる文字列幅。
            natural_width = pdfmetrics.stringWidth(
                text,
                font_name,
                fontsize,
            )

            if natural_width > 1e-6:
                # bbox 幅と完全に同じ advance にする。
                #
                # フォントサイズ（高さ）は変えず、
                # 横方向だけを調整するのがポイント。
                horiz_scale = (
                    100.0 * width / natural_width
                )

                text_obj.setHorizScale(horiz_scale)

            text_obj.textOut(text)

        canvas_obj.drawText(text_obj)

    finally:
        canvas_obj.restoreState()

def embed_text_layer_pdf(input_pdf: str, output_pdf: str, page_results: list, visible_text: bool = False):
    try:
        from io import BytesIO
        from pypdf import PdfReader, PdfWriter
        from reportlab.pdfgen import canvas
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    except ImportError as exc:
        raise RuntimeError(
            "PDF text-layer output requires pypdf and reportlab. Install dependencies from requirements.txt."
        ) from exc

    output_path = Path(output_pdf)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.resolve() == Path(input_pdf).resolve():
        raise ValueError("Output PDF must be different from the input PDF.")

    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5", isVertical=False))
    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiMin-W3", isVertical=True))

    reader = PdfReader(input_pdf)
    if len(reader.pages) != len(page_results):
        raise ValueError(f"PDF page count mismatch: {len(reader.pages)} pages, {len(page_results)} OCR results")

    writer = PdfWriter()
    if reader.metadata:
        writer.add_metadata({key: str(value) for key, value in reader.metadata.items() if value is not None})
    for page, page_result in zip(reader.pages, page_results):
        page_width = float(page.mediabox.width)
        page_height = float(page.mediabox.height)
        overlay_buffer = BytesIO()
        overlay_canvas = canvas.Canvas(overlay_buffer, pagesize=(page_width, page_height))
        if visible_text:
            overlay_canvas.setFillColorRGB(0, 0, 1)
        else:
            overlay_canvas.setFillAlpha(0)
        for line in page_result["text_layer_lines"]:
            _draw_text_layer_line(
                canvas_obj=overlay_canvas,
                line=line,
                img_width=page_result["img_width"],
                img_height=page_result["img_height"],
                page_width=page_width,
                page_height=page_height,
                visible=visible_text,
            )
        overlay_canvas.save()
        overlay_buffer.seek(0)
        overlay_reader = PdfReader(overlay_buffer)
        page.merge_page(overlay_reader.pages[0])
        writer.add_page(page)

    with open(output_path, "wb") as wf:
        writer.write(wf)

def process_pdf_documents(args, pdf_paths: list[str]):
    try:
        import pypdfium2
    except ImportError as exc:
        raise RuntimeError(
            "PDF input requires pypdfium2. Install dependencies from requirements.txt."
        ) from exc
    try:
        import pypdf  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "PDF text-layer output requires pypdf. Install dependencies from requirements.txt."
        ) from exc

    if len(pdf_paths) > 1 and getattr(args, "pdf_output", None):
        print("--pdf-output can only be used with a single PDF input.")
        return

    if not os.path.exists(args.output):
        print("Output Directory is not found.")
        return

    print("[INFO] Intialize Model")
    detector = get_detector(args)
    recognizer100 = get_recognizer(args=args)
    recognizer30 = get_recognizer(args=args, weights_path=args.rec_weights30)
    recognizer50 = get_recognizer(args=args, weights_path=args.rec_weights50)

    render_scale = max(float(getattr(args, "pdf_render_dpi", 150)), 1.0) / 72.0

    for pdf_path in pdf_paths:
        start = time.time()
        pdf_path_obj = Path(pdf_path)
        output_stem = pdf_path_obj.stem
        pdf_doc = pypdfium2.PdfDocument(str(pdf_path_obj))
        page_results = []
        all_json_contents = []
        page_infos = []
        all_text_pages = []
        all_page_xml = []
        separate_page_outputs = should_output_pdf_pages_separately(
            len(pdf_doc),
            getattr(args, "pdf_page_output", True),
        )

        for page_index in range(len(pdf_doc)):
            page_name = f"{output_stem}_{page_index + 1:05}.png"
            print(f"[INFO] OCR PDF page {page_index + 1}/{len(pdf_doc)}: {pdf_path_obj.name}")
            rendered_pages = pdf_doc.render(
                pypdfium2.PdfBitmap.to_pil,
                page_indices=[page_index],
                scale=render_scale,
            )
            pil_image = next(iter(rendered_pages)).convert("RGB")
            img = np.array(pil_image)
            page_result = _run_ocr_on_image_array(
                detector=detector,
                recognizer30=recognizer30,
                recognizer50=recognizer50,
                recognizer100=recognizer100,
                inputname=page_name,
                img=img,
                outputpath=args.output,
                save_viz=args.viz,
            )
            page_results.append(page_result)
            all_json_contents.append(page_result["json_lines"])
            page_infos.append({
                "page_index": page_index,
                "img_width": page_result["img_width"],
                "img_height": page_result["img_height"],
                "img_name": page_result["img_name"],
            })
            all_text_pages.append(page_result["text"])
            all_page_xml.append(page_result["page_xml"])
            write_pdf_page_outputs(
                args.output,
                output_stem,
                page_index,
                page_result,
                source_path=str(pdf_path_obj),
                write_json=separate_page_outputs,
                write_xml=separate_page_outputs and not getattr(args, "json_only", False),
                write_txt=separate_page_outputs and not getattr(args, "json_only", False),
            )

        pdf_doc.close()

        if not getattr(args, "json_only", False) and not separate_page_outputs:
            with open(os.path.join(args.output, output_stem + ".xml"), "w", encoding="utf-8") as wf:
                wf.write("<OCRDATASET>\n")
                wf.write("\n".join(all_page_xml))
                wf.write("\n</OCRDATASET>")
            with open(os.path.join(args.output, output_stem + ".txt"), "w", encoding="utf-8") as wtf:
                wtf.write("\n\n".join(all_text_pages))

        if not separate_page_outputs:
            with open(os.path.join(args.output, output_stem + ".json"), "w", encoding="utf-8") as wf:
                alljsonobj = {
                    "contents": all_json_contents,
                    "pdfinfo": {
                        "pdf_path": str(pdf_path_obj),
                        "pdf_name": pdf_path_obj.name,
                        "page_count": len(page_results),
                        "render_dpi": float(getattr(args, "pdf_render_dpi", 150)),
                    },
                    "pages": page_infos,
                }
                wf.write(json.dumps(alljsonobj, ensure_ascii=False, indent=2))

        output_pdf = getattr(args, "pdf_output", None)
        if not output_pdf:
            output_pdf = os.path.join(args.output, output_stem + "_text.pdf")
        print(f"[INFO] Writing text-layer PDF: {output_pdf}")
        embed_text_layer_pdf(
            input_pdf=str(pdf_path_obj),
            output_pdf=output_pdf,
            page_results=page_results,
            visible_text=getattr(args, "pdf_visible_text", False),
        )
        print("Total PDF calculation time:", time.time() - start)

def process(args):
    rawinputpathlist=[]
    inputpathlist=[]
    pdfpathlist=[]
    if args.sourcedir is not None:
        for inputpath in glob.glob(os.path.join(args.sourcedir,"*")):
            rawinputpathlist.append(inputpath)
    if args.sourceimg is not None:
        rawinputpathlist.append(args.sourceimg)
    if args.sourcepdf is not None:
        pdfpathlist.append(args.sourcepdf)
    for inputpath in rawinputpathlist:
        ext=inputpath.split(".")[-1]
        if ext.lower() in ["jpg","png","tiff","jp2","tif","jpeg","bmp","webp"]:
            inputpathlist.append(inputpath)
        elif ext.lower() in ["pdf",]:
            pdfpathlist.append(inputpath)

    if len(pdfpathlist) > 0:
        process_pdf_documents(args, pdfpathlist)
        if len(inputpathlist) == 0:
            return
    if len(inputpathlist)==0:
        print("Images are not found.")
        return
    if not os.path.exists(args.output):
        print("Output Directory is not found.")
        return
    
    detector=get_detector(args)
    recognizer100=get_recognizer(args=args)
    recognizer30=get_recognizer(args=args,weights_path=args.rec_weights30)
    recognizer50=get_recognizer(args=args,weights_path=args.rec_weights50)
    tatelinecnt=0
    alllinecnt=0
    
    for inputpath in inputpathlist:
        ext=inputpath.split(".")[-1]
        pil_image = Image.open(inputpath).convert('RGB')
        img = np.array(pil_image)
        start = time.time()
        allxmlstr="<OCRDATASET>\n"
        alltextlist=[]
        resjsonarray=[]
        imgname=os.path.basename(inputpath)
        img_h,img_w=img.shape[:2]
        detections,classeslist=process_detector(detector,inputname=imgname,npimage=img,outputpath=args.output,issaveimg=args.viz)
        e1=time.time()
        resultobj=[dict(),dict()]
        resultobj[0][0]=list()
        for i in range(17):
            resultobj[1][i]=[]
        for det in detections:
            xmin,ymin,xmax,ymax=det["box"]
            conf=det["confidence"]
            char_count=det["pred_char_count"]
            if det["class_index"]==0:
                resultobj[0][0].append([xmin,ymin,xmax,ymax])
            resultobj[1][det["class_index"]].append([xmin,ymin,xmax,ymax,conf,char_count])
        xmlstr=convert_to_xml_string3(img_w, img_h, imgname, classeslist, resultobj)
        xmlstr = xmlstr.replace("&", "&amp;")
        xmlstr="<OCRDATASET>"+xmlstr+"</OCRDATASET>"
        # print(xmlstr)
        root = ET.fromstring(xmlstr)
        eval_xml(root, logger=None)
        alllineobj = []
        alltextlist = []

        for idx, lineobj in enumerate(root.findall(".//LINE")):
            xmin = int(lineobj.get("X"))
            ymin = int(lineobj.get("Y"))
            line_w = int(lineobj.get("WIDTH"))
            line_h = int(lineobj.get("HEIGHT"))
            try:
                pred_char_cnt = float(lineobj.get("PRED_CHAR_CNT"))
            except:
                pred_char_cnt = 100.0
            
            if line_h > line_w:
                tatelinecnt += 1
            alllinecnt += 1
            # 部分画像の切り出し
            lineimg = img[ymin:ymin+line_h, xmin:xmin+line_w, :]
            linerecogobj = RecogLine(lineimg, idx, pred_char_cnt)
            alllineobj.append(linerecogobj)

        if len(alllineobj) == 0 and len(detections) > 0:
            # LINE 要素がないが検出がある場合は検出領域を LINE として扱う
            page = root.find("PAGE")
            for idx, det in enumerate(detections):
                xmin, ymin, xmax, ymax = det["box"]
                line_w = int(xmax - xmin)
                line_h = int(ymax - ymin)
                if line_w > 0 and line_h > 0:
                    line_elem = ET.SubElement(page, "LINE")
                    c_idx = int(det["class_index"])
                    type_name = "本文"
                    line_elem.set("TYPE", type_name)
                    line_elem.set("X", str(int(xmin)))
                    line_elem.set("Y", str(int(ymin)))
                    line_elem.set("WIDTH", str(line_w))
                    line_elem.set("HEIGHT", str(line_h))
                    line_elem.set("CONF", f"{det['confidence']:0.3f}")
                    pred_char_cnt = det.get("pred_char_count", 100.0)
                    line_elem.set("PRED_CHAR_CNT", f"{pred_char_cnt:0.3f}")
                    if line_h > line_w:
                        tatelinecnt += 1
                    alllinecnt += 1
                    lineimg = img[int(ymin):int(ymax), int(xmin):int(xmax), :]
                    linerecogobj = RecogLine(lineimg, idx, pred_char_cnt)
                    alllineobj.append(linerecogobj)

        # 認識プロセス
        resultlinesall = process_cascade(
            alllineobj, recognizer30, recognizer50, recognizer100, is_cascade=True
        )
        alltextlist.append("\n".join(resultlinesall))
        
        for idx,lineobj in enumerate(root.findall(".//LINE")):
            lineobj.set("STRING",resultlinesall[idx])
            xmin=int(lineobj.get("X"))
            ymin=int(lineobj.get("Y"))
            line_w=int(lineobj.get("WIDTH"))
            line_h=int(lineobj.get("HEIGHT"))
            try:
                conf=float(lineobj.get("CONF"))
            except:
                conf=0.0
            
            # XML TYPE -> c_idx
            type_str = lineobj.get("TYPE", "")
            c_idx = classeslist.index(type_str) if type_str in classeslist else 1

            jsonobj={"boundingBox": [[xmin,ymin],[xmin,ymin+line_h],[xmin+line_w,ymin],[xmin+line_w,ymin+line_h]],
                "id": idx,"isVertical": "true" if line_h > line_w else "false","text": resultlinesall[idx],"isTextline": "true","confidence": conf, "class_index": c_idx}
            resjsonarray.append(jsonobj)

        allxmlstr+=(ET.tostring(root.find("PAGE"), encoding='unicode')+"\n")
        allxmlstr+="</OCRDATASET>"
        if alllinecnt>0 and tatelinecnt/alllinecnt>0.5:
            alltextlist=alltextlist[::-1]
        output_stem = os.path.splitext(os.path.basename(inputpath))[0]
        
        if not getattr(args, "json_only", False):
            with open(os.path.join(args.output,output_stem+".xml"),"w",encoding="utf-8") as wf:
                wf.write(allxmlstr)
                
        with open(os.path.join(args.output,output_stem+".json"),"w",encoding="utf-8") as wf:
            alljsonobj={
                "contents":[resjsonarray],
                "imginfo": {
                    "img_width": img_w,
                    "img_height": img_h,
                    "img_path":inputpath,
                    "img_name":os.path.basename(inputpath)
                }
            }
            alljsonstr=json.dumps(alljsonobj,ensure_ascii=False,indent=2)
            wf.write(alljsonstr)
            
        if not getattr(args, "json_only", False):
            with open(os.path.join(args.output,output_stem+".txt"),"w",encoding="utf-8") as wtf:
                wtf.write("\n".join(alltextlist))
        print("Total calculation time (Detection + Recognition):",time.time()-start)

def main():
    import argparse
    from pathlib import Path
    base_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Arguments for NDLkotenOCR-Lite")

    parser.add_argument("--sourcedir", type=str, required=False, help="Path to image directory")
    parser.add_argument("--sourceimg", type=str, required=False, help="Path to image directory")
    parser.add_argument("--sourcepdf", type=str, required=False, help="Path to source PDF")
    parser.add_argument("--output", type=str, required=True, help="Path to output directory")
    parser.add_argument("--viz", type=bool, required=False, help="Save visualized image",default=False)
    parser.add_argument("--pdf-output", type=str, required=False, help="Path to output text-layer PDF")
    parser.add_argument("--pdf-render-dpi", "--pdf-dpi", dest="pdf_render_dpi", type=float, required=False, default=150.0, help="DPI used to render PDF pages for OCR")
    parser.add_argument("--pdf-page-output", action=argparse.BooleanOptionalAction, default=True, help="When a multi-page PDF is input, output TXT/JSON/XML per page")
    parser.add_argument("--pdf-visible-text", action="store_true", help="Draw PDF text layer visibly in blue for debugging")
    parser.add_argument("--det-weights", type=str, required=False, help="Path to deim onnx file", default=str(base_dir / "model" / "deim-s-1024x1024.onnx"))
    parser.add_argument("--det-classes", type=str, required=False, help="Path to list of class in yaml file", default=str(base_dir / "config" / "ndl.yaml"))
    parser.add_argument("--det-score-threshold", type=float, required=False, default=0.2)
    parser.add_argument("--det-conf-threshold", type=float, required=False, default=0.25)
    parser.add_argument("--det-iou-threshold", type=float, required=False, default=0.2)
    parser.add_argument("--simple-mode", type=bool, required=False, help="Read line with one model(Setting this option to True will slow down processing, but it simplifies the architecture and may slightly improve accuracy.)",default=False)
    parser.add_argument("--rec-weights30", type=str, required=False, help="Path to parseq-tiny onnx file", default=str(base_dir / "model" / "parseq-ndl-24x256-30-tiny-189epoch-tegaki3-r8data-202604.onnx"))
    parser.add_argument("--rec-weights50", type=str, required=False, help="Path to parseq-tiny onnx file", default=str(base_dir / "model" / "parseq-ndl-24x384-50-tiny-300epoch-tegaki3-r8data-202604.onnx"))
    parser.add_argument("--rec-weights", type=str, required=False, help="Path to parseq-tiny onnx file", default=str(base_dir / "model" / "parseq-ndl-24x768-100-tiny-153epoch-tegaki3-r8data-202604.onnx"))
    parser.add_argument("--rec-classes", type=str, required=False, help="Path to list of class in yaml file", default=str(base_dir / "config" / "NDLmoji.yaml"))
    parser.add_argument("--device", type=str, required=False, help="Device use (cpu or cuda)", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--enable-tcy", action="store_true", dest="enable_tcy", default=False, help="Enable tate-chuu-yoko (縦中横) detection for vertical text (e.g. newspaper OCR)")
    parser.add_argument("--json-only", action="store_true", help="Disable .xml and .txt output and only output JSON")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args, remaining = parser.parse_known_args()
    if args.enable_tcy and remaining:
        from tcy_wrapper import add_tcy_arguments
        tcy_parser = add_tcy_arguments(parser)
        tcy_args = tcy_parser.parse_args(remaining)
        for k, v in vars(tcy_args).items():
            if v is not None:
                setattr(args, k, v)
    args = parser.parse_args()
    process(args)

if __name__=="__main__":
    main()
