import sys
sys.setrecursionlimit(5000)

from .detector import DEIMDetector
from .recognizer import PARSeqRecognizer
from .pipeline import run_ocr_on_image, OcrResult

__all__ = ["DEIMDetector", "PARSeqRecognizer", "run_ocr_on_image", "OcrResult"]
