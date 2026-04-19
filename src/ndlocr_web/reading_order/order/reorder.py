# Copyright (c) 2023, National Diet Library, Japan
#
# This software is released under the CC BY 4.0.
# https://creativecommons.org/licenses/by/4.0/


import numpy as np
import functools


from reading_order.utils.xml import ConstantNumberOfTags
from reading_order.order.smooth_order import smooth_order
from reading_order.order.warichu_block import GroupWarichu


def check_iou(a, b):
    """
    a: [xmin, ymin, xmax, ymax]
    b: [xmin, ymin, xmax, ymax]

    return: array(iou)
    """
    b = np.asarray(b)
    a_area = (a[  2] - a[  0]) * (a[  3] - a[  1])
    b_area = (b[  2] - b[  0]) * (b[  3] - b[  1])
    intersection_xmin = np.maximum(a[0], b[0])
    intersection_ymin = np.maximum(a[1], b[1])
    intersection_xmax = np.minimum(a[2], b[2])
    intersection_ymax = np.minimum(a[3], b[3])
    intersection_w = np.maximum(0, intersection_xmax - intersection_xmin)
    intersection_h = np.maximum(0, intersection_ymax - intersection_ymin)
    intersection_area = intersection_w * intersection_h
    min_area=min(a_area,b_area)
    #print(intersection_area/min_area)
    sum_area=a_area+b_area-intersection_area
    if min_area>0 and intersection_area/min_area>0.8:
        return True
    return False

def check_dup(aconf,bconf):
    if check_iou(aconf[:4],bconf[:4]):
        if aconf[-1]>=bconf[-1]:
            return 1
        else:
            return 2
    return 0

def remove_dup(childrenlist):
    lines=list()
    complines = list()
    for element in childrenlist:
        if element.tag in ["LINE", "WARICHUBLOCK"]:
            w = float(element.get("WIDTH", -1))
            h = float(element.get("HEIGHT", -1))
            conf = float(element.get("CONF", -1))
            x = float(element.get("X", -1))
            y = float(element.get("Y", -1))
            checkdupval=0
            if len(lines)!=0 and lines[-1].tag in ["LINE", "WARICHUBLOCK"]:
                checkdupval=check_dup(complines[-1],[x,y,x+w,y+h,conf])
            if checkdupval==0:#重複なし
                lines.append(element)
                complines.append([x,y,x+w,y+h,conf])
            elif checkdupval==1:#重複あり （今見ているlineをスキップ）
                continue
            elif checkdupval==2:#重複あり（比較対象を削除）
                del lines[-1]
                del complines[-1]
                lines.append(element)
                complines.append([x,y,x+w,y+h,conf])
            else:
                print("error!")
        else:
            lines.append(element)
    return lines



def sort_lines_local(root):
    """
    Sorts LINE tags that exist directly under the `root`. This sorting is based
    on the coordinates of the LINE tag and ignores their ORDER. The tag of
    `root` can be anytGhing, but now I am considering of either TEXTBLOCK or
    WARICHUBLOCK.
    """

    num_vertical = 0
    num_lines = 0
    lines = list()
    not_lines = list()
    widths, heights = list(), list()
    for element in root:
        if element.tag in ["LINE", "WARICHUBLOCK"]:
            w = float(element.get("WIDTH", -1))
            h = float(element.get("HEIGHT", -1))
            num_vertical += 1 if w < h else 0
            num_lines += 1
            widths.append(w)
            heights.append(h)
            x = float(element.get("X", -1))
            y = float(element.get("Y", -1))
            order = float(element.get("ORDER", np.nan))
            lines.append((x+w/2, y+h/2, order, element))
        else:
            not_lines.append(element)

    if not widths:
        return root, -1

    is_vertical = num_lines < num_vertical * 2

    # Define comparison functions.
    span_median = np.median(widths) if is_vertical else np.median(heights)
    margin = span_median * 0.3

    def cmp_v(a0, a1):
        x0, y0, _, _, = a0  # Ignore order, obj
        x1, y1, _, _, = a1  # Ignore order, obj
        if margin < x1 - x0:
            return 1
        elif margin < x0 - x1:
            return -1
        else:
            return y0 - y1

    def cmp_h(a0, a1):
        x0, y0, _, _, = a0  # Ignore order, obj
        x1, y1, _, _, = a1  # Ignore order, obj
        if margin < y0 - y1:
            return 1
        elif margin < y1 - y0:
            return -1
        else:
            return x0 - x1

    # Sort LINE by coordinates.
    lines = sorted(lines, key=functools.cmp_to_key(
        cmp_v if is_vertical else cmp_h))
    sorted_lines = [line for _, _, _, line in lines]
    if len(sorted_lines)>0:
        sorted_lines =remove_dup(sorted_lines)
    # Calc median.
    valid_orders = [order for _, _, order, _ in lines if not np.isnan(order)]
    median = sorted(valid_orders)[
        len(valid_orders)//2] if valid_orders else np.nan

    # Assign lines.
    root[:] = sorted_lines + not_lines
    return root, median


def sort_lines(root, smoothing=True):
    """
    An example of acceptable XML as input or intermediate states.
    <PAGE>                 - One or more PAGE tags should exist in a file.
     <TEXTBLOCK>           - TEXTBLOCK must be placed right under PAGE tag.
      <LINE ORDER="0.1"/>  - LINE tags inside TEXTBOCK.
      <WARICHUBLOCK>       - WARICHUBLOCK is temporarily added to group up 割注s.
       <LINE ORDER="0.2"/> - 割注 LINE tags.
       <LINE ORDER="0.3"/>
      </WARICHUBLOCK>
     </TEXTBLOCK>
     <LINE ORDER="0.8"/>   - LINE tags can be also placed outside TEXTBLOCK.
    </PAGE>
    """

    def traverse(page_or_block):
        tobe_sorted = list()
        unsorted = list()
        for element in page_or_block:
            if "TEXTBLOCK" == element.tag:
                # Sort 割注 block inside TEXTBLOCK.
                for wari in element.findall("./WARICHUBLOCK"):
                    _, _ = sort_lines_local(wari)
                # Sort LINEs inside TEXTBLOCK.
                element, median = sort_lines_local(element)
                tobe_sorted.append((median, element))
            elif "LINE" == element.tag:
                # Sort LINEs outside TEXTBLOCK.
                order = float(element.get("ORDER", np.nan))
                tobe_sorted.append((order, element))
            elif "WARICHUBLOCK" == element.tag:
                # Sort 割注 outside TEXTBLOCK.
                element, median = sort_lines_local(element)
                tobe_sorted.append((median, element))
            elif "BLOCK" == element.tag:
                traverse(element)
                unsorted.append(element)
            elif "PAGE" == element.tag:
                traverse(element)
                unsorted.append(element)
            else:
                unsorted.append(element)
        sorted_children = sorted(tobe_sorted, key=lambda x: x[0])
        sorted_children = [obj for _, obj in sorted_children]
        if len(sorted_children)>0:
            sorted_children =remove_dup(sorted_children)
        page_or_block[:] = sorted_children + unsorted

    # To check that the number of tags does not change by sorting.
    """
    with ConstantNumberOfTags(root):
        with GroupWarichu(root):
            traverse(root)
        if smoothing:
            smooth_order(root)
    """
    with GroupWarichu(root):
        traverse(root)
    if smoothing:
        smooth_order(root)
