"""T1-6a: Smoke test - verify pipeline runs with dummy infer functions (no real models)."""
import asyncio
import sys
import os
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ndlocr_web.pipeline import run_ocr_on_image
from ndlocr_web.detector import DEIMDetector, Detection
from ndlocr_web.recognizer import PARSeqRecognizer


def _dummy_detector_infer(feeds):
    """Return empty detections."""
    return [
        np.array([[]]),   # class_ids (empty)
        np.array([[]]),   # bboxes
        np.array([[]]),   # scores
    ]


def _dummy_recognizer_infer(feeds):
    """Return single <EOS> token (empty string)."""
    # logits shape: (1, 1, num_classes+1); argmax 0 → EOS
    return [np.zeros((1, 1, 10), dtype=np.float32)]


def main():
    _NDL_CLASSES = {
        0: "text_block", 1: "line_main", 2: "line_caption", 3: "line_ad",
        4: "line_note", 5: "line_note_tochu", 6: "block_fig", 7: "block_ad",
        8: "block_pillar", 9: "block_folio", 10: "block_rubi", 11: "block_chart",
        12: "block_eqn", 13: "block_cfm", 14: "block_eng", 15: "block_table",
        16: "line_title",
    }
    dummy_det = DEIMDetector(
        infer=_dummy_detector_infer,
        classes=_NDL_CLASSES,
        input_size=(64, 64),
    )
    dummy_rec = PARSeqRecognizer(
        infer=_dummy_recognizer_infer,
        charlist=["あ", "い", "う"],
        input_size=(64, 16),
    )

    img = np.zeros((100, 100, 3), dtype=np.uint8)
    result = asyncio.run(run_ocr_on_image(
        img,
        detector=dummy_det,
        recognizer30=dummy_rec,
        recognizer50=dummy_rec,
        recognizer100=dummy_rec,
        img_name="smoke.jpg",
    ))

    assert isinstance(result.xml, str), "xml must be str"
    assert isinstance(result.text, str), "text must be str"
    assert isinstance(result.json, dict), "json must be dict"
    assert result.viz_png is None

    print("Smoke test PASSED")
    print(f"  xml length  : {len(result.xml)}")
    print(f"  text        : {repr(result.text)}")


if __name__ == "__main__":
    main()
