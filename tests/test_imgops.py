"""T1-1b: Verify imgops pixel-level equivalence against OpenCV where available."""
import numpy as np
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ndlocr_web.imgops import (
    resize_bicubic,
    resize_bilinear,
    rotate_ccw_90,
    pad_to_square,
)


def _random_rgb(h=64, w=48, seed=0):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, (h, w, 3), dtype=np.uint8)


def _smooth_rgb(h=128, w=96):
    """Smooth gradient image; Pillow and OpenCV bicubic/bilinear agree closely on it."""
    y = np.linspace(0, 255, h, dtype=np.float32)
    x = np.linspace(0, 255, w, dtype=np.float32)
    yy, xx = np.meshgrid(y, x, indexing="ij")
    img = np.stack([yy, xx, (yy + xx) / 2], axis=2).clip(0, 255).astype(np.uint8)
    return img


def test_resize_bicubic_shape():
    img = _random_rgb(64, 48)
    out = resize_bicubic(img, (32, 16))
    assert out.shape == (16, 32, 3)


def test_resize_bilinear_shape():
    img = _random_rgb(64, 48)
    out = resize_bilinear(img, (32, 16))
    assert out.shape == (16, 32, 3)


def test_rotate_ccw_90_shape():
    img = _random_rgb(64, 48)
    out = rotate_ccw_90(img)
    assert out.shape == (48, 64, 3)


def test_rotate_ccw_90_content():
    img = _random_rgb(4, 6)
    out = rotate_ccw_90(img)
    # np.rot90(k=1): out shape (W, H); out[r, c] == img[c, W-1-r]
    W = img.shape[1]
    for r in range(out.shape[0]):
        for c in range(out.shape[1]):
            assert np.array_equal(out[r, c], img[c, W - 1 - r])


def test_pad_to_square_wide():
    img = _random_rgb(30, 50)
    padded, side = pad_to_square(img)
    assert side == 50
    assert padded.shape == (50, 50, 3)
    assert np.array_equal(padded[:30, :50], img)
    assert np.all(padded[30:, :] == 0)


def test_pad_to_square_tall():
    img = _random_rgb(60, 40)
    padded, side = pad_to_square(img)
    assert side == 60
    assert padded.shape == (60, 60, 3)
    assert np.array_equal(padded[:60, :40], img)
    assert np.all(padded[:, 40:] == 0)


def test_pad_to_square_square():
    img = _random_rgb(32, 32)
    padded, side = pad_to_square(img)
    assert side == 32
    assert np.array_equal(padded, img)


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("cv2"),
    reason="opencv not installed",
)
def test_resize_bicubic_mse_vs_opencv():
    import cv2
    img = _smooth_rgb(128, 96)
    target = (48, 64)  # (w, h)
    our = resize_bicubic(img, target).astype(np.float32)
    cv_out = cv2.resize(img, target, interpolation=cv2.INTER_CUBIC).astype(np.float32)
    mse = float(np.mean((our - cv_out) ** 2))
    assert mse < 0.5, f"BICUBIC MSE vs cv2 is {mse:.4f} >= 0.5"


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("cv2"),
    reason="opencv not installed",
)
def test_resize_bilinear_mse_vs_opencv():
    import cv2
    img = _smooth_rgb(128, 96)
    target = (48, 64)
    our = resize_bilinear(img, target).astype(np.float32)
    cv_out = cv2.resize(img, target, interpolation=cv2.INTER_LINEAR).astype(np.float32)
    mse = float(np.mean((our - cv_out) ** 2))
    assert mse < 0.5, f"BILINEAR MSE vs cv2 is {mse:.4f} >= 0.5"
