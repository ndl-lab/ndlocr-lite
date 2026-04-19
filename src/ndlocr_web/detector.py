"""DEIM detector with injectable inference backend."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Any
import numpy as np
from PIL import Image, ImageDraw

from .imgops import resize_bicubic, pad_to_square


@dataclass
class Detection:
    class_index: int
    class_name: str
    confidence: float
    box: np.ndarray       # [xmin, ymin, xmax, ymax] int32
    pred_char_count: float


class DEIMDetector:
    """Layout detector backed by DEIM.

    Parameters
    ----------
    infer:
        Callable that takes ``{"images": tensor, "orig_target_sizes": tensor}``
        and returns a list of numpy arrays matching DEIM's ONNX output order
        ``[class_ids, bboxes, scores]`` or ``[class_ids, bboxes, scores, char_counts]``.
    classes:
        Mapping from int index to class-name string (0-based).
    score_threshold, conf_threshold, iou_threshold:
        Detection filtering parameters.
    input_size:
        (H, W) expected by the model.
    """

    _COLORLIST = [
        (0, 0, 0), (255, 0, 0), (0, 0, 142), (0, 0, 230), (106, 0, 228),
        (0, 60, 100), (0, 80, 100), (0, 0, 70), (0, 0, 192), (250, 170, 30),
        (100, 170, 30), (220, 220, 0), (175, 116, 175), (250, 0, 30),
        (165, 42, 42), (255, 77, 255), (255, 0, 0),
    ]

    def __init__(
        self,
        infer: Callable[[dict[str, np.ndarray]], list[np.ndarray]],
        classes: list[str] | dict[int, str],
        score_threshold: float = 0.1,
        conf_threshold: float = 0.25,
        iou_threshold: float = 0.2,
        input_size: tuple[int, int] = (1024, 1024),
    ) -> None:
        self.infer = infer
        if isinstance(classes, dict):
            self.classes = classes
        else:
            self.classes = {i: name for i, name in enumerate(classes)}
        self.score_threshold = score_threshold
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.input_h, self.input_w = input_size
        self._image_side = 1  # updated in preprocess

    def preprocess(self, img: np.ndarray) -> dict[str, np.ndarray]:
        """Return ONNX-ready feed dict for a single RGB image."""
        padded, max_side = pad_to_square(img)
        self._image_side = max_side
        resized = resize_bicubic(padded, (self.input_w, self.input_h))
        tensor = resized.astype(np.float32) / 255.0
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        tensor = (tensor - mean) / std
        tensor = tensor.transpose(2, 0, 1)[np.newaxis]
        orig_sizes = np.array([[self.input_h, self.input_w]], dtype=np.int64)
        return {"images": tensor, "orig_target_sizes": orig_sizes}

    def postprocess(
        self,
        outputs: list[np.ndarray],
        img_w: int,
        img_h: int,
    ) -> list[Detection]:
        """Convert raw model outputs to Detection list."""
        if len(outputs) == 4:
            class_ids, bboxes, scores, char_counts = outputs
        else:
            class_ids, bboxes, scores = outputs
            char_counts = np.full(np.squeeze(scores).shape, 100.0, dtype=np.float32)

        class_ids = np.squeeze(class_ids)
        bboxes = np.squeeze(bboxes)
        scores = np.squeeze(scores)
        char_counts = np.squeeze(char_counts)

        # Flatten to 1-D; handle empty / misshapen outputs from dummy/real inference
        scores = np.atleast_1d(scores.ravel())
        class_ids = np.atleast_1d(class_ids.ravel())
        char_counts = np.atleast_1d(char_counts.ravel())
        bboxes = np.atleast_2d(bboxes) if bboxes.ndim >= 2 else bboxes.reshape(-1, 4) if bboxes.size else np.empty((0, 4), dtype=np.float32)

        # Align lengths (guard against mismatched dummy outputs)
        n = min(len(scores), len(class_ids), len(bboxes))
        scores = scores[:n]
        class_ids = class_ids[:n]
        char_counts = char_counts[:n] if len(char_counts) >= n else np.full(n, 100.0)
        bboxes = bboxes[:n]

        if n == 0:
            return []

        mask = scores > self.conf_threshold
        bboxes = bboxes[mask]
        scores = scores[mask]
        class_ids = class_ids[mask]
        char_counts = char_counts[mask]

        if len(scores) == 0:
            return []

        side = self._image_side
        scale = np.array(
            [side / self.input_w, side / self.input_h,
             side / self.input_w, side / self.input_h],
            dtype=np.float32,
        )
        boxes = (bboxes[:, :4] * scale).astype(np.int32)
        boxes[:, [0, 2]] = np.clip(boxes[:, [0, 2]], 0, side)
        boxes[:, [1, 3]] = np.clip(boxes[:, [1, 3]], 0, side)

        detections: list[Detection] = []
        for bbox, score, label, char_count in zip(boxes, scores, class_ids, char_counts):
            class_index = int(label) - 1
            detections.append(Detection(
                class_index=class_index,
                class_name=self.classes.get(class_index, str(class_index)),
                confidence=float(score),
                box=bbox,
                pred_char_count=float(char_count),
            ))
        return detections

    def detect(self, img: np.ndarray) -> list[Detection]:
        """Full preprocess → infer → postprocess pipeline."""
        img_h, img_w = img.shape[:2]
        feeds = self.preprocess(img)
        outputs = self.infer(feeds)
        return self.postprocess(outputs, img_w, img_h)

    def draw_detections(self, npimg: np.ndarray, detections: list[Detection]) -> Image.Image:
        pil_image = Image.fromarray(npimg)
        draw = ImageDraw.Draw(pil_image)
        for det in detections:
            x1, y1, x2, y2 = det.box
            color = self._COLORLIST[det.class_index % len(self._COLORLIST)]
            draw.rectangle([x1, y1, x2, y2], outline=color, width=2)
        return pil_image
