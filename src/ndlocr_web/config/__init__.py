"""Config helpers for ndlocr_web.

Uses importlib.resources so the YAML files are accessible whether the package
is installed from a wheel (Pyodide) or run directly from the source tree.
"""
from __future__ import annotations
from importlib.resources import files


def load_class_names() -> dict[int, str]:
    """Return DEIM class-name mapping {index: name} from ndl.yaml."""
    from yaml import safe_load
    text = files("ndlocr_web.config").joinpath("ndl.yaml").read_text(encoding="utf-8")
    return safe_load(text)["names"]


def load_charlist() -> list[str]:
    """Return PARSeq character list from NDLmoji.yaml."""
    from yaml import safe_load
    text = files("ndlocr_web.config").joinpath("NDLmoji.yaml").read_text(encoding="utf-8")
    return list(safe_load(text)["model"]["charset_train"])
