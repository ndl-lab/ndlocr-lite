"""Download and cache default models for the pip-distributed CLI."""

import hashlib
from http.client import IncompleteRead
import os
from pathlib import Path
import sys
import tempfile
from urllib.error import URLError
from urllib.request import urlopen


MODEL_BASE_URL = "https://lab.ndl.go.jp/dataset/ndlocr-lite/onnx/v2/"
TABLE_MODEL = (
    "tablecenternet_s_800_v3.onnx",
    "fc26e20e0ece97be0d03b495481c33a3982f268966920297061b8c90215cdefa",
)
MODELS = {
    "det_weights": (
        "deim-s-1024x1024-261124.onnx",
        "f9a41c8453dd687f370bee199fd6d47ecf583cc14f1d839a0b416db39b356e8c",
    ),
    "rec_weights30": (
        "parseq-ndl-24x256-30-tiny-499epoch-261124.onnx",
        "063ec5b3201bce79d56c3ad73b5726cf76ec6a82f42e11a89466ae683755296c",
    ),
    "rec_weights50": (
        "parseq-ndl-24x384-50-tiny-278epoch-261124.onnx",
        "d3dd9e7733aeff5c9f50687c7da6e8d01915a0bf07cfbefe1d9ac58241c61471",
    ),
    "rec_weights": (
        "parseq-ndl-24x768-100-tiny-153epoch-261124.onnx",
        "06462b0dbd5b0b8508545c8c3d485cf20dbf4ffa652fe145e69c9e7457080602",
    ),
}


def model_cache_dir():
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Caches"
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return root / "ndlocr-lite" / "models" / "v2"


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_model_path(filename, expected_hash, bundled_dir):
    bundled = bundled_dir / filename
    if bundled.is_file():
        return bundled

    cache = model_cache_dir()
    destination = cache / filename
    if destination.is_file() and _sha256(destination) == expected_hash:
        return destination

    cache.mkdir(parents=True, exist_ok=True)
    url = MODEL_BASE_URL + filename
    print(f"Downloading model: {url}", file=sys.stderr)
    temporary = None
    try:
        # A unique temporary file and atomic replacement also allow concurrent CLIs.
        with tempfile.NamedTemporaryFile(dir=cache, suffix=".part", delete=False) as output:
            temporary = Path(output.name)
            with urlopen(url, timeout=60) as response:
                for chunk in iter(lambda: response.read(1024 * 1024), b""):
                    output.write(chunk)
        if _sha256(temporary) != expected_hash:
            raise ValueError(f"SHA-256 mismatch for model {filename}")
        temporary.replace(destination)
    except (OSError, URLError, IncompleteRead, ValueError) as error:
        raise RuntimeError(f"Could not download model from {url}: {error}") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


def resolve_model_paths(args, bundled_dir):
    for option, (filename, expected_hash) in MODELS.items():
        if getattr(args, option) is None:
            setattr(args, option, str(get_model_path(filename, expected_hash, bundled_dir)))
