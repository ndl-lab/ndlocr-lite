#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone TableCenterNet ONNX inference.

Supports two decoders without importing the original TableCenterNet repository:

  --decoder simple    : equivalent to the official --not_relocate path.
  --decoder official  : default TableCenterNet decoder with corner relocation.
  --decoder both      : run both and save both results.

The preprocessing and postprocessing follow the official TableCenterNet
predictor: OpenCV BGR input, affine square resize, the official mean/std, and
inverse affine mapping from the output feature map to the original image.

Required packages:
    numpy, opencv-python(-headless), onnxruntime

NOTE:
    The supplied tablecenternet_s_512.onnx contains the standard ONNX
    DeformConv operator. ONNX Runtime 1.23.2 cannot execute that model. Use an
    ORT build with DeformConv support (1.25+), or re-export the model using a
    GridSample-based DCN decomposition for ORT 1.23.2.
"""

from __future__ import annotations

import html
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import cv2
import numpy as np
import onnxruntime as ort


MEAN = np.array([0.40789654, 0.44719302, 0.47026115], dtype=np.float32).reshape(1, 1, 3)
STD = np.array([0.28863828, 0.27408164, 0.27809835], dtype=np.float32).reshape(1, 1, 3)

# Official predictor defaults.
DEFAULT_CENTER_K = 3000
DEFAULT_CORNER_K = 5000
DEFAULT_CENTER_THRESH = 0.2
DEFAULT_CORNER_THRESH = 0.3
DEFAULT_IOU_THRESH = 0.5
CELL_MIN_OPTIMIZE_COUNT = 2
CELL_DECAY = 0.4

REQUIRED_OUTPUTS = ("hm", "reg", "ct2cn", "cn2ct", "lc", "sp")


@dataclass
class Meta:
    center: np.ndarray
    scale: float
    shift: np.ndarray
    input_width: int
    input_height: int
    output_width: int
    output_height: int


@dataclass
class DecodedBatch:
    cells: np.ndarray          # [N, 4, 2] feature-map coordinates
    scores: np.ndarray         # [N]
    logic: np.ndarray          # [N, 4], 1-based [start_col,end_col,start_row,end_row]
    corner_counts: Optional[np.ndarray] = None  # [N, 2] for official decoder


# ---------------------------------------------------------------------------
# Affine preprocessing/postprocessing
# ---------------------------------------------------------------------------

def _get_dir(src_point: Sequence[float], rot_rad: float) -> np.ndarray:
    sn, cs = math.sin(rot_rad), math.cos(rot_rad)
    return np.array(
        [src_point[0] * cs - src_point[1] * sn,
         src_point[0] * sn + src_point[1] * cs],
        dtype=np.float32,
    )


def _get_3rd_point(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    direct = a - b
    return b + np.array([-direct[1], direct[0]], dtype=np.float32)


def get_affine_transform(
    center: np.ndarray,
    scale: float | np.ndarray,
    output_size: Sequence[int],
    *,
    rot: float = 0.0,
    inv: bool = False,
) -> np.ndarray:
    if not isinstance(scale, np.ndarray):
        scale = np.array([scale, scale], dtype=np.float32)
    else:
        scale = scale.astype(np.float32)

    src_w = float(scale[0])
    dst_w, dst_h = float(output_size[0]), float(output_size[1])
    rot_rad = np.pi * rot / 180.0

    src_dir = _get_dir([0.0, -0.5 * src_w], rot_rad)
    dst_dir = np.array([0.0, -0.5 * dst_w], dtype=np.float32)

    src = np.zeros((3, 2), dtype=np.float32)
    dst = np.zeros((3, 2), dtype=np.float32)
    src[0] = center
    src[1] = center + src_dir
    src[2] = _get_3rd_point(src[0], src[1])

    dst[0] = [0.5 * dst_w, 0.5 * dst_h]
    dst[1] = dst[0] + dst_dir
    dst[2] = _get_3rd_point(dst[0], dst[1])

    if inv:
        return cv2.getAffineTransform(dst, src)
    return cv2.getAffineTransform(src, dst)


def transform_points(points: np.ndarray, transform: np.ndarray) -> np.ndarray:
    points = np.asarray(points, dtype=np.float32)
    flat = points.reshape(-1, 2)
    ones = np.ones((flat.shape[0], 1), dtype=np.float32)
    out = np.concatenate([flat, ones], axis=1) @ transform.T
    return out.reshape(points.shape)


def pad_image(image: np.ndarray, padding: int) -> np.ndarray:
    if padding <= 0:
        return image
    return cv2.copyMakeBorder(
        image,
        padding,
        padding,
        padding,
        padding,
        cv2.BORDER_CONSTANT,
        value=(0, 0, 0),
    )


def preprocess(image: np.ndarray, input_h: int, input_w: int, padding: int = 0) -> Tuple[np.ndarray, Meta]:
    original = image
    image = pad_image(image, padding)
    shift = np.array([padding, padding], dtype=np.float32) if padding > 0 else np.zeros(2, dtype=np.float32)

    h, w = image.shape[:2]
    center = np.array([w / 2.0, h / 2.0], dtype=np.float32)
    scale = float(max(h, w))

    trans = get_affine_transform(center, scale, [input_w, input_h])
    warped = cv2.warpAffine(image, trans, (input_w, input_h), flags=cv2.INTER_LINEAR)

    # Keep BGR order: this matches the official OpenCV predictor.
    x = warped.astype(np.float32) / 255.0
    x = (x - MEAN) / STD
    x = x.transpose(2, 0, 1)[None].astype(np.float32)

    # The output resolution is inferred later from hm; initialize with /4 as a fallback.
    meta = Meta(
        center=center,
        scale=scale,
        shift=shift,
        input_width=input_w,
        input_height=input_h,
        output_width=input_w // 4,
        output_height=input_h // 4,
    )
    return x, meta


# ---------------------------------------------------------------------------
# CenterNet-style decode primitives
# ---------------------------------------------------------------------------

def sigmoid(x: np.ndarray) -> np.ndarray:
    # Numerical clipping does not affect normal model outputs.
    x = np.clip(x, -50.0, 50.0)
    return 1.0 / (1.0 + np.exp(-x))


def nms_heatmap(heat: np.ndarray) -> np.ndarray:
    """3x3 local-maximum suppression for a single 2-D heatmap."""
    kernel = np.ones((3, 3), dtype=np.uint8)
    hmax = cv2.dilate(heat, kernel, borderType=cv2.BORDER_CONSTANT, borderValue=0)
    return heat * (hmax == heat)


def topk_2d(heat: np.ndarray, k: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return scores, flattened indexes, ys, xs sorted descending."""
    flat = heat.reshape(-1)
    k = min(int(k), flat.size)
    if k <= 0:
        e_f = np.empty((0,), dtype=np.float32)
        e_i = np.empty((0,), dtype=np.int64)
        return e_f, e_i, e_i, e_i

    if k == flat.size:
        inds = np.argsort(flat)[::-1]
    else:
        inds = np.argpartition(flat, -k)[-k:]
        inds = inds[np.argsort(flat[inds])[::-1]]

    h, w = heat.shape
    ys = inds // w
    xs = inds % w
    return flat[inds].astype(np.float32), inds.astype(np.int64), ys.astype(np.int64), xs.astype(np.int64)


def gather_map(feat: np.ndarray, ys: np.ndarray, xs: np.ndarray) -> np.ndarray:
    """Gather [1,C,H,W] at N points -> [N,C]."""
    return feat[0, :, ys, xs].copy()


def polygons_decode(
    heatmap_1ch: np.ndarray,
    vec: np.ndarray,
    reg: np.ndarray,
    k: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Independent NumPy implementation of TableCenterNet polygons_decode."""
    heat = nms_heatmap(heatmap_1ch[0, 0])
    scores, flat_inds, ys, xs = topk_2d(heat, k)

    point_offset = gather_map(reg, ys, xs)
    vectors = gather_map(vec, ys, xs)

    px = xs.astype(np.float32) + point_offset[:, 0]
    py = ys.astype(np.float32) + point_offset[:, 1]

    polygons = np.empty((scores.shape[0], 4, 2), dtype=np.float32)
    for j in range(4):
        polygons[:, j, 0] = px - vectors[:, 2 * j]
        polygons[:, j, 1] = py - vectors[:, 2 * j + 1]

    return scores, flat_inds, px, py, polygons


def round_at_least_one(value: float) -> int:
    # np.rint uses ties-to-even, matching torch.round for the values relevant here.
    return max(1, int(np.rint(float(value))))


def get_logic_coords(lc_logic: np.ndarray, cell_span: np.ndarray) -> np.ndarray:
    """TableCenterNet mtable logical-coordinate reconstruction (1-based)."""
    col_span = round_at_least_one(cell_span[0])
    row_span = round_at_least_one(cell_span[1])

    cols = [round_at_least_one(v[0]) for v in lc_logic]
    rows = [round_at_least_one(v[1]) for v in lc_logic]

    # Corner order used by the network: TL, TR, BR, BL.
    if cols[0] == cols[3]:
        start_col = cols[0]
        end_col = start_col + col_span - 1
    elif cols[1] == cols[2]:
        end_col = max(col_span + 1, cols[1]) - 1
        start_col = end_col + 1 - col_span
    elif abs(float(lc_logic[0, 0] - lc_logic[3, 0])) <= abs(float(lc_logic[1, 0] - lc_logic[2, 0])):
        start_col = round_at_least_one((float(lc_logic[0, 0]) + float(lc_logic[3, 0])) / 2.0)
        end_col = start_col + col_span - 1
    else:
        end_col = max(
            col_span + 1,
            int(np.rint((float(lc_logic[1, 0]) + float(lc_logic[2, 0])) / 2.0)),
        ) - 1
        start_col = end_col + 1 - col_span

    if rows[0] == rows[1]:
        start_row = rows[0]
        end_row = start_row + row_span - 1
    elif rows[2] == rows[3]:
        end_row = max(row_span + 1, rows[2]) - 1
        start_row = end_row + 1 - row_span
    elif abs(float(lc_logic[0, 1] - lc_logic[1, 1])) <= abs(float(lc_logic[2, 1] - lc_logic[3, 1])):
        start_row = round_at_least_one((float(lc_logic[0, 1]) + float(lc_logic[1, 1])) / 2.0)
        end_row = start_row + row_span - 1
    else:
        end_row = max(
            row_span + 1,
            int(np.rint((float(lc_logic[2, 1]) + float(lc_logic[3, 1])) / 2.0)),
        ) - 1
        start_row = end_row + 1 - row_span

    return np.array([start_col, end_col, start_row, end_row], dtype=np.int32)


def sample_lc_at_polygon(lc: np.ndarray, polygon: np.ndarray) -> np.ndarray:
    """Sample the 2-channel logical coordinate map at four polygon corners."""
    _, _, h, w = lc.shape
    out = np.empty((4, 2), dtype=np.float32)
    for j, (x, y) in enumerate(polygon):
        xi = int(np.clip(int(float(x)), 0, w - 1))
        yi = int(np.clip(int(float(y)), 0, h - 1))
        out[j] = lc[0, :, yi, xi]
    return out


# ---------------------------------------------------------------------------
# Simple decoder (--not_relocate equivalent)
# ---------------------------------------------------------------------------

def decode_simple(
    hm_logits: np.ndarray,
    reg: np.ndarray,
    ct2cn: np.ndarray,
    lc: np.ndarray,
    span: np.ndarray,
    *,
    center_k: int,
    center_thresh: float,
) -> DecodedBatch:
    heat = sigmoid(hm_logits)
    scores, center_inds, _, _, cells = polygons_decode(heat[:, 0:1], ct2cn, reg, center_k)

    h, w = heat.shape[2:]
    ys = center_inds // w
    xs = center_inds % w
    cell_spans = gather_map(span, ys, xs)

    logic = np.zeros((len(scores), 4), dtype=np.int32)
    for i in range(len(scores)):
        if scores[i] < center_thresh:
            break
        lc_logic = sample_lc_at_polygon(lc, cells[i])
        logic[i] = get_logic_coords(lc_logic, cell_spans[i])

    return DecodedBatch(cells=cells, scores=scores, logic=logic)


# ---------------------------------------------------------------------------
# Official default decoder (corner relocation)
# ---------------------------------------------------------------------------

def aabb_intersects(a: np.ndarray, b: np.ndarray) -> bool:
    amin = a.min(axis=0)
    amax = a.max(axis=0)
    bmin = b.min(axis=0)
    bmax = b.max(axis=0)
    return not (amin[0] > bmax[0] or bmin[0] > amax[0] or amin[1] > bmax[1] or bmin[1] > amax[1])


def any_corner_strictly_inside(center_polygon: np.ndarray, corner_polygon: np.ndarray) -> bool:
    # cv2.pointPolygonTest(..., False) returns +1 strictly inside, 0 on the
    # boundary, and -1 outside. This matches the Shapely Point.within
    # semantics used by the official decoder without adding a dependency.
    contour = np.asarray(center_polygon, dtype=np.float32).reshape(-1, 1, 2)
    for xy in corner_polygon:
        if cv2.pointPolygonTest(contour, (float(xy[0]), float(xy[1])), False) > 0:
            return True
    return False


def nearest_corner_index(polygon: np.ndarray, x: float, y: float) -> int:
    d2 = (polygon[:, 0] - x) ** 2 + (polygon[:, 1] - y) ** 2
    return int(np.argmin(d2))


def decode_official(
    hm_logits: np.ndarray,
    reg: np.ndarray,
    ct2cn: np.ndarray,
    cn2ct: np.ndarray,
    lc: np.ndarray,
    span: np.ndarray,
    *,
    center_k: int,
    corner_k: int,
    center_thresh: float,
    corner_thresh: float,
) -> DecodedBatch:
    """Standalone semantic port of the official default mtable decoder."""
    heat = sigmoid(hm_logits)

    center_scores, center_inds, _, _, center_polygons = polygons_decode(
        heat[:, 0:1], ct2cn, reg, center_k
    )
    corner_scores, corner_inds, corner_xs, corner_ys, corner_polygons = polygons_decode(
        heat[:, 1:2], cn2ct, reg, corner_k
    )

    h, w = heat.shape[2:]
    center_ys = center_inds // w
    center_xs = center_inds % w
    cell_spans = gather_map(span, center_ys, center_xs)

    corner_ys_idx = corner_inds // w
    corner_xs_idx = corner_inds % w
    corner_logics = gather_map(lc, corner_ys_idx, corner_xs_idx)

    corrected = center_polygons.copy()
    logic = np.zeros((len(center_scores), 4), dtype=np.int32)
    corner_counts = np.zeros((len(center_scores), 2), dtype=np.int32)

    valid_center_indices = np.flatnonzero(center_scores >= center_thresh)
    valid_corner_indices = np.flatnonzero(corner_scores >= corner_thresh)

    # The original implementation uses BoxesFinder to accelerate this AABB
    # candidate query. Direct AABB filtering is semantically equivalent and
    # keeps this standalone implementation much smaller.
    valid_corner_polys = corner_polygons[valid_corner_indices]

    for i in valid_center_indices:
        origin = center_polygons[i]
        fixed = corrected[i]
        lc_logic: List[Optional[np.ndarray]] = [None, None, None, None]
        first_fixes = 0
        replacement_fixes = 0

        for local_j, j in enumerate(valid_corner_indices):
            candidate_poly = valid_corner_polys[local_j]
            if not aabb_intersects(origin, candidate_poly):
                continue
            if not any_corner_strictly_inside(origin, candidate_poly):
                continue

            cx = float(corner_xs[j])
            cy = float(corner_ys[j])
            ci = nearest_corner_index(origin, cx, cy)

            origin_xy = origin[ci]
            current_xy = fixed[ci]
            candidate_xy = np.array([cx, cy], dtype=np.float32)

            # Since fixed starts as an exact copy, exact equality reproduces the
            # first-update branch of the official implementation.
            if np.array_equal(current_xy, origin_xy):
                first_fixes += 1
                fixed[ci] = candidate_xy
                lc_logic[ci] = corner_logics[j].copy()
            else:
                old_d2 = float(np.sum((origin_xy - current_xy) ** 2))
                new_d2 = float(np.sum((origin_xy - candidate_xy) ** 2))
                if old_d2 >= new_d2:
                    replacement_fixes += 1
                    fixed[ci] = candidate_xy
                    lc_logic[ci] = corner_logics[j].copy()

        corner_counts[i] = [first_fixes, replacement_fixes]

        # Missing corner logical coordinates are sampled from the lc map at the
        # final corrected physical corner, as in the official decoder.
        fallback_logic = sample_lc_at_polygon(lc, fixed)
        for j in range(4):
            if lc_logic[j] is None:
                lc_logic[j] = fallback_logic[j]

        logic[i] = get_logic_coords(np.asarray(lc_logic, dtype=np.float32), cell_spans[i])

    # Predictor-level confidence decay after corner relocation.
    scores = center_scores.copy()
    modified = False
    for i in range(len(scores)):
        if scores[i] < center_thresh:
            break
        if int(corner_counts[i].sum()) <= CELL_MIN_OPTIMIZE_COUNT:
            scores[i] *= CELL_DECAY
            modified = True

    if modified:
        order = np.argsort(scores)[::-1]
        corrected = corrected[order]
        scores = scores[order]
        logic = logic[order]
        corner_counts = corner_counts[order]

    return DecodedBatch(
        cells=corrected,
        scores=scores,
        logic=logic,
        corner_counts=corner_counts,
    )


# ---------------------------------------------------------------------------
# Postprocess / polygon NMS / JSON
# ---------------------------------------------------------------------------

def polygon_overlap_nms(detections: np.ndarray, iou_thresh: float) -> np.ndarray:
    """Official-style convex polygon NMS using intersection/min(area)."""
    if len(detections) <= 1:
        return detections

    polys = [np.asarray(row[:8], dtype=np.float32).reshape(4, 2) for row in detections]
    areas = np.array([abs(cv2.contourArea(p)) for p in polys], dtype=np.float64)
    invalid = np.zeros(len(detections), dtype=bool)

    for i in range(len(detections)):
        if invalid[i]:
            continue
        for j in range(i + 1, len(detections)):
            if invalid[j]:
                continue
            try:
                inter, _ = cv2.intersectConvexConvex(polys[i], polys[j])
            except cv2.error:
                inter = 0.0
            denom = min(areas[i], areas[j])
            overlap = (float(inter) / denom) if denom > 0 else 0.0
            if overlap > iou_thresh:
                if detections[i, 8] > detections[j, 8]:
                    invalid[j] = True
                else:
                    invalid[i] = True
                    break
    return detections[~invalid]


def postprocess(
    decoded: DecodedBatch,
    meta: Meta,
    *,
    center_thresh: float,
    use_nms: bool,
    iou_thresh: float,
) -> np.ndarray:
    keep = decoded.scores >= center_thresh
    cells = decoded.cells[keep]
    scores = decoded.scores[keep]
    logic = decoded.logic[keep]

    if len(cells) == 0:
        return np.empty((0, 13), dtype=np.float32)

    inv = get_affine_transform(
        meta.center,
        meta.scale,
        [meta.output_width, meta.output_height],
        inv=True,
    )
    cells_orig = transform_points(cells, inv)
    cells_orig -= meta.shift.reshape(1, 1, 2)

    detections = np.concatenate(
        [
            cells_orig.reshape(-1, 8).astype(np.float32),
            scores[:, None].astype(np.float32),
            logic.astype(np.float32),
        ],
        axis=1,
    )

    if use_nms:
        detections = polygon_overlap_nms(detections, iou_thresh)
    return detections


def detections_to_cells(detections: np.ndarray) -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    for row in detections:
        logic_1 = [int(v) for v in row[9:13]]
        logic_0 = [v - 1 for v in logic_1]
        corners = row[:8].reshape(4, 2)
        out.append(
            {
                "score": float(row[8]),
                "corners": [[float(x), float(y)] for x, y in corners],
                "logic_axis_1based": logic_1,
                "logic_axis_0based": logic_0,
                "start_col": logic_1[0],
                "end_col": logic_1[1],
                "start_row": logic_1[2],
                "end_row": logic_1[3],
            }
        )
    return out


def save_visualization(image: np.ndarray, cells: List[Mapping[str, object]], path: Path, title: str = "") -> None:
    canvas = image.copy()
    for cell in cells:
        pts = np.rint(np.asarray(cell["corners"], dtype=np.float32)).astype(np.int32)
        cv2.polylines(canvas, [pts], True, (0, 0, 255), 2, cv2.LINE_AA)
        logic = cell["logic_axis_0based"]
        label = f"c{logic[0]}-{logic[1]} r{logic[2]}-{logic[3]}"
        anchor = pts[0]
        cv2.putText(
            canvas,
            label,
            (int(anchor[0]), max(12, int(anchor[1]) - 3)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.38,
            (255, 0, 0),
            1,
            cv2.LINE_AA,
        )
    if title:
        cv2.putText(canvas, title, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 128, 0), 2, cv2.LINE_AA)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), canvas):
        raise OSError(f"Failed to save visualization: {path}")


# ---------------------------------------------------------------------------
# ONNX Runtime
# ---------------------------------------------------------------------------

def make_session(model_path: Path, device: str) -> ort.InferenceSession:
    available = ort.get_available_providers()
    if device == "cuda":
        if "CUDAExecutionProvider" not in available:
            raise RuntimeError(
                "CUDAExecutionProvider is unavailable. Install a compatible onnxruntime-gpu build."
            )
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    else:
        providers = ["CPUExecutionProvider"]

    try:
        return ort.InferenceSession(str(model_path), providers=providers)
    except Exception as exc:
        msg = str(exc)
        if "DeformConv" in msg or "NOT_IMPLEMENTED" in msg or "No Op registered" in msg:
            raise RuntimeError(
                "Failed to load the ONNX model. This model contains ONNX DeformConv. "
                "onnxruntime==1.23.2 does not support this model; use ORT 1.25+ or "
                "re-export the model with DCN decomposed to GridSample.\n"
                f"Original error: {msg}"
            ) from exc
        raise


def fixed_input_hw(session: ort.InferenceSession) -> Tuple[str, int, int]:
    inp = session.get_inputs()[0]
    shape = inp.shape
    if len(shape) != 4:
        raise RuntimeError(f"Expected NCHW rank-4 input, got {shape}")
    if not isinstance(shape[2], int) or not isinstance(shape[3], int):
        raise RuntimeError(f"This sample expects fixed H/W ONNX input, got {shape}")
    return inp.name, int(shape[2]), int(shape[3])


def run_onnx(session: ort.InferenceSession, input_name: str, tensor: np.ndarray) -> Dict[str, np.ndarray]:
    outputs = session.run(None, {input_name: tensor})
    names = [o.name for o in session.get_outputs()]
    output_map = dict(zip(names, outputs))

    missing = sorted(set(REQUIRED_OUTPUTS) - set(output_map))
    if missing:
        raise RuntimeError(
            f"Missing required ONNX outputs: {missing}. Available outputs: {names}"
        )
    return output_map



# ---------------------------------------------------------------------------
# Flet/NDLOCR integration helpers
# ---------------------------------------------------------------------------

TABLE_BLOCK_TYPE = "表組"


class TableCenterNetRecognizer:
    """TableCenterNet ONNX Runtime wrapper.

    Call :meth:`load` during application startup to create the ONNX Runtime
    session eagerly. ``recognize_rgb`` also guards against an unloaded session
    for standalone/backward-compatible use.
    """

    def __init__(
        self,
        model_path: str | Path,
        device: str = "cpu",
        decoder: str = "official",
        center_k: int = DEFAULT_CENTER_K,
        corner_k: int = DEFAULT_CORNER_K,
        center_thresh: float = DEFAULT_CENTER_THRESH,
        corner_thresh: float = DEFAULT_CORNER_THRESH,
        use_nms: bool = False,
        iou_thresh: float = DEFAULT_IOU_THRESH,
    ) -> None:
        if decoder not in {"simple", "official"}:
            raise ValueError("decoder must be 'simple' or 'official'")
        self.model_path = Path(model_path)
        self.device = device
        self.decoder = decoder
        self.center_k = int(center_k)
        self.corner_k = int(corner_k)
        self.center_thresh = float(center_thresh)
        self.corner_thresh = float(corner_thresh)
        self.use_nms = bool(use_nms)
        self.iou_thresh = float(iou_thresh)
        self._session: Optional[ort.InferenceSession] = None
        self._input_name: Optional[str] = None
        self._input_h: Optional[int] = None
        self._input_w: Optional[int] = None

    @property
    def loaded(self) -> bool:
        return self._session is not None

    def load(self) -> "TableCenterNetRecognizer":
        """Create the ONNX Runtime session immediately and return ``self``."""
        self._ensure_session()
        return self

    def _ensure_session(self) -> None:
        if self._session is not None:
            return
        if not self.model_path.exists():
            raise FileNotFoundError(f"TableCenterNet model is not found: {self.model_path}")
        session = make_session(self.model_path, self.device)
        input_name, input_h, input_w = fixed_input_hw(session)
        self._session = session
        self._input_name = input_name
        self._input_h = input_h
        self._input_w = input_w

    def recognize_rgb(self, image_rgb: np.ndarray) -> List[Dict[str, object]]:
        """Infer table cells from an RGB crop and return crop-local coordinates."""
        if image_rgb.ndim != 3 or image_rgb.shape[2] != 3:
            raise ValueError(f"Expected HxWx3 RGB image, got {image_rgb.shape}")
        self._ensure_session()
        assert self._session is not None
        assert self._input_name is not None
        assert self._input_h is not None and self._input_w is not None

        # The official TableCenterNet preprocessing consumes OpenCV BGR.
        image_bgr = np.ascontiguousarray(image_rgb[:, :, ::-1])
        tensor, meta = preprocess(image_bgr, self._input_h, self._input_w, padding=0)
        out = run_onnx(self._session, self._input_name, tensor)
        _, _, out_h, out_w = out["hm"].shape
        meta.output_height = int(out_h)
        meta.output_width = int(out_w)

        if self.decoder == "simple":
            decoded = decode_simple(
                out["hm"], out["reg"], out["ct2cn"], out["lc"], out["sp"],
                center_k=self.center_k,
                center_thresh=self.center_thresh,
            )
        else:
            decoded = decode_official(
                out["hm"], out["reg"], out["ct2cn"], out["cn2ct"], out["lc"], out["sp"],
                center_k=self.center_k,
                corner_k=self.corner_k,
                center_thresh=self.center_thresh,
                corner_thresh=self.corner_thresh,
            )

        detections = postprocess(
            decoded,
            meta,
            center_thresh=self.center_thresh,
            use_nms=self.use_nms,
            iou_thresh=self.iou_thresh,
        )
        return detections_to_cells(detections)


def _table_region_bboxes_overlap(
    a: Sequence[float],
    b: Sequence[float],
) -> bool:
    """Return whether two table rectangles overlap with positive area."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    return max(ax1, bx1) < min(ax2, bx2) and max(ay1, by1) < min(ay2, by2)


def _merge_overlapping_table_regions(
    regions: Sequence[Mapping[str, object]],
) -> List[Dict[str, object]]:
    """Replace each connected group of overlapping regions with its envelope."""
    merged: List[Dict[str, object]] = []
    visited = set()

    for start in range(len(regions)):
        if start in visited:
            continue

        component = []
        pending = [start]
        visited.add(start)
        while pending:
            current = pending.pop()
            component.append(current)
            current_bbox = regions[current]["bbox"]  # type: ignore[index]
            for candidate in range(len(regions)):
                if candidate in visited:
                    continue
                candidate_bbox = regions[candidate]["bbox"]  # type: ignore[index]
                if _table_region_bboxes_overlap(current_bbox, candidate_bbox):
                    visited.add(candidate)
                    pending.append(candidate)

        members = [regions[index] for index in component]
        boxes = [member["bbox"] for member in members]
        source_indexes = sorted({
            int(source_index)
            for member in members
            for source_index in member.get("source_table_indexes", [])
        })
        merged.append({
            "index": min(int(member.get("index", 0)) for member in members),
            "bbox": [
                min(int(box[0]) for box in boxes),  # type: ignore[index]
                min(int(box[1]) for box in boxes),  # type: ignore[index]
                max(int(box[2]) for box in boxes),  # type: ignore[index]
                max(int(box[3]) for box in boxes),  # type: ignore[index]
            ],
            "confidence": max(float(member.get("confidence", 0.0)) for member in members),
            "source_table_indexes": source_indexes,
        })

    return merged


def extract_table_regions(page_xml: str) -> List[Dict[str, object]]:
    """Extract ``表組`` rectangles, merging overlaps into outer envelopes."""
    import xml.etree.ElementTree as ET

    if not page_xml:
        return []
    root = ET.fromstring(page_xml)
    regions: List[Dict[str, object]] = []
    table_index = 0
    for index, block in enumerate(root.findall(".//BLOCK")):
        if (block.get("TYPE") or "").strip() != TABLE_BLOCK_TYPE:
            continue
        table_index += 1
        try:
            x = int(float(block.get("X", "0")))
            y = int(float(block.get("Y", "0")))
            w = int(float(block.get("WIDTH", "0")))
            h = int(float(block.get("HEIGHT", "0")))
            conf = float(block.get("CONF", "0"))
        except ValueError:
            continue
        if w <= 0 or h <= 0:
            continue
        regions.append({
            "index": index,
            "bbox": [x, y, x + w, y + h],
            "confidence": conf,
            "source_table_indexes": [table_index],
        })
    return _merge_overlapping_table_regions(regions)


def _line_bbox(line: Mapping[str, object]) -> Optional[Tuple[float, float, float, float]]:
    bbox = line.get("boundingBox")
    if not isinstance(bbox, list) or len(bbox) < 2:
        return None
    try:
        points = np.asarray(bbox, dtype=np.float32).reshape(-1, 2)
    except Exception:
        return None
    if len(points) == 0:
        return None
    return (
        float(points[:, 0].min()),
        float(points[:, 1].min()),
        float(points[:, 0].max()),
        float(points[:, 1].max()),
    )


def _center_in_any_region(bbox: Tuple[float, float, float, float], regions: Sequence[Mapping[str, object]]) -> bool:
    x1, y1, x2, y2 = bbox
    cx = (x1 + x2) * 0.5
    cy = (y1 + y2) * 0.5
    for region in regions:
        rx1, ry1, rx2, ry2 = region["bbox"]  # type: ignore[index]
        if float(rx1) <= cx <= float(rx2) and float(ry1) <= cy <= float(ry2):
            return True
    return False


def filter_tables_from_page_result(page_result: Mapping[str, object], regions: Sequence[Mapping[str, object]]) -> Dict[str, object]:
    """Compatibility helper that now preserves the complete OCR result.

    Table HTML is an additional output. Table text and table blocks remain in
    TXT/JSON/XML/PDF text-layer data exactly as produced by the normal OCR.
    """
    return dict(page_result)


def _bbox_intersection_ratio(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    area = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    return inter / area if area > 0 else 0.0


def _bbox_iou(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    """Return axis-aligned IoU for two OCR rectangles."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0.0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0.0 else 0.0


def _deduplicate_assigned_ocr_lines(
    assigned: Sequence[Tuple[int, int, Mapping[str, object]]],
    iou_threshold: float = 0.5,
) -> List[Tuple[int, int, Dict[str, object]]]:
    """Suppress overlapping OCR boxes after logical-grid assignment.

    Candidates are considered globally across the table, not only inside one
    slot, because duplicate OCR detections can have slightly shifted centres
    and therefore land in neighbouring slots.  When IoU is at least the
    threshold, the larger-area rectangle wins.  Equal-area ties preserve the
    original OCR order for deterministic output.
    """
    candidates = []
    for order, (r, c, line) in enumerate(assigned):
        bbox = _line_bbox(line)
        if bbox is None:
            continue
        x1, y1, x2, y2 = bbox
        area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        candidates.append((float(area), order, int(r), int(c), dict(line), bbox))

    candidates.sort(key=lambda item: (-item[0], item[1]))
    kept = []
    kept_boxes: List[Tuple[float, float, float, float]] = []
    for area, order, r, c, line, bbox in candidates:
        if any(_bbox_iou(bbox, other) >= float(iou_threshold) for other in kept_boxes):
            continue
        kept.append((order, r, c, line))
        kept_boxes.append(bbox)

    kept.sort(key=lambda item: item[0])
    return [(r, c, line) for _, r, c, line in kept]


def _cell_grid_range(cell: Mapping[str, object]) -> Optional[Tuple[int, int, int, int]]:
    """Return normalized ``(c1, c2, r1, r2)`` for one predicted cell."""
    logic = cell.get("logic_axis_0based", [0, 0, 0, 0])
    try:
        c1, c2, r1, r2 = [int(v) for v in logic]  # type: ignore[arg-type]
    except Exception:
        return None
    c1, c2 = min(c1, c2), max(c1, c2)
    r1, r2 = min(r1, r2), max(r1, r2)
    if min(c1, r1) < 0 or max(c2, r2) > 255:
        return None
    return c1, c2, r1, r2


def table_grid_shape(cells: Sequence[Mapping[str, object]]) -> Tuple[int, int]:
    """Return the logical row/column counts, including merged cells."""
    rows = cols = 0
    for cell in cells:
        grid_range = _cell_grid_range(cell)
        if grid_range is not None:
            _, c2, _, r2 = grid_range
            rows = max(rows, r2 + 1)
            cols = max(cols, c2 + 1)
    return rows, cols


def _ruled_table_cells(
    image_rgb: np.ndarray,
    cells: Sequence[Mapping[str, object]],
    table_bbox: Sequence[float],
) -> Optional[List[Dict[str, object]]]:
    """Use a complete ruled frame instead of noisy regressed logical indices."""
    height, width = image_rgb.shape[:2]
    x1, y1, x2, y2 = [int(round(float(value))) for value in table_bbox]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(width, x2), min(height, y2)
    if x2 - x1 < 32 or y2 - y1 < 32 or not cells:
        return None
    gray = cv2.cvtColor(image_rgb[y1:y2, x1:x2], cv2.COLOR_RGB2GRAY)
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 10
    )
    h, w = gray.shape
    tolerance = max(2, int(round(min(h, w) * 0.004)))
    boundaries = []
    strong_rulings = True
    for axis, kernel in ((0, (1, max(15, h // 30))), (1, (max(15, w // 30), 1))):
        mask = cv2.morphologyEx(
            binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, kernel)
        )
        support = np.count_nonzero(mask, axis=axis) / mask.shape[axis]
        groups: List[List[int]] = []
        for index in np.flatnonzero(support > 0.15):
            if not groups or index - groups[-1][-1] > tolerance:
                groups.append([int(index)])
            else:
                groups[-1].append(int(index))
        values = np.array([
            np.average(group, weights=support[group]) for group in groups
        ])
        extent = w if axis == 0 else h
        # Borderless, partial, or strongly skewed tables retain model topology.
        if (
            not 3 <= len(values) <= 256
            or values[0] > extent * 0.03
            or values[-1] < extent * 0.97
            or min(np.max(support[groups[0]]), np.max(support[groups[-1]])) < 0.5
            or np.min(np.diff(values)) <= tolerance * 2
        ):
            return None
        strengths = np.array([np.max(support[group]) for group in groups])
        strong_rulings = strong_rulings and (
            min(strengths[0], strengths[-1]) >= 0.8
            and np.mean(strengths >= 0.8) >= 0.75
        )
        boundaries.append(values)
    xs, ys = boundaries
    rows, cols = len(ys) - 1, len(xs) - 1

    # A frame alone is insufficient: predicted physical edges must corroborate
    # the recovered axes, independently of their regressed row/column numbers.
    aligned = 0
    valid = 0
    predicted_spans: List[Tuple[int, int, int, int]] = []
    predicted_single_cells: set[Tuple[int, int]] = set()
    for cell in cells:
        points = np.asarray(cell.get("corners", []), dtype=np.float32)
        if points.shape != (4, 2) or not np.all(np.isfinite(points)):
            continue
        valid += 1
        left, top = points.min(axis=0) - [x1, y1]
        right, bottom = points.max(axis=0) - [x1, y1]
        if all(
            np.min(np.abs(axis - value)) <= max(tolerance * 2, np.median(np.diff(axis)) * 0.2)
            for axis, value in ((xs, left), (xs, right), (ys, top), (ys, bottom))
        ):
            aligned += 1
            c1, c2 = [int(np.argmin(np.abs(xs - value))) for value in (left, right)]
            r1, r2 = [int(np.argmin(np.abs(ys - value))) for value in (top, bottom)]
            if c2 > c1 and r2 > r1 and (c2 - c1) * (r2 - r1) > 1:
                predicted_spans.append((c1, c2 - 1, r1, r2 - 1))
            elif c2 == c1 + 1 and r2 == r1 + 1:
                predicted_single_cells.add((r1, c1))
    # Dense, continuous rulings can recover tables even when most cell
    # predictions are distorted. Faint rules still need stronger model evidence.
    required_alignment = 0.25 if strong_rulings else 0.7
    if not valid or aligned < valid * required_alignment:
        return None

    # Short kernels retain separators that exist only within one header/body
    # cell. Long projection kernels above are only for discovering grid axes.
    vertical = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(5, int(np.min(np.diff(ys)) * 0.6)))),
    )
    horizontal = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(5, int(np.min(np.diff(xs)) * 0.6)), 1)),
    )

    def separator(
        mask: np.ndarray, axis: int, position: float, start: float, end: float,
        threshold: float = 0.25,
    ) -> bool:
        inset = min(tolerance, (end - start) * 0.15)
        lo, hi = int(round(start + inset)), int(round(end - inset))
        pos = int(round(position))
        band = (
            mask[lo:hi, max(0, pos - tolerance):pos + tolerance + 1]
            if axis == 0 else
            mask[max(0, pos - tolerance):pos + tolerance + 1, lo:hi]
        )
        return bool(np.mean(np.any(band, axis=1 if axis == 0 else 0)) >= threshold)

    parent = list(range(rows * cols))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def join(a: int, b: int) -> None:
        parent[find(a)] = find(b)

    for r in range(rows):
        for c in range(cols):
            index = r * cols + c
            if c and not (
                separator(vertical, 0, xs[c], ys[r], ys[r + 1])
                or separator(binary, 0, xs[c], ys[r], ys[r + 1], 0.8)
            ):
                join(index, index - 1)
            if r and not (
                separator(horizontal, 1, ys[r], xs[c], xs[c + 1])
                or separator(binary, 1, ys[r], xs[c], xs[c + 1], 0.8)
            ):
                join(index, index - cols)

    components: Dict[int, List[Tuple[int, int]]] = {}
    for r in range(rows):
        for c in range(cols):
            components.setdefault(find(r * cols + c), []).append((r, c))

    result: List[Dict[str, object]] = []
    for slots in components.values():
        r1, c1 = np.min(slots, axis=0).tolist()
        r2, c2 = np.max(slots, axis=0).tolist()
        area = (r2 - r1 + 1) * (c2 - c1 + 1)
        corroborated = len(slots) == 1 or any(
            c1 <= pc1 <= pc2 <= c2 and r1 <= pr1 <= pr2 <= r2
            and (pc2 - pc1 + 1) * (pr2 - pr1 + 1) >= area * 0.5
            for pc1, pc2, pr1, pr2 in predicted_spans
        )
        if strong_rulings and not any(slot in predicted_single_cells for slot in slots):
            corroborated = True
        # Missing ink is not evidence of a merge on its own. Require a
        # rectangular component and either model support or strong rulings
        # without contradicting detected single cells.
        ranges = [(c1, c2, r1, r2)] if len(slots) == area and corroborated else [
            (c, c, r, r) for r, c in slots
        ]
        for c1, c2, r1, r2 in ranges:
            left, right = float(xs[c1] + x1), float(xs[c2 + 1] + x1)
            top, bottom = float(ys[r1] + y1), float(ys[r2 + 1] + y1)
            result.append({
                "score": 0.0,
                "corners": [[left, top], [right, top], [right, bottom], [left, bottom]],
                "logic_axis_0based": [c1, c2, r1, r2],
                "logic_axis_1based": [c1 + 1, c2 + 1, r1 + 1, r2 + 1],
                "start_col": c1 + 1, "end_col": c2 + 1,
                "start_row": r1 + 1, "end_row": r2 + 1,
                "structure_source": "ruling_lines",
            })
    return result


def _interpolate_grid_boundaries(
    votes: Mapping[int, Sequence[float]],
    boundary_count: int,
    minimum: float,
    maximum: float,
) -> np.ndarray:
    """Turn noisy boundary observations into a monotonic 1-D grid axis.

    TableCenterNet predicts a quadrilateral for every (possibly merged) cell.
    Each left/right or top/bottom edge therefore votes for one logical grid
    boundary.  The median suppresses outlier cells, and missing boundaries are
    linearly interpolated between their neighbours.
    """
    values = np.full((boundary_count,), np.nan, dtype=np.float32)
    for idx, coords in votes.items():
        if 0 <= int(idx) < boundary_count and coords:
            values[int(idx)] = float(np.median(np.asarray(coords, dtype=np.float32)))

    if boundary_count == 0:
        return values
    if not np.isfinite(values[0]):
        values[0] = float(minimum)
    if boundary_count > 1 and not np.isfinite(values[-1]):
        values[-1] = float(maximum)

    known = np.flatnonzero(np.isfinite(values))
    if len(known) == 0:
        return np.linspace(float(minimum), float(maximum), boundary_count, dtype=np.float32)
    if len(known) == 1:
        # A degenerate prediction is still made usable as a regular axis.
        return np.linspace(float(minimum), float(maximum), boundary_count, dtype=np.float32)

    missing = np.flatnonzero(~np.isfinite(values))
    if len(missing):
        values[missing] = np.interp(missing, known, values[known]).astype(np.float32)

    # Logical columns/rows must be ordered even when a noisy prediction makes
    # two neighbouring medians cross. Sorting changes only the 1-D boundary
    # locations; the logical indices themselves remain unchanged.
    values = np.sort(values)
    span = max(float(maximum) - float(minimum), 1.0)
    epsilon = max(span * 1.0e-5, 1.0e-3)
    for i in range(1, len(values)):
        if values[i] <= values[i - 1]:
            values[i] = values[i - 1] + epsilon
    return values.astype(np.float32)


def _build_logical_ocr_grid(
    cells: Sequence[Mapping[str, object]],
    *,
    table_bbox: Optional[Sequence[float]] = None,
    ocr_lines: Sequence[Mapping[str, object]] = (),
) -> Optional[Tuple[np.ndarray, np.ndarray, List[List[List[Dict[str, object]]]]]]:
    """Build the elementary logical grid, including recoverable outer edges.

    Interior boundaries come from TableCenterNet cell edges and logic axes.
    When the layout recognizer's full table rectangle is available, its four
    edges are treated as the authoritative outer frame.  Missing leading rows
    or columns are naturally represented when TableCenterNet logic indices
    start above zero.  Missing trailing rows or columns are inferred from the
    remaining distance to the table frame, the median elementary cell size,
    and OCR evidence in that outer band.
    """
    x_votes: Dict[int, List[float]] = {}
    y_votes: Dict[int, List[float]] = {}
    all_x: List[float] = []
    all_y: List[float] = []
    unit_widths: List[float] = []
    unit_heights: List[float] = []
    max_col = -1
    max_row = -1

    for cell in cells:
        grid_range = _cell_grid_range(cell)
        if grid_range is None:
            continue
        try:
            pts = np.asarray(cell["corners"], dtype=np.float32).reshape(4, 2)
        except Exception:
            continue
        if not np.all(np.isfinite(pts)):
            continue

        c1, c2, r1, r2 = grid_range
        max_col = max(max_col, c2)
        max_row = max(max_row, r2)
        tl, tr, br, bl = pts

        x_votes.setdefault(c1, []).extend([float(tl[0]), float(bl[0])])
        x_votes.setdefault(c2 + 1, []).extend([float(tr[0]), float(br[0])])
        y_votes.setdefault(r1, []).extend([float(tl[1]), float(tr[1])])
        y_votes.setdefault(r2 + 1, []).extend([float(bl[1]), float(br[1])])
        all_x.extend(float(v) for v in pts[:, 0])
        all_y.extend(float(v) for v in pts[:, 1])

        col_span = max(1, c2 - c1 + 1)
        row_span = max(1, r2 - r1 + 1)
        left_x = float((tl[0] + bl[0]) * 0.5)
        right_x = float((tr[0] + br[0]) * 0.5)
        top_y = float((tl[1] + tr[1]) * 0.5)
        bottom_y = float((bl[1] + br[1]) * 0.5)
        if right_x > left_x:
            unit_widths.append((right_x - left_x) / col_span)
        if bottom_y > top_y:
            unit_heights.append((bottom_y - top_y) / row_span)

    if max_col < 0 or max_row < 0 or not all_x or not all_y:
        return None

    if table_bbox is not None and len(table_bbox) >= 4:
        tx1, ty1, tx2, ty2 = [float(v) for v in table_bbox[:4]]
        if tx2 <= tx1 or ty2 <= ty1:
            tx1, ty1, tx2, ty2 = min(all_x), min(all_y), max(all_x), max(all_y)
    else:
        tx1, ty1, tx2, ty2 = min(all_x), min(all_y), max(all_x), max(all_y)

    # Clamp the frame only to sane page geometry; detected cells may protrude a
    # few pixels beyond layout bounds, but the layout rectangle is intentionally
    # kept as the logical grid's outer edge when supplied.
    base_cols = max_col + 1
    base_rows = max_row + 1

    median_w = float(np.median(unit_widths)) if unit_widths else max((tx2 - tx1) / max(base_cols, 1), 1.0)
    median_h = float(np.median(unit_heights)) if unit_heights else max((ty2 - ty1) / max(base_rows, 1), 1.0)
    median_w = max(median_w, 1.0)
    median_h = max(median_h, 1.0)

    detected_right = max(float(v) for vals in x_votes.values() for v in vals)
    detected_bottom = max(float(v) for vals in y_votes.values() for v in vals)
    right_gap = max(0.0, tx2 - detected_right)
    bottom_gap = max(0.0, ty2 - detected_bottom)

    def has_ocr_in_band(axis: str, start: float, end: float) -> bool:
        if end <= start:
            return False
        for line in ocr_lines:
            bbox = _line_bbox(line)
            if bbox is None:
                continue
            x1, y1, x2, y2 = bbox
            cx = (x1 + x2) * 0.5
            cy = (y1 + y2) * 0.5
            if axis == "x":
                if start <= cx <= end and ty1 <= cy <= ty2:
                    return True
            else:
                if start <= cy <= end and tx1 <= cx <= tx2:
                    return True
        return False

    extra_cols = 0
    if right_gap >= median_w * 0.55 and has_ocr_in_band("x", detected_right, tx2):
        extra_cols = max(1, int(np.rint(right_gap / median_w)))
        extra_cols = min(extra_cols, 32)

    extra_rows = 0
    if bottom_gap >= median_h * 0.55 and has_ocr_in_band("y", detected_bottom, ty2):
        extra_rows = max(1, int(np.rint(bottom_gap / median_h)))
        extra_rows = min(extra_rows, 32)

    cols = base_cols + extra_cols
    rows = base_rows + extra_rows

    # The full layout-table rectangle defines the outermost grid boundaries.
    # This also fixes leading missed rows/columns when logic indices begin at
    # 1 or greater: missing boundary votes between the frame and first detected
    # cell are interpolated into the appropriate logical slots.
    x_boundaries = _interpolate_grid_boundaries(x_votes, cols + 1, tx1, tx2)
    y_boundaries = _interpolate_grid_boundaries(y_votes, rows + 1, ty1, ty2)
    x_boundaries[0], x_boundaries[-1] = tx1, tx2
    y_boundaries[0], y_boundaries[-1] = ty1, ty2

    # Re-interpolate after pinning the frame so trailing inferred slots divide
    # the remaining outer gap regularly instead of collapsing at the endpoint.
    def repopulate(values: np.ndarray, votes: Mapping[int, Sequence[float]], lo: float, hi: float) -> np.ndarray:
        n = len(values)
        result = np.full((n,), np.nan, dtype=np.float32)
        result[0], result[-1] = float(lo), float(hi)
        span = max(float(hi) - float(lo), 1.0)
        eps = max(span * 1e-5, 1e-3)
        for idx, coords in votes.items():
            idx = int(idx)
            if 0 < idx < n - 1 and coords:
                value = float(np.median(np.asarray(coords, dtype=np.float32)))
                result[idx] = min(max(value, lo + eps), hi - eps)
        known = np.flatnonzero(np.isfinite(result))
        missing = np.flatnonzero(~np.isfinite(result))
        if len(missing):
            result[missing] = np.interp(missing, known, result[known]).astype(np.float32)
        # Enforce strict monotonicity while preserving the exact layout frame.
        for i in range(1, n - 1):
            min_allowed = result[i - 1] + eps
            max_allowed = hi - eps * (n - 1 - i)
            result[i] = min(max(float(result[i]), float(min_allowed)), float(max_allowed))
        result[0], result[-1] = float(lo), float(hi)
        return result.astype(np.float32)


    x_boundaries = repopulate(x_boundaries, x_votes, tx1, tx2)
    y_boundaries = repopulate(y_boundaries, y_votes, ty1, ty2)

    grid: List[List[List[Dict[str, object]]]] = [
        [[] for _ in range(cols)] for _ in range(rows)
    ]
    return x_boundaries, y_boundaries, grid

def _grid_slot_for_ocr_line(
    bbox: Tuple[float, float, float, float],
    x_boundaries: np.ndarray,
    y_boundaries: np.ndarray,
) -> Optional[Tuple[int, int]]:
    """Choose exactly one elementary grid slot for an OCR line."""
    x1, y1, x2, y2 = bbox
    cx = (x1 + x2) * 0.5
    cy = (y1 + y2) * 0.5
    cols = len(x_boundaries) - 1
    rows = len(y_boundaries) - 1
    if cols <= 0 or rows <= 0:
        return None

    # Primary rule: place the OCR-line centre in the reconstructed logical
    # grid, rather than asking whether it lies inside one predicted polygon.
    if (
        float(x_boundaries[0]) <= cx <= float(x_boundaries[-1])
        and float(y_boundaries[0]) <= cy <= float(y_boundaries[-1])
    ):
        c = int(np.searchsorted(x_boundaries, cx, side="right") - 1)
        r = int(np.searchsorted(y_boundaries, cy, side="right") - 1)
        c = min(max(c, 0), cols - 1)
        r = min(max(r, 0), rows - 1)
        return r, c

    # A line can protrude slightly outside a noisy outer boundary.  In that
    # case select the elementary rectangle that covers the largest fraction of
    # the OCR bounding box, but reject unrelated neighbouring text.
    best_slot: Optional[Tuple[int, int]] = None
    best_ratio = 0.0
    for r in range(rows):
        gy1, gy2 = float(y_boundaries[r]), float(y_boundaries[r + 1])
        for c in range(cols):
            gx1, gx2 = float(x_boundaries[c]), float(x_boundaries[c + 1])
            ratio = _bbox_intersection_ratio(bbox, (gx1, gy1, gx2, gy2))
            if ratio > best_ratio:
                best_ratio = ratio
                best_slot = (r, c)
    return best_slot if best_ratio >= 0.2 else None


def _is_vertical_ocr_line(line: Mapping[str, object]) -> bool:
    value = line.get("isVertical", False)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def _sort_ocr_lines_in_grid(lines: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    """Order OCR snippets inside one elementary grid slot."""
    copied = [dict(line) for line in lines]
    if not copied:
        return copied
    vertical = sum(1 for line in copied if _is_vertical_ocr_line(line)) > len(copied) / 2.0
    if vertical:
        # Japanese vertical text: columns are normally read right-to-left,
        # while glyph/line progression inside one column is top-to-bottom.
        copied.sort(
            key=lambda x: (
                -((_line_bbox(x) or (0, 0, 0, 0))[0]),
                (_line_bbox(x) or (0, 0, 0, 0))[1],
            )
        )
    else:
        copied.sort(
            key=lambda x: (
                (_line_bbox(x) or (0, 0, 0, 0))[1],
                (_line_bbox(x) or (0, 0, 0, 0))[0],
            )
        )
    return copied


def _recover_missing_cells_from_grid(
    cells: List[Dict[str, object]],
    grid: Sequence[Sequence[Sequence[Mapping[str, object]]]],
    x_boundaries: np.ndarray,
    y_boundaries: np.ndarray,
    *,
    outer_frame_support: bool = False,
) -> List[Dict[str, object]]:
    """Recover OCR-bearing holes in the logical grid as synthetic cells.

    TableCenterNet remains the source of the table topology.  This recovery is
    deliberately conservative: a slot is synthesized only when

    * it is not covered by any detected/merged cell,
    * at least one OCR line was assigned to the slot, and
    * structural support totals at least two, where the table outer frame counts
      as one support direction and occupied orthogonal neighbours provide the rest.

    The neighbour test is repeated until no new slots can be recovered.  This
    allows a short run of missed cells to be filled from its detected edges
    without turning every empty logical slot into a cell.  Existing rowspan /
    colspan regions are marked occupied before recovery, so they are never
    split into synthetic cells.
    """
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    if rows <= 0 or cols <= 0:
        return []

    occupied = np.zeros((rows, cols), dtype=bool)
    for cell in cells:
        grid_range = _cell_grid_range(cell)
        if grid_range is None:
            continue
        c1, c2, r1, r2 = grid_range
        if r1 >= rows or c1 >= cols:
            continue
        r2 = min(r2, rows - 1)
        c2 = min(c2, cols - 1)
        occupied[r1:r2 + 1, c1:c2 + 1] = True

    recovered: List[Dict[str, object]] = []

    def neighbour_count(r: int, c: int) -> int:
        count = 0
        if r > 0 and occupied[r - 1, c]:
            count += 1
        if r + 1 < rows and occupied[r + 1, c]:
            count += 1
        if c > 0 and occupied[r, c - 1]:
            count += 1
        if c + 1 < cols and occupied[r, c + 1]:
            count += 1
        # The layout-recognized table rectangle is a structural boundary too.
        # Count it once (not once per touching edge) so a completely missed
        # outer row/column can start recovery from frame + one inward cell,
        # while OCR alone at a corner is still insufficient.
        if outer_frame_support and (r == 0 or r == rows - 1 or c == 0 or c == cols - 1):
            count += 1
        return count

    # Repeating permits e.g. two adjacent missed cells to be recovered from
    # the outside in, but only while every added slot itself contains OCR.
    while True:
        candidates: List[Tuple[int, int, int]] = []
        for r in range(rows):
            for c in range(cols):
                if occupied[r, c] or not grid[r][c]:
                    continue
                support = neighbour_count(r, c)
                if support >= 2:
                    candidates.append((r, c, support))

        if not candidates:
            break

        # Strongest structural support first.  The stable row/column tie-break
        # makes recovery deterministic for tests and reproducible HTML output.
        candidates.sort(key=lambda item: (-item[2], item[0], item[1]))
        changed = False
        for r, c, support in candidates:
            if occupied[r, c]:
                continue
            # Re-check because earlier candidates in this pass may have changed
            # the neighbourhood.
            support = neighbour_count(r, c)
            if support < 2:
                continue

            x1 = float(x_boundaries[c])
            x2 = float(x_boundaries[c + 1])
            y1 = float(y_boundaries[r])
            y2 = float(y_boundaries[r + 1])
            if not (x2 > x1 and y2 > y1):
                continue

            cell: Dict[str, object] = {
                # Synthetic cells must never outrank a real TableCenterNet
                # detection if a duplicate logical start somehow appears.
                "score": 0.0,
                "corners": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
                "logic_axis_0based": [c, c, r, r],
                "logic_axis_1based": [c + 1, c + 1, r + 1, r + 1],
                "start_col": c + 1,
                "end_col": c + 1,
                "start_row": r + 1,
                "end_row": r + 1,
                "synthetic": True,
                "recovery_reason": "ocr_grid_hole",
                "recovery_neighbor_count": int(support),
                "_ocr_lines": [],
            }
            cells.append(cell)
            recovered.append(cell)
            occupied[r, c] = True
            changed = True

        if not changed:
            break

    return recovered


def _assign_ocr_lines_to_cells(
    cells: List[Dict[str, object]],
    ocr_lines: Sequence[Mapping[str, object]],
    *,
    table_bbox: Optional[Sequence[float]] = None,
    ocr_iou_threshold: float = 0.5,
) -> None:
    """Assign OCR text through an intermediate logical grid.

    The layout table rectangle, when available, anchors the outermost row and
    column boundaries so OCR-bearing first/last rows or columns can be
    synthesized even if TableCenterNet missed them entirely.  After every OCR
    line has been assigned to a logical slot, overlapping OCR rectangles are
    globally suppressed at IoU >= ``ocr_iou_threshold`` by retaining the
    larger rectangle.
    """
    for cell in cells:
        cell["_ocr_lines"] = []

    built = _build_logical_ocr_grid(
        cells, table_bbox=table_bbox, ocr_lines=ocr_lines
    )
    if built is None:
        return
    x_boundaries, y_boundaries, grid = built

    assigned: List[Tuple[int, int, Mapping[str, object]]] = []
    for line in ocr_lines:
        bbox = _line_bbox(line)
        if bbox is None:
            continue
        slot = _grid_slot_for_ocr_line(bbox, x_boundaries, y_boundaries)
        if slot is None:
            continue
        r, c = slot
        assigned.append((r, c, line))

    # OCR engines can emit near-duplicate rectangles for the same text line.
    # Suppress only after grid assignment, as requested, so unrelated page OCR
    # outside this table never participates in the comparison.
    for r, c, line in _deduplicate_assigned_ocr_lines(assigned, ocr_iou_threshold):
        grid[r][c].append(line)

    for r in range(len(grid)):
        for c in range(len(grid[r])):
            grid[r][c] = _sort_ocr_lines_in_grid(grid[r][c])

    _recover_missing_cells_from_grid(
        cells, grid, x_boundaries, y_boundaries,
        outer_frame_support=table_bbox is not None,
    )

    for cell in cells:
        grid_range = _cell_grid_range(cell)
        if grid_range is None:
            continue
        c1, c2, r1, r2 = grid_range
        if r2 >= len(grid) or c2 >= len(grid[0]):
            continue

        covered = [
            line
            for rr in range(r1, r2 + 1)
            for cc in range(c1, c2 + 1)
            for line in grid[rr][cc]
        ]
        vertical = sum(1 for line in covered if _is_vertical_ocr_line(line)) > len(covered) / 2.0 if covered else False

        ordered: List[Dict[str, object]] = []
        if vertical:
            for cc in range(c2, c1 - 1, -1):
                for rr in range(r1, r2 + 1):
                    ordered.extend(grid[rr][cc])
        else:
            for rr in range(r1, r2 + 1):
                for cc in range(c1, c2 + 1):
                    ordered.extend(grid[rr][cc])

        seen = set()
        unique: List[Dict[str, object]] = []
        for line in ordered:
            key = (
                line.get("id"),
                tuple(np.asarray(line.get("boundingBox", []), dtype=object).reshape(-1).tolist()),
                line.get("text"),
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(line)
        cell["_ocr_lines"] = unique

def cells_to_html(
    cells: List[Dict[str, object]],
    ocr_lines: Sequence[Mapping[str, object]],
    *,
    table_bbox: Optional[Sequence[float]] = None,
    ocr_iou_threshold: float = 0.5,
) -> str:
    """Create a rowspan/colspan HTML table from TableCenterNet cells."""
    if not cells:
        return '<table class="tablecenternet"><tbody></tbody></table>'

    _assign_ocr_lines_to_cells(
        cells, ocr_lines, table_bbox=table_bbox, ocr_iou_threshold=ocr_iou_threshold
    )

    # Prefer the highest-confidence cell when duplicate logical starts occur.
    starts: Dict[Tuple[int, int], Dict[str, object]] = {}
    max_row = 0
    max_col = 0
    for cell in sorted(cells, key=lambda c: float(c.get("score", 0.0)), reverse=True):
        logic = cell.get("logic_axis_0based", [0, 0, 0, 0])
        try:
            c1, c2, r1, r2 = [int(v) for v in logic]
        except Exception:
            continue
        c1, c2 = min(c1, c2), max(c1, c2)
        r1, r2 = min(r1, r2), max(r1, r2)
        if min(c1, r1) < 0:
            continue
        # Guard against a pathological regression value allocating a huge grid.
        if max(c2, r2) > 255:
            continue
        cell["_grid"] = [c1, c2, r1, r2]
        starts.setdefault((r1, c1), cell)
        max_row = max(max_row, r2)
        max_col = max(max_col, c2)

    if not starts:
        return '<table class="tablecenternet"><tbody></tbody></table>'

    occupied: set[Tuple[int, int]] = set()
    out = ['<table class="tablecenternet">', '  <tbody>']
    for r in range(max_row + 1):
        out.append('    <tr>')
        for c in range(max_col + 1):
            if (r, c) in occupied:
                continue
            cell = starts.get((r, c))
            if cell is None:
                out.append('      <td></td>')
                continue
            c1, c2, r1, r2 = cell["_grid"]  # type: ignore[misc]
            rowspan = r2 - r1 + 1
            colspan = c2 - c1 + 1
            attrs = []
            if rowspan > 1:
                attrs.append(f'rowspan="{rowspan}"')
            if colspan > 1:
                attrs.append(f'colspan="{colspan}"')
            attr_text = (" " + " ".join(attrs)) if attrs else ""

            lines = list(cell.get("_ocr_lines", []))
            lines.sort(
                key=lambda x: (
                    (_line_bbox(x) or (0, 0, 0, 0))[1],
                    (_line_bbox(x) or (0, 0, 0, 0))[0],
                )
            )
            content = '<br>'.join(html.escape(str(line.get("text", ""))) for line in lines if line.get("text"))
            out.append(f'      <td{attr_text}>{content}</td>')
            for rr in range(r1, r2 + 1):
                for cc in range(c1, c2 + 1):
                    occupied.add((rr, cc))
        out.append('    </tr>')
    out.extend(['  </tbody>', '</table>'])
    return "\n".join(out)



def _markdown_cell_text(cell: Mapping[str, object]) -> str:
    """Return OCR text assigned to a cell, escaped for a Markdown table."""
    lines = list(cell.get("_ocr_lines", []))
    lines.sort(
        key=lambda x: (
            (_line_bbox(x) or (0, 0, 0, 0))[1],
            (_line_bbox(x) or (0, 0, 0, 0))[0],
        )
    )
    text = "<br>".join(str(line.get("text", "")) for line in lines if line.get("text"))
    # A pipe would terminate the Markdown cell. Backslashes are escaped first
    # so an existing escape sequence from OCR text is not consumed.
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def cells_to_markdown(
    cells: List[Dict[str, object]],
    ocr_lines: Sequence[Mapping[str, object]] | None = None,
    *,
    table_bbox: Optional[Sequence[float]] = None,
    ocr_iou_threshold: float = 0.5,
) -> str:
    """Create a GitHub-style Markdown table from TableCenterNet cells.

    Markdown has no rowspan/colspan syntax. To keep the logical grid readable,
    merged-cell text is repeated over every logical slot occupied by that cell.
    """
    if not cells:
        return "_表セルを検出できませんでした。_"

    if ocr_lines is not None:
        _assign_ocr_lines_to_cells(
            cells, ocr_lines, table_bbox=table_bbox, ocr_iou_threshold=ocr_iou_threshold
        )

    # Keep the highest-confidence cell when starts overlap, matching the HTML
    # conversion behavior.
    starts: Dict[Tuple[int, int], Dict[str, object]] = {}
    max_row = 0
    max_col = 0
    for cell in sorted(cells, key=lambda c: float(c.get("score", 0.0)), reverse=True):
        logic = cell.get("logic_axis_0based", [0, 0, 0, 0])
        try:
            c1, c2, r1, r2 = [int(v) for v in logic]
        except Exception:
            continue
        c1, c2 = min(c1, c2), max(c1, c2)
        r1, r2 = min(r1, r2), max(r1, r2)
        if min(c1, r1) < 0 or max(c2, r2) > 255:
            continue
        cell["_grid"] = [c1, c2, r1, r2]
        starts.setdefault((r1, c1), cell)
        max_row = max(max_row, r2)
        max_col = max(max_col, c2)

    if not starts:
        return "_有効な表グリッドを復元できませんでした。_"

    rows = max_row + 1
    cols = max_col + 1
    grid = [["" for _ in range(cols)] for _ in range(rows)]

    for cell in starts.values():
        c1, c2, r1, r2 = cell["_grid"]  # type: ignore[misc]
        text = _markdown_cell_text(cell)
        for rr in range(r1, r2 + 1):
            for cc in range(c1, c2 + 1):
                # Duplicating merged-cell text is deliberate: Markdown itself
                # cannot encode rowspan/colspan, and this preserves meaning when
                # viewed as a plain grid.
                grid[rr][cc] = text

    header = "| " + " | ".join(grid[0]) + " |"
    separator = "| " + " | ".join(["---"] * cols) + " |"
    body = ["| " + " | ".join(row) + " |" for row in grid[1:]]
    return "\n".join([header, separator, *body])


def build_tables_markdown(page_tables: Sequence[Mapping[str, object]]) -> str:
    """Build Markdown sections for table results suitable for ft.Markdown."""
    sections: List[str] = []
    for item in page_tables:
        table_index = int(item.get("table_index", len(sections) + 1))
        page_index = item.get("page_index")
        if page_index is None:
            heading = f"### 表 {table_index}"
        else:
            heading = f"### ページ {int(page_index) + 1} / 表 {table_index}"

        if item.get("error"):
            error_message = str(item.get("error")).replace("|", "\\|")
            body = f"_表構造の推定に失敗しました: {error_message}_"
        else:
            cells = list(item.get("cells", []))
            body = cells_to_markdown(cells)  # _ocr_lines were assigned during inference.
        sections.append(f"{heading}\n\n{body}")
    return "\n\n".join(sections)


def infer_tables_from_page(
    image_rgb: np.ndarray,
    page_result: Mapping[str, object],
    recognizer: TableCenterNetRecognizer,
    *,
    crop_padding: int = 8,
) -> Tuple[Dict[str, object], List[Dict[str, object]]]:
    """Run TableCenterNet only on layout-detected table regions.

    Table regions are expanded slightly before cropping. ``crop_padding`` is
    the minimum pixel margin; a 2% proportional margin is also applied so
    high-resolution pages retain a useful amount of context around the table.

    Returns ``(page_result, tables)``. The ordinary OCR result is preserved
    unchanged, while each table item contains an additional standalone HTML.
    """
    regions = extract_table_regions(str(page_result.get("page_xml", "")))
    if not regions:
        return dict(page_result), []

    img_h, img_w = image_rgb.shape[:2]
    original_lines = list(page_result.get("json_lines", []))
    tables: List[Dict[str, object]] = []

    for table_index, region in enumerate(regions, start=1):
        bx1, by1, bx2, by2 = [int(v) for v in region["bbox"]]  # type: ignore[index]
        table_w = max(1, bx2 - bx1)
        table_h = max(1, by2 - by1)
        margin_x = max(int(crop_padding), int(round(table_w * 0.02)))
        margin_y = max(int(crop_padding), int(round(table_h * 0.02)))
        x1 = max(0, bx1 - margin_x)
        y1 = max(0, by1 - margin_y)
        x2 = min(img_w, bx2 + margin_x)
        y2 = min(img_h, by2 + margin_y)
        table_obj: Dict[str, object] = {
            "table_index": table_index,
            "layout_bbox": [bx1, by1, bx2, by2],
            "crop_bbox": [x1, y1, x2, y2],
            "layout_confidence": float(region.get("confidence", 0.0)),
            "source_table_indexes": list(region.get("source_table_indexes", [table_index])),
            "decoder": recognizer.decoder,
        }
        try:
            crop = image_rgb[y1:y2, x1:x2]
            local_cells = recognizer.recognize_rgb(crop)
            global_cells: List[Dict[str, object]] = []
            for cell in local_cells:
                copied = dict(cell)
                copied["corners"] = [
                    [float(px) + x1, float(py) + y1]
                    for px, py in cell["corners"]  # type: ignore[index]
                ]
                global_cells.append(copied)

            # Only OCR lines whose centers are inside the layout table block are
            # candidates for this table; this prevents neighboring body text
            # from being pulled into a border cell by the overlap fallback.
            table_lines = []
            one_region = [region]
            for line in original_lines:
                bbox = _line_bbox(line)
                if bbox is not None and _center_in_any_region(bbox, one_region):
                    table_lines.append(line)

            detected_cell_count = len(global_cells)
            ruled_cells = _ruled_table_cells(
                image_rgb, global_cells, [bx1, by1, bx2, by2]
            )
            if ruled_cells is not None:
                global_cells = ruled_cells
                table_obj["structure_source"] = "ruling_lines"
            table_obj["cells"] = global_cells
            table_obj["detected_cell_count"] = detected_cell_count
            table_obj["html"] = cells_to_html(
                global_cells, table_lines, table_bbox=[bx1, by1, bx2, by2]
            )
            recovered_cell_count = sum(1 for cell in global_cells if cell.get("synthetic"))
            table_obj["recovered_cell_count"] = recovered_cell_count
            table_obj["num_cells"] = len(global_cells)
        except Exception as exc:
            print(f"[WARN] TableCenterNet failed for table {table_index}: {exc}")
            table_obj["cells"] = []
            table_obj["num_cells"] = 0
            table_obj["error"] = str(exc)
            table_obj["html"] = (
                '<div class="table-error">Table structure inference failed: '
                + html.escape(str(exc)) + '</div>'
            )
        tables.append(table_obj)

    # Table HTML is supplementary: keep table text/blocks in every ordinary
    # OCR output (TXT, JSON, XML and the PDF text layer).
    return dict(page_result), tables


def build_tables_html_document(source_name: str, page_tables: Sequence[Mapping[str, object]]) -> str:
    """Wrap per-page/per-region table snippets in a complete UTF-8 HTML file."""
    title = html.escape(f"Table structures - {source_name}")
    out = [
        '<!doctype html>',
        '<html lang="ja">',
        '<head>',
        '  <meta charset="utf-8">',
        f'  <title>{title}</title>',
        '  <style>',
        '    body{font-family:system-ui,-apple-system,"Yu Gothic",sans-serif;margin:24px;}',
        '    section{margin:0 0 32px 0;}',
        '    .tablecenternet{border-collapse:collapse;margin-top:8px;}',
        '    .tablecenternet td,.tablecenternet th{border:1px solid #555;padding:4px 8px;vertical-align:top;}',
        '    .meta{color:#666;font-size:0.9em;}',
        '    .table-error{color:#b00020;white-space:pre-wrap;}',
        '  </style>',
        '</head>',
        '<body>',
        f'<h1>{title}</h1>',
    ]
    for item in page_tables:
        page_index = item.get("page_index")
        table_index = item.get("table_index", 1)
        heading = f"Table {table_index}" if page_index is None else f"Page {int(page_index) + 1} / Table {table_index}"
        bbox = item.get("layout_bbox", [])
        out.append('<section>')
        out.append(f'  <h2>{html.escape(heading)}</h2>')
        out.append(f'  <div class="meta">layout bbox: {html.escape(str(bbox))}</div>')
        out.append(str(item.get("html", "")))
        out.append('</section>')
    out.extend(['</body>', '</html>'])
    return "\n".join(out)
