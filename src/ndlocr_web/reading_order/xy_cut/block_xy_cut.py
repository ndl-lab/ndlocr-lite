# Copyright (c) 2023, National Diet Library, Japan
#
# This software is released under the CC BY 4.0.
# https://creativecommons.org/licenses/by/4.0/


import numpy as np


class BlockNode:
    """
    Node class for document block tree
    """

    def __init__(self, x0, y0, x1, y1, parent):
        self.x0 = int(x0)
        self.y0 = int(y0)
        self.x1 = int(x1)
        self.y1 = int(y1)
        self.parent = parent
        self.children = []
        self.line_idx = []
        self.num_lines = 0
        self.num_vertical_lines = 0

    def get_coords(self):
        return self.x0, self.y0, self.x1, self.y1

    def append(self, child):
        self.children.append(child)

    def is_x_split(self):
        _, y0, _, y1 = self.get_coords()
        for child in self.children:
            _, c0, _, c1 = child.get_coords()
            if (y0, y1) != (c0, c1):
                return False
        return True

    def is_vertical(self):
        return self.num_lines < self.num_vertical_lines * 2


def calc_max_gap(hist):
    """
    Experimental code to replace `calc_min_span` which is not functional yet.
    ---
    hist: [N], int
    """
    if 1 == hist.size:
        return 0, 1, 0
    is_positive = 0 < hist
    if 0 == is_positive.sum():
        return 0, len(hist), 0
    m = np.median(hist[is_positive])
    diff = np.diff(np.concatenate(([0], hist < m, [0])))
    area_start = np.where(diff == 1)[0]
    area_end = np.where(diff == -1)[0]
    areas = [(m - hist[start:end]).sum()
             for start, end in zip(area_start, area_end)]
    if 0 == len(areas):
        return 0, hist.size, 0
    i_max = np.argmax(areas)
    return area_start[i_max], area_end[i_max], areas[i_max]


def calc_min_span(hist):
    """
    hist: [N], int
    """
    if 1 == hist.size:
        return 0, 1, hist
    min_val = hist.min()
    max_val = hist.max()
    diff = np.diff(np.concatenate(([0], hist == min_val, [0])))
    start_idx = np.where(diff == 1)[0]
    end_idx = np.where(diff == -1)[0]
    i = np.argmax(end_idx - start_idx)
    return start_idx[i], end_idx[i], -min_val/max_val if 0. < max_val else 0.


def calc_hist(table, x0, y0, x1, y1):
    """
    table: [H, W], int
    """
    x_hist = table[y0:y1:, x0:x1].sum(axis=0)
    y_hist = table[y0:y1:, x0:x1].sum(axis=1)
    return x_hist, y_hist


def split(parent, table, x0=None, y0=None, x1=None, y1=None):
    """
    Call `block_xy_cut` recursively
    """
    x0 = parent.x0 if x0 is None else x0
    y0 = parent.y0 if y0 is None else y0
    x1 = parent.x1 if x1 is None else x1
    y1 = parent.y1 if y1 is None else y1
    if not (x0 < x1 and y0 < y1):
        return
    if (x0, y0, x1, y1) == parent.get_coords():
        return
    child = BlockNode(x0, y0, x1, y1, parent)
    parent.append(child)
    block_xy_cut(table, child)


def split_x(parent, table, val, x0, x1):
    """
    Call `split`
    """
    split(parent, table, x1=x0)
    split(parent, table, x0=x0, x1=x1)
    split(parent, table, x0=x1)


def split_y(parent, table, val, y0, y1):
    """
    Call `split`
    """
    split(parent, table, y1=y0)
    split(parent, table, y0=y0, y1=y1)
    split(parent, table, y0=y1)


def block_xy_cut(table, me_node):
    """
    table  : [H, W], int
    me_node: BlockNode
    """
    x0, y0, x1, y1 = me_node.get_coords()
    x_hist, y_hist = calc_hist(table, x0, y0, x1, y1)
    x_beg, x_end, x_val = calc_min_span(x_hist)
    y_beg, y_end, y_val = calc_min_span(y_hist)
    x_beg += x0
    x_end += x0
    y_beg += y0
    y_end += y0
    if (x0, x1, y0, y1) == (x_beg, x_end, y_beg, y_end):
        return
    if y_val < x_val:
        split_x(me_node, table, x_val, x_beg, x_end)
    elif x_val < y_val:
        split_y(me_node, table, y_val, y_beg, y_end)
    elif (x_end - x_beg) < (y_end - y_beg):
        split_y(me_node, table, y_val, y_beg, y_end)
    else:
        split_x(me_node, table, x_val, x_beg, x_end)


def get_optimal_grid(bboxes):
    """
    Determine optimal grid size from bboxes
    ---
    num: int
    """
    num = len(bboxes)
    grid_size = 100 * np.sqrt(num)
    return grid_size


def normalize_bboxes(bboxes, grid, scale=1.0, tolerance=0.25):
    """
    bboxes: [N, 4], int
    grid  : int
    """
    # Make width and height non-negative
    bboxes[:, 2] = np.where(bboxes[:, 0] < bboxes[:, 2],
                            bboxes[:, 2], bboxes[:, 0])
    bboxes[:, 3] = np.where(bboxes[:, 1] < bboxes[:, 3],
                            bboxes[:, 3], bboxes[:, 1])
    # Dilation (or erosion)
    if 1.0 != scale:
        w = bboxes[:, 2] - bboxes[:, 0]
        h = bboxes[:, 3] - bboxes[:, 1]
        m = np.median(np.minimum(w, h))
        lower = m * (1.0 - tolerance)
        upper = m * (1.0 + tolerance)
        _x = np.logical_and(w < h, lower <= w, w < upper)
        _y = np.logical_and(h < w, lower <= h, h < upper)
        bboxes[_x, 0] -= ((scale-1.0) * w[_x] // 2).astype(np.int64)
        bboxes[_x, 2] += ((scale-1.0) * w[_x] // 2).astype(np.int64)
        bboxes[_y, 1] -= ((scale-1.0) * h[_y] // 2).astype(np.int64)
        bboxes[_y, 3] += ((scale-1.0) * h[_y] // 2).astype(np.int64)
    # Coarse-grain
    x_min = bboxes[:, 0].min()
    y_min = bboxes[:, 1].min()
    w_page = bboxes[:, 2].max() - x_min
    h_page = bboxes[:, 3].max() - y_min
    x_grid = grid if w_page < h_page else grid * (w_page/h_page)
    y_grid = grid if h_page < w_page else grid * (h_page/w_page)
    bboxes[:, 0] = (bboxes[:, 0] - x_min) * x_grid // w_page
    bboxes[:, 1] = (bboxes[:, 1] - y_min) * y_grid // h_page
    bboxes[:, 2] = (bboxes[:, 2] - x_min) * x_grid // w_page
    bboxes[:, 3] = (bboxes[:, 3] - y_min) * y_grid // h_page
    # Avoid negative indices
    bboxes = np.where(bboxes < 0, 0, bboxes)
    return bboxes


def make_mesh_table(bboxes):
    """
    bboxes: [N, 4], int, normalized
    """
    x_grid = bboxes[:, 2].max() + 1
    y_grid = bboxes[:, 3].max() + 1
    table = np.zeros((y_grid, x_grid)).astype(np.int32)
    for bbox in bboxes:
        x0, y0, x1, y1 = bbox
        table[y0:y1, x0:x1] = 1
    return table


def get_ranking(node, ranks, rank=0):
    """
    node : BlockNode
    ranks: [N], int, where N is number of bboxes
    rank : int
    """
    for i in node.line_idx:
        ranks[i] = rank
        rank += 1
    for child in node.children:
        rank = get_ranking(child, ranks, rank)
    return rank


def calc_iou(box, boxes):
    """
    IoU of 1 v.s. many
    ---
    box  : [4], int
    boxes: [N,4], int
    """
    x0 = np.maximum(box[0], boxes[:, 0])
    y0 = np.maximum(box[1], boxes[:, 1])
    x1 = np.minimum(box[2], boxes[:, 2])
    y1 = np.minimum(box[3], boxes[:, 3])
    inter_area = np.maximum(0, x1 - x0 + 1) * np.maximum(0, y1 - y0 + 1)
    box_area = (box[2] - boxes[:, 0] + 1) * (box[3] - boxes[:, 1] + 1)
    boxes_area = (boxes[:, 2] - box[0] + 1) * (boxes[:, 3] - box[1] + 1)
    with np.errstate(divide='ignore', invalid='ignore'):
        iou = inter_area / (box_area + boxes_area - inter_area)
    return iou


def get_block_node_bboxes(root):
    """
    root: BlockNode
    """
    bboxes = []
    routers = []

    def collect(node, router):
        if 0 == len(node.children):
            bboxes.append(node.get_coords())
            routers.append(router)
        for i, child in enumerate(node.children):
            collect(child, router + [i])
    collect(root, [])
    bboxes = np.array(bboxes)
    return routers, bboxes


def route_tree(root, router):
    """
    root  : BlockNode
    router: list of int
    """
    node = root
    for i in router:
        node = node.children[i]
    return node


def assign_bbox_to_node(root, bboxes):
    """
    Find optimal (= max IoU) node to assign a bbox
    ---
    root  : BlockNode
    bboxes: [N,4], int
    """
    routers, leaves = get_block_node_bboxes(root)
    for i, bbox in enumerate(bboxes):
        iou = calc_iou(bbox, leaves)
        j = np.nanargmax(iou)
        route_tree(root, routers[j]).line_idx.append(i)


def sort_nodes(node, bboxes):
    """
    Sort nodes based on assinged lines
    ---
    node  : BlockNode
    bboxes: [N,4], int
    NOTE  : When splitting up and down, the reading order is always up to down.
            Branches should be made so that the upper side is first in depth first
            order. When splitting left and right, the order changes depending on
            whether the content is written vertically or horizontally.
    """
    if 0 < len(node.line_idx):
        w = bboxes[node.line_idx, 2] - bboxes[node.line_idx, 0]
        h = bboxes[node.line_idx, 3] - bboxes[node.line_idx, 1]
        node.num_lines = len(node.line_idx)
        node.num_vertical_lines = (w < h).sum()
        if 1 < node.num_lines:
            x0, y0, _, _ = bboxes[node.line_idx, :].T
            perm = np.lexsort((y0, -x0) if node.is_vertical else (x0, y0))
            node.line_idx[:] = [node.line_idx[i] for i in perm]
    else:
        for child in node.children:
            num, v_num = sort_nodes(child, bboxes)
            node.num_lines += num
            node.num_vertical_lines += v_num
        if node.is_x_split() and node.is_vertical():
            node.children = node.children[::-1]
    return node.num_lines, node.num_vertical_lines


def draw_partition_tree(node, draw, color=[10, 10, 10]):
    """
    Visualization utility
    ---
    node: BlockNode
    draw: ImageDraw.Draw
    """
    draw.rectangle(node.get_coords(), outline=(*map(int, color), 255), width=1)
    for child in node.children:
        draw_partition_tree(child, draw, color)


def solve(bboxes, grid=None, plot_path=None, logger=None, scale=1.0):
    """
    Library API
    ---
    bboxes   : [N,4], int
    grid     : int, number of bins for shorter side
    plot_path: str, path of partition plot
    """
    if 0 == len(bboxes):
        return []
    if grid is None:
        grid = get_optimal_grid(bboxes)
    if logger is None:
        from reading_order.utils.logger import get_logger
        logger = get_logger(__name__)

    bboxes = normalize_bboxes(bboxes, grid, scale)
    table = make_mesh_table(bboxes)
    h, w = table.shape
    root = BlockNode(0, 0, w, h, None)
    block_xy_cut(table, root)
    assign_bbox_to_node(root, bboxes)
    sort_nodes(root, bboxes)
    if root.num_lines != len(bboxes):
        logger.warning("Num of lines do not match: %d, %d" % (
            root.num_lines, len(bboxes)))
    ranks = [-1 for _ in range(len(bboxes))]
    get_ranking(root, ranks)
    if plot_path:
        from PIL import Image, ImageDraw
        with Image.new("RGB", size=(w, h), color=(255, 255, 255)) as im:
            draw = ImageDraw.Draw(im)
            draw_partition_tree(root, draw)
            im.save(plot_path)
    return ranks
