"""Thin wrapper around ndl_parser.convert_to_xml_string3."""
from __future__ import annotations

from .ndl_parser import convert_to_xml_string3


def build_xml(
    img_w: int,
    img_h: int,
    img_name: str,
    classes: list[str],
    result_obj: list[dict],
) -> str:
    """Return the raw XML fragment produced by ndl_parser."""
    return convert_to_xml_string3(img_w, img_h, img_name, classes, result_obj)
