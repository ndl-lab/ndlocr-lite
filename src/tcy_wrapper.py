"""Tate-Chuu-Yoko (縦中横) wrapper for PARSEQ recognizer.

Wraps a PARSEQ recognizer to detect and correctly OCR tate-chuu-yoko
(horizontal text embedded in vertical lines), commonly found in newspaper text.

Usage:
    recognizer = PARSEQ(...)
    recognizer = TateChuYokoWrapper(recognizer)
    text = recognizer.read(img)  # same interface as PARSEQ
"""

import cv2
import numpy as np
from typing import Tuple, List


def _softmax(x, axis=-1):
    e_x = np.exp(x - np.max(x, axis=axis, keepdims=True))
    return e_x / np.sum(e_x, axis=axis, keepdims=True)


def add_tcy_arguments(parser):
    tcy_parser = parser.__class__(add_help=False)
    tcy_parser.add_argument("--tcy-min-line-width", type=int, dest="tcy_min_line_width")
    tcy_parser.add_argument("--tcy-max-line-width", type=int, dest="tcy_max_line_width")
    tcy_parser.add_argument("--tcy-det-margin-ratio", type=float, dest="tcy_det_margin_ratio")
    tcy_parser.add_argument("--tcy-ocr-margin-ratio", type=float, dest="tcy_ocr_margin_ratio")
    tcy_parser.add_argument("--tcy-min-components", type=int, dest="tcy_min_components")
    tcy_parser.add_argument("--tcy-max-aspect-ratio", type=float, dest="tcy_max_aspect_ratio")
    tcy_parser.add_argument("--tcy-seg-min-gap", type=int, dest="tcy_seg_min_gap")
    tcy_parser.add_argument("--tcy-ink-threshold-ratio", type=float, dest="tcy_ink_threshold_ratio")
    return tcy_parser


class TateChuYokoWrapper:
    def __init__(self, recognizer,
                 tcy_min_line_width: int = 30,
                 tcy_max_line_width: int = 80,
                 tcy_det_margin_ratio: float = 0.1,
                 tcy_ocr_margin_ratio: float = 0.5,
                 tcy_min_components: int = 2,
                 tcy_max_aspect_ratio: float = 0.75,
                 tcy_seg_min_gap: int = 5,
                 tcy_ink_threshold_ratio: float = 0.10):
        self._rec = recognizer
        self.min_line_width = tcy_min_line_width
        self.max_line_width = tcy_max_line_width
        self.det_margin_ratio = tcy_det_margin_ratio
        self.ocr_margin_ratio = tcy_ocr_margin_ratio
        self.min_components = tcy_min_components
        self.max_aspect_ratio = tcy_max_aspect_ratio
        self.seg_min_gap = tcy_seg_min_gap
        self.ink_threshold_ratio = tcy_ink_threshold_ratio

    def read(self, img: np.ndarray) -> str:
        if img is None or img.size == 0:
            return ""
        h, w = img.shape[:2]
        if h > w:
            return self._detect_and_fix_tatechuyoko(img)
        return self._rec.read(img)

    def _read_with_confidence(self, img: np.ndarray, rotate: bool = True) -> Tuple[str, List[float]]:
        if img is None or img.size == 0:
            return "", []
        rec = self._rec
        if rotate:
            input_tensor = rec.preprocess(img)
        else:
            input_tensor = self._preprocess_no_rotation(img)
        outputs = rec.session.run(rec.output_names, {rec.input_names[0]: input_tensor})[0]
        probs = _softmax(outputs, axis=2)
        indices = np.argmax(probs, axis=2)[0]
        max_probs = np.max(probs, axis=2)[0]
        stop_idx = np.where(indices == 0)[0]
        end_pos = stop_idx[0] if stop_idx.size > 0 else len(indices)
        char_indices = indices[:end_pos].tolist()
        confidences = max_probs[:end_pos].tolist()
        text = "".join([rec.charlist[i - 1] for i in char_indices])
        return text, confidences

    def _preprocess_no_rotation(self, img: np.ndarray) -> np.ndarray:
        rec = self._rec
        resized = cv2.resize(img, (rec.input_width, rec.input_height), interpolation=cv2.INTER_LINEAR)
        input_image = np.ascontiguousarray(resized[:, :, ::-1]).astype(np.float32)
        input_image /= 127.5
        input_image -= 1.0
        input_image = input_image.transpose(2, 0, 1)
        return input_image[np.newaxis, :, :, :]

    def _segment_blocks(self, img: np.ndarray) -> List[Tuple[int, int]]:
        if img.ndim == 3:
            gray = np.mean(img, axis=2).astype(np.uint8)
        else:
            gray = img
        threshold = int(np.mean(gray))
        binary = (gray < threshold).astype(np.int32)
        proj = np.sum(binary, axis=1)
        is_ink = proj > 0
        blocks: List[Tuple[int, int]] = []
        in_block = False
        start = 0
        for y in range(len(is_ink)):
            if is_ink[y] and not in_block:
                start = y
                in_block = True
            elif not is_ink[y] and in_block:
                blocks.append((start, y))
                in_block = False
        if in_block:
            blocks.append((start, len(is_ink)))
        merged: List[Tuple[int, int]] = []
        for b in blocks:
            if merged and b[0] - merged[-1][1] < self.seg_min_gap:
                merged[-1] = (merged[-1][0], b[1])
            else:
                merged.append(b)
        return merged

    def _count_horizontal_components(self, segment: np.ndarray) -> int:
        if segment.ndim == 3:
            gray = np.mean(segment, axis=2).astype(np.uint8)
        else:
            gray = segment
        threshold = int(np.mean(gray))
        binary = (gray < threshold).astype(np.int32)
        col_sum = np.sum(binary, axis=0)
        if col_sum.max() == 0:
            return 0
        ink_threshold = col_sum.max() * self.ink_threshold_ratio
        is_ink = col_sum > ink_threshold
        components = 0
        in_component = False
        for v in is_ink:
            if v and not in_component:
                components += 1
                in_component = True
            elif not v:
                in_component = False
        return components

    def _detect_and_fix_tatechuyoko(self, img: np.ndarray) -> str:
        h, w = img.shape[:2]
        full_text, full_conf = self._read_with_confidence(img, rotate=True)
        if not full_text:
            return full_text
        blocks = self._segment_blocks(img)
        if not blocks or w < self.min_line_width:
            return full_text

        tcy_flags: List[bool] = []
        for y_start, y_end in blocks:
            block_height = y_end - y_start
            det_margin = max(2, int(block_height * self.det_margin_ratio))
            y0 = max(0, y_start - det_margin)
            y1 = min(h, y_end + det_margin)
            block_img = img[y0:y1, :, :] if img.ndim == 3 else img[y0:y1, :]
            is_tcy = (block_height >= self.seg_min_gap
                      and self._count_horizontal_components(block_img) >= self.min_components
                      and block_height <= w * self.max_aspect_ratio)
            tcy_flags.append(is_tcy)

        if not any(tcy_flags):
            return full_text

        block_parts: List[str] = []
        i = 0
        n = len(blocks)
        while i < n:
            if tcy_flags[i]:
                y_start, y_end = blocks[i]
                block_height = y_end - y_start
                ocr_margin = max(5, int(block_height * self.ocr_margin_ratio))
                y0 = max(0, y_start - ocr_margin)
                y1 = min(h, y_end + ocr_margin)
                block_img = img[y0:y1, :, :] if img.ndim == 3 else img[y0:y1, :]
                if block_img.ndim == 2:
                    block_img = np.stack([block_img] * 3, axis=-1)
                seg_text, _ = self._read_with_confidence(block_img, rotate=False)
                block_parts.append(seg_text)
                i += 1
            else:
                group_start = i
                while i < n and not tcy_flags[i]:
                    i += 1
                if group_start > 0 and tcy_flags[group_start - 1]:
                    crop_y0 = blocks[group_start - 1][1]
                else:
                    crop_y0 = blocks[group_start][0]
                if i < n and tcy_flags[i]:
                    crop_y1 = blocks[i][0]
                else:
                    crop_y1 = blocks[i - 1][1]
                group_img = img[crop_y0:crop_y1, :, :] if img.ndim == 3 else img[crop_y0:crop_y1, :]
                if group_img.shape[0] > 0 and group_img.shape[1] > 0:
                    group_text, _ = self._read_with_confidence(group_img, rotate=True)
                    block_parts.append(group_text)

        block_text = "".join(block_parts)
        if len(block_text) > len(full_text):
            return block_text
        return full_text
