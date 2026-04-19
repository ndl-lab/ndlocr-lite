"""Thin wrapper around ndl_parser.convert_to_xml_string3."""
from __future__ import annotations
import sys
import os

# Allow importing ndl_parser from the parent src/ directory when this package
# is used from within that directory tree.
_SRC_DIR = os.path.dirname(os.path.dirname(__file__))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from ndl_parser import convert_to_xml_string3  # noqa: E402


def build_xml(
    img_w: int,
    img_h: int,
    img_name: str,
    classes: list[str],
    result_obj: list[dict],
) -> str:
    """Return the raw XML fragment produced by ndl_parser."""
    return convert_to_xml_string3(img_w, img_h, img_name, classes, result_obj)
