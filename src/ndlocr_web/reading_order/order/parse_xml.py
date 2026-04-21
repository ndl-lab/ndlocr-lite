# Copyright (c) 2023, National Diet Library, Japan
#
# This software is released under the CC BY 4.0.
# https://creativecommons.org/licenses/by/4.0/


from pathlib import Path
import re
import xml.etree.ElementTree as et


LINE_TYPE_TEXT = 0
LINE_TYPE_CAPTION = 1
LINE_TYPE_TOCHU = 2
LINE_TYPE_WARICHU = 3
LINE_TYPE_ADTEXT = 4
LINE_TYPE_TITLE=5
LINE_TYPE_UNKNOWN = 6


LINE_TYPE_COLORS = [
    (255, 0, 0),  # LINE_TYPE_TEXT,
    (0, 255, 0),  # LINE_TYPE_CAPTION,
    (255, 255, 0),  # LINE_TYPE_TOCHU,
    (0, 0, 255),  # LINE_TYPE_WARICHU,
    (0, 255, 255),  # LINE_TYPE_ADTEXT,
    (0, 0, 0), #LINE_TYPE_TITLE
    (200, 200, 200),  # LINE_TYPE_UNKNOWN
]


def get_line_type(string_type):
    return {
        "本文": LINE_TYPE_TEXT,
        "キャプション": LINE_TYPE_CAPTION,
        "頭注": LINE_TYPE_TOCHU,
        "割注": LINE_TYPE_WARICHU,
        "広告文字": LINE_TYPE_ADTEXT,
        "タイトル本文": LINE_TYPE_TITLE,
    }.get(string_type, LINE_TYPE_UNKNOWN)


def parse_line(line, page_w, page_h, index, block_id=None, block_idx=None):
    x = int(line.get("X", -1))
    y = int(line.get("Y", -1))
    w = int(line.get("WIDTH", -1))
    h = int(line.get("HEIGHT", -1))
    is_vertical = bool(line.get("DIRECTION", "縦" if w < h else "横") == "縦")
    return {
        "index": index,
        "x": x,
        "y": y,
        "width": w,
        "height": h,
        "bbox": [x, y, x+w, y+h],
        "unilm_bbox": [
            min(999, max(0, x*1000//page_w)),
            min(999, max(0, y*1000//page_h)),
            min(999, max(0, (x+w)*1000//page_w)),
            min(999, max(0, (y+h)*1000//page_h))],
        "is_vertical": is_vertical,
        "string": line.get("STRING", ""),
        "type": get_line_type(line.get("TYPE", "")),
        "is_title": bool(line.get("TITLE", "false").lower() == "true"),
        "is_author": bool(line.get("AUTHOR", "false").lower() == "true"),
        "order": float(line.get("ORDER", -1)),
        "block_id": block_id,
        "block_index": block_idx,
        "obj": line,
    }


def parse_root(root, xml_path=None):
    dat = dict()
    dat["pages"] = list()
    for i, page in enumerate(root.findall("PAGE")):
        kyokaku = page.get("KYOKAKU")
        if kyokaku is not None:
            kyokaku = True if "true" == kyokaku.lower(
            ) else False if "false" == kyokaku.lower() else None
        dat["pages"].append({
            "index": i,
            "pid": Path(xml_path).name.split(".")[0] if xml_path else "unknown",
            "width": int(page.get("WIDTH", -1)),
            "height": int(page.get("HEIGHT", -1)),
            "image": page.get("IMAGENAME"),
            "kyokaku": kyokaku,
            "lines": [],
            "text_blocks": [],
            "obj": page,
        })
        page_w = dat["pages"][-1]["width"]
        page_h = dat["pages"][-1]["height"]
        lines = dat["pages"][-1]["lines"]

        def traverse(page_or_block, block_idx=0):
            for child in page_or_block:
                if "LINE" == child.tag:
                    lines.append(parse_line(child, page_w, page_h, len(lines)))
                elif "TEXTBLOCK" == child.tag:
                    for shape in child.iter("SHAPE"):
                        for polygon in shape.iter("POLYGON"):
                            polygon = list(
                                map(int, polygon.get("POINTS").split(",")))
                            break
                        break
                    try:
                        block_id = int(child.get("BLOCKID"))
                    except TypeError:
                        block_id = block_idx
                    block_lines = list()
                    for line in child.findall("LINE"):
                        block_lines.append(parse_line(line, page_w, page_h,
                                                      len(lines) +
                                                      len(block_lines),
                                                      block_id=block_id, block_idx=block_idx))
                    lines.extend(block_lines)
                    num_vert_block_lines = sum(
                        int(block_line["is_vertical"]) for block_line in block_lines)
                    dat["pages"][-1]["text_blocks"].append({
                        "block_id": block_id,
                        "block_index": block_idx,
                        "polygon": polygon,
                        "is_vertical": bool(len(block_lines) < num_vert_block_lines * 2),
                        "obj": child,
                    })
                    block_idx += 1
                elif "BLOCK" == child.tag:
                    traverse(child, block_idx)
        traverse(page)
        num_vert_lines = sum(int(line["is_vertical"]) for line in lines)
        dat["pages"][-1].update({
            "is_vertical": bool(len(lines) < num_vert_lines * 2)})
    return dat


def parse_xml(xml_path):
    xml_path = xml_path
    with open(xml_path, "r", encoding="utf-8") as f:
        xml = f.read()
    # Delete namespace.
    root = et.fromstring(re.sub('xmlns=".*?"', '', xml, count=1))
    return parse_root(root, xml_path=xml_path)
