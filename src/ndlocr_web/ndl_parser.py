#!/usr/bin/env python

# Copyright (c) 2023, National Diet Library, Japan
#
# This software is released under the CC BY 4.0.
# https://creativecommons.org/licenses/by/4.0/

from typing import List
from enum import IntEnum, auto
import sys
import os
from pathlib import Path
from lxml import etree as ET
try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable=None, *args, **kwargs):  # type: ignore[misc]
        return iterable if iterable is not None else range(0)
import numpy as np

class Category(IntEnum):
    TEXT_BLOCK = 0
    LINE_MAIN = auto()
    LINE_CAPTION = auto()
    LINE_AD = auto()
    LINE_NOTE = auto()
    LINE_NOTE_TOCHU = auto()

    BLOCK_FIG = auto()
    BLOCK_AD = auto()
    BLOCK_PILLAR = auto()
    BLOCK_FOLIO = auto()
    BLOCK_RUBI = auto()

    BLOCK_CHART = auto()
    BLOCK_EQN = auto()
    BLOCK_CFM = auto()
    BLOCK_ENG = auto()

    BLOCK_TABLE = auto()

    LINE_TITLE = auto()
    
    


class InlineCategory(IntEnum):
    INLINE_ENG = auto()
    INLINE_RENG = auto()
    INLINE_COLOR = auto()
    INLINE_EQN = auto()
    INLINE_CFM = auto()
    INLINE_HINV = auto()
    INLINE_HAND = auto()


categories = [
    {'id': int(Category.TEXT_BLOCK),    'name': 'text_block',    'org_name': '本文ブロック'},
    {'id': int(Category.LINE_MAIN),     'name': 'line_main',     'org_name': '本文'},
    {'id': int(Category.LINE_CAPTION),  'name': 'line_caption',  'org_name': 'キャプション'},
    {'id': int(Category.LINE_AD),       'name': 'line_ad',       'org_name': '広告文字'},
    {'id': int(Category.LINE_NOTE),    'name': 'line_note',    'org_name': '割注'},
    {'id': int(Category.LINE_NOTE_TOCHU),    'name': 'line_note_tochu',    'org_name': '頭注'},
    {'id': int(Category.BLOCK_FIG),     'name': 'block_fig',     'org_name': '図版'},
    {'id': int(Category.BLOCK_AD),      'name': 'block_ad',      'org_name': '広告'},
    {'id': int(Category.BLOCK_PILLAR),  'name': 'block_pillar',  'org_name': '柱'},
    {'id': int(Category.BLOCK_FOLIO),   'name': 'block_folio',   'org_name': 'ノンブル'},
    {'id': int(Category.BLOCK_RUBI),    'name': 'block_rubi',    'org_name': 'ルビ'},
    {'id': int(Category.BLOCK_CHART),   'name': 'block_chart',   'org_name': '組織図'},
    {'id': int(Category.BLOCK_EQN),     'name': 'block_eqn',     'org_name': '数式'},
    {'id': int(Category.BLOCK_CFM),     'name': 'block_cfm',     'org_name': '化学式'},
    {'id': int(Category.BLOCK_ENG),     'name': 'block_eng',     'org_name': '欧文'},
    {'id': int(Category.BLOCK_TABLE),     'name': 'block_table',     'org_name': '表組'},
    {'id': int(Category.LINE_TITLE),    'name': 'line_title',    'org_name': 'タイトル本文'},
    ]

categories_org_name_index = {elem['org_name']: elem for elem in categories}
categories_name_index = {elem['name']: elem for elem in categories}


def org_name_to_id(s: str):
    return categories_org_name_index[s]['id']


def name_to_org_name(s: str):
    return categories_name_index[s]['org_name']


inline_categories = [
    {'id': int(InlineCategory.INLINE_ENG),   'name': 'inline_eng',   'org_name': '欧文'},
    {'id': int(InlineCategory.INLINE_RENG),  'name': 'inline_reng',  'org_name': '回転欧文'},
    {'id': int(InlineCategory.INLINE_COLOR), 'name': 'inline_color', 'org_name': '色付文字'},
    {'id': int(InlineCategory.INLINE_EQN),   'name': 'inline_eqn',   'org_name': '数式'},
    {'id': int(InlineCategory.INLINE_CFM),   'name': 'inline_cfn',   'org_name': '化学式'},
    {'id': int(InlineCategory.INLINE_HINV),  'name': 'inline_hinv',  'org_name': '縦中横'},
    {'id': int(InlineCategory.INLINE_HAND),  'name': 'inline_hand',  'org_name': '手書き'}]

inline_categories_org_name_index = {elem['org_name']: elem for elem in inline_categories}
inline_categories_name_index = {elem['name']: elem for elem in inline_categories}


import math

def point_in_polygon(point, polygon, measureDist=False):
    def point_line_distance(px, py, x1, y1, x2, y2):
        """ Calculate the minimum distance from point to line segment. """
        line_len_square = (x2 - x1) ** 2 + (y2 - y1) ** 2
        if line_len_square == 0:
            return math.hypot(px - x1, py - y1)
        t = ((px - x1) * (x2 - x1) + (py - y1) * (y2 - y1)) / line_len_square
        t = max(0, min(1, t))
        projection_x = x1 + t * (x2 - x1)
        projection_y = y1 + t * (y2 - y1)
        return math.hypot(px - projection_x, py - projection_y)
    polygon=np.squeeze(polygon)
    #print(point,polygon)
    x, y = point
    n = len(polygon)
    inside = False
    min_dist = float('inf')

    px, py = polygon[0]
    for i in range(1, n + 1):
        sx, sy = polygon[i % n]
        if min(py, sy) < y <= max(py, sy) and x <= max(px, sx):
            if py != sy:
                xinters = (y - py) * (sx - px) / (sy - py) + px
            if px == sx or x <= xinters:
                inside = not inside
        if measureDist:
            dist = point_line_distance(x, y, px, py, sx, sy)
            if dist < min_dist:
                min_dist = dist
        px, py = sx, sy

    # Check if the point is on the boundary of the polygon
    for i in range(n):
        sx, sy = polygon[i]
        ex, ey = polygon[(i + 1) % n]
        if (sy == ey and sy == y and min(sx, ex) <= x <= max(sx, ex)) or \
           (sx == ex and sx == x and min(sy, ey) <= y <= max(sy, ey)):
            return 0 if not measureDist else 0.0

    if measureDist:
        return min_dist if inside else -min_dist
    return 1 if inside else -1


def inline_org_name_to_id(s: str):
    return inline_categories_org_name_index[s]['id']


def inline_name_to_org_name(s: str):
    return inline_categories_name_index[s]['org_name']


class NDLObject:
    def __init__(self, x, y, width, height, category_id=-1):
        self.x, self.y = x, y
        self.width, self.height = width, height
        self.category_id = category_id

    def __repr__(self):
        return f'NDLObject({self.x}, {self.y}, {self.width}, {self.height}, category_id={self.category_id})'


class NDLBlock(NDLObject):
    def __init__(self, type, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.category_id = org_name_to_id(type)
        self.type = type

    def __repr__(self):
        return f'NDLBlock({self.type}, {self.x}, {self.y}, {self.width}, {self.height}, category_id={self.category_id})'


class NDLChar(NDLObject):
    def __init__(self, moji: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.moji = moji
        self.category_id = Category.CHAR

    def __repr__(self):
        return f'NDLChar(\'{self.moji}\', {self.x}, {self.y}, {self.width}, {self.height}, category_id={self.category_id})'


class NDLInline(NDLObject):
    def __init__(self, type, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.category_id = inline_org_name_to_id(type)
        self.type = type

    def __repr(self):
        return f'NDLInline({self.type}, {self.x}, {self.y}, {self.width}, {self.height}, category_id={self.category_id})'


class NDLLine(NDLObject):
    def __init__(self, chars: List[NDLChar], opt: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.chars = chars
        self.category_id = org_name_to_id(opt)
        self.opt = opt

    def __repr__(self):
        return f'NDLLine({self.chars}, {self.opt}, {self.x}, {self.y}, {self.width}, {self.height}, category_id={self.category_id})'


class NDLTextblock(NDLObject):
    def __init__(self, points, opt: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.category_id = org_name_to_id(opt)
        self.type = opt
        self.points = points

    def __repr__(self):
        return f'NDLTextblock({self.type}, {self.x}, {self.y}, {self.width}, {self.height}, category_id={self.category_id})'


class NDLPage:
    def __init__(self, img_path: str, objects: List[NDLObject], source_xml: str):
        self.img_path = img_path
        self.objects = objects
        self.source_xml = source_xml

    def __repr__(self):
        return f'NDLPage({self.img_path}, {self.objects}, {self.source_xml})'


class NDLDataset:
    def __init__(self, pages=None):
        self.pages = [] if pages is None else pages

    def parse(self, xml_path: str, img_dir: str):
        import xml.etree.ElementTree as ET
        from pathlib import Path

        print(f'loading from {xml_path} ... ', end='')

        tree = ET.parse(xml_path)
        root = tree.getroot()
        pages = []

        def parse_bbox(elem):
            return float(elem.attrib['X']), float(elem.attrib['Y']), float(elem.attrib['WIDTH']), float(elem.attrib['HEIGHT'])

        def get_tag(elem):
            prefix, has_namespace, postfix = elem.tag.partition('}')
            if has_namespace:
                tag = postfix
            else:
                tag = child_elem.tag
            return tag

        def parse_points(elem):
            points_str = elem[0].attrib['POINTS']
            points_list = [float(i) for i in points_str.split(',')]
            return points_list

        def points_to_bbox(points_list):
            if len(points_list) % 2 == 1:
                print("ERROR: Invalid polygon points")
                return 0, 0, 0, 0
            it = iter(points_list)
            min_x = points_list[0]
            min_y = points_list[1]
            max_x = 0
            max_y = 0
            for i in range(2, len(it), 2):
                tx = it[i]
                ty = it[i+1]
                if min_x > tx:
                    min_x = tx
                if max_x < tx:
                    max_x = tx
                if min_y > ty:
                    min_y = ty
                if max_y < ty:
                    max_y = ty
            return min_x, min_y, (max_x-min_x), (max_y-min_y)

        def parse_textblock(elem, objects, ad=False):
            for child_elem in elem:
                prefix, has_namespace, postfix = child_elem.tag.partition('}')
                if has_namespace:
                    tag = postfix
                else:
                    tag = child_elem.tag

                if tag == 'SHAPE':  # SHAPE info in TEXT_BLOCK
                    points = parse_points(child_elem)
                    bbox = points_to_bbox(points)
                    opt = '本文ブロック'
                    if ad:
                        opt = '広告本文ブロック'
                    objects.append(
                        NDLTextblock(points, opt, *bbox))
                elif tag == 'LINE':  # LINE in TEXT_BLOCK
                    chars = []
                    bbox = parse_bbox(child_elem)
                    for char in child_elem:
                        bbox_char = parse_bbox(char)
                        if get_tag(char) != 'CHAR':  # INLINE
                            print(char.attrib['TYPE'])
                            chars.append(NDLInline(char.attrib['TYPE'], *bbox_char))
                        else:  # CHAR
                            chars.append(NDLChar(char.attrib['MOJI'], *bbox_char))
                    objects.append(
                        NDLLine(chars, child_elem.attrib.get('TYPE', ''), *bbox))
                else:
                    continue

        for page in root:
            img_path = str(Path(img_dir) / page.attrib['IMAGENAME'])
            objects = []
            # print('\n**len {}'.format(len(page)))
            for elem in page:
                prefix, has_namespace, postfix = elem.tag.partition('}')
                if has_namespace:
                    tag = postfix
                else:
                    tag = elem.tag

                if elem.get('ERROR') is not None:
                    print('ERROR attrib is found!!!!')
                    print('skip the element')
                    continue

                if tag == 'BLOCK':
                    bbox = parse_bbox(elem)
                    objects.append(NDLBlock(elem.attrib['TYPE'], *bbox))
                    if elem.attrib['TYPE'] == '広告':
                        print("parse_ad")
                        for child_elem in elem:
                            if get_tag(child_elem) == 'TEXTBLOCK':
                                parse_textblock(child_elem, objects, ad=True)

                elif tag == 'LINE':
                    chars = []
                    bbox = parse_bbox(elem)
                    for char in elem:
                        bbox_char = parse_bbox(char)
                        if get_tag(char) != 'CHAR':  # INLINE
                            print(char.attrib['TYPE'])
                            chars.append(NDLInline(char.attrib['TYPE'], *bbox_char))
                        else:  # CHAR
                            chars.append(NDLChar(char.attrib['MOJI'], *bbox_char))
                    objects.append(
                        NDLLine(chars, elem.attrib.get('TYPE', ''), *bbox))
                elif tag == 'TEXTBLOCK':
                    parse_textblock(elem, objects)
                else:
                    pass
            pages.append(NDLPage(img_path, objects, Path(xml_path).stem))
        print(f'done! {len(pages)} loaded')
        self.pages.extend(pages)


    def summary(self, output_dir: str = "./generated/"):
        import numpy as np
        import matplotlib.pyplot as plt
        from collections import defaultdict
        sizes = []
        bbox_nums = []
        opts = defaultdict(int)
        types = defaultdict(int)
        for page in self.pages:
            cnt = 0
            for obj in page.objects:
                sizes.append(
                    np.array([obj.width, obj.height], dtype=np.float32))
                if isinstance(obj, NDLBlock):
                    types[obj.type] += 1
                cnt += 1
                if isinstance(obj, NDLLine):
                    cnt += len(obj.chars)
                    opts[obj.opt] += 1
            bbox_nums.append(cnt)

        print(opts)
        print(types)

        sizes = np.array(sizes)
        bbox_nums = np.array(bbox_nums)

        def savefig(data, file_name):
            plt.figure()
            plt.hist(data)
            plt.savefig(output_dir + file_name)

        savefig(sizes[:, 0], "hist_width.png")
        savefig(sizes[:, 1], "hist_height.png")
        savefig(sizes[:, 1] / sizes[:, 0], "hist_aspect.png")
        savefig(bbox_nums, "hist_bbox_num.png")


    def to_coco_fmt(self, fx=1.0, fy=1.0, add_char: bool = True, add_block: bool = True, add_prefix: bool = False, suffix: str = ".jpg"):
        import cv2
        import numpy as np
        from pathlib import Path
        from tqdm import tqdm
        from collections import defaultdict
        output = {'images': [], 'annotations': []}
        image_id = 0
        annotation_id = 0
        instance_num = defaultdict(int)

        print("start to_coco_fmt")

        def make_bbox(obj):
            x1, y1 = fx * obj.x, fy * obj.y
            width, height = fx * obj.width, fy * obj.height
            x2, y2 = x1 + width, y1 + height
            bbox = [x1, y1, width, height]
            area = width * height
            contour = [x1, y1, x2, y1, x2, y2, x1, y2]
            return bbox, contour, area

        def make_contours(obj):
            x1, y1 = fx * obj.x, fy * obj.y
            width, height = fx * obj.width, fy * obj.height
            bbox = [x1, y1, width, height]
            it = iter(obj.points)
            cv_contours = []
            for tx, ty in zip(*[it]*2):
                tmp = np.array([tx, ty], dtype='float32')
                cv_contours.append(tmp)
            area = cv2.contourArea(np.array(cv_contours))
            contour = obj.points
            return bbox, contour, area

        def add_annotation(obj):
            bbox, contour, area = make_bbox(obj)
            ann = {'image_id': image_id, 'id': annotation_id, 'bbox': bbox, 'area': area,
                   'iscrowd': 0, 'category_id': int(obj.category_id)}
            ann['segmentation'] = [contour]
            output['annotations'].append(ann)

        def add_line_annotation(obj):
            bbox, _, area_sum = make_bbox(obj)
            area = 0
            contours = []

            # chars as line
            _, contour, area = make_bbox(obj)
            contours.append(contour)
            if area == 0:
                area = area_sum

            ann = {'image_id': image_id, 'id': annotation_id, 'bbox': bbox, 'area': area,
                   'iscrowd': 0, 'category_id': int(obj.category_id)}
            ann['segmentation'] = contours
            output['annotations'].append(ann)

        def add_textblock_annotation(obj):
            bbox, contour, area = make_contours(obj)
            ann = {'image_id': image_id, 'id': annotation_id, 'bbox': bbox, 'area': area,
                   'iscrowd': 0, 'category_id': int(obj.category_id)}
            ann['segmentation'] = [contour]
            output['annotations'].append(ann)

        for page in tqdm(self.pages):
            img = cv2.imread(page.img_path)
            if img is None:
                print(f"Cannot load {page.img_path}")
                continue

            prefix = page.source_xml + "_" if add_prefix else ""
            file_name = prefix + str(Path(page.img_path).name)
            if Path(file_name).suffix != suffix:
                file_name = str(Path(file_name).with_suffix('.jpg'))
            image = {'file_name': file_name,
                     'width': int(fx * img.shape[1]), 'height': int(fy * img.shape[0]), "id": image_id}
            output['images'].append(image)
            for obj in page.objects:
                if add_block:
                    if isinstance(obj, NDLLine):
                        add_line_annotation(obj)
                    elif isinstance(obj, NDLTextblock):
                        add_textblock_annotation(obj)
                    else:  # BLOCK
                        add_annotation(obj)
                    instance_num[int(obj.category_id)] += 1
                    annotation_id += 1

            image_id += 1

        print(instance_num)

        output['categories'] = categories
        output['info'] = {
            "description": "NDL",
            "url": "",
            "version": "0.1a",
            "year": 2021,
            "contributor": "morpho",
            "date_created": "2021/09/01"
        }
        output['licenses'] = []
        return output

    def train_test_split(self, ratio: float = 0.9):
        import random
        from copy import deepcopy
        print("start train_test_split")
        pages = deepcopy(self.pages)
        random.shuffle(pages)
        split = int(ratio * len(pages))
        return NDLDataset(pages[:split]), NDLDataset(pages[split:])


def json_to_file(data, output_path: str):
    import json
    with open(output_path, 'w') as f:
        json.dump(data, f, indent=4)

def textblock_to_polygon(classes, res_segm, min_bbox_size=5):
    import cv2
    import numpy as np

    tb_cls_id = classes.index('text_block')
    polygons = []

    for segm in res_segm[tb_cls_id]:
        mask_img = segm.astype(np.uint8)
        contours, _ = cv2.findContours(mask_img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE) # 輪郭、階層の抽出
        if len(contours)==0: # 領域が存在しないケース
            polygons.append(None)
            continue

        # 領域が存在するケース
        # 複数のcontourに分裂している場合、頂点数の多いもののみを主領域として採用する。 # method4とする
        main_contours_id = 0
        for i in range(len(contours)):
            if len(contours[i]) > len(contours[main_contours_id]):
                main_contours_id = i
        if len(contours[main_contours_id])<4:
            # 主領域が小さい場合、領域は存在しないものとして除外
            polygons.append(None)
            continue
        arclen = cv2.arcLength(contours[main_contours_id], True)
        app_cnt = cv2.approxPolyDP(contours[main_contours_id], epsilon=0.001 * arclen, closed=True)
        _, _, w, h = make_bbox_from_poly(app_cnt)
        if w < min_bbox_size and h < min_bbox_size:
            continue
        polygons.append(app_cnt)

    return polygons

def textblock_to_rect(classes, res_textboxes, min_bbox_size=5):
    #import cv2
    import numpy as np

    rect_polygons = []
    for res_textbox in res_textboxes[0]:
        #print(res_textbox)
        xmin,ymin,xmax,ymax=res_textbox
        if xmax-xmin < min_bbox_size and ymax-ymin < min_bbox_size:
            continue
        #polygon = np.array([[[xmin,ymin]], [[xmax,ymin]], [[xmin,ymax]], [[xmax,ymax]]], dtype=np.int32)
        polygon = np.array([[[xmin,ymin]], [[xmin,ymax]], [[xmax,ymax]], [[xmax,ymin]]], dtype=np.int32)
        rect_polygons.append(polygon)
    return rect_polygons

def add_text_block_head(s, poly, conf=0.0, indent=''):
    s += indent + f'<TEXTBLOCK CONF = "{conf:0.3f}">\n'
    s += indent + '  ' + '<SHAPE><POLYGON POINTS = "'
    for id_poly, pt in enumerate(poly):
        if id_poly > 0:
            s += f',{pt[0][0]},{pt[0][1]}'
        else:
            s += f'{pt[0][0]},{pt[0][1]}'
    s += '"/></SHAPE>\n'
    return s


def add_block_ad_head(s, block_ad, conf=0.0):
    # block_ad [x1, y1, x2, y2, score]
    x, y = int(block_ad[0]), int(block_ad[1])
    w, h = int(block_ad[2] - block_ad[0]), int(block_ad[3] - block_ad[1])
    s += f'<BLOCK TYPE="広告" X="{x}" Y="{y}" WIDTH="{w}" HEIGHT="{h}" CONF = "{conf:0.3f}">\n'
    return s

def add_block_table_head(s, block_table, conf=0.0):
    x, y = int(block_table[0]), int(block_table[1])
    w, h = int(block_table[2] - block_table[0]), int(block_table[3] - block_table[1])
    s += f'<BLOCK TYPE="表組" X="{x}" Y="{y}" WIDTH="{w}" HEIGHT="{h}" CONF = "{conf:0.3f}">\n'
    return s

def make_bbox_from_poly(poly):
    x1, y1 = poly[0][0][0], poly[0][0][1]
    x2, y2 = poly[0][0][0], poly[0][0][1]
    for pt in poly[1:]:
        x1 = min(x1, pt[0][0])
        y1 = min(y1, pt[0][1])
        x2 = max(x2, pt[0][0])
        y2 = max(y2, pt[0][1])

    return x1, y1, (x2-x1), (y2-y1)


def is_in_block_ad(block_ad, poly):
    if str(type(poly[0])) == "<class 'numpy.ndarray'>":
        x1, y1 = poly[0][0][0], poly[0][0][1]
        x2, y2 = poly[0][0][0], poly[0][0][1]
        for pt in poly[1:]:
            x1 = min(x1, pt[0][0])
            y1 = min(y1, pt[0][1])
            x2 = max(x2, pt[0][0])
            y2 = max(y2, pt[0][1])
    else:  # when poly is bbox
        x1, y1, x2, y2 = int(poly[0]), int(poly[1]), int(poly[2]), int(poly[3])
    cx = (x1+x2)//2
    cy = (y1+y2)//2
    #print(block_ad)
    # block_ad [x1, y1, x2, y2, score]
    if block_ad[0] <= cx and cx <= block_ad[2] and block_ad[1] <= cy and cy <= block_ad[3]:
        #print(x1, y1, x2, y2,block_ad)
        return True
    else:
        return False


def set_elm_detail(elm, bbox):
    elm.set('X', str(int(bbox[0])))
    elm.set('Y', str(int(bbox[1])))
    elm.set('WIDTH', str(int(bbox[2]-bbox[0])))
    elm.set('HEIGHT', str(int(bbox[3]-bbox[1])))
    elm.set('CONF', f'{bbox[4]:0.3f}')
    return

# Remove overlappiong polygons
def refine_tb_polygons(polygons, margin: int = 50):
    import cv2
    from copy import deepcopy
    res_polygons = deepcopy(polygons)

    for i, child_poly in enumerate(res_polygons):
        if child_poly is None:
            continue
        for j, parent_poly in enumerate(res_polygons):
            if i==j: # The child and parent are the same polygon
                continue
            if parent_poly is None:
                continue
            all_points_is_in = True
            for p in child_poly:
                x = int(p[0][0])
                y = int(p[0][1])
                #if cv2.pointPolygonTest(parent_poly, (x, y), True) < -margin: # > 0 means in
                if point_in_polygon((int(x), int(y)),parent_poly, False)< -margin:
                    all_points_is_in = False

            if  all_points_is_in:
                res_polygons[i] = None
                break

    return res_polygons

def get_relationship(res_bbox, tb_polygons, classes, use_block_ad: bool = True, score_thr: float = 0.3):
    #import cv2
    tb_cls_id = classes.index('text_block')
    tb_info = [[] for i in range(len(tb_polygons))]
    independ_lines = []

    ad_info = None
    if use_block_ad:
        ba_cls_id = classes.index('block_ad')
        ad_info = [[] for i in range(len(res_bbox[ba_cls_id]))]

    if use_block_ad:
        for i, poly in enumerate(tb_polygons):
            if res_bbox[tb_cls_id][i][4] < score_thr or tb_polygons[i] is None:
                tb_info[i] = None
                continue
            for j, block_ad in enumerate(res_bbox[ba_cls_id]):
                if res_bbox[ba_cls_id][j][4] < score_thr:
                    ad_info[j] = None
                    continue
                if is_in_block_ad(block_ad, poly):
                    ad_info[j].append([tb_cls_id, i])
                    break

    for c in range(len(classes)):
        cls = classes[c]
        if not cls.startswith('line_'):
            continue
        for j, line in enumerate(res_bbox[c]):
            if float(line[4]) < score_thr:
                continue
            in_any_block = False
            # elems belonging to text_block
            for i, poly in enumerate(tb_polygons):
                if res_bbox[tb_cls_id][i][4] < score_thr or tb_polygons[i] is None:
                    tb_info[i] = None
                    continue
                cx, cy = (line[0]+line[2])//2, (line[1]+line[3])//2

                #if cv2.pointPolygonTest(poly, (cx, cy), False) > 0:
                if point_in_polygon( (int(cx), int(cy)),poly,False)>=0:
                    tb_info[i].append([c, j])
                    in_any_block = True
                    break
            # elems belonging to ad_block
            if not in_any_block:
                for i, block_ad in enumerate(res_bbox[ba_cls_id]):
                    if ad_info[i] is None:
                        continue
                    if is_in_block_ad(block_ad, line):
                        ad_info[i].append([c, j])
                        in_any_block = True
                        break
            # Line elements not belonging to any text_block or ad_block
            if not in_any_block:
                independ_lines.append([c, j])

    return tb_info, ad_info, independ_lines

def get_relationship_rect(res_bbox, tb_polygons, classes, use_block_ad: bool = True, score_thr: float = 0.1):
    #import cv2
    tb_cls_id = classes.index('text_block')
    ba_cls_id = classes.index('block_ad')
    table_cls_id = classes.index('block_table')
    tb_info = [[] for i in range(len(tb_polygons))]
    independ_lines = []
    table_info=[[] for i in range(len(res_bbox[table_cls_id]))]
    ad_info = [[] for i in range(len(res_bbox[ba_cls_id]))]
    for c in range(len(classes)):
        cls = classes[c]
        if not cls.startswith('line_'):
            continue
        for j, line in enumerate(res_bbox[c]):
            
            if float(line[4]) < score_thr:
                continue
            in_any_block = False
            # elems belonging to text_block
            for i, poly in enumerate(tb_polygons):
                if tb_polygons[i] is None:
                    tb_info[i] = None
                    continue
                cx, cy = (line[0]+line[2])//2, (line[1]+line[3])//2
                poly=np.array([p[0] for p in poly])
                #print(poly)
                #if cv2.pointPolygonTest(poly, (int(cx), int(cy)), False) >= 0:
                if point_in_polygon((int(cx), int(cy)),poly,False)>=0:
                    tb_info[i].append([c, j])
                    in_any_block = True
                    break
            # elems belonging to ad_block
            if not in_any_block:
                for i, block_ad in enumerate(res_bbox[ba_cls_id]):
                    if is_in_block_ad(block_ad, line):
                        ad_info[i].append([c, j])
                        in_any_block = True
                        break
            if not in_any_block:
                for i, block_table in enumerate(res_bbox[table_cls_id]):
                    if is_in_block_ad(block_table, line):
                        table_info[i].append([c, j])
                        in_any_block = True
                        break
            # Line elements not belonging to any text_block or ad_block
            if not in_any_block:
                independ_lines.append([c, j])
    return tb_info, ad_info,table_info, independ_lines

def refine_tb_relationship(tb_polygons, tb_info, classes, margin: int = 50):
    #import cv2
    tb_cls_id = classes.index('text_block')

    for c_index, child_poly in enumerate(tb_polygons):
        if child_poly is None or tb_info[c_index] is None:
            continue
        for p_index, parent_poly in enumerate(tb_polygons):
            if c_index == p_index:  # The child and parent are the same polygon
                continue
            if parent_poly is None or tb_info[p_index] is None:
                continue
            all_points_is_in = True
            for p in child_poly:
                x = int(p[0][0])
                y = int(p[0][1])
                #if cv2.pointPolygonTest(parent_poly, (x, y), True) < -margin:
                if point_in_polygon((int(x), int(y)),parent_poly, True)<-margin:
                    # cv2.pointPolygonTest () > 0 means (x,y) is in parent_poly
                    all_points_is_in = False

            if all_points_is_in:  # c is in p
                if len(tb_info[c_index]) == 0:
                    tb_info[p_index].append([tb_cls_id, c_index])
                    tb_info[c_index] = None
                else:  # tb[i] has childen
                    for child_elm in tb_info[c_index]:
                        tb_info[p_index].append(child_elm)
                    tb_info[c_index] = None
                break

    # merge text blocks
    for i in range(len(tb_info)):
        have_only_tb = True
        if tb_info[i] is None:
            continue
        for c_id, _ in tb_info[i]:
            if c_id != tb_cls_id:
                have_only_tb = False
                break
        if have_only_tb:
            tb_info[i] = []

    return tb_info


def convert_to_xml_string3(img_w, img_h, img_path, classes, result,
                           score_thr: float = 0.1,
                           min_bbox_size: int = 5,
                           use_block_ad: bool = True):
    #import cv2

    img_name = os.path.basename(img_path)
    s = f'<PAGE IMAGENAME = "{img_name}" WIDTH = "{img_w}" HEIGHT = "{img_h}">\n'

    res_textblockes = result[0]
    res_bbox = result[1]
    

    # convert text block masks to polygons
    tb_polygons = textblock_to_rect(classes, res_textblockes, min_bbox_size)

    tb_info, ad_info, table_info, independ_lines = get_relationship_rect(res_bbox, tb_polygons, classes,score_thr=score_thr)
    
    # refine text blocks : remove overlapping text blocks
    tb_info = refine_tb_relationship(tb_polygons, tb_info, classes, margin=50)
    #print(tb_info)
    tb_cls_id = classes.index('text_block')
    table_cls_id = classes.index('block_table')
    ##Tableの中身
    for i_ba, block_table in enumerate(res_bbox[table_cls_id]):
        #print(ad_info[i_ba], block_ad)
        if table_info[i_ba] is None:
            continue
        #s += '  '
        s = add_block_table_head(s, block_table, block_table[4])
        for c, j in table_info[i_ba]:
            if c == table_cls_id:
                if table_cls_id[j] is None:
                    continue
                # add lines in textblock in block_ad
                if len(tb_info[j]) == 0:
                    # create and add a line_main elem at least one
                    x, y, w, h = make_bbox_from_poly(tb_polygons[j])
                    if w >= min_bbox_size and h >= min_bbox_size:
                        s += f'      <LINE TYPE = "{name_to_org_name(classes[0])}" X = "{x}" Y = "{y}" WIDTH = "{w}" HEIGHT = "{h}" ></LINE>\n'
                else:
                    for c_id, i in tb_info[j]:
                        line = res_bbox[c_id][i]
                        conf = float(line[4])

                        if conf < score_thr:
                            continue
                        pred_char_cnt=0
                        
                        if len(line)==6:
                            pred_char_cnt=float(line[5])
                        if c_id == tb_cls_id:  # write as Line_main
                            x, y, w, h = make_bbox_from_poly(tb_polygons[i])
                            if w >= min_bbox_size and h >= min_bbox_size:
                                s += f'      <LINE TYPE = "{name_to_org_name(classes[0])}" X = "{x}" Y = "{y}" WIDTH = "{w}" HEIGHT = "{h}" CONF = "{conf:0.3f}"PRED_CHAR_CNT="{pred_char_cnt:0.3f}"></LINE>\n'
                        else:
                            x, y = int(line[0]), int(line[1])
                            w, h = int(line[2] - line[0]), int(line[3] - line[1])
                            s += f'      <LINE TYPE = "{name_to_org_name(classes[c_id])}" X = "{x}" Y = "{y}" WIDTH = "{w}" HEIGHT = "{h}" CONF = "{conf:0.3f}" PRED_CHAR_CNT="{pred_char_cnt:0.3f}"></LINE>\n'
                tb_info[j] = None
            else:
                line = res_bbox[c][j]
                conf = float(line[4])
                if conf < score_thr:
                    continue
                pred_char_cnt=0
                if len(line)==6:
                    pred_char_cnt=float(line[5])
                x, y = int(line[0]), int(line[1])
                w, h = int(line[2] - line[0]), int(line[3] - line[1])
                s += f'      <LINE TYPE = "{name_to_org_name(classes[c])}" X = "{x}" Y = "{y}" WIDTH = "{w}" HEIGHT = "{h}" CONF = "{conf:0.3f}" PRED_CHAR_CNT="{pred_char_cnt:0.3f}"></LINE>\n'
        s += '    </BLOCK>\n'
    ##Tableの中身
    if use_block_ad:
        ba_cls_id = classes.index('block_ad')
        for i_ba, block_ad in enumerate(res_bbox[ba_cls_id]):
            #print(ad_info[i_ba], block_ad)
            if ad_info[i_ba] is None:
                continue
            s += '  '
            s = add_block_ad_head(s, block_ad, block_ad[4])
            for c, j in ad_info[i_ba]:
                if c == tb_cls_id:
                    if tb_info[j] is None:
                        continue
                    s = add_text_block_head(s, tb_polygons[j], res_bbox[tb_cls_id][j][4], '    ')
                    # add lines in textblock in block_ad
                    if len(tb_info[j]) == 0:
                        # create and add a line_main elem at least one
                        x, y, w, h = make_bbox_from_poly(tb_polygons[j])
                        if w >= min_bbox_size and h >= min_bbox_size:
                            s += f'      <LINE TYPE = "{name_to_org_name(classes[0])}" X = "{x}" Y = "{y}" WIDTH = "{w}" HEIGHT = "{h}"></LINE>\n'
                    else:
                        for c_id, i in tb_info[j]:
                            line = res_bbox[c_id][i]
                            conf = float(line[4])
                            if conf < score_thr:
                                continue
                            pred_char_cnt=0
                            if len(line)==6:
                                pred_char_cnt=float(line[5])
                            if c_id == tb_cls_id:  # write as Line_main
                                x, y, w, h = make_bbox_from_poly(tb_polygons[i])
                                if w >= min_bbox_size and h >= min_bbox_size:
                                    s += f'      <LINE TYPE = "{name_to_org_name(classes[0])}" X = "{x}" Y = "{y}" WIDTH = "{w}" HEIGHT = "{h}" CONF = "{conf:0.3f}" PRED_CHAR_CNT="{pred_char_cnt:0.3f}"></LINE>\n'
                            else:
                                x, y = int(line[0]), int(line[1])
                                w, h = int(line[2] - line[0]), int(line[3] - line[1])
                                s += f'      <LINE TYPE = "{name_to_org_name(classes[c_id])}" X = "{x}" Y = "{y}" WIDTH = "{w}" HEIGHT = "{h}" CONF = "{conf:0.3f}" PRED_CHAR_CNT="{pred_char_cnt:0.3f}"></LINE>\n'
                    s += '    </TEXTBLOCK>\n'
                    tb_info[j] = None
                else:
                    line = res_bbox[c][j]
                    conf = float(line[4])
                    if conf < score_thr:
                        continue
                    pred_char_cnt=0
                    if len(line)==6:
                        pred_char_cnt=float(line[5])
                    x, y = int(line[0]), int(line[1])
                    w, h = int(line[2] - line[0]), int(line[3] - line[1])
                    s += f'      <LINE TYPE = "{name_to_org_name(classes[c])}" X = "{x}" Y = "{y}" WIDTH = "{w}" HEIGHT = "{h}" CONF = "{conf:0.3f}" PRED_CHAR_CNT="{pred_char_cnt:0.3f}"></LINE>\n'
            s += '    </BLOCK>\n'
    # Text block and line elems inside the text block
    for j in range(len(tb_info)):
        
        #if tb_info[j] is None or res_bbox[tb_cls_id][j][4] < score_thr or tb_polygons[j] is None:  # text block already converted
        if tb_info[j] is None or tb_polygons[j] is None:  # text block already converted
            continue
        
        if len(tb_info[j]) == 0:  # text block without line elms
            # create and add a line_main elem at least one
            x, y, w, h = make_bbox_from_poly(tb_polygons[j])
            s = add_text_block_head(s, tb_polygons[j], res_bbox[tb_cls_id][j][4], '  ')
            if w >= min_bbox_size and h >= min_bbox_size:
                s += f'    <LINE TYPE = "{name_to_org_name(classes[1])}" X = "{x}" Y = "{y}" WIDTH = "{w}" HEIGHT = "{h}"></LINE>\n'
                #continue
            #pass
        else:
            s = add_text_block_head(s, tb_polygons[j], res_bbox[tb_cls_id][j][4], '  ')
            for c_id, i in tb_info[j]:
                line = res_bbox[c_id][i]
                conf = float(line[4])
                if conf < score_thr:
                    continue
                pred_char_cnt=0
                if len(line)==6:
                    pred_char_cnt=float(line[5])
                if c_id == tb_cls_id:  # write as line_main
                    x, y, w, h = make_bbox_from_poly(tb_polygons[i])
                    if w >= min_bbox_size and h >= min_bbox_size:
                        s += f'    <LINE TYPE = "{name_to_org_name(classes[1])}" X = "{x}" Y = "{y}" WIDTH = "{w}" HEIGHT = "{h}" CONF = "{conf:0.3f}" PRED_CHAR_CNT="{pred_char_cnt:0.3f}"></LINE>\n'
                else:
                    x, y = int(line[0]), int(line[1])
                    w, h = int(line[2] - line[0]), int(line[3] - line[1])
                    s += f'    <LINE TYPE = "{name_to_org_name(classes[c_id])}" X = "{x}" Y = "{y}" WIDTH = "{w}" HEIGHT = "{h}" CONF = "{conf:0.3f}" PRED_CHAR_CNT="{pred_char_cnt:0.3f}"></LINE>\n'
        s += '  </TEXTBLOCK>\n'
    

    # Line elems outside text_block and block_ad
    for c, j in independ_lines:
        line = res_bbox[c][j]
        conf = float(line[4])
        if conf < score_thr:
            continue
        pred_char_cnt=0
        if len(line)==6:
            pred_char_cnt=float(line[5])
        x, y = int(line[0]), int(line[1])
        w, h = int(line[2] - line[0]), int(line[3] - line[1])
        s += f'    <LINE TYPE = "{name_to_org_name(classes[c])}" X = "{x}" Y = "{y}" WIDTH = "{w}" HEIGHT = "{h}" CONF = "{conf:0.3f}" PRED_CHAR_CNT="{pred_char_cnt:0.3f}"></LINE>\n'

    # Block elms other than block_ad
    for c in range(len(classes)):
        cls = classes[c]
        if cls.startswith('block_') and cls!='block_table':
            for block in res_bbox[c]:
                conf = float(block[4])
                if conf < score_thr:
                    continue
                x, y = int(block[0]), int(block[1])
                w, h = int(block[2] - block[0]), int(block[3] - block[1])
                s += f'  <BLOCK TYPE = "{name_to_org_name(cls)}" X = "{x}" Y = "{y}" WIDTH = "{w}" HEIGHT = "{h}" CONF = "{conf:0.3f}"></BLOCK>\n'

    s += '</PAGE>\n'

    return s


def run_layout_detection(img_paths: List[str] = None, list_path: str = None, output_path: str = "layout_prediction.xml",
                         config: str = './models/config_file.py',
                         checkpoint: str = 'models/weight_file.pth',
                         device: str = 'cuda:0', score_thr: float = 0.3,
                         use_show: bool = False, dump_dir: str = None,
                         use_time: bool = False):
    if use_time:
        import time
        st_time = time.time()

    if list_path is not None:
        img_paths = list([s.strip() for s in open(list_path).readlines()])
    if img_paths is None:
        print('Please specify --img_paths or --list_path')
        return -1

    detector = LayoutDetector(config, checkpoint, device)

    if dump_dir is not None:
        Path(dump_dir).mkdir(exist_ok=True)

    with open(output_path, 'w') as f:
        def tee(s):
            print(s, file=f, end="")
            print(s, file=sys.stdout, end="")

        tee('<?xml version="1.0" encoding="utf-8" standalone="yes"?><OCRDATASET xmlns="">\n')
        if use_time:
            head_time = time.time()

        for img_path in tqdm(img_paths):
            import cv2
            img = cv2.imread(img_path)

            result = detector.predict(img)

            if use_show:
                dump_img = detector.show(img, img_path, result, score_thr=score_thr)
                cv2.namedWindow('show')
                cv2.imshow('show', dump_img)
                if 27 == cv2.waitKey(0):
                    break

            if dump_dir is not None:
                import cv2
                dump_img = detector.show(img, img_path, result, score_thr=score_thr)
                cv2.imwrite(str(Path(dump_dir) / Path(img_path).name), dump_img)
            img_h, img_w = img.shape[0:2]
            xml_str = convert_to_xml_string2(
                img_w, img_h, img_path, detector.classes, result, score_thr=score_thr)
            tee(xml_str)

        tee('</OCRDATASET>\n')
    # end with
    if use_time:
        end_time = time.time()
        print("+---------------------------------------+")
        print(f"all elapsed time        : {end_time-st_time:0.6f} [sec]")
        print(f"head time               : {head_time-st_time:0.6f} [sec]")
        infer_time = end_time-head_time
        infer_per_img = infer_time / len(img_paths)
        print(f"inference & xmlize time : {infer_time:0.6f} [sec]")
        print(f"                per img : {infer_per_img:0.6f} [sec]")
        print("+---------------------------------------+")

"""
class InferencerWithCLI:
    def __init__(self):
        pass

    def inference_with_cli(self, img, img_path,
                           score_thr: float = 0.3, dump: bool = False):

        node = ET.fromstring(
            '<?xml version="1.0" standalone="yes"?><OCRDATASET xmlns="">\n</OCRDATASET>\n')

        # prediction
        if self.detector is None:
            print('ERROR: Layout detector is not created.')
            return None
        result = self.detector.predict(img)

        # xml creation
        xml_str = convert_to_xml_string_with_data(
            img.shape[1], img.shape[0], img_path, self.detector.classes, result, score_thr=score_thr)
"""
