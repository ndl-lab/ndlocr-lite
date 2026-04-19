"""PARSeq recognizer with injectable inference backend."""
from __future__ import annotations
from typing import Callable
import numpy as np

from .imgops import rotate_ccw_90, resize_bilinear


class PARSeqRecognizer:
    """Character recognizer backed by PARSeq.

    Parameters
    ----------
    infer:
        Callable taking ``{"input": tensor}`` and returning list of ndarrays;
        first element must be the logits array of shape (1, seq_len, num_classes+1).
    charlist:
        Character vocabulary (0-indexed; index 0 in the model output is <EOS>).
    input_size:
        (W, H) expected by the model.
    """

    def __init__(
        self,
        infer: Callable[[dict[str, np.ndarray]], list[np.ndarray]],
        charlist: list[str],
        input_size: tuple[int, int] = (384, 16),
    ) -> None:
        self.infer = infer
        self.charlist = charlist
        self.input_w, self.input_h = input_size

    def preprocess(self, img: np.ndarray) -> dict[str, np.ndarray]:
        """Return ONNX-ready feed dict for a single line image."""
        h, w = img.shape[:2]
        if h > w:
            img = rotate_ccw_90(img)
        resized = resize_bilinear(img, (self.input_w, self.input_h))
        # BGR→RGB already in RGB (ndarray from PIL/numpy pipeline)
        tensor = resized.astype(np.float32) / 127.5 - 1.0
        tensor = tensor.transpose(2, 0, 1)[np.newaxis]
        return {"input": tensor}

    def postprocess(self, outputs: list[np.ndarray]) -> str:
        """Decode logits to string."""
        logits = outputs[0]  # (1, seq_len, num_classes+1)
        indices = np.argmax(logits[0], axis=1)
        stop_idx = np.where(indices == 0)[0]
        end_pos = stop_idx[0] if stop_idx.size > 0 else len(indices)
        resval = indices[:end_pos].tolist()
        return "".join(self.charlist[i - 1] for i in resval)

    def read(self, img: np.ndarray) -> str:
        """Full preprocess → infer → postprocess pipeline."""
        if img is None or img.size == 0:
            return ""
        feeds = self.preprocess(img)
        outputs = self.infer(feeds)
        return self.postprocess(outputs)
