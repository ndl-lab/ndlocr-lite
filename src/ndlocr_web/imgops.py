"""OpenCV-free image operations using NumPy and Pillow."""
import numpy as np
from PIL import Image


def resize_bicubic(img: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Resize img to (width, height) using bicubic interpolation."""
    w, h = size
    pil = Image.fromarray(img)
    resized = pil.resize((w, h), Image.BICUBIC)
    return np.array(resized)


def resize_bilinear(img: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Resize img to (width, height) using bilinear interpolation."""
    w, h = size
    pil = Image.fromarray(img)
    resized = pil.resize((w, h), Image.BILINEAR)
    return np.array(resized)


def rotate_ccw_90(img: np.ndarray) -> np.ndarray:
    """Rotate image 90 degrees counter-clockwise."""
    return np.rot90(img, k=1)


def pad_to_square(img: np.ndarray) -> tuple[np.ndarray, int]:
    """Pad image to square (top-left aligned) and return (padded, max_side)."""
    h, w = img.shape[:2]
    max_side = max(h, w)
    if len(img.shape) == 3:
        padded = np.zeros((max_side, max_side, img.shape[2]), dtype=img.dtype)
    else:
        padded = np.zeros((max_side, max_side), dtype=img.dtype)
    padded[:h, :w] = img
    return padded, max_side
