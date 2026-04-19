"""Pyodide ↔ ONNX Runtime inference bridge.

JSDeimInfer and JSParseqInfer wrap JS async inference functions so Python
can await DEIM / PARSeq runs via the same Web Worker's JS event loop.

Usage in a Pyodide worker (Python side):
    import js
    from ndlocr_web.bridge import JSDeimInfer, JSParseqInfer

    deim_infer   = JSDeimInfer(js.jsDeimInfer)
    parseq_infer = JSParseqInfer(js.jsParseqInfer, "100")
"""
from __future__ import annotations
import numpy as np


class JSDeimInfer:
    """Async DEIM infer callable backed by a JS async function.

    The JS function is called with:
      images      – Float32Array  (shape 1×3×H×W, C-contiguous)
      imagesShape – JS Array      [1, 3, H, W]
      origSizes   – BigInt64Array (shape 1×2)

    It must resolve to an object with properties:
      classIds  : Float32Array  – length N
      bboxes    : Float32Array  – length N×4
      scores    : Float32Array  – length N
      charCounts: Float32Array | null – length N
      N         : number        – number of detections
    """

    def __init__(self, js_func) -> None:
        self._js_func = js_func

    async def __call__(self, feeds: dict[str, np.ndarray]) -> list[np.ndarray]:
        import pyodide.ffi  # type: ignore[import]  # only in Pyodide context

        images = np.ascontiguousarray(feeds["images"].astype(np.float32))
        orig_sizes = np.ascontiguousarray(feeds["orig_target_sizes"].astype(np.int64))

        result = await self._js_func(
            pyodide.ffi.to_js(images),
            pyodide.ffi.to_js(list(images.shape)),
            pyodide.ffi.to_js(orig_sizes),
        )

        N = int(result.N)
        if N == 0:
            return [
                np.empty((1, 0), dtype=np.float32),
                np.empty((1, 0, 4), dtype=np.float32),
                np.empty((1, 0), dtype=np.float32),
            ]

        class_ids = np.asarray(result.classIds, dtype=np.float32).reshape(1, N)
        bboxes = np.asarray(result.bboxes, dtype=np.float32).reshape(1, N, 4)
        scores = np.asarray(result.scores, dtype=np.float32).reshape(1, N)
        outputs: list[np.ndarray] = [class_ids, bboxes, scores]

        if result.charCounts is not None:
            outputs.append(np.asarray(result.charCounts, dtype=np.float32).reshape(1, N))
        return outputs


class JSParseqInfer:
    """Async PARSeq infer callable backed by a JS async function.

    The JS function is called with:
      variant – string '30' | '50' | '100'
      input   – Float32Array (shape 1×3×H×W, C-contiguous)
      shape   – JS Array     [1, 3, H, W]

    It must resolve to an object with properties:
      logits      : Float32Array – flat logit data
      logitsShape : JS Array     [1, seqLen, numChars]
    """

    def __init__(self, js_func, variant: str) -> None:
        self._js_func = js_func
        self._variant = variant

    async def __call__(self, feeds: dict[str, np.ndarray]) -> list[np.ndarray]:
        import pyodide.ffi  # type: ignore[import]

        input_arr = np.ascontiguousarray(feeds["input"].astype(np.float32))

        result = await self._js_func(
            self._variant,
            pyodide.ffi.to_js(input_arr),
            pyodide.ffi.to_js(list(input_arr.shape)),
        )

        logits_shape = list(result.logitsShape.to_py())
        logits = np.asarray(result.logits, dtype=np.float32).reshape(logits_shape)
        return [logits]
