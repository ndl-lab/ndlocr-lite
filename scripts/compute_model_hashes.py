#!/usr/bin/env python3
"""Generate manifest.json with SHA-256 hashes for ONNX model files.

Usage:
    python scripts/compute_model_hashes.py [--models-dir PATH] [--out PATH] [--version VERSION]
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

MODELS = [
    ("deim",   "deim-s-1024x1024.onnx"),
    ("rec30",  "parseq-ndl-16x256-30-tiny-192epoch-tegaki3.onnx"),
    ("rec50",  "parseq-ndl-16x384-50-tiny-146epoch-tegaki2.onnx"),
    ("rec100", "parseq-ndl-16x768-100-tiny-165epoch-tegaki2.onnx"),
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute model hashes and write manifest.json")
    parser.add_argument("--models-dir", default="src/model", help="Directory containing ONNX files")
    parser.add_argument("--out", default="ndlocr-lite-web/public/manifest.json", help="Output path")
    parser.add_argument("--version", default=None, help="Manifest version string (default: today's date)")
    args = parser.parse_args()

    if args.version is None:
        from datetime import date
        args.version = date.today().strftime("%Y.%m.%d")

    models_dir = Path(args.models_dir)
    entries = []
    for model_id, fname in MODELS:
        path = models_dir / fname
        if not path.exists():
            raise FileNotFoundError(f"Model not found: {path}")
        size = path.stat().st_size
        sha256 = sha256_file(path)
        entries.append({"id": model_id, "file": fname, "sha256": sha256, "size": size})
        print(f"  {model_id}: {sha256[:16]}... ({size:,} bytes)")

    manifest = {"version": args.version, "models": entries}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Written: {out_path}")


if __name__ == "__main__":
    main()
