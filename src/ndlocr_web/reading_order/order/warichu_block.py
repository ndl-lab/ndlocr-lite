# Copyright (c) 2023, National Diet Library, Japan
#
# This software is released under the CC BY 4.0.
# https://creativecommons.org/licenses/by/4.0/


import numpy as np
import xml.etree.ElementTree as et


from ..utils.logger import get_logger
from ..utils.xml import IndexedTags, insert_before


def group_warichu(root):
    """
    LINE tags of 割注 type are grouped through the dilation process. Generate a
    new WARICHUBLOCK tag for each group and store the 割注 LINEs under it.
    WARICHUBLOCK are intermediate products that should not be included in the
    final output. When you are finished using them, discard them using
    `ungroup_warichu`.
    """

    logger = get_logger("ReadingOrder")

    def parse_bbox(tag):
        x = int(tag.get("X"))
        y = int(tag.get("Y"))
        w = int(tag.get("WIDTH"))
        h = int(tag.get("HEIGHT"))
        return (x, y, x+w, y+h)

    def parse_order(tag):
        f = float(tag.get("ORDER"))
        return f

    def dilate_bbox(data):
        x0, y0, x1, y1 = data["bbox"]
        w, h = x1 - x0, y1 - y0
        is_vertical = w < h
        if is_vertical:
            step = w * 0.1
            data["bbox"] = (x0 - step/2, y0, x1 + step/2, y1)
        else:
            step = h * 0.1
            data["bbox"] = (x0, y0 - step/2, x1, y1 + step/2)
        return data

    def detect_parent(group):
        for warichu in group:
            parent = warichu["parent"]
            if "TEXTBLOCK" == parent.tag:
                return parent, warichu["obj"]
            elif "PAGE" != parent.tag:
                logger.warning("Warichu was found under: %s" % parent.tag)
        if group:
            return group[0]["parent"], group[0]["obj"]
        else:
            return None, None

    def bounding_bbox(*bbox):
        min_x = min(x0 for x0, _, _, _ in bbox)
        min_y = min(y0 for _, y0, _, _ in bbox)
        max_x = max(x1 for _, _, x1, _ in bbox)
        max_y = max(y1 for _, _, _, y1 in bbox)
        return (min_x, min_y, max_x, max_y)

    def intersect_1d(line0, line1):
        x0, x1 = line0
        y0, y1 = line1
        # line0    ----   cases
        #     / --         (1)
        #    |    --       (2)
        # line1     --     (3)
        #    |        --   (4)
        #     ¥         -- (5)
        if y1 < x0:
            return 0       # (1)
        elif y0 < x0:
            return y1 - x0  # (2)
        elif y1 < x1:
            return y1 - y0  # (3)
        elif y0 < x1:
            return x1 - y0  # (4)
        else:
            return 0       # (5)

    def intersect_bbox(bbox0, bbox1):
        x00, y00, x01, y01 = bbox0
        x10, y10, x11, y11 = bbox1
        return (intersect_1d((x00, x01), (x10, x11)) *
                intersect_1d((y00, y01), (y10, y11)))

    def apply_page(page):
        with IndexedTags(root) as it:
            warichu_list = list()

            for line in page.findall(".//LINE[@TYPE='割注']"):
                index = int(line.get(it.key))
                warichu_list.append({
                    "bbox": parse_bbox(line),
                    "bbox_orig": parse_bbox(line),
                    "obj": line,
                    "index": index,
                    "order": parse_order(line),
                    "parent": page.find(".//LINE[@%s='%d']/.." %
                                        (it.key, index)),
                })

            for i, warichu in enumerate(warichu_list):
                warichu_list[i] = dilate_bbox(warichu)

            groups = list()
            grouped = list()
            for w0 in warichu_list:
                if w0["index"] in grouped:
                    continue
                groups.append([w0])
                grouped.append(w0["index"])
                for w1 in warichu_list:
                    if w1["index"] in grouped:
                        continue
                    if 0 < intersect_bbox(w0["bbox"], w1["bbox"]):
                        groups[-1].append(w1)
                        grouped.append(w1["index"])

            for group in groups:
                x0, y0, x1, y1 = bounding_bbox(
                    *[w["bbox_orig"] for w in group])
                order = np.median([w["order"] for w in group])
                block = et.Element("WARICHUBLOCK", attrib={
                    "X": str(x0),
                    "Y": str(y0),
                    "WIDTH": str(x1 - x0),
                    "HEIGHT": str(y1 - y0),
                    "ORDER": str(order),
                })
                for warichu in group:
                    block.append(warichu["obj"])
                parent, child = detect_parent(group)
                insert_before(parent, block, child)
                for warichu in group:
                    warichu["parent"].remove(warichu["obj"])

    if "PAGE" == root.tag:
        apply_page(root)
    else:
        for page in root.findall(".//PAGE"):
            apply_page(page)


def ungroup_warichu(root):
    if "WARICHUBLOCK" == root.tag:
        raise ValueError("Cannot move child elements outside the root element.")
    def _ungroup_warichu(parent):
        new_children = []
        for child in parent:
            _ungroup_warichu(child)
            if "WARICHUBLOCK" == child.tag:
                for tag in child:
                    new_children.append(tag)
            else:
                new_children.append(child)
        parent[:] = new_children
    _ungroup_warichu(root)
    return root


class GroupWarichu:
    def __init__(self, xml):
        self.xml = xml
        group_warichu(self.xml)

    def __enter__(self):
        pass

    def __exit__(self, *_):
        ungroup_warichu(self.xml)
