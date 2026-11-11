import argparse
import hashlib
from http.client import IncompleteRead
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import model_download


class ModelDownloadTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.cache = self.root / "cache"
        self.bundled = self.root / "model"
        self.bundled.mkdir()
        self.content = b"test model"
        self.digest = hashlib.sha256(self.content).hexdigest()
        cache_patch = patch.object(model_download, "model_cache_dir", return_value=self.cache)
        cache_patch.start()
        self.addCleanup(cache_patch.stop)
        network_patch = patch.object(model_download, "urlopen")
        self.network = network_patch.start()
        self.addCleanup(network_patch.stop)

    def resolve(self):
        return model_download.get_model_path("test.onnx", self.digest, self.bundled)

    def test_bundled_model_is_used_without_download(self):
        model = self.bundled / "test.onnx"
        model.write_bytes(self.content)
        self.assertEqual(self.resolve(), model)
        self.network.assert_not_called()
        self.assertFalse(self.cache.exists())

    def test_download_is_verified_and_cached(self):
        self.network.return_value = io.BytesIO(self.content)
        model = self.resolve()
        self.assertEqual(model.read_bytes(), self.content)
        self.network.assert_called_once_with(
            model_download.MODEL_BASE_URL + "test.onnx", timeout=60
        )
        self.assertEqual(self.resolve(), model)
        self.assertEqual(self.network.call_count, 1)
        self.assertEqual(list(self.cache.iterdir()), [model])

    def test_corrupt_cache_is_replaced(self):
        self.cache.mkdir()
        model = self.cache / "test.onnx"
        model.write_bytes(b"corrupt")
        self.network.return_value = io.BytesIO(self.content)
        self.assertEqual(self.resolve().read_bytes(), self.content)
        self.network.assert_called_once()

    def test_failed_downloads_are_not_cached(self):
        for error in (URLError("offline"), TimeoutError("timeout")):
            with self.subTest(error=error):
                self.network.side_effect = error
                with self.assertRaisesRegex(RuntimeError, "Could not download model"):
                    self.resolve()
                self.assertEqual(list(self.cache.iterdir()), [])

    def test_wrong_hash_is_not_cached(self):
        self.network.return_value = io.BytesIO(b"wrong model")
        with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
            self.resolve()
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_interrupted_response_is_not_cached(self):
        for error in (OSError("connection lost"), IncompleteRead(b"partial", 100)):
            with self.subTest(error=error):
                response = self.network.return_value.__enter__.return_value
                response.read.side_effect = [b"partial", error]
                with self.assertRaisesRegex(RuntimeError, "Could not download model"):
                    self.resolve()
                self.assertEqual(list(self.cache.iterdir()), [])

    def test_only_unspecified_models_are_resolved(self):
        args = argparse.Namespace(**{
            option: None for option in model_download.MODELS
        })
        args.det_weights = "custom.onnx"
        with patch.object(model_download, "get_model_path", return_value=self.root / "cached.onnx") as resolve:
            model_download.resolve_model_paths(args, self.bundled)
        self.assertEqual(args.det_weights, "custom.onnx")
        self.assertEqual(resolve.call_count, 3)
        self.assertEqual(args.rec_weights, str(self.root / "cached.onnx"))

    def test_cache_locations(self):
        for platform, expected in (
            ("win32", self.root / "local" / "ndlocr-lite" / "models" / "v1.2"),
            ("darwin", self.root / "Library" / "Caches" / "ndlocr-lite" / "models" / "v1.2"),
            ("linux", self.root / "xdg" / "ndlocr-lite" / "models" / "v1.2"),
        ):
            with self.subTest(platform=platform), patch.object(model_download.sys, "platform", platform), patch.object(Path, "home", return_value=self.root), patch.dict(
                model_download.os.environ,
                {"LOCALAPPDATA": str(self.root / "local"), "XDG_CACHE_HOME": str(self.root / "xdg")},
            ):
                # The per-test cache override is bypassed for the platform test.
                self.assertEqual(self.cache_function(), expected)

    def test_explicit_models_do_not_download(self):
        args = argparse.Namespace(**{
            option: "custom.onnx" for option in model_download.MODELS
        })
        model_download.resolve_model_paths(args, self.bundled)
        self.network.assert_not_called()
        self.assertFalse(self.cache.exists())

    cache_function = staticmethod(model_download.model_cache_dir)


if __name__ == "__main__":
    unittest.main()
